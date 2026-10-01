"""
Notation de qualité des Order Blocks — Grade A/B/C/D (Phase 12).

Objectif métier : passer d'un bot qui trade « tous les Order Blocks » à un bot
qui ne trade que les Order Blocks **de haute qualité** (Grade A/B) confirmés sur
M5, afin de maximiser les pips par trade sur XAUUSD.

Le module est **pur** et **déterministe** :

- aucune lecture de configuration implicite (la ``OBQualitySettings`` est
  injectée) ;
- aucun accès disque / réseau / MT5 ;
- aucune exécution d'ordre (il ne fait que classer une détection).

Notation
--------
Chaque Order Block reçoit un score 0-100, somme pondérée et normalisée de sept
composantes (les pondérations viennent de la configuration) :

- ``displacement`` : corps du déplacement (bougies suivant l'OB) en multiple d'ATR
- ``zone_height`` : hauteur de la zone OB en multiple d'ATR (trop large = clump)
- ``mitigation`` : nombre de retours déjà effectués sur la zone
- ``freshness`` : âge de la zone en bougies
- ``trend`` : alignement avec la tendance maître H1
- ``confluence`` : sweep de liquidité / CHoCH-MSS / FVG en confluence
- ``premium_discount`` : zone du bon côté du range (discount BUY / premium SELL)

Le score est ensuite converti en grade :

- **A** : score >= ``grade_a_threshold`` (setup premium)
- **B** : score >= ``grade_b_threshold`` (setup valide)
- **C** : score >= ``grade_c_threshold`` (setup faible, à ignorer)
- **D** : en dessous (setup à rejeter)

Toutes les distances sont exprimées en multiples d'ATR (jamais en distance
fixe), conformément au reste du moteur SMC.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from arty_trading.config.settings import OBQualitySettings

#: Catalogue des grades, du meilleur au moins bon (source : configuration).
OB_GRADES: tuple[str, ...] = ("A", "B", "C", "D")


class OBQualityGrade(StrEnum):
    """Grade de qualité d'un Order Block."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


#: Concepts SMC considérés comme un shift de structure.
_STRUCTURE_CONCEPTS = ("change_of_character", "market_structure_shift", "choch", "mss")

#: Poids relatifs des confluences (normalisés dans ``_score_confluence``).
_CONFLUENCE_WEIGHTS: dict[str, float] = {
    "liquidity_sweep": 0.45,
    "structure_shift": 0.35,
    "fair_value_gap": 0.20,
}


def grade_rank(grade: str | OBQualityGrade) -> int:
    """Rang numérique d'un grade (A=4 … D=1). 0 si inconnu."""
    value = grade.value if isinstance(grade, OBQualityGrade) else str(grade).strip().upper()
    try:
        return len(OB_GRADES) - OB_GRADES.index(value)
    except ValueError:
        return 0


def grade_meets_min(grade: str | OBQualityGrade, min_grade: str | OBQualityGrade) -> bool:
    """``True`` si ``grade`` est au moins aussi bon que ``min_grade``."""
    return grade_rank(grade) >= grade_rank(min_grade)


@dataclass(frozen=True)
class OBQualityResult:
    """Résultat de la notation d'un Order Block.

    Attributes:
        score: Score global 0-100.
        grade: Grade dérivé du score.
        components: Contribution (points) de chaque composante au score.
        fractions: Score brut (0-1) de chaque composante avant pondération.
        reasons: Détails lisibles (displacement, confluences, pénalités…).
    """

    score: int
    grade: OBQualityGrade
    components: dict[str, int]
    fractions: dict[str, float]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (métadonnées de setup / API)."""
        return {
            "ob_quality_score": self.score,
            "ob_grade": self.grade.value,
            "ob_quality_components": dict(self.components),
            "ob_quality_reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class OBGateDecision:
    """Décision du filtre qualité OB appliqué à un setup.

    Attributes:
        allowed: ``True`` si le setup peut produire un signal.
        reason: Code de décision (``ok``, ``grade_below_min``, …).
        grade: Grade lu dans les métadonnées du setup (``None`` si absent).
    """

    allowed: bool
    reason: str
    grade: str | None = None


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    """Convertit une valeur en ``float``, ou ``None`` si non convertible."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    """Convertit une valeur en ``int``, ou ``default`` si non convertible."""
    converted = _as_float(value)
    return default if converted is None else int(converted)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Borne ``value`` dans ``[low, high]``."""
    return max(low, min(high, value))


def _grade_for_score(score: int, settings: OBQualitySettings) -> OBQualityGrade:
    """Convertit un score 0-100 en grade A/B/C/D."""
    if score >= settings.grade_a_threshold:
        return OBQualityGrade.A
    if score >= settings.grade_b_threshold:
        return OBQualityGrade.B
    if score >= settings.grade_c_threshold:
        return OBQualityGrade.C
    return OBQualityGrade.D


def _matching_events(
    smc_data: Sequence[Mapping[str, Any]],
    direction: str,
    zone_index: int,
    lookback_bars: int,
    concepts: Sequence[str],
) -> list[int]:
    """Indices des détections ``concepts``, même direction, antérieures à la zone."""
    indices: list[int] = []
    for detection in smc_data:
        if str(detection.get("concept", "")) not in concepts:
            continue
        if str(detection.get("direction", "")) != direction:
            continue
        index = _as_int(detection.get("index"), 0)
        if index > zone_index or (zone_index - index) > lookback_bars:
            continue
        indices.append(index)
    return indices


# ---------------------------------------------------------------------------
# Composantes du score
# ---------------------------------------------------------------------------


def _score_displacement(
    details: Mapping[str, Any], atr: float, settings: OBQualitySettings
) -> tuple[float, str]:
    """Qualité du déplacement institutionnel (corps cumulé / ATR)."""
    size = _as_float(details.get("displacement_size"))
    if size is None or atr <= 0:
        return 0.0, "displacement=unknown"
    ratio = size / atr
    fraction = _clamp(ratio / (2.0 * settings.displacement_reference_atr))
    return fraction, f"displacement={ratio:.2f}atr"


def _score_zone_height(
    zone_top: float,
    zone_bottom: float,
    atr: float,
    settings: OBQualitySettings,
) -> tuple[float, str]:
    """Hauteur de la zone : un OB fin est exploitable, un OB géant ne l'est pas."""
    if atr <= 0:
        return 0.5, "zone_height=unknown"
    height_atr = (zone_top - zone_bottom) / atr
    if height_atr <= 0:
        return 0.0, "zone_height=invalid"
    if height_atr < settings.min_zone_height_atr:
        return 0.4, f"zone_height={height_atr:.2f}atr (trop étroit)"
    if height_atr > settings.max_zone_height_atr:
        return 0.0, f"zone_height={height_atr:.2f}atr (trop large)"
    # 1.0 jusqu'à 1 ATR, puis décroissance linéaire jusqu'à 0.5 au maximum.
    span = max(settings.max_zone_height_atr - 1.0, 1e-9)
    decay = _clamp((height_atr - 1.0) / span)
    return 1.0 - 0.5 * decay, f"zone_height={height_atr:.2f}atr"


def _score_mitigation(mitigation_count: int) -> tuple[float, str]:
    """Nombre de retours déjà effectués sur la zone (0 = zone jamais testée)."""
    table = {0: 1.0, 1: 0.6, 2: 0.2}
    fraction = table.get(max(mitigation_count, 0), 0.0)
    return fraction, f"mitigations={mitigation_count}"


def _score_freshness(
    bars_since_zone: int | None, settings: OBQualitySettings
) -> tuple[float, str]:
    """Âge de la zone en bougies (une zone ancienne a déjà été travaillée)."""
    if bars_since_zone is None:
        return 0.6, "zone_age=unknown"
    age = max(bars_since_zone, 0)
    fraction = _clamp(1.0 - (age / float(settings.max_zone_age_bars)))
    return fraction, f"zone_age={age}bars"


def _score_trend(direction: str, htf_trend: str) -> tuple[float, str]:
    """Alignement avec la tendance maître H1 (gate directionnel existant)."""
    if htf_trend in ("bullish", "bearish"):
        fraction = 1.0 if direction == htf_trend else 0.0
    else:
        fraction = 0.4
    return fraction, f"trend={htf_trend}/{direction}"


def _score_confluence(
    smc_data: Sequence[Mapping[str, Any]],
    direction: str,
    zone_index: int,
    zone_top: float,
    zone_bottom: float,
    settings: OBQualitySettings,
) -> tuple[float, str]:
    """Confluences ICT : sweep de liquidité, shift de structure, FVG superposé."""
    lookback = settings.confluence_lookback_bars
    found: list[str] = []

    if _matching_events(smc_data, direction, zone_index, lookback, ("liquidity_sweep",)):
        found.append("liquidity_sweep")
    if _matching_events(smc_data, direction, zone_index, lookback, _STRUCTURE_CONCEPTS):
        found.append("structure_shift")

    for detection in smc_data:
        if str(detection.get("concept", "")) != "fair_value_gap":
            continue
        if str(detection.get("direction", "")) != direction:
            continue
        index = _as_int(detection.get("index"), 0)
        if index > zone_index or (zone_index - index) > lookback:
            continue
        gap_details = detection.get("details", {}) or {}
        gap_top = _as_float(gap_details.get("gap_top"))
        gap_bottom = _as_float(gap_details.get("gap_bottom"))
        if gap_top is None or gap_bottom is None:
            continue
        if min(gap_top, zone_top) - max(gap_bottom, zone_bottom) > 0:
            found.append("fair_value_gap")
            break

    fraction = _clamp(sum(_CONFLUENCE_WEIGHTS[name] for name in set(found)))
    label = "+".join(found) if found else "none"
    return fraction, f"confluence={label}"


def _score_premium_discount(
    smc_data: Sequence[Mapping[str, Any]], direction: str, zone_mid: float
) -> tuple[float, str]:
    """Bon côté du range : discount pour BUY, premium pour SELL (ou OTE)."""
    location = "unknown"

    for detection in smc_data:
        concept = str(detection.get("concept", ""))
        if concept == "ote" and str(detection.get("direction", "")) == direction:
            ote_details = detection.get("details", {}) or {}
            ote_low = _as_float(ote_details.get("ote_zone_low"))
            ote_high = _as_float(ote_details.get("ote_zone_high"))
            if ote_low is not None and ote_high is not None and ote_low <= zone_mid <= ote_high:
                return 1.0, "pd=ote"
        if concept != "premium_discount":
            continue
        pd_details = detection.get("details", {}) or {}
        equilibrium = _as_float(pd_details.get("equilibrium"))
        if equilibrium is None:
            continue
        location = "discount" if zone_mid < equilibrium else "premium"

    if location == "unknown":
        return 0.5, "pd=unknown"
    expected = "discount" if direction == "bullish" else "premium"
    return (1.0 if location == expected else 0.0), f"pd={location}"


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def assess_order_block_quality(
    detection: Mapping[str, Any],
    *,
    atr: float,
    htf_trend: str = "neutral",
    smc_data: Sequence[Mapping[str, Any]] = (),
    zone_index: int | None = None,
    bars_since_zone: int | None = None,
    settings: OBQualitySettings | None = None,
) -> OBQualityResult:
    """Note un Order Block et lui attribue un grade A/B/C/D.

    Args:
        detection: Détection SMC normalisée (``SMCDetection.to_dict()``) du
            concept ``order_block``.
        atr: ATR courant du timeframe de setup (0 si inconnu).
        htf_trend: Tendance maître H1 (``bullish`` / ``bearish`` / ``neutral``).
        smc_data: Détections SMC du timeframe de setup (confluences).
        zone_index: Index de la bougie de la zone (anti look-ahead).
        bars_since_zone: Âge de la zone en bougies (``None`` = inconnu).
        settings: Paramètres de notation (valeurs par défaut si ``None``).

    Returns:
        ``OBQualityResult`` — score 0-100, grade, composantes et raisons.

    Note:
        Une détection sans bornes de zone exploitables retourne un résultat
        Grade D (score 0, raison ``missing_zone_bounds``) : elle ne peut pas
        être tradée.
    """
    config = settings or OBQualitySettings()
    direction = str(detection.get("direction", "neutral"))
    raw_details = detection.get("details", {})
    details: Mapping[str, Any] = raw_details if isinstance(raw_details, Mapping) else {}
    resolved_index = zone_index
    if resolved_index is None:
        resolved_index = _as_int(detection.get("index"), 0)

    zone_top = _as_float(details.get("ob_top"))
    zone_bottom = _as_float(details.get("ob_bottom"))
    if zone_top is None or zone_bottom is None or zone_top <= zone_bottom:
        return OBQualityResult(
            score=0,
            grade=OBQualityGrade.D,
            components={},
            fractions={},
            reasons=("missing_zone_bounds",),
        )

    zone_mid = (zone_top + zone_bottom) / 2.0

    components: dict[str, tuple[float, str]] = {
        "displacement": _score_displacement(details, atr, config),
        "zone_height": _score_zone_height(zone_top, zone_bottom, atr, config),
        "mitigation": _score_mitigation(_as_int(details.get("mitigation_count"), 0)),
        "freshness": _score_freshness(bars_since_zone, config),
        "trend": _score_trend(direction, htf_trend),
        "confluence": _score_confluence(
            smc_data, direction, resolved_index, zone_top, zone_bottom, config
        ),
        "premium_discount": _score_premium_discount(smc_data, direction, zone_mid),
    }

    weights: dict[str, float] = {
        "displacement": config.weight_displacement,
        "zone_height": config.weight_zone_height,
        "mitigation": config.weight_mitigation,
        "freshness": config.weight_freshness,
        "trend": config.weight_trend,
        "confluence": config.weight_confluence,
        "premium_discount": config.weight_premium_discount,
    }
    total_weight = sum(weights.values())

    fractions: dict[str, float] = {}
    points: dict[str, int] = {}
    reasons: list[str] = []
    for name, (fraction, reason) in components.items():
        fractions[name] = round(fraction, 4)
        contribution = 100.0 * weights[name] * fraction / total_weight if total_weight > 0 else 0.0
        points[name] = int(round(contribution))
        reasons.append(reason)

    score = max(0, min(100, int(round(sum(points.values())))))

    return OBQualityResult(
        score=score,
        grade=_grade_for_score(score, config),
        components=points,
        fractions=fractions,
        reasons=tuple(reasons),
    )


def evaluate_setup_ob_gate(
    metadata: Mapping[str, Any],
    *,
    min_grade: str = "B",
    require_m5_confirmation: bool = True,
) -> OBGateDecision:
    """Décide si un setup passe le filtre qualité OB.

    Args:
        metadata: Métadonnées du setup (``Setup.metadata``).
        min_grade: Grade minimum accepté.
        require_m5_confirmation: Exige ``ob_m5_confirmed`` dans les métadonnées.

    Returns:
        ``OBGateDecision``. Un setup sans ``ob_grade`` (zone FVG, ou notation
        désactivée) est toujours autorisé : le filtre ne s'applique qu'aux OB
        effectivement notés.
    """
    grade = metadata.get("ob_grade")
    if not isinstance(grade, str) or not grade:
        return OBGateDecision(allowed=True, reason="no_grade", grade=None)
    if not grade_meets_min(grade, min_grade):
        return OBGateDecision(allowed=False, reason="grade_below_min", grade=grade)
    if require_m5_confirmation and not bool(metadata.get("ob_m5_confirmed", False)):
        return OBGateDecision(allowed=False, reason="m5_confirmation_missing", grade=grade)
    return OBGateDecision(allowed=True, reason="ok", grade=grade)
