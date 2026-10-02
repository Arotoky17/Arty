"""MT5 calls must leave the event loop responsive and preserve broker errors."""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arty_trading.modules.execution.executor import MT5OrderError, OrderExecutor


async def test_send_order_uses_to_thread() -> None:
    request = {"symbol": "XAUUSD", "volume": 0.1}
    result = object()
    broker = MagicMock()
    executor = OrderExecutor(mock_mode=True)
    with (
        patch("arty_trading.modules.execution.executor.mt5", broker),
        patch("arty_trading.modules.execution.executor.asyncio.to_thread", new_callable=AsyncMock)
        as to_thread,
    ):
        to_thread.return_value = result
        assert await executor._send_order(request) is result
        to_thread.assert_awaited_once_with(broker.order_send, request)
        broker.order_send.assert_not_called()


async def test_send_order_does_not_block_event_loop() -> None:
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    both_started = asyncio.Event()
    release = threading.Event()
    lock = threading.Lock()
    workers: set[int] = set()
    active = 0

    def send(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal active
        with lock:
            workers.add(threading.get_ident())
            active += 1
            if active == 2:
                loop.call_soon_threadsafe(both_started.set)
        if not release.wait(timeout=3):
            raise RuntimeError("event loop did not release the broker workers")
        return request

    broker = MagicMock(order_send=MagicMock(side_effect=send))
    executor = OrderExecutor(mock_mode=True)
    requests = [{"symbol": "XAUUSD", "id": i} for i in range(2)]
    with patch("arty_trading.modules.execution.executor.mt5", broker):
        tasks = [asyncio.create_task(executor._send_order(request)) for request in requests]
        try:
            # The event loop resumes while both synchronous broker calls are blocked.
            await asyncio.wait_for(both_started.wait(), timeout=2)
            assert all(not task.done() for task in tasks)
            assert loop_thread not in workers
            assert len(workers) == 2
            release.set()
            assert await asyncio.gather(*tasks) == requests
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_send_order_propagates_mt5_error() -> None:
    error = MT5OrderError("broker unavailable")
    broker = MagicMock(order_send=MagicMock(side_effect=error))
    executor = OrderExecutor(mock_mode=True)
    with patch("arty_trading.modules.execution.executor.mt5", broker):
        with pytest.raises(MT5OrderError) as caught:
            await executor._send_order({"symbol": "XAUUSD"})
    assert caught.value is error
    broker.order_send.assert_called_once()


async def test_send_order_timeout_if_configured() -> None:
    release = threading.Event()
    finished = threading.Event()

    def send(request: dict[str, Any]) -> object:
        try:
            release.wait(timeout=3)
            return object()
        finally:
            finished.set()

    broker = MagicMock(order_send=MagicMock(side_effect=send))
    executor = OrderExecutor(mock_mode=True)
    executor.order_send_timeout = 0.1
    with patch("arty_trading.modules.execution.executor.mt5", broker):
        try:
            with pytest.raises(TimeoutError):
                await executor._send_order({"symbol": "XAUUSD"})
            assert not finished.is_set()
            broker.order_send.assert_called_once()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)


async def test_send_order_preserves_none_result() -> None:
    executor = OrderExecutor(mock_mode=True)
    assert executor.order_send_timeout is None
    broker = MagicMock(order_send=MagicMock(return_value=None))
    with patch("arty_trading.modules.execution.executor.mt5", broker):
        assert await executor._send_order({"symbol": "XAUUSD"}) is None


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_order_send_timeout_rejects_invalid_values(timeout: float) -> None:
    executor = OrderExecutor(mock_mode=True)
    with pytest.raises(ValueError):
        executor.order_send_timeout = timeout


async def test_reconciliation_moves_initialization_and_position_reads_off_loop() -> None:
    loop_thread = threading.get_ident()
    calls: list[int] = []

    def initialize() -> bool:
        calls.append(threading.get_ident())
        return True

    def positions_get() -> tuple[()]:
        calls.append(threading.get_ident())
        return ()

    broker = MagicMock(initialize=initialize, positions_get=positions_get)
    executor = OrderExecutor(mock_mode=False)
    with (
        patch("arty_trading.modules.execution.executor.mt5", broker),
        patch("arty_trading.modules.execution.executor.MT5_AVAILABLE", True),
    ):
        assert await executor.get_open_positions() == []
    assert len(calls) == 2
    assert loop_thread not in calls
