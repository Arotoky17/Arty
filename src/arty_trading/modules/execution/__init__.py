"""
Module Execution — Exécuteur d'ordres.

L'OrderExecutor gère l'ouverture, fermeture et modification d'ordres via MT5.
Inclut le trailing stop et le break-even automatique.
"""

from arty_trading.modules.execution.executor import OrderExecutor

__all__ = ["OrderExecutor"]
