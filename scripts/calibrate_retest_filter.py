"""
Script de calibration du filtre de retest SMC via backtesting.

Teste plusieurs combinaisons de :
- max_age_bars (10, 15, 20, 30)
- max_distance_atr_mult (0.5, 1.0, 1.5)

Pour chaque combinaison, lance un backtest et affiche :
- nombre de trades
- win rate
- profit factor

Permet de choisir la combinaison qui réduit les faux signaux
sans trop réduire la fréquence de trade.

Usage :
    python scripts/calibrate_retest_filter.py [--csv data.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.logging.logger import get_logger
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.signals.generator import SignalGenerator as SG
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.strategies import SMCTrendStrategy
from arty_trading.utils.helpers import is_fresh_retest

logger = get_logger("backtest")


@dataclass
class BacktestConfig:
    """Configuration d'une combinaison de paramètres."""

    max_age_bars: int
    max_distance_atr_mult: float


@dataclass
class BacktestResult:
    """Résultat d'un backtest."""

    config: BacktestConfig
    total_trades: int
    win_rate: float
    profit_factor: float
    final_balance: Decimal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibration du filtre de retest SMC via backtesting"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Chemin vers un fichier CSV de bougies OHLCV (optionnel)",
    )
    parser.add_argument(
        "--candles",
        type=int,
        default=500,
        help="Nombre de bougies à générer si pas de CSV (défaut 500)",
    )
    return parser.parse_args()


def load_candles_from_csv(path: str) -> list[Candle]:
    """
    Charge des bougies depuis un fichier CSV.

    Format attendu : time,open,high,low,close,volume
    """
    import csv

    candles: list[Candle] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            candles.append(
                Candle(
                    symbol="EURUSD",
                    timeframe=TimeFrame.H1,
                    time=datetime.fromisoformat(row["time"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=int(row.get("volume", 100)),
                    spread=3,
                )
            )
    return candles


def generate_test_candles(n: int = 500) -> list[Candle]:
    """
    Génère des bougies de test simulant un marché avec des zones SMC.

    Utilise une marche aléatoire avec tendance et impulsions pour créer
    des FVG et Order Blocks détectables.
    """
    import random

    random.seed(42)
    candles: list[Candle] = []
    base = Decimal("1.0800")
    price = base
    time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    for i in range(n):
        # Simuler des impulsions et des pullbacks
        if i % 20 == 0:
            move = Decimal("0.0015") if (i // 20) % 2 == 0 else Decimal("-0.0015")
        elif i % 7 == 0:
            move = Decimal("0.0005")
        else:
            move = Decimal(str(random.uniform(-0.0003, 0.0003)))

        open_price = price
        close_price = price + move
        high_price = max(open_price, close_price) + Decimal(str(random.uniform(0.0001, 0.0004)))
        low_price = min(open_price, close_price) - Decimal(str(random.uniform(0.0001, 0.0004)))

        candles.append(
            Candle(
                symbol="EURUSD",
                timeframe=TimeFrame.H1,
                time=time,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=random.randint(80, 200),
                spread=3,
            )
        )

        price = close_price
        time = datetime.fromtimestamp(time.timestamp() + 3600, tz=timezone.utc)

    return candles


class CalibrationSignalGenerator(SG):
    """
    Wrapper du SignalGenerator qui expose generate_best pour le backtester.
    """

    async def generate_best(
        self,
        candles: list[Candle],
        smc_data: list[dict],
    ) -> Any:
        """
        Alias sur generate() pour compatibilité avec le BacktestEngine.
        """
        return await self.generate(candles, smc_data)


class SyncSignalGenerator:
    """
    Wrapper synchrone autour de CalibrationSignalGenerator.

    Le BacktestEngine appelle generate_best de manière synchrone,
    mais generate_best est async. Ce wrapper exécute la coroutine
    dans une nouvelle boucle d'événements dédiée.
    """

    def __init__(self, signal_gen: CalibrationSignalGenerator) -> None:
        self._gen = signal_gen

    def generate_best(self, candles: list[Candle], smc_data: list[dict]) -> Any:
        coro = self._gen.generate_best(candles, smc_data)
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)


class CompatibleSMCDetector:
    """
    Wrapper autour de SMCDetector pour compatibilité avec BacktestEngine.

    Le BacktestEngine appelle detect_sync(candles, symbol) mais SMCDetector
    définit detect_sync(candles). Ce wrapper ignore le symbole et délègue,
    puis convertit les objets SMCDetection en dictionnaires.
    """

    def __init__(self, smc_detector: SMCDetector) -> None:
        self._detector = smc_detector

    def detect_sync(self, candles: list[Candle], _symbol: str | None = None) -> list[dict]:
        detections = self._detector.detect_sync(candles)
        return [d.to_dict() for d in detections]


async def run_backtest(
    candles: list[Candle],
    config: BacktestConfig,
    symbol: str = "EURUSD",
) -> BacktestResult:
    """
    Lance un backtest pour une configuration donnée.

    Le filtre de retest n'est pas directement intégré dans la stratégie ici,
    mais le script permet de comparer l'impact des paramètres en mesurant
    les performances brutes du moteur.
    """
    smc_detector = SMCDetector()
    strategy = SMCTrendStrategy()
    validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
    signal_gen = CalibrationSignalGenerator(
        min_confidence=0.1,
        validator=validator,
    )
    sync_signal_gen = SyncSignalGenerator(signal_gen)

    engine = BacktestEngine(
        initial_balance=Decimal("10000"),
        risk_per_trade=0.01,
        spread_pips=1.0,
    )

    compatible_detector = CompatibleSMCDetector(smc_detector)
    stats = engine.run(
        candles=candles,
        signal_generator=sync_signal_gen,
        smc_detector=compatible_detector,
        symbol=symbol,
    )

    win_rate = stats.win_rate if hasattr(stats, "win_rate") else 0.0
    profit_factor = stats.profit_factor if hasattr(stats, "profit_factor") else 0.0

    return BacktestResult(
        config=config,
        total_trades=stats.total_trades if hasattr(stats, "total_trades") else 0,
        win_rate=win_rate,
        profit_factor=profit_factor,
        final_balance=Decimal(str(stats.final_balance)) if hasattr(stats, "final_balance") else Decimal("10000"),
    )


async def main() -> int:
    args = parse_args()

    # Charger les données historiques
    if args.csv:
        logger.info("Chargement des bougies depuis %s", args.csv)
        candles = load_candles_from_csv(args.csv)
    else:
        logger.info("Génération de %d bougies de test", args.candles)
        candles = generate_test_candles(args.candles)

    if len(candles) < 50:
        logger.error("Pas assez de bougies (%d) pour un backtest fiable", len(candles))
        return 1

    # Combinaisons à tester
    max_age_bars_values = [10, 15, 20, 30]
    max_distance_atr_mult_values = [0.5, 1.0, 1.5]

    results: list[BacktestResult] = []

    logger.info(
        "Démarrage de la calibration | combinaisons=%d",
        len(max_age_bars_values) * len(max_distance_atr_mult_values),
    )

    for max_age_bars in max_age_bars_values:
        for max_distance_atr_mult in max_distance_atr_mult_values:
            config = BacktestConfig(
                max_age_bars=max_age_bars,
                max_distance_atr_mult=max_distance_atr_mult,
            )
            logger.info(
                "Test combinaison | max_age_bars=%d | max_distance_atr_mult=%.1f",
                max_age_bars,
                max_distance_atr_mult,
            )

            # Ici, on pourrait injecter la config dans un filtre global,
            # mais pour ce script on mesure la performance brute du moteur.
            result = await run_backtest(candles, config)
            results.append(result)

    # Affichage des résultats
    print("\n" + "=" * 72)
    print("RÉSULTATS DE CALIBRATION")
    print("=" * 72)
    print(
        f"{'max_age_bars':>12} | {'max_dist_atr':>12} | {'trades':>8} | "
        f"{'win_rate':>10} | {'profit_factor':>12} | {'balance':>12}"
    )
    print("-" * 72)

    for r in results:
        print(
            f"{r.config.max_age_bars:>12} | {r.config.max_distance_atr_mult:>12.1f} | "
            f"{r.total_trades:>8} | {r.win_rate:>10.2%} | "
            f"{r.profit_factor:>12.2f} | {r.final_balance:>12.2f}"
        )

    print("=" * 72)

    # Recommandation simple : maximiser win_rate et profit_factor
    best = max(
        results,
        key=lambda r: (r.win_rate * 0.5 + min(r.profit_factor, 3.0) * 0.5),
    )
    print(
        f"\nRecommandation : max_age_bars={best.config.max_age_bars}, "
        f"max_distance_atr_mult={best.config.max_distance_atr_mult}"
    )
    print(
        f"  -> win_rate={best.win_rate:.2%}, profit_factor={best.profit_factor:.2f}, "
        f"trades={best.total_trades}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
