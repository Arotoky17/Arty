"""
Module SMC (Smart Money Concepts) — Détection automatique des concepts ICT/SMC.

Détecteurs disponibles :
- Structure (BOS, Internal BOS, External BOS, CHoCH, MSS)
- Fair Value Gap (FVG, IFVG)
- Order Blocks (OB, Breaker, Mitigation)
- Liquidité (Sweep, Equal High, Equal Low)
- Premium/Discount et OTE
- Sessions (Asia, London, New York, Kill Zones)
- Suivi d'état des setups (SetupTracker, SetupStateMachine)

Chaque détecteur est indépendant et retourne uniquement des informations de
marché. Aucune fonction n'ouvre de trade.
"""

from arty_trading.modules.smc.base import (
    BaseDetector,
    SMCDetection,
    SwingPoint,
    find_external_swing_points,
    find_internal_swing_points,
    find_swing_highs,
    find_swing_lows,
    find_swing_points,
)
from arty_trading.modules.smc.detector import SMCDetector
from arty_trading.modules.smc.fair_value_gap import FairValueGapDetector
from arty_trading.modules.smc.liquidity import LiquidityDetector
from arty_trading.modules.smc.order_blocks import OrderBlockDetector
from arty_trading.modules.smc.premium_discount import PremiumDiscountDetector
from arty_trading.modules.smc.sessions import SessionDetector, SessionWindow
from arty_trading.modules.smc.setup_tracker import Setup, SetupState, SetupStateMachine, SetupTracker
from arty_trading.modules.smc.structure import StructureDetector

__all__ = [
    "BaseDetector",
    "SMCDetection",
    "SwingPoint",
    "SMCDetector",
    "StructureDetector",
    "FairValueGapDetector",
    "OrderBlockDetector",
    "LiquidityDetector",
    "PremiumDiscountDetector",
    "SessionDetector",
    "SessionWindow",
    "Setup",
    "SetupState",
    "SetupStateMachine",
    "SetupTracker",
    "find_swing_points",
    "find_swing_highs",
    "find_swing_lows",
    "find_external_swing_points",
    "find_internal_swing_points",
]