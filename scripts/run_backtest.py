"""
Backtest de la NOUVELLE version du bot.

Utilise le ``BacktestEngine.run_async`` (nouvelle API) avec le pipeline complet :
SMCDetector -> SignalGenerator -> SignalValidator -> simulation de trades.

Génère des données historiques synthétiques avec structure SMC réaliste
(impulsions, pullbacks, BOS/FVG/Order Block/OTE) puis balaie plusieurs
configurations (validateur on/off, seuil de confiance) pour caractériser la
nouvelle version.

Usage :
    python scripts/run_backtest.py [--symbol XAUUSD] [--candles 600]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.strategies import SMCTrendStrategy


@dataclass
class Scale:
    """Échelle de prix par symbole."""

    base: float
    impulse: float
    push: float
    noise: float


SCALES: dict[str, Scale] = {
    "EURUSD": Scale(base=1.0800, impulse=0.0015, push=0.0005, noise=0.0003),
    "XAUUSD": Scale(base=2000.0, impulse=1.5, push=0.5, noise=0.3),
}


def generate_candles(symbol: str, n: int = 600, seed: int = 42) -> list[Candle]:
    """Génère des bougies H1 avec structure SMC (impulsions + pullbacks)."""
    import random

    rng = random.Random(seed)
    sc = SCALES[symbol]
    time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = Decimal(str(sc.base))
    candles: list[Candle] = []

    timespan = TimeFrame.H1

    for i in range(n):
        if i % 20 == 0:
            move = Decimal(str(sc.impulse)) if (i // 20) % 2 == 0 else Decimal(str(-sc.impulse))
        elif i % 7 == 0:
            move = Decimal(str(sc.push))
        else:
            move = Decimal(str(rng.uniform(-sc.noise, sc.noise)))

        o = price
        c = price + move
        h = max(o, c) + Decimal(str(rng.uniform(0.0001, 0.0004))) if symbol == "EURUSD" else max(o, c) + Decimal(str(rng.uniform(0.1, 0.4)))
        l = min(o, c) - Decimal(str(rng.uniform(0.0001, 0.0004))) if symbol == "EURUSD" else min(o, c) - Decimal(str(rng.uniform(0.1, 0.4)))

        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timespan,
                time=time,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=rng.randint(80, 200),
                spread=3,
            )
        )
        price = c
        time = datetime.fromtimestamp(time.timestamp() + 3600, tz=timezone.utc)

    return candles


@dataclass
class Config:
    name: str
    min_confidence: float
    validator: SignalValidator | None


def configure_detector_for_symbol(detector: SMCDetector, symbol: str) -> None:
    from arty_trading.config.operational import apply_operational_definitions
    apply_operational_definitions(detector, symbol)


async def run_one(symbol: str, candles: list[Candle], cfg: Config) -> dict[str, Any]:
    detector = SMCDetector()
    configure_detector_for_symbol(detector, symbol)
    strategy = SMCTrendStrategy()
    generator = SignalGenerator(
        min_confidence=cfg.min_confidence,
        active_strategy="SMC Trend Following",
        strategies=[strategy],
        validator=cfg.validator,
    )
    engine = BacktestEngine(
        initial_balance=Decimal("10000"),
        risk_per_trade=0.01,
        symbol=symbol,
    )
    stats = await engine.run_async(candles, signal_generator=generator, smc_detector=detector)
    return {
        "config": cfg.name,
        "trades": stats.total_trades,
        "win_rate": stats.win_rate,
        "profit_factor": stats.profit_factor,
        "sharpe": stats.sharpe_ratio,
        "max_dd": stats.max_drawdown,
        "final_balance": stats.final_balance,
        "expectancy": stats.expectancy,
        "cost_sensitivity": stats.cost_sensitivity,
        "cost_assumptions": stats.cost_assumptions,
        "xauusd_diagnostics": stats.xauusd_diagnostics,
        "execution_audit": stats.execution_audit,
        "consec_wins": stats.max_consecutive_wins,
        "consec_losses": stats.max_consecutive_losses,
        # Phase 3F : breakdown par type de setup / direction.
        "setup_breakdown": engine.setup_type_breakdown(),
    }


def print_setup_breakdown(name: str, breakdown: dict[str, dict]) -> None:
    """Affiche les statistiques par type de setup (Phase 3F)."""
    if not breakdown:
        return
    print(f"\n  Breakdown par type de setup — {name}:")
    print(f"  {'type':<38} {'trades':>6} {'win%':>7} {'avg R':>7} {'profit':>10}")
    for key, s in sorted(breakdown.items()):
        if "|" in key:  # Lignes par type|direction, affichées indentées
            print(
                f"    {'  ' + key:<36} {s['trades']:>6} {s['win_rate']*100:>6.1f}% "
                f"{s['avg_r']:>7.2f} {s['total_profit']:>10.2f}"
            )
        else:
            print(
                f"  {key:<38} {s['trades']:>6} {s['win_rate']*100:>6.1f}% "
                f"{s['avg_r']:>7.2f} {s['total_profit']:>10.2f}"
            )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest nouvelle version Arty")
    parser.add_argument("--symbol", default="XAUUSD", choices=["XAUUSD"])
    parser.add_argument("--candles", type=int, default=600)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    print(f"Génération de {args.candles} bougies H1 pour {symbol} ...")
    candles = generate_candles(symbol, args.candles)

    max_spread = 200 if symbol == "XAUUSD" else 30
    configs: list[Config] = [
        Config("Stratégie seule (conf=0.30)", 0.30, None),
        Config("Stratégie seule (conf=0.60)", 0.60, None),
        Config("Stratégie seule (conf=0.85)", 0.85, None),
        Config("Validateur RR>=1.5 (conf=0.60)", 0.60, SignalValidator(min_risk_reward=1.5, max_spread=max_spread)),
        Config("Validateur RR>=2.0 (conf=0.60)", 0.60, SignalValidator(min_risk_reward=2.0, max_spread=max_spread)),
    ]

    print("=" * 96)
    print(f"BACKTEST NOUVELLE VERSION — {symbol} | bougies={args.candles} | balance initiale=10000 | risque=1%/trade")
    print("=" * 96)
    header = f"{'configuration':<28} {'trades':>6} {'win%':>7} {'PF':>6} {'sharpe':>7} {'maxDD':>7} {'balance':>12} {'exp':>10}"
    print(header)
    print("-" * 96)

    for cfg in configs:
        r = await run_one(symbol, candles, cfg)
        print(
            f"{r['config']:<28} {r['trades']:>6} {r['win_rate']*100:>6.1f}% "
            f"{r['profit_factor']:>6.2f} {r['sharpe']:>7.2f} {r['max_dd']*100:>6.1f}% "
            f"{r['final_balance']:>12.2f} {float(r['expectancy']):>10.2f}"
        )
        print("Cost sensitivity:", r["cost_sensitivity"])
        print("Cost assumptions:", r["cost_assumptions"])
        if cfg.validator is not None:
            print_setup_breakdown(r["config"], r["setup_breakdown"])

    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
