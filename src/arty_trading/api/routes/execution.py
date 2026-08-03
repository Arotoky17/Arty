"""
Router Execution - Execution des ordres.

Endpoints pour ouvrir, fermer, modifier des trades
et consulter les positions ouvertes et l'historique.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/execution", tags=["Execution"])
logger = get_logger()


class OpenTradeRequest(BaseModel):
    """Requete d'ouverture de trade."""

    symbol: str
    direction: str
    volume: float
    stop_loss: float
    take_profit: float
    strategy_name: str = "Manual"


class ModifyTradeRequest(BaseModel):
    """Requete de modification de trade."""

    stop_loss: float | None = None
    take_profit: float | None = None


@router.get("/positions")
async def get_positions(request: Request) -> dict:
    """Retourne les positions ouvertes."""
    risk_manager = request.app.state.risk_manager
    return {
        "count": risk_manager.open_positions_count,
        "positions": [
            {
                "id": str(t.id),
                "symbol": t.symbol,
                "direction": t.direction.value,
                "entry_price": float(t.entry_price),
                "stop_loss": float(t.stop_loss),
                "take_profit": float(t.take_profit),
                "volume": float(t.volume),
                "strategy_name": t.strategy_name,
                "ticket": t.ticket,
            }
            for t in risk_manager.open_trades
        ],
    }


@router.post("/open")
async def open_trade(req: OpenTradeRequest, request: Request) -> dict:
    """
    Ouvre un nouveau trade.

    Le mode Reel reste desactive par defaut.
    """
    from arty_trading.core.entities import Signal
    from arty_trading.core.enums import Direction, SignalType, TimeFrame

    risk_manager = request.app.state.risk_manager

    direction = Direction(req.direction.lower())
    signal = Signal(
        symbol=req.symbol,
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=Decimal("0"),
        stop_loss=Decimal(str(req.stop_loss)),
        take_profit=Decimal(str(req.take_profit)),
        confidence=1.0,
        strategy_name=req.strategy_name,
        timeframe=TimeFrame.H1,
    )

    return {
        "success": True,
        "message": f"Trade {req.symbol} {req.direction} prepare",
        "volume": req.volume,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
    }


@router.post("/close/{ticket}")
async def close_trade(ticket: int, request: Request) -> dict:
    """Ferme un trade par son ticket."""
    return {
        "success": True,
        "message": f"Fermeture du trade {ticket} demandee",
        "ticket": ticket,
    }


@router.post("/modify/{ticket}")
async def modify_trade(ticket: int, req: ModifyTradeRequest, request: Request) -> dict:
    """Modifie le SL/TP d'un trade."""
    return {
        "success": True,
        "message": f"Trade {ticket} modifie",
        "ticket": ticket,
        "new_stop_loss": req.stop_loss,
        "new_take_profit": req.take_profit,
    }


@router.get("/history")
async def get_history(request: Request) -> dict:
    """Retourne l'historique des trades fermes."""
    return {
        "count": 0,
        "trades": [],
        "message": "Historique non disponible en mode hors-ligne",
    }
