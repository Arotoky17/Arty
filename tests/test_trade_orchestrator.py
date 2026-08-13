"""Tests de caractérisation de l'orchestration risque/exécution (TradeOrchestrator).

Extrait de ``application/trading_engine`` (``_calculate_risk`` / ``_execute_trade``)
sans changement de comportement. Ces tests figent : le calcul du volume via le
RiskManager, le refus en cas d'échec, le garde-fou DEMO, et l'ouverture d'ordre.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arty_trading.application.trade_orchestrator import TradeOrchestrator
from arty_trading.core.entities import Signal, Trade, TradingAccount
from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingMode


def _signal() -> Signal:
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("1.0800"),
        stop_loss=Decimal("1.0780"),
        take_profit=Decimal("1.0840"),
        confidence=0.8,
        strategy_name="Test",
        timeframe=TimeFrame.M5,
    )


def _account() -> TradingAccount:
    return TradingAccount(login=1, server="demo", balance=Decimal("10000"), mode=TradingMode.PAPER)


def _orchestrator(*, mt5=None, risk=None, executor=None, setup=None,
                  mode=TradingMode.PAPER, notified_critical=False,
                  notified_opened=False, open_count=0):
    mt5_c = mt5 if mt5 is not None else MagicMock(get_account_info=AsyncMock(return_value=_account()))
    risk_m = risk if risk is not None else MagicMock(
        can_open_trade=AsyncMock(return_value=True),
        validate_signal=AsyncMock(return_value=True),
        calculate_position_size=AsyncMock(return_value=0.1),
    )
    ex = executor if executor is not None else MagicMock(open_order=AsyncMock(return_value=_mk_trade()))
    nc = AsyncMock()
    nto = AsyncMock()
    orch = TradeOrchestrator(
        mt5_connector=mt5_c, risk_manager=risk_m, executor=ex,
        setup_tracker=setup if setup is not None else MagicMock(),
        trading_mode=mode, notify_critical=nc, notify_trade_opened=nto,
        get_open_positions_count=lambda: open_count,
    )
    return orch, mt5_c, risk_m, ex, nc, nto


def _mk_trade() -> Trade:
    return Trade(symbol="EURUSD", direction=Direction.BUY, entry_price=Decimal("1.0800"),
                 stop_loss=Decimal("1.0780"), take_profit=Decimal("1.0840"), volume=Decimal("0.1"))


@pytest.mark.asyncio
async def test_calculate_risk_returns_volume() -> None:
    orch, _, risk, _, _, _ = _orchestrator()
    volume = await orch.calculate_risk("EURUSD", _signal())
    assert volume == 0.1
    risk.can_open_trade.assert_awaited()
    risk.validate_signal.assert_awaited()
    risk.calculate_position_size.assert_awaited()


@pytest.mark.asyncio
async def test_calculate_risk_none_when_can_not_open() -> None:
    risk = MagicMock(can_open_trade=AsyncMock(return_value=False))
    orch, *_ = _orchestrator(risk=risk)
    assert await orch.calculate_risk("EURUSD", _signal()) is None


@pytest.mark.asyncio
async def test_calculate_risk_none_when_demo_real_account() -> None:
    real = _account()
    real.mode = TradingMode.LIVE
    mt5 = MagicMock(get_account_info=AsyncMock(return_value=real))
    orch, _, _, _, nc, _ = _orchestrator(mt5=mt5, mode=TradingMode.DEMO)
    assert await orch.calculate_risk("EURUSD", _signal()) is None
    nc.assert_awaited()  # alerte sécurité DEMO envoyée


@pytest.mark.asyncio
async def test_execute_trade_returns_trade_and_notifies() -> None:
    orch, _, _, ex, _, nto = _orchestrator()
    trade = await orch.execute_trade("EURUSD", _signal(), 0.1)
    assert trade is not None
    ex.open_order.assert_awaited_once()
    nto.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_trade_none_on_failure() -> None:
    ex = MagicMock(open_order=AsyncMock(side_effect=RuntimeError("boom")))
    orch, *_ = _orchestrator(executor=ex)
    assert await orch.execute_trade("EURUSD", _signal(), 0.1) is None