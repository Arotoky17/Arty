import io
import sys
import asyncio
import logging
from collections import Counter

sys.path.insert(0, "src")
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

settings = Settings()
m5, spans = generate_multi_regime_candles(n_m5=1200, seed=42)
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

trends = Counter()
stages = Counter()
reasons = Counter()

orig_generate = gen.generate


async def spy_generate(*a, **kw):
    res = await orig_generate(*a, **kw)
    return res


engine = MTFBacktestEngine(
    initial_balance=Decimal("10000"), risk_per_trade=0.01,
    symbol="XAUUSD", settings=settings, setup_tracker=tracker,
)

# Wrapper du moteur pour compter les tendances/stades de rejet.
orig_process = engine._process_m5_close


async def spy_process(candle, index, h1_view, m15_view, m5_view):
    ctx = await engine._context_builder.build(
        "XAUUSD", h1_view, m5_view, setup_tf_candles=m15_view
    )
    if ctx is not None:
        trends[getattr(ctx, "master_trend", "?")] += 1
        sig = await gen.generate(
            m5_view, ctx.ltf_smc_data,
            htf_smc_data=ctx.htf_smc_data,
            master_trend=ctx.master_trend, market_context=ctx,
        )
        stages[gen.last_rejection_stage] += 1
        reasons[gen.last_rejection_reason] += 1
    return None


engine._process_m5_close = spy_process

# Préparer le contexte interne du moteur (normalement fait dans run_mtf_async).
from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.modules.backtesting.mtf_engine import _CachedSMCDetector


async def empty_dl(s, tf):
    return []


engine._context_builder = MarketContextBuilder(
    smc_detector=_CachedSMCDetector(det), download_data=empty_dl
)
engine._signal_generator = gen
engine._trade_journal = []
engine._journal_by_ticket = {}

async def main():
    await engine.run_mtf_async(m5, m15, h1, gen, det, regime_spans=spans)
    out = [
        f"trades={len(engine.trades)}",
        f"trends={dict(trends)}",
        f"stages={dict(stages)}",
        f"reasons={dict(reasons)}",
    ]
    io.open("_diag2.txt", "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out))


asyncio.run(main())
