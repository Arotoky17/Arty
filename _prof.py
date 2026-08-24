import io
import sys
import time
import asyncio

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)
from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.config.settings import Settings
from arty_trading.modules.smc import SMCDetector, SetupTracker

m5, spans = generate_multi_regime_candles(n_m5=800, seed=42)
m15 = aggregate_candles(m5, TimeFrame.M15)
h1 = aggregate_candles(m5, TimeFrame.H1)
det = SMCDetector()


async def empty_dl(s, tf):
    return []


builder = MarketContextBuilder(smc_detector=det, download_data=empty_dl)
tracker = SetupTracker()
settings = Settings()

async def main():
    t_build = t_setup = t_total = 0.0
    n = 0
    created = 0
    trends = {}
    h1_idx = m15_idx = 0
    from datetime import timedelta

    orig_create = tracker.create_setup

    def counting_create(**kw):
        nonlocal created
        created += 1
        return orig_create(**kw)

    tracker.create_setup = counting_create

    for i, candle in enumerate(m5):
        t0 = time.time()
        close_time = candle.time + timedelta(minutes=5)
        while h1_idx < len(h1) and h1[h1_idx].time + timedelta(minutes=60) <= close_time:
            h1_idx += 1
        while m15_idx < len(m15) and m15[m15_idx].time + timedelta(minutes=15) <= close_time:
            m15_idx += 1
        h1_view = h1[max(0, h1_idx - 120):h1_idx]
        m15_view = m15[max(0, m15_idx - 120):m15_idx]
        m5_view = m5[max(0, i + 1 - 120):i + 1]
        if len(h1_view) >= 30 and len(m15_view) >= 20 and len(m5_view) >= 20:
            t1 = time.time()
            ctx = await builder.build("XAUUSD", h1_view, m5_view, setup_tf_candles=m15_view)
            t2 = time.time()
            update_setups_from_market_context(tracker, "XAUUSD", ctx, settings)
            t3 = time.time()
            t_build += t2 - t1
            t_setup += t3 - t2
            n += 1
            tr = getattr(ctx, "master_trend", "?")
            trends[tr] = trends.get(tr, 0) + 1
        t_total += time.time() - t0

    out = [
        f"iterations={n} total={t_total:.1f}s build={t_build:.1f}s setup={t_setup:.1f}s",
        f"active_setups={len(tracker.get_active_setups('XAUUSD'))}",
        f"created={created}",
        f"trends={trends}",
    ]
    io.open("_prof.txt", "w").write("\n".join(out))
    print("\n".join(out))


asyncio.run(main())
