"""
Diagnostic de décision de trading — observabilité du pipeline Arty.

Ce module capture chaque étape du pipeline de décision pour répondre à :
"Pourquoi Arty n'a-t-il pas ouvert ce trade ?"

Le diagnostic ne modifie AUCUNE logique de trading. Il observe uniquement
les résultats déjà produits par les composants existants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from arty_trading.core.enums import TimeFrame

# =============================================================================
# Constantes d'étapes du pipeline
# =============================================================================

class PipelineStep(str, Enum):  # noqa: UP042
    """Étapes observables du pipeline de trading."""

    DATA_AVAILABLE = "data_available"
    MARKET_CONTEXT = "market_context"
    MARKET_REGIME = "market_regime"
    STRATEGY_EVALUATION = "strategy_evaluation"
    SETUP_DETECTED = "setup_detected"
    SIGNAL_GENERATED = "signal_generated"
    MASTER_DIRECTION_GATE = "master_direction_gate"
    SIGNAL_VALIDATOR = "signal_validator"
    DECISION_SCORE = "decision_score"
    RISK_REWARD = "risk_reward"
    RISK_MANAGER = "risk_manager"
    EXECUTION_REVALIDATION = "execution_revalidation"
    EXECUTION_FINAL_GATE = "execution_final_gate"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_EXECUTED = "order_executed"


class RejectionReason(str, Enum):  # noqa: UP042
    """Codes structurés de rejet basés sur la logique existante."""

    NO_MARKET_DATA = "no_market_data"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_SETUP = "no_setup"
    NO_SIGNAL = "no_signal"
    MASTER_TREND_CONFLICT = "master_trend_conflict"
    MASTER_TREND_UNKNOWN = "master_trend_unknown"
    REGIME_BLOCKED = "regime_blocked"
    LOW_CONFIDENCE = "low_confidence"
    LOW_SCORE = "low_score"
    VALIDATOR_REJECTED = "validator_rejected"
    BOS_MISSING = "bos_missing"
    CHOCH_MISSING = "choch_missing"
    MSS_MISSING = "mss_missing"
    FVG_MISSING = "fvg_missing"
    ORDER_BLOCK_MISSING = "order_block_missing"
    LIQUIDITY_MISSING = "liquidity_missing"
    PREMIUM_DISCOUNT_INVALID = "premium_discount_invalid"
    RR_TOO_LOW = "rr_too_low"
    SPREAD_TOO_HIGH = "spread_too_high"
    NEWS_BLOCKED = "news_blocked"
    RISK_REJECTED = "risk_rejected"
    INVALID_SL = "invalid_sl"
    INVALID_TP = "invalid_tp"
    SETUP_EXPIRED = "setup_expired"
    SETUP_DUPLICATED = "setup_duplicated"
    REVALIDATION_FAILED = "revalidation_failed"
    FINAL_GATE_REJECTED = "final_gate_rejected"
    MT5_ERROR = "mt5_error"


# =============================================================================
# Modèle de diagnostic
# =============================================================================

@dataclass
class SMCValidatorDiagnostic:
    """Résultat détaillé des conditions SMC du validateur."""

    htf_trend: bool = False
    bos: bool = False
    choch: bool = False
    order_block: bool = False
    fvg: bool = False
    liquidity_sweep: bool = False
    premium_discount: bool = False


@dataclass
class ScoreDiagnostic:
    """Détails du calcul de score."""

    total: int = 0
    minimum_required: int = 70
    tier: str = "no_trade"
    contributions: dict[str, int] = field(default_factory=dict)


@dataclass
class RRDiagnostic:
    """Diagnostic du ratio risque/rendement."""

    entry: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    risk_distance: Decimal = Decimal("0")
    reward_distance: Decimal = Decimal("0")
    risk_reward: float = 0.0
    minimum_required_rr: float = 2.0


@dataclass
class SpreadDiagnostic:
    """Diagnostic du spread."""

    current_spread: int = 0
    maximum_allowed_spread: int = 30
    filter_enabled: bool = True
    passed: bool = True


@dataclass
class MasterTrendDiagnostic:
    """Diagnostic du Master Direction Gate."""

    h1_trend: str = "neutral"
    candidate_direction: str = "neutral"
    enabled: bool = True
    allow_counter_trend: bool = False
    passed: bool = False
    reason: str = ""


@dataclass
class ExecutionGuardDiagnostic:
    """Diagnostic des garde-fous d'exécution."""

    revalidate_passed: bool = False
    revalidate_reason: str = ""
    final_gate_passed: bool = False
    final_gate_reason: str = ""


@dataclass
class RiskManagerDiagnostic:
    """Diagnostic du Risk Manager."""

    can_open_trade: bool = False
    validate_signal: bool = False
    position_size: float = 0.0
    rejection_reason: str = ""


@dataclass
class TradeDecisionDiagnostic:
    """
    Diagnostic complet d'une décision de trading.

    Capture toutes les étapes du pipeline et les raisons de rejet
    pour permettre un diagnostic précis.
    """

    # Identification
    opportunity_id: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    symbol: str = ""
    timeframe: TimeFrame = TimeFrame.H1
    candle_time: datetime | None = None
    direction_candidate: str = "neutral"
    market_regime: str = "unknown"
    h1_trend: str = "neutral"
    h4_context: str | None = None
    m15_context: str | None = None

    # Résultat final
    decision: str = "pending"  # "accepted", "rejected", "pending"
    primary_rejection_reason: str = ""
    rejection_reasons: list[str] = field(default_factory=list)

    # Détails par étape
    steps_completed: list[str] = field(default_factory=list)
    steps_failed: list[str] = field(default_factory=list)

    # Informations du signal
    setup_type: str = ""
    strategy_name: str = ""
    score: int = 0
    confidence: float = 0.0

    # Diagnostics détaillés
    smc_validator: SMCValidatorDiagnostic = field(
        default_factory=SMCValidatorDiagnostic
    )
    score_diagnostic: ScoreDiagnostic = field(default_factory=ScoreDiagnostic)
    rr_diagnostic: RRDiagnostic = field(default_factory=RRDiagnostic)
    spread_diagnostic: SpreadDiagnostic = field(default_factory=SpreadDiagnostic)
    master_trend_diagnostic: MasterTrendDiagnostic = field(
        default_factory=MasterTrendDiagnostic
    )
    execution_guard_diagnostic: ExecutionGuardDiagnostic = field(
        default_factory=ExecutionGuardDiagnostic
    )
    risk_manager_diagnostic: RiskManagerDiagnostic = field(
        default_factory=RiskManagerDiagnostic
    )

    # Métadonnées
    metadata: dict[str, Any] = field(default_factory=dict)

    # Diagnostics de retest et premium/discount (Phase 3A — instrumentation)
    retest_diagnostic: dict[str, Any] | None = None
    premium_discount_diagnostic: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le diagnostic en dictionnaire."""
        return {
            "opportunity_id": self.opportunity_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "candle_time": self.candle_time.isoformat() if self.candle_time else None,
            "direction_candidate": self.direction_candidate,
            "market_regime": self.market_regime,
            "h1_trend": self.h1_trend,
            "h4_context": self.h4_context,
            "m15_context": self.m15_context,
            "decision": self.decision,
            "primary_rejection_reason": self.primary_rejection_reason,
            "rejection_reasons": list(self.rejection_reasons),
            "steps_completed": list(self.steps_completed),
            "steps_failed": list(self.steps_failed),
            "setup_type": self.setup_type,
            "strategy_name": self.strategy_name,
            "score": self.score,
            "confidence": self.confidence,
            "smc_validator": {
                "htf_trend": self.smc_validator.htf_trend,
                "bos": self.smc_validator.bos,
                "choch": self.smc_validator.choch,
                "order_block": self.smc_validator.order_block,
                "fvg": self.smc_validator.fvg,
                "liquidity_sweep": self.smc_validator.liquidity_sweep,
                "premium_discount": self.smc_validator.premium_discount,
            },
            "score_diagnostic": {
                "total": self.score_diagnostic.total,
                "minimum_required": self.score_diagnostic.minimum_required,
                "tier": self.score_diagnostic.tier,
                "contributions": dict(self.score_diagnostic.contributions),
            },
            "rr_diagnostic": {
                "entry": float(self.rr_diagnostic.entry),
                "stop_loss": float(self.rr_diagnostic.stop_loss),
                "take_profit": float(self.rr_diagnostic.take_profit),
                "risk_distance": float(self.rr_diagnostic.risk_distance),
                "reward_distance": float(self.rr_diagnostic.reward_distance),
                "risk_reward": self.rr_diagnostic.risk_reward,
                "minimum_required_rr": self.rr_diagnostic.minimum_required_rr,
            },
            "spread_diagnostic": {
                "current_spread": self.spread_diagnostic.current_spread,
                "maximum_allowed_spread": self.spread_diagnostic.maximum_allowed_spread,
                "filter_enabled": self.spread_diagnostic.filter_enabled,
                "passed": self.spread_diagnostic.passed,
            },
            "master_trend_diagnostic": {
                "h1_trend": self.master_trend_diagnostic.h1_trend,
                "candidate_direction": self.master_trend_diagnostic.candidate_direction,
                "enabled": self.master_trend_diagnostic.enabled,
                "allow_counter_trend": self.master_trend_diagnostic.allow_counter_trend,
                "passed": self.master_trend_diagnostic.passed,
                "reason": self.master_trend_diagnostic.reason,
            },
            "execution_guard_diagnostic": {
                "revalidate_passed": self.execution_guard_diagnostic.revalidate_passed,
                "revalidate_reason": self.execution_guard_diagnostic.revalidate_reason,
                "final_gate_passed": self.execution_guard_diagnostic.final_gate_passed,
                "final_gate_reason": self.execution_guard_diagnostic.final_gate_reason,
            },
            "risk_manager_diagnostic": {
                "can_open_trade": self.risk_manager_diagnostic.can_open_trade,
                "validate_signal": self.risk_manager_diagnostic.validate_signal,
                "position_size": self.risk_manager_diagnostic.position_size,
                "rejection_reason": self.risk_manager_diagnostic.rejection_reason,
            },
            "metadata": dict(self.metadata),
            "retest_diagnostic": self.retest_diagnostic,
            "premium_discount_diagnostic": self.premium_discount_diagnostic,
        }

    def to_log_summary(self) -> str:
        """Génère un résumé textuel pour les logs."""
        if self.decision == "accepted":
            return (
                f"DIAGNOSTIC | {self.symbol} | {self.direction_candidate} | "
                f"ACCEPTED | score={self.score} | conf={self.confidence:.2f} | "
                f"RR={self.rr_diagnostic.risk_reward:.2f}"
            )
        retest_reason = ""
        if self.retest_diagnostic:
            retest_reason = f" | retest_reason={self.retest_diagnostic.get('reason', '')}"
        pd_reason = ""
        if self.premium_discount_diagnostic:
            pd_location = self.premium_discount_diagnostic.get("location", "?")
            pd_expected = self.premium_discount_diagnostic.get("expected_location", "?")
            pd_reason = f" | pd={pd_location}/{pd_expected}"
        return (
            f"DIAGNOSTIC | {self.symbol} | {self.direction_candidate} | "
            f"REJECTED | primary={self.primary_rejection_reason} | "
            f"reasons={self.rejection_reasons} | score={self.score} | "
            f"steps_failed={self.steps_failed}{retest_reason}{pd_reason}"
        )
