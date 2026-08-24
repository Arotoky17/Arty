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

SETUP_TYPES = (
    "CONTINUATION",
    "REVERSAL",
    "LIQUIDITY_SWEEP_REVERSAL",
    "FVG_RETRACE",
    "ORDER_BLOCK_RETRACE",
)


def classify_setup_type(
    zone_concept: str,
    direction: str,
    smc_data: list[dict],
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
