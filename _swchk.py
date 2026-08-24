import sys, random
sys.path.insert(0, 'src')
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import generate_multi_regime_candles, aggregate_candles
from arty_trading.modules.smc.base import find_swing_points
from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer
m5, _ = generate_multi_regime_candles(n_m5=3000, seed=42, regimes=[('BEAR', 3000)])
h1 = aggregate_candles(m5, TimeFrame.H1)
sp = find_swing_points(h1)
print('full-series swings:', len(sp))
cs = h1[:50]
print('sample highs:', [float(c.high) for c in cs[:20]])
# random control
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from arty_trading.core.models import Candle
rnd = random.Random(1)
base = 2000.0
cc = []
prev = base
for i in range(100):
    o = prev; cl = o + rnd.uniform(-2,2)
    hi = max(o,cl)+rnd.uniform(0,1); lo = min(o,cl)-rnd.uniform(0,1)
    cc.append(Candle(time=datetime(2024,1,1,tzinfo=timezone.utc)+timedelta(hours=i),
                     open=Decimal(str(o)), high=Decimal(str(hi)), low=Decimal(str(lo)),
                     close=Decimal(str(cl)), volume=Decimal('100'), symbol='XAUUSD', timeframe=TimeFrame.H1))
    prev = cl
sp2 = find_swing_points(cc)
print('random-control swings:', len(sp2))
