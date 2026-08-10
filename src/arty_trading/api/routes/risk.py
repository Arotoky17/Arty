"""
Router Risque - Gestion du risque.

Endpoints pour consulter l'etat du risque et valider des signaux.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/risk", tags=["Risque"])
logger = get_logger()


@router.get("/report")
async def get_risk_report(request: Request) -> dict:
    """Retourne le rapport de risque actuel."""
    risk_manager = request.app.state.risk_manager
    return risk_manager.get_risk_report()


@router.get("/status")
async def get_risk_status(request: Request) -> dict:
    """Retourne le statut du risque (positions ouvertes, drawdown, etc.)."""
    risk_manager = request.app.state.risk_manager
    return {
        "open_positions": risk_manager.open_positions_count,
        "daily_loss": str(risk_manager.daily_loss),
        "consecutive_losses": risk_manager.consecutive_losses,
        "current_drawdown": risk_manager.current_drawdown,
        "open_trades": [
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


class ValidateSignalRequest(BaseModel):
    """Requete de validation d'un signal."""

    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    strategy_name: str


@router.post("/validate")
async def validate_signal(req: ValidateSignalRequest, request: Request) -> dict:
    """
    Valide un signal selon les regles de risque.

    Verifie : confiance min, ratio RR min, positions max,
    drawdown max, pertes consecutives max, etc.
    """
    risk_manager = request.app.state.risk_manager

    from decimal import Decimal

    from arty_trading.core.entities import Signal, TradingAccount
    from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingMode

    direction = Direction(req.direction.lower())
    signal = Signal(
        symbol=req.symbol,
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=Decimal(str(req.entry_price)),
        stop_loss=Decimal(str(req.stop_loss)),
        take_profit=Decimal(str(req.take_profit)),
        confidence=req.confidence,
        strategy_name=req.strategy_name,
        timeframe=TimeFrame.H1,
    )

    account = TradingAccount(
        login=0,
        server="demo",
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        mode=TradingMode.PAPER,
    )

    is_valid = await risk_manager.validate_signal(signal, account)

    return {
        "valid": is_valid,
        "reason": "" if is_valid else "Signal rejete par le gestionnaire de risque",
        "risk_report": risk_manager.get_risk_report(),
    }
