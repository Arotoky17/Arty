"""
Module Signaux — Générateur de signaux de trading.

Le SignalGenerator orchestre les stratégies activées, fusionne les données SMC,
et retourne le meilleur signal basé sur le score de confiance.
"""

from arty_trading.modules.signals.generator import SignalGenerator

__all__ = ["SignalGenerator"]