import sys
sys.path.insert(0, 'src')
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import generate_multi_regime_candles, aggregate_candles
from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer, find_swing_points
m5, _ = generate_multi_regime_candles(n_m5=3000, seed=42, regimes=[('BEAR', 3000)])
h1 = aggregate_candles(m5, TimeFrame.H1)
ta = MasterTrendAnalyzer(htf=TimeFrame.H1)
r = ta.analyze(h1)
print('trend:', r.trend, 'conf:', r.confidence)
print('details:', r.details)
sp = find_swing_points(h1[:-1], ta._swing_window, ta._external_window)
print('params: swing_window=', ta._swing_window, 'external=', ta._external_window, 'min_swings=', ta._min_swings)
print('n swings:', len(sp))
print([(s.type, float(s.price)) for s in sp[-8:]])
print('first h1 closes:', [float(c.close) for c in h1[:10]])
print('last h1 closes:', [float(c.close) for c in h1[-10:]])
