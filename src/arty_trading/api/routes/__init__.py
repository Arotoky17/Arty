"""
Routes API — Routers FastAPI pour les modules métier.

SMC, Signaux, Stratégies, Risque, Exécution, Backtesting.
"""

from arty_trading.api.routes.ai import router as ai_router
from arty_trading.api.routes.notifications import router as notifications_router
from arty_trading.api.routes.backtesting import router as backtesting_router
from arty_trading.api.routes.execution import router as execution_router
from arty_trading.api.routes.risk import router as risk_router
from arty_trading.api.routes.signals import router as signals_router
from arty_trading.api.routes.smc import router as smc_router

__all__ = [
    "smc_router",
    "signals_router",
    "risk_router",
    "execution_router",
    "backtesting_router",
    "ai_router",
    "notifications_router",
]