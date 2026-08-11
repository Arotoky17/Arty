"""
Module Execution — Exécuteurs d'ordres.

Deux exécuteurs sont disponibles :

- **OrderExecutor** : Exécution réelle via MT5 (mode LIVE) avec mode mock
  de secours si MT5 n'est pas disponible.
- **PaperOrderExecutor** : Simulation complète en mémoire (mode PAPER),
  aucun ordre MT5 n'est envoyé.
"""

from arty_trading.modules.execution.executor import MT5OrderError, OrderExecutor
from arty_trading.modules.execution.paper_executor import PaperOrderExecutor
from arty_trading.modules.execution.position_manager import PositionAction, PositionManager

__all__ = [
    "OrderExecutor",
    "PaperOrderExecutor",
    "PositionAction",
    "PositionManager",
    "MT5OrderError",
]
