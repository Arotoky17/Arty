import io
import sys
import time
import asyncio

sys.path.insert(0, "src")

from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)
from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer

m5, spans = generate_multi_regime_candles(n_m5=3000, seed=42)
h1 = aggregate_candles(m5, TimeFrame.H1)
m15 = aggregate_candles(m5, TimeFrame.M15)
an = MasterTrendAnalyzer(htf=TimeFrame.H1)
lines = []
for k in (30, 50, 80, 120, len(h1)):
    tr = an.get_master_trend(h1[:k])
    lines.append(f"H1 n={k} trend={tr}")
for k in (30, 80, len(m15)):
    tr = an.get_master_trend(m15[:k])
    lines.append(f"M15 n={k} trend={tr}")
lines.append(f"price start={float(m5[0].close)} end={float(m5[-1].close)}")
io.open("_trend.txt", "w").write("\n".join(lines))
print("\n".join(lines))
