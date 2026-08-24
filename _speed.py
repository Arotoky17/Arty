import io
import sys
import time
import asyncio

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import logging

logging.getLogger("arty_trading").setLevel(logging.WARNING)

from decimal import Decimal
from arty_trading.config.settings import Settings
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)
from arty_trading.modules.backtesting.mtf_engine import MTFBacktestEngine
from arty_trading.modules.backtesting.symbol_config import configure_detector_for_symbol
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector, SetupTracker
from arty_trading.modules.strategies import SMCTrendStrategy


async def main():
    settings = Settings()
    m5, spans = generate_multi_regime_candles(
        n_m5=2000, seed=42, regimes=[("BULL", 2000)]
    )
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    det = SMCDetector()
    configure_detector_for_symbol(det, "XAUUSD")
    tracker = SetupTracker()
    gen = SignalGenerator(
        min_confidence=0.30,
        active_strategy="SMC Trend Following",
        strategies=[SMCTrendStrategy()],
        validator=SignalValidator(min_risk_reward=1.5, max_spread=200),
        decision_engine=DecisionEngine(settings.decision),
        setup_tracker=tracker,
    )
    eng = MTFBacktestEngine(
        initial_balance=Decimal("10000"),
        risk_per_trade=0.01,
        symbol="XAUUSD",
        settings=settings,
        setup_tracker=tracker,
        h1_window=170,
        m15_window=80,
        m5_window=80,
    )
    t = time.time()
    await eng.run_mtf_async(m5, m15, h1, gen, det, regime_spans=spans)
    dt = time.time() - t
    out = [
        f"elapsed={dt:.1f}s trades={len(eng.trades)}",
        f"journal={len(eng.trade_journal)}",
    ]
    for e in eng.trade_journal[:10]:
        out.append(str({k: e.get(k) for k in ('setup_type','direction','tier','score','regime','profit','r_multiple')}))
    io.open("_speed.txt", "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out))


asyncio.run(main())
