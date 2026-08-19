"""
Module Execution — Exécuteurs d'ordres.

Deux exécuteurs sont disponibles :

- **OrderExecutor** : Exécution réelle via MT5 (mode LIVE) avec mode mock
  de secours si MT5 n'est pas disponible.
- **PaperOrderExecutor** : Simulation complète en mémoire (mode PAPER),
  aucun ordre MT5 n'est envoyé.
"""

from arty_trading.modules.execution.executor import (
    MT5OrderError,
    MT5PositionNotFoundError,
    OrderExecutor,
)
from arty_trading.modules.execution.paper_executor import PaperOrderExecutor
from arty_trading.modules.execution.position_manager import PositionAction, PositionManager
from arty_trading.modules.execution.profit_lock import ProfitLockLevel, ProfitLockManager
from arty_trading.modules.execution.runner import RunnerManager
from arty_trading.modules.execution.sl_guard import (
    StructureContext,
    is_more_protective,
    most_protective,
)
from arty_trading.modules.execution.structure_trailing import StructureTrailingManager
from arty_trading.modules.execution.partial_profit import PartialLevel, PartialProfitManager

__all__ = [
    "OrderExecutor",
    "PaperOrderExecutor",
    "PositionAction",
    "PositionManager",
    "ProfitLockManager",
    "ProfitLockLevel",
    "PartialProfitManager",
    "PartialLevel",
    "RunnerManager",
    "StructureTrailingManager",
    "StructureContext",
    "is_more_protective",
    "most_protective",
    "MT5OrderError",
]
