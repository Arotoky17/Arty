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
- Notation de qualité des Order Blocks (Grade A/B/C/D) et confirmation M5
  (Phase 12, activable via ``OB_QUALITY_ENABLED``)

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
from arty_trading.modules.smc.confirmation import (
    ConfirmationResult,
    M5ConfirmationChecker,
    M5ConfirmationType,
)
from arty_trading.modules.smc.detector import SMCDetector
from arty_trading.modules.smc.fair_value_gap import FairValueGapDetector
from arty_trading.modules.smc.liquidity import LiquidityDetector
from arty_trading.modules.smc.m5_confirmation import (
    CONFIRMATION_TYPES,
    CONFIRMED_TYPES,
    M5ConfirmationResult,
    evaluate_m5_confirmation,
)
from arty_trading.modules.smc.ob_quality import (
    OB_GRADES,
    OBGateDecision,
    OBQualityGrade,
    OBQualityResult,
    assess_order_block_quality,
    evaluate_setup_ob_gate,
    grade_meets_min,
    grade_rank,
)
from arty_trading.modules.smc.order_block_quality import (
    GRADE_A_THRESHOLD,
    GRADE_B_THRESHOLD,
    OBGrade,
    OrderBlockQuality,
    OrderBlockQualityScorer,
)
from arty_trading.modules.smc.order_block_tracker import (
    PRUNE_EVERY,
    OrderBlockTracker,
    TrackedOB,
)
from arty_trading.modules.smc.order_blocks import OrderBlockDetector
from arty_trading.modules.smc.premium_discount import PremiumDiscountDetector
from arty_trading.modules.smc.sessions import SessionDetector, SessionWindow
from arty_trading.modules.smc.setup_tracker import (
    Setup,
    SetupState,
    SetupStateMachine,
    SetupTracker,
)
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
    "OB_GRADES",
    "OBGateDecision",
    "OBQualityGrade",
    "OBQualityResult",
    "assess_order_block_quality",
    "evaluate_setup_ob_gate",
    "grade_meets_min",
    "grade_rank",
    "GRADE_A_THRESHOLD",
    "GRADE_B_THRESHOLD",
    "OBGrade",
    "OrderBlockQuality",
    "OrderBlockQualityScorer",
    "PRUNE_EVERY",
    "OrderBlockTracker",
    "TrackedOB",
    "CONFIRMATION_TYPES",
    "CONFIRMED_TYPES",
    "M5ConfirmationResult",
    "evaluate_m5_confirmation",
    "M5ConfirmationChecker",
    "M5ConfirmationType",
    "ConfirmationResult",
    "find_swing_points",
    "find_swing_highs",
    "find_swing_lows",
    "find_external_swing_points",
    "find_internal_swing_points",
]
