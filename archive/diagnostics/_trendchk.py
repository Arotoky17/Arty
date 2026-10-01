import sys
sys.path.insert(0, 'src')
from collections import Counter
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import generate_multi_regime_candles, aggregate_candles
from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer
m5, _ = generate_multi_regime_candles(n_m5=3000, seed=42, regimes=[('BEAR', 3000)])
h1 = aggregate_candles(m5, TimeFrame.H1)
ta = MasterTrendAnalyzer(htf=TimeFrame.H1)
c = Counter()
for i in range(60, len(h1)+1):
    c[ta.get_master_trend(h1[:i])] += 1
print('window=full:', dict(c))
for w in (30, 50, 100):
    c2 = Counter()
    for i in range(w, len(h1)+1):
        c2[ta.get_master_trend(h1[i-w:i])] += 1
    print(f'window={w}:', dict(c2))
print('n_h1', len(h1))
