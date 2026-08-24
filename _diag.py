import io
import sys
import time
import asyncio

sys.path.insert(0, "src")
import logging

logging.getLogger("arty_trading").setLevel(logging.WARNING)

from decimal import Decimal
from arty_trading.config.settings import Settings
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)
from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.modules.smc import SMCDetector, SetupTracker


async def main():
    settings = Settings()
    m5, spans = generate_multi_regime_candles(
        n_m5=2200, seed=42, regimes=[("BULL", 2200)]
    )
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    det = SMCDetector()

    async def empty_dl(s, tf):
        return []

    builder = MarketContextBuilder(smc_detector=det, download_data=empty_dl)
    tracker = SetupTracker()
    created = 0
    trends = {}
    ready = 0
    orig_create = tracker.create_setup

    def counting(**kw):
        nonlocal created
        created += 1
        return orig_create(**kw)

    tracker.create_setup = counting

    from datetime import timedelta

    h1_idx = m15_idx = 0
    for i, candle in enumerate(m5):
        close_time = candle.time + timedelta(minutes=5)
        while h1_idx < len(h1) and h1[h1_idx].time + timedelta(minutes=60) <= close_time:
            h1_idx += 1
        while m15_idx < len(m15) and m15[m15_idx].time + timedelta(minutes=15) <= close_time:
            m15_idx += 1
        h1_view = h1[max(0, h1_idx - 170):h1_idx]
        m15_view = m15[max(0, m15_idx - 80):m15_idx]
        m5_view = m5[max(0, i + 1 - 80):i + 1]
        if len(h1_view) >= 30 and len(m15_view) >= 20 and len(m5_view) >= 20:
            ctx = await builder.build("XAUUSD", h1_view, m5_view, setup_tf_candles=m15_view)
            update_setups_from_market_context(tracker, "XAUUSD", ctx, settings)
            tr = getattr(ctx, "master_trend", "?")
            trends[tr] = trends.get(tr, 0) + 1
            ready += len(tracker.get_ready_setups("XAUUSD"))

    out = [
        f"created={created} ready_sum={ready} trends={trends}",
        f"active={len(tracker.get_active_setups('XAUUSD'))}",
        f"h1_total={len(h1)} h1_view_last={len(h1[max(0, h1_idx - 170):h1_idx])}",
    ]
    io.open("_diag.txt", "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out))


asyncio.run(main())
