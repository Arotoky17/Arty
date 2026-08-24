import sys
sys.path.insert(0, 'src')
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import generate_multi_regime_candles, aggregate_candles
m5, _ = generate_multi_regime_candles(n_m5=60, seed=42)
for c in m5[:5]:
    print('M5', c.time, 'o', c.open, 'h', c.high, 'l', c.low, 'c', c.close)
h1 = aggregate_candles(m5, TimeFrame.H1)
for c in h1[:3]:
    print('H1', c.time, 'o', c.open, 'h', c.high, 'l', c.low, 'c', c.close)
