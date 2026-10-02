"""
Classification des setups SMC (Phase 3).

Attribue à chaque setup un type normalisé dérivé des détections SMC du
timeframe de setup (M15) :

- ``CONTINUATION`` : zone FVG/OB dans le sens de la tendance M15, sans
  retournement de structure récent.
- ``REVERSAL`` : un CHoCH/MSS dans la direction du setup précède la zone.
- ``LIQUIDITY_SWEEP_REVERSAL`` : un liquidity sweep dans la direction du
  setup précède la zone (retournement sur liquidité).
- ``FVG_RETRACE`` : zone FVG sans événement de structure ni sweep.
- ``ORDER_BLOCK_RETRACE`` : zone OB sans événement de structure ni sweep.

La classification est purement descriptive : elle n'affecte ni le
SignalValidator (gelé en Phase 3) ni la gestion de position. Elle alimente
les statistiques par type de setup (journal Phase 3E).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arty_trading.modules.smc.setup_tracker import MarketPhaseType, SetupType

SETUP_TYPES = (
    "CONTINUATION",
    "REVERSAL",
    "LIQUIDITY_SWEEP_REVERSAL",
    "FVG_RETRACE",
    "ORDER_BLOCK_RETRACE",
)

_BOS_CONCEPTS = {
    "bos",
    "break_of_structure",
    "internal_bos",
    "external_bos",
}
_CHOCH_CONCEPTS = {"choch", "change_of_character", "mss", "market_structure_shift"}
_STRUCTURE_CONCEPTS = _BOS_CONCEPTS | _CHOCH_CONCEPTS


@dataclass(frozen=True)
class SetupQualification:
    """Explainable result of validating a setup family from closed-bar evidence."""

    setup_type: SetupType
    phases: tuple[MarketPhaseType, ...]
    evidence: tuple[str, ...]
    reasons: tuple[str, ...]


def qualify_setup_type(
    *,
    direction: str,
    smc_data: list[dict[str, Any]],
    zone_index: int,
    h4_trend: str,
    h1_trend: str,
    m5_confirmed: bool,
    m5_retested: bool,
) -> SetupQualification:
    """Return a setup family only when its structural and retest evidence agrees.

    Detection indexes are limited to events at or before the zone, so a later
    BOS/CHoCH cannot retroactively validate an earlier zone.
    """
    if direction not in ("bullish", "bearish"):
        return _no_setup("invalid_direction")
    if not m5_retested:
        return _no_setup("zone_not_retested_after_creation")
    if not m5_confirmed:
        return _no_setup("m5_confirmation_missing")
    if h1_trend != direction:
        return _no_setup("h1_direction_conflict")

    prior: list[tuple[str, int, dict[str, Any]]] = []
    for item in smc_data:
        try:
            event_index = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        concept = str(item.get("concept", "")).strip().lower()
        if event_index <= zone_index:
            prior.append((concept, event_index, item))

    directional_sweeps = [
        (index, item)
        for concept, index, item in prior
        if concept == "liquidity_sweep"
        and item.get("direction") == direction
        and _valid_sweep(item)
    ]
    for sweep_index, sweep in reversed(directional_sweeps):
        displacement = bool(sweep.get("details", {}).get("displacement_confirmed"))
        follow_through = next(
            (
                (concept, index, item)
                for concept, index, item in prior
                if sweep_index < index <= zone_index
                and concept in _STRUCTURE_CONCEPTS
                and item.get("direction") == direction
            ),
            None,
        )
        if displacement or follow_through is not None:
            evidence = [f"liquidity_sweep@{sweep_index}"]
            if displacement:
                evidence.append("sweep_displacement_confirmed")
            if follow_through is not None:
                evidence.append(f"{follow_through[0]}@{follow_through[1]}")
            return SetupQualification(
                SetupType.SWEEP_REVERSAL,
                (MarketPhaseType.LIQUIDITY_SWEEP, MarketPhaseType.DISPLACEMENT,
                 MarketPhaseType.RETRACEMENT),
                tuple(evidence),
                (),
            )

    directional_choch = [
        (index, item)
        for concept, index, item in prior
        if concept in _CHOCH_CONCEPTS and item.get("direction") == direction
    ]
    for choch_index, choch in reversed(directional_choch):
        previous_structure = next(
            (
                (concept, index, item)
                for concept, index, item in reversed(prior)
                if index < choch_index
                and concept in _STRUCTURE_CONCEPTS
                and item.get("direction") != direction
            ),
            None,
        )
        if previous_structure is not None:
            return SetupQualification(
                SetupType.CHOCH_REVERSAL,
                (MarketPhaseType.STRUCTURE_SHIFT, MarketPhaseType.RETRACEMENT),
                (
                    f"prior_{previous_structure[0]}@{previous_structure[1]}",
                    f"{str(choch.get('concept'))}@{choch_index}",
                ),
                (),
            )

    matching_bos = [
        (index, item)
        for concept, index, item in prior
        if concept in _BOS_CONCEPTS and item.get("direction") == direction
    ]
    if matching_bos and h1_trend == direction and h4_trend == direction:
        bos_index, _ = matching_bos[-1]
        return SetupQualification(
            SetupType.BOS_RETEST_CONTINUATION,
            (MarketPhaseType.STRUCTURE_SHIFT, MarketPhaseType.RETRACEMENT,
             MarketPhaseType.CONTINUATION),
            (f"bos@{bos_index}", "h4_h1_aligned", "m5_retest_rejection"),
            (),
        )

    reason = "no_coherent_structure_family"
    if h4_trend != direction and h1_trend == direction:
        reason = "counter_h4_requires_sweep_or_choch"
    elif h1_trend != direction:
        reason = "h1_direction_conflict"
    return _no_setup(reason)


def _valid_sweep(event: dict[str, Any]) -> bool:
    """Require the detector's swept level and positive re-entry evidence."""
    details = event.get("details", {})
    try:
        return (
            details.get("swept_level") is not None
            and float(details.get("rejection_ratio", 0.0)) > 0
        )
    except (TypeError, ValueError):
        return False


def _no_setup(reason: str) -> SetupQualification:
    return SetupQualification(
        SetupType.NO_TRADE,
        (MarketPhaseType.NO_VALID_SETUP,),
        (),
        (reason,),
    )


def classify_setup_type(
    zone_concept: str,
    direction: str,
    smc_data: list[dict[str, Any]],
    zone_index: int = 0,
    lookback_bars: int = 30,
) -> str:
    """
    Classifie un setup à partir du contexte SMC du timeframe de setup.

    Args:
        zone_concept: "fair_value_gap" ou "order_block".
        direction: Direction du setup ("bullish"/"bearish").
        smc_data: Détections SMC du timeframe de setup (M15).
        zone_index: Index de la bougie de la zone (les événements postérieurs
            sont ignorés — pas de look-ahead).
        lookback_bars: Fenêtre maximale avant la zone pour chercher un
            sweep ou un CHoCH.

    Returns:
        Un des types de ``SETUP_TYPES``.
    """
    zone_index = int(zone_index or 0)

    # Événements dans la même direction, antérieurs ou simultanés à la zone.
    sweep = None
    choch = None
    for d in smc_data:
        concept = d.get("concept", "")
        if d.get("direction") != direction:
            continue
        index = int(d.get("index", 0) or 0)
        if index > zone_index or (zone_index - index) > lookback_bars:
            continue
        if concept == "liquidity_sweep" and (sweep is None or index > sweep):
            sweep = index
        elif concept in ("choch", "mss") and (choch is None or index > choch):
            choch = index

    if sweep is not None:
        return "LIQUIDITY_SWEEP_REVERSAL"
    if choch is not None:
        return "REVERSAL"

    if zone_concept == "fair_value_gap":
        return "FVG_RETRACE"
    if zone_concept == "order_block":
        return "ORDER_BLOCK_RETRACE"
    return "CONTINUATION"
