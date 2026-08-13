# =============================================================
# TEST D'EFFICACITÉ du bot sur données réelles (démo).
#
# Objectif : mesurer l'efficacité des signaux (validés par toute la
# chaîne de production) AVANT de passer en trading réel. Aucun ordre
# n'est ouvert : on simule l'exécution via le BacktestEngine.
#
# Données : bougies réelles H1 + M5 récupérées depuis le compte MT5
# démo configuré dans le fichier .env (MT5_* / TRADING_MODE=demo).
#
# Usage :
#   python scripts/test_efficacy.py [--symbols EURUSD,XAUUSD]
#                                   [--days 30]
# =============================================================

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from arty_trading.config.settings import get_settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.logging.logger import get_logger
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector

logger = get_logger("efficacy")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test d'efficacité du bot sur données réelles (démo)")
    parser.add_argument("--symbols", type=str, default="EURUSD,XAUUSD", help="Symboles séparés par des virgules")
    parser.add_argument("--days", type=int, default=30, help="Nombre de jours d'historique à récupérer")
    return parser.parse_args()


def df_to_candles(df, symbol: str, timeframe: TimeFrame) -> list[Candle]:
    """Convertit un DataFrame OHLC (format provider) en liste de Candle triée."""
    candles: list[Candle] = []
    for _, row in df.iterrows():
        ts = row.get("time") or row.get("datetime")
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        candles.append(
            Candle(
                symbol=symbol.upper(),
                timeframe=timeframe,
                time=ts,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=int(row.get("tick_volume", row.get("volume", 100))),
                spread=3,
            )
        )
    candles.sort(key=lambda c: c.time)
    return candles


def build_signal_chain():
    """Reproduit la chaîne de signaux de production (identique à api.main)."""
    settings = get_settings()
    validator = SignalValidator(
        min_risk_reward=settings.validator.min_risk_reward,
        max_spread=settings.validator.max_spread,
        require_htf_alignment=settings.validator.require_htf_alignment,
        require_news_filter=settings.validator.require_news_filter,
        min_confluence_count=settings.validator.min_confluence_count,
    )
    generator = SignalGenerator(
        min_confidence=settings.signals.min_confidence,
        active_strategy=settings.signals.active_strategy,
        decision_engine=DecisionEngine(settings.decision) if settings.decision.enabled else None,
        validator=validator,
    )
    return SMCDetector(), generator


async def fetch_candles(mt5_connector, market_data, symbol: str, timeframe: TimeFrame, days: int) -> list[Candle]:
    """Récupère l'historique réel via le provider MT5 (compte démo)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    df = await market_data.get_historical(symbol, timeframe, start, end)
    if df is None or df.empty:
        return []
    return df_to_candles(df, symbol, timeframe)
async def run_efficacy(mt5_connector, market_data, symbol: str, m5: list[Candle]) -> dict:
    """Lance le backtest sur les bougies M5 réelles (0 ordre réel)."""
    detector, generator = build_signal_chain()
    engine = BacktestEngine(initial_balance=Decimal("10000"), risk_per_trade=0.01, symbol=symbol)

    stats = await engine.run_async(m5, signal_generator=generator, smc_detector=detector)

    return {
        "symbol": symbol,
        "bars_reales": len(m5),
        "total_trades": stats.total_trades,
        "win_rate": stats.win_rate,
        "profit_factor": stats.profit_factor,
        "expectancy": stats.expectancy,
        "max_drawdown_pct": stats.max_drawdown,
        "final_balance": stats.final_balance,
    }


async def main() -> int:
    args = parse_args()
    settings = get_settings()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    from arty_trading.infrastructure.mt5.connector import MT5Connector
    from arty_trading.infrastructure.mt5.market_data import MT5MarketDataProvider, MT5_AVAILABLE

    if not MT5_AVAILABLE:
        logger.error(
            "Le package MetaTrader5 n'est pas disponible ici. "
            "Lance ce script sur la machine où le terminal MT5 (compte démo) est configuré et connecté."
        )
        return 1

    connector = MT5Connector(settings=settings)
    ok = await connector.connect()
    if not ok:
        logger.error("Connexion MT5 impossible. Vérifie MT5_* dans .env et que le terminal démo est lancé/connecté.")
        return 1

    market_data = MT5MarketDataProvider()

    print("\n" + "=" * 76)
    print("TEST D'EFFICACITÉ — signaux validés reproduits en backtest (aucun ordre réel)")
    print("=" * 76)

    try:
        for symbol in symbols:
            logger.info("Récupération de l'historique réel %s (H1 + M5, %d jns)", symbol, args.days)
            h1 = await fetch_candles(connector, market_data, symbol, TimeFrame.H1, args.days)
            m5 = await fetch_candles(connector, market_data, symbol, TimeFrame.M5, args.days)
            if len(m5) < 100:
                logger.warning("Pas assez de bougies M5 pour %s (%d) — ignoré", symbol, len(m5))
                continue

            result = await run_efficacy(connector, market_data, symbol, m5)
            print(f"\n--- {result['symbol']} ---")
            print(f"  Bougies réelles M5 : {result['bars_reales']}  (H1={len(h1)})")
            print(f"  Trades :          {result['total_trades']}")
            print(f"  Win rate :        {result['win_rate']:.2%}")
            print(f"  Profit factor :   {result['profit_factor']:.2f}")
            print(f"  Espérance/trade : {result['expectancy']}")
            print(f"  Max drawdown :    {result['max_drawdown_pct']:.2%}")
            print(f"  Balance finale :  {result['final_balance']:.2f}")
    finally:
        await connector.disconnect()

    print("\n" + "=" * 76)
    print("Interprétation : win_rate + profit_factor > 1 et drawdown maîtrisé")
    print("=> bon signe avant de passer PAPER/DEMO réel, puis LIVE.")
    print("C'est une simulation : aucune position réellement ouverte dans ce test.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))