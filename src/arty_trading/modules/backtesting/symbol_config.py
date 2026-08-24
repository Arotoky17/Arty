"""Configuration du détecteur SMC par symbole (partagé live / backtest).

Fonction extraite de ``scripts/run_backtest.py`` (copie conforme de
``TradingEngine._configure_detector_for_symbol``) afin de dédupliquer la
logique profil instrument → détecteurs SMC entre le live et les backtests.
"""

from __future__ import annotations

from arty_trading.config.settings import Settings


def configure_detector_for_symbol(detector, symbol: str) -> None:
    """
    Phase 3 — applique les filtres de qualité du profil instrument aux
    sous-détecteurs SMC (même logique que
    ``TradingEngine._configure_detector_for_symbol``), pour que les backtests
    utilisent les mêmes paramètres que le chemin live.
    """
    try:
        profile = Settings().get_instrument_profile(symbol)
    except Exception:  # noqa: BLE001
        profile = None
    if profile is None:
        return

    detectors = getattr(detector, "detectors", None)
    if not isinstance(detectors, dict):
        return

    param_map = {
        "liquidity": {
            "_min_rejection_ratio": profile.sweep_min_rejection_ratio,
            "_displacement_atr_mult": profile.sweep_displacement_atr_mult,
        },
        "fair_value_gap": {
            "_min_gap_atr": profile.min_fvg_atr,
        },
        "order_blocks": {
            "_max_ob_atr_mult": profile.max_ob_atr_mult,
            "_displacement_confirmation_bars": profile.displacement_confirmation_bars,
        },
    }
    for name, params in param_map.items():
        sub = detectors.get(name)
        if sub is None:
            continue
        for attr, value in params.items():
            if hasattr(sub, attr):
                setattr(sub, attr, value)
