"""
Module Risk — Gestionnaire de risque.

Le RiskManager valide les signaux, calcule la taille de position,
et gère les limites de risque (positions, drawdown, pertes consécutives).
"""

from arty_trading.modules.risk.manager import RiskManager

__all__ = ["RiskManager"]