"""
Politique de confiance adaptative — Phase « Adaptive Confidence / RR Execution ».

Ce module définit une politique **pure** (aucun effet de bord) qui détermine
si un signal peut être accepté selon sa confiance et son ratio risque/rendement :

- ``confidence < 0.60``                        → rejet (``low_confidence``).
- ``0.60 <= confidence < 0.85``                → autorisé **uniquement** si
  ``RR >= 2.0`` (``rr_too_low`` sinon). Tous les autres gates (Master Direction
  Gate, SignalValidator, Decision Score, Risk Manager, Final Gate) restent
  obligatoires et inchangés.
- ``confidence >= 0.85``                       → comportement historique
  conservé (aucun assouplissement des autres filtres).

La politique adaptative n'est active que si le seuil configuré
(``SignalSettings.min_confidence``) est >= 0.85 (valeur de production). Si un
seuil plus bas est explicitement configuré (tests, calibration), le
comportement historique (comparaison plate au seuil) est conservé afin de ne
changer aucune logique existante.

Ce module ne modifie aucune stratégie, aucun seuil SMC, le Master Trend, le
SignalValidator, le Decision Score, le Risk Manager ni le Final Gate.
"""

from __future__ import annotations

from dataclasses import dataclass

# Plancher absolu de confiance : en dessous, rejet systématique.
ABSOLUTE_CONFIDENCE_FLOOR = 0.60

# RR minimum obligatoire pour les confiances 0.60–0.84 (politique adaptative).
# Conforme au RR minimum déjà appliqué par le Final Gate (profil instrument,
# défaut 2.0) : aucun assouplissement.
ADAPTIVE_MIN_RR = 2.0

# La politique adaptative ne s'active qu'avec le seuil de production (0.85).
ADAPTIVE_ACTIVATION_THRESHOLD = 0.85

# Bornes des buckets de sécurité.
_BUCKET_HIGH_CEILING = 0.80  # 0.60–0.79 → sécurité renforcée (HIGH)


@dataclass(frozen=True)
class ConfidencePolicyDecision:
    """Résultat de l'évaluation de la politique confiance/RR."""

    allowed: bool
    confidence: float
    confidence_threshold: float
    confidence_bucket: str
    required_rr: float | None
    actual_rr: float
    security_level: str  # "NONE" | "HIGH" | "STANDARD"
    reason: str  # "" | "low_confidence" | "rr_too_low"

    def to_dict(self) -> dict[str, object]:
        """Sérialise la décision pour le Trade Decision Debugger."""
        return {
            "confidence": self.confidence,
            "confidence_threshold": self.confidence_threshold,
            "confidence_bucket": self.confidence_bucket,
            "required_rr": self.required_rr,
            "actual_rr": self.actual_rr,
            "rr_security_level": self.security_level,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def confidence_bucket(confidence: float) -> str:
    """Retourne le bucket de confiance (bornes : 0.60, 0.70, 0.80, 0.85)."""
    if confidence < ABSOLUTE_CONFIDENCE_FLOOR:
        return "<0.60"
    if confidence < 0.70:
        return "0.60-0.69"
    if confidence < _BUCKET_HIGH_CEILING:
        return "0.70-0.79"
    if confidence < ADAPTIVE_ACTIVATION_THRESHOLD:
        return "0.80-0.84"
    return ">=0.85"


def evaluate_confidence_policy(
    confidence: float,
    risk_reward: float,
    base_threshold: float = ADAPTIVE_ACTIVATION_THRESHOLD,
) -> ConfidencePolicyDecision:
    """
    Évalue la politique adaptative confiance/RR pour un signal.

    Args:
        confidence: Confiance du signal (0-1).
        risk_reward: Ratio risque/rendement du signal (>= 0).
        base_threshold: Seuil de confiance configuré. Si < 0.85, la politique
            retombe sur le comportement historique (comparaison plate).

    Returns:
        Une :class:`ConfidencePolicyDecision` (purement informative pour les
        couches appelantes ; cette fonction ne rejette rien elle-même).
    """
    bucket = confidence_bucket(confidence)
    adaptive = base_threshold >= ADAPTIVE_ACTIVATION_THRESHOLD

    if not adaptive:
        # Comportement historique strictement conservé.
        return ConfidencePolicyDecision(
            allowed=confidence >= base_threshold,
            confidence=confidence,
            confidence_threshold=base_threshold,
            confidence_bucket=bucket,
            required_rr=None,
            actual_rr=risk_reward,
            security_level="STANDARD" if confidence >= base_threshold else "NONE",
            reason="" if confidence >= base_threshold else "low_confidence",
        )

    if confidence < ABSOLUTE_CONFIDENCE_FLOOR:
        return ConfidencePolicyDecision(
            allowed=False,
            confidence=confidence,
            confidence_threshold=ABSOLUTE_CONFIDENCE_FLOOR,
            confidence_bucket=bucket,
            required_rr=ADAPTIVE_MIN_RR,
            actual_rr=risk_reward,
            security_level="NONE",
            reason="low_confidence",
        )

    if confidence >= ADAPTIVE_ACTIVATION_THRESHOLD:
        # Comportement actuel conservé, sans assouplissement.
        return ConfidencePolicyDecision(
            allowed=True,
            confidence=confidence,
            confidence_threshold=base_threshold,
            confidence_bucket=bucket,
            required_rr=None,
            actual_rr=risk_reward,
            security_level="STANDARD",
            reason="",
        )

    # 0.60 <= confidence < 0.85 : autorisé uniquement si RR >= 2.0.
    security = "HIGH" if confidence < _BUCKET_HIGH_CEILING else "STANDARD"
    allowed = risk_reward >= ADAPTIVE_MIN_RR
    return ConfidencePolicyDecision(
        allowed=allowed,
        confidence=confidence,
        confidence_threshold=base_threshold,
        confidence_bucket=bucket,
        required_rr=ADAPTIVE_MIN_RR,
        actual_rr=risk_reward,
        security_level=security,
        reason="" if allowed else "rr_too_low",
    )
