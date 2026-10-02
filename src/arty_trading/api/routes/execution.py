"""
Router Execution - Execution des ordres.

Endpoints pour ouvrir, fermer, modifier des trades
et consulter les positions ouvertes et l'historique.
"""

from __future__ import annotations

from collections.abc import Awaitable
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, NoReturn, Protocol, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from arty_trading.api.dependencies import get_executor
from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.core.interfaces import IOrderExecutor

router = APIRouter(prefix="/execution", tags=["Execution"])
logger = structlog.get_logger(__name__)
ExecutorDependency = Annotated[IOrderExecutor, Depends(get_executor)]


class TickProvider(Protocol):
    async def get_tick(self, symbol: str) -> dict[str, Any] | None: ...


class ExecutionResultDTO(BaseModel):
    """Outcome derived from the executor's returned domain trade."""

    success: bool
    ticket: int
    error: str | None = None
    stop_loss: Decimal
    take_profit: Decimal
    volume: Decimal

    @classmethod
    def from_trade(cls, result: object) -> ExecutionResultDTO:
        if not isinstance(result, Trade):
            error = getattr(result, "error", None) or "Executor returned no valid trade"
            raise HTTPException(status_code=400, detail=str(error))
        ticket = result.ticket
        if ticket is None or ticket <= 0:
            raise HTTPException(status_code=400, detail="Executor returned no valid ticket")
        return cls(
            success=ticket > 0,
            ticket=ticket,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            volume=result.volume,
        )


def execution_error(operation: str, exc: Exception) -> NoReturn:
    logger.exception("execution_failed", operation=operation, error=str(exc))
    raise HTTPException(status_code=500, detail=str(exc)) from exc


async def execute(operation: str, command: Awaitable[Trade]) -> Trade:
    """Map every executor exception, including HTTPException, to HTTP 500."""
    try:
        return await command
    except Exception as exc:
        execution_error(operation, exc)


async def find_position(executor: IOrderExecutor, ticket: int) -> Trade:
    try:
        positions = await executor.get_open_positions()
    except Exception as exc:
        execution_error("positions", exc)
    for position in positions:
        if position.ticket == ticket:
            return position
    raise HTTPException(status_code=404, detail=f"Position {ticket} not found")


class OpenTradeRequest(BaseModel):
    """Requete d'ouverture de trade."""

    symbol: str = Field(min_length=1)
    direction: Direction
    volume: float = Field(gt=0, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, allow_inf_nan=False)
    take_profit: float = Field(gt=0, allow_inf_nan=False)
    strategy_name: str = "Manual"


class ModifyTradeRequest(BaseModel):
    """Requete de modification de trade."""

    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    take_profit: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_change(self) -> ModifyTradeRequest:
        if self.stop_loss is None and self.take_profit is None:
            raise ValueError("Provide stop_loss or take_profit")
        return self


@router.get("/positions")
async def get_positions(request: Request) -> dict[str, Any]:
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


@router.post("/open", response_model=ExecutionResultDTO)
async def open_trade(
    req: OpenTradeRequest, request: Request, executor: ExecutorDependency
) -> ExecutionResultDTO:
    """Open a manual order through the configured executor."""
    try:
        provider = getattr(request.app.state, "market_data", None)
        if provider is None:
            raise HTTPException(status_code=503, detail="Market data is unavailable")
        tick = await cast(TickProvider, provider).get_tick(req.symbol)
        key = "ask" if req.direction == Direction.BUY else "bid"
        try:
            price = Decimal(str(tick.get(key))) if tick else Decimal("0")
        except InvalidOperation as exc:
            raise HTTPException(status_code=400, detail="No valid market price available") from exc
        if not price.is_finite() or price <= 0:
            raise HTTPException(status_code=400, detail="No valid market price available")
        signal = Signal(
            symbol=req.symbol,
            signal_type=SignalType.BUY if req.direction == Direction.BUY else SignalType.SELL,
            direction=req.direction,
            entry_price=price,
            stop_loss=Decimal(str(req.stop_loss)),
            take_profit=Decimal(str(req.take_profit)),
            confidence=1.0,
            strategy_name=req.strategy_name,
            timeframe=TimeFrame.H1,
        )
        result = await execute("open", executor.open_order(signal, req.volume))
        return ExecutionResultDTO.from_trade(result)
    except HTTPException:
        raise
    except Exception as exc:
        execution_error("open", exc)


@router.post("/close/{ticket}", response_model=ExecutionResultDTO)
async def close_trade(
    ticket: int, request: Request, executor: ExecutorDependency
) -> ExecutionResultDTO:
    """Close the position identified by its actual executor ticket."""
    try:
        position = await find_position(executor, ticket)
        result = await execute("close", executor.close_order(position))
        response = ExecutionResultDTO.from_trade(result)
        if result.is_open or response.ticket != ticket:
            raise HTTPException(status_code=400, detail="Executor did not confirm position closure")
        return response
    except HTTPException:
        raise
    except Exception as exc:
        execution_error("close", exc)


@router.post("/modify/{ticket}", response_model=ExecutionResultDTO)
async def modify_trade(
    ticket: int, req: ModifyTradeRequest, request: Request, executor: ExecutorDependency
) -> ExecutionResultDTO:
    """Modify SL/TP through the configured executor."""
    try:
        position = await find_position(executor, ticket)
        result = await execute(
            "modify", executor.modify_order(position, req.stop_loss, req.take_profit)
        )
        response = ExecutionResultDTO.from_trade(result)
        if response.ticket != ticket:
            raise HTTPException(status_code=400, detail="Executor returned a different position")
        return response
    except HTTPException:
        raise
    except Exception as exc:
        execution_error("modify", exc)


@router.get("/history")
async def get_history(request: Request) -> dict[str, Any]:
    """Retourne l'historique des trades fermes."""
    return {
        "count": 0,
        "trades": [],
        "message": "Historique non disponible en mode hors-ligne",
    }
