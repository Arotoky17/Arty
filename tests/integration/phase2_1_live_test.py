"""
PHASE 2.1 PAPER TEST — 4-hour live TradingEngine run on MT5 Demo.

Connects to MT5 demo, runs the TradingEngine for 4 hours on EURUSD+XAUUSD
with ENHANCED diagnostics (DECISION_DIAGNOSTICS_ENABLED=true).

Usage:
    python phase2_1_live_test.py [--hours 4] [--symbols EURUSD,XAUUSD]

Environment:
    Reads MT5 credentials and trading mode from .env.
    TRADING_MODE=demo or paper (never live).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime

os.environ.setdefault("TRADING_MODE", "demo")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
os.environ.setdefault("DECISION_DIAGNOSTICS_ENABLED", "true")
os.environ.setdefault("DECISION_DIAGNOSTICS_INTERVAL", "10")

from arty_trading.application import TradingEngine
from arty_trading.application.trade_decision_debugger import TradeDecisionDebugger
from arty_trading.config import get_settings
from arty_trading.core.enums import LogCategory
from arty_trading.infrastructure.mt5 import MT5Connector, MT5MarketDataProvider
from arty_trading.logging import get_logger, setup_logging
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.execution import OrderExecutor, PaperOrderExecutor
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector

logger = get_logger(LogCategory.SYSTEM)


async def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.log_level, logs_dir=settings.logs_dir, app_env=settings.app_env)

    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    symbols = sys.argv[2] if len(sys.argv) > 2 else "EURUSD,XAUUSD"
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    logger.warning("=" * 60)
    logger.warning("PHASE 2.1 LIVE PAPER TEST — %d heures", int(hours))
    logger.warning("Mode=%s  Symbols=%s  Diag=%s",
                   settings.trading_mode.value, symbol_list, settings.decision_diagnostics_enabled)
    logger.warning("MT5 Server=%s  Login=%s", settings.mt5.server, settings.mt5.login)
    logger.warning("=" * 60)

    # Connect MT5
    connector = MT5Connector(settings=settings)
    market_data = MT5MarketDataProvider()
    connected = await connector.connect()
    if not connected:
        logger.error("MT5 connection FAILED. Check MT5 terminal is running.")
        return
    logger.warning("MT5 connected | server=%s | login=%s", settings.mt5.server, settings.mt5.login)

    # Init modules (following api/main.py pattern)
    smc_detector = SMCDetector()
    signal_validator = SignalValidator(
        min_risk_reward=settings.validator.min_risk_reward,
        max_spread=settings.validator.max_spread,
        require_htf_alignment=settings.validator.require_htf_alignment,
        require_news_filter=settings.validator.require_news_filter,
        min_confluence_count=settings.validator.min_confluence_count,
    )
    signal_generator = SignalGenerator(
        min_confidence=settings.signals.min_confidence,
        active_strategy=settings.signals.active_strategy,
        strategies=None,
        validator=signal_validator,
        decision_engine=DecisionEngine(settings=settings.decision),
    )

    risk_manager = RiskManager(settings=settings.risk)
    risk_manager.market_data = market_data

    # Use Paper executor (demo mode doesn't place real orders unless LIVE)
    # In demo mode with ALLOW_LIVE_TRADING=false, we use paper executor
    from arty_trading.modules.execution import OrderExecutor
    executor = PaperOrderExecutor()

    decision_debugger = TradeDecisionDebugger(
        enabled=settings.decision_diagnostics_enabled,
        summary_interval=settings.decision_diagnostics_interval,
    )

    engine = TradingEngine(
        settings=settings,
        market_data=market_data,
        smc_detector=smc_detector,
        signal_generator=signal_generator,
        risk_manager=risk_manager,
        executor=executor,
        mt5_connector=connector,
        decision_debugger=decision_debugger,
    )

    # Start engine (creates background task that calls analyze_symbol in a loop)
    # For direct live testing, call analyze_symbol directly in a loop
    start_time = time.time()
    deadline = start_time + hours * 3600

    logger.warning("Engine running directly | deadline=%s UTC",
                   datetime.fromtimestamp(deadline, tz=UTC).isoformat())

    cycle = 0
    try:
        while time.time() < deadline:
            cycle += 1
            cycle_start = time.time()
            for symbol in symbol_list:
                try:
                    await engine.analyze_symbol(symbol)
                except Exception as exc:
                    logger.error("Error analyzing %s: %s", symbol, exc)

            elapsed = time.time() - start_time
            remaining = deadline - time.time()
            cycle_time = time.time() - cycle_start
            logger.info("Cycle #%d | elapsed=%.0fs | remaining=%.0fs | cycle_time=%.1fs",
                        cycle, elapsed, remaining, cycle_time)

            if remaining <= 0:
                break

            # Wait for next M5 candle (poll every 30s)
            await asyncio.sleep(30)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
    finally:
        await connector.disconnect()
        elapsed = time.time() - start_time
        logger.warning("=" * 60)
        logger.warning("TEST COMPLETED | runtime=%.0fs (%.1f min) | cycles=%d",
                       elapsed, elapsed / 60, cycle)
        status = engine.get_status()
        logger.warning("Status: %s", status)
        logger.warning("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
