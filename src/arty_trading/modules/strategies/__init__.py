"""
Module Stratégies — 6 stratégies de trading implémentant IStrategy.

Stratégies disponibles :
- SMC Trend Following (BOS + FVG + OB)
- Breakout (cassure de range + volume)
- Momentum (déplacement fort + FVG)
- Reversal (CHoCH + Liquidity Sweep)
- Scalping (FVG + spread serré)
- Swing Trading (BOS + OTE + OB)
"""

from arty_trading.modules.strategies.base import BaseStrategy
from arty_trading.modules.strategies.strategies import (
    BreakoutStrategy,
    MomentumStrategy,
    ReversalStrategy,
    ScalpingStrategy,
    SMCTrendStrategy,
    SwingStrategy,
)

__all__ = [
    "BaseStrategy",
    "SMCTrendStrategy",
    "BreakoutStrategy",
    "MomentumStrategy",
    "ReversalStrategy",
    "ScalpingStrategy",
    "SwingStrategy",
]