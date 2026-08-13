"""Tests de caractérisation de la surveillance de positions (PositionMonitor).

Extrait de ``application/trading_engine._monitor_open_positions`` sans
changement de comportement. Ces tests figent l'application des actions
(modify / close / partial_close) et le nettoyage du dictionnaire partagé.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from arty_trading.application.position_monitor import PositionMonitor
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction


def _trade(direction: Direction = Direction.BUY) -> Trade:
    return Trade(
        symbol="EURUSD",
        direction=direction,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0950"),
        take_profit=Decimal("1.1100"),
        volume=Decimal("0.1"),
    )


def _monitor(position_manager=None, market_data=None, executor=None,
             risk_manager=None, statistics=None, journal=None,
             notify_critical=None, notify_trade_closed=None):
    pm = position_manager if position_manager is not None else MagicMock()
    md = market_data if market_data is not None else MagicMock(get_tick=AsyncMock(return_value={"bid": 1.1020, "ask": 1.1022}))
    ex = executor if executor is not None else MagicMock()
    rm = risk_manager if risk_manager is not None else MagicMock()
    st = statistics if statistics is not None else MagicMock()
    jr = journal
    nc = notify_critical if notify_critical is not None else AsyncMock()
    ntc = notify_trade_closed if notify_trade_closed is not None else AsyncMock()
    return PositionMonitor(pm, md, ex, rm, st, jr, nc, ntc)


@pytest.mark.asyncio
async def test_modify_action_updates_stop_loss() -> None:
    trade = _trade()
    pos = MagicMock()
    pos.evaluate.return_value = [SimpleNamespace(kind="modify", stop_loss=1.0960, reason="break_even")]
    ex = MagicMock(modify_order=AsyncMock())
    mon = _monitor(position_manager=pos, executor=ex)
    managed = {"t1": trade}

    await mon.monitor(managed)

    ex.modify_order.assert_awaited_once_with(trade, stop_loss=1.0960)
    assert managed == {"t1": trade}  # pas de suppression


@pytest.mark.asyncio
async def test_close_action_removes_trade_and_notifies() -> None:
    trade = _trade()
    closed = _trade()
    closed.profit = Decimal("10")
    pos = MagicMock()
    pos.evaluate.return_value = [SimpleNamespace(kind="close", reason="take_profit", close_fraction=None)]
    ex = MagicMock(close_order=AsyncMock(return_value=closed))
    st = MagicMock()
    jr = SimpleNamespace(record=MagicMock())
    ntc = AsyncMock()
    mon = _monitor(position_manager=pos, executor=ex, statistics=st, journal=jr, notify_trade_closed=ntc)
    managed = {"t1": trade}

    await mon.monitor(managed)

    assert "t1" not in managed  # trade retiré du suivi partagé
    st.record_trade_closed.assert_called_once_with(closed)
    assert jr.record.call_count == 1
    ntc.assert_awaited_once()


@pytest.mark.asyncio
async def test_none_position_manager_returns_immediately() -> None:
    mon = PositionMonitor(
        position_manager=None,
        market_data=MagicMock(),
        executor=MagicMock(),
        risk_manager=MagicMock(),
        statistics=MagicMock(),
        journal=None,
        notify_critical=AsyncMock(),
        notify_trade_closed=AsyncMock(),
    )
    await mon.monitor({"t1": _trade()})
    assert mon._position_manager is None


@pytest.mark.asyncio
async def test_partial_close_skipped_when_not_supported() -> None:
    trade = _trade()
    pos = MagicMock()
    pos.evaluate.return_value = [SimpleNamespace(kind="partial_close", close_fraction=0.5, reason="partial_tp")]
    ex = MagicMock()  # pas de close_partial_order
    mon = _monitor(position_manager=pos, executor=ex)
    managed = {"t1": trade}

    await mon.monitor(managed)
    # Aucune fermeture partielle n'a eu lieu : le trade reste suivi.
    assert "t1" in managed