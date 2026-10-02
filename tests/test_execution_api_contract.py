"""Execution API contracts using FastAPI TestClient and isolated executors."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from arty_trading.api.dependencies import get_executor
from arty_trading.api.routes.execution import router
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction

PAYLOAD = {
    "symbol": "XAUUSD",
    "direction": "buy",
    "volume": 0.1,
    "stop_loss": 2900,
    "take_profit": 3100,
}


def make_trade():
    return Trade(
        symbol="XAUUSD",
        direction=Direction.BUY,
        entry_price=Decimal("3000"),
        stop_loss=Decimal("2900"),
        take_profit=Decimal("3100"),
        volume=Decimal("0.1"),
        ticket=987654,
    )


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    executor = AsyncMock()
    executor.open_order.return_value = make_trade()
    executor.get_open_positions.return_value = [make_trade()]
    executor.close_order.return_value = make_trade().model_copy(update={"is_open": False})
    executor.modify_order.return_value = make_trade().model_copy(
        update={"stop_loss": Decimal("2950")}
    )
    app.dependency_overrides[get_executor] = lambda: executor
    app.state.market_data = SimpleNamespace(
        get_tick=AsyncMock(return_value={"bid": 2999, "ask": 3000})
    )
    return app, executor


@pytest.fixture
async def execution_client(api):
    app, executor = api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, executor


def test_open_position_calls_executor(api):
    app, executor = api
    response = TestClient(app).post("/execution/open", json=PAYLOAD)
    assert response.status_code == 200
    executor.open_order.assert_awaited_once()
    signal, volume = executor.open_order.await_args.args
    assert signal.symbol == "XAUUSD"
    assert signal.entry_price == Decimal("3000")
    assert volume == 0.1


def test_open_position_returns_real_ticket(api):
    app, executor = api
    response = TestClient(app).post("/execution/open", json=PAYLOAD)
    assert response.json()["ticket"] == executor.open_order.return_value.ticket
    assert response.json()["success"] is True
    assert response.json()["error"] is None


def test_close_position_calls_executor(api):
    app, executor = api
    response = TestClient(app).post("/execution/close/987654")
    assert response.status_code == 200
    executor.close_order.assert_awaited_once_with(executor.get_open_positions.return_value[0])
    assert response.json()["ticket"] == 987654


def test_modify_position_calls_executor(api):
    app, executor = api
    response = TestClient(app).post("/execution/modify/987654", json={"stop_loss": 2950})
    assert response.status_code == 200
    executor.modify_order.assert_awaited_once_with(
        executor.get_open_positions.return_value[0], 2950, None
    )
    assert Decimal(response.json()["stop_loss"]) == Decimal("2950")


def assert_failure(api, method, path, payload):
    app, executor = api
    getattr(executor, method).side_effect = RuntimeError("MT5 refused order")
    response = TestClient(app).post(path, json=payload)
    assert response.status_code == 500
    assert response.json() == {"detail": "MT5 refused order"}
    getattr(executor, method).assert_awaited_once()


def test_open_position_propagates_executor_failure(api):
    assert_failure(api, "open_order", "/execution/open", PAYLOAD)


def test_close_position_propagates_executor_failure(api):
    assert_failure(api, "close_order", "/execution/close/987654", None)


def test_modify_position_propagates_executor_failure(api):
    assert_failure(api, "modify_order", "/execution/modify/987654", {"stop_loss": 2950})


@pytest.mark.parametrize(
    "result",
    [
        None,
        SimpleNamespace(success=False, error="Rejected"),
        make_trade().model_copy(update={"ticket": None}),
    ],
)
def test_open_rejects_invalid_executor_result(api, result):
    app, executor = api
    executor.open_order.return_value = result
    response = TestClient(app).post("/execution/open", json=PAYLOAD)
    assert response.status_code == 400
    assert "success" not in response.json()


def test_unknown_position_does_not_execute(api):
    app, executor = api
    executor.get_open_positions.return_value = []
    assert TestClient(app).post("/execution/close/123").status_code == 404
    executor.close_order.assert_not_called()


def test_missing_executor():
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).post("/execution/close/123").status_code == 503


@pytest.mark.parametrize(
    "field,value",
    [
        ("direction", "invalid"),
        ("symbol", ""),
        ("volume", 0),
        ("volume", -1),
        ("stop_loss", 0),
        ("take_profit", -1),
    ],
)
async def test_invalid_open_request(execution_client, field, value):
    client, executor = execution_client
    payload = {
        "symbol": "XAUUSD",
        "direction": "buy",
        "volume": 0.1,
        "stop_loss": 2900,
        "take_profit": 3100,
    }
    payload[field] = value
    response = await client.post("/execution/open", json=payload)
    assert response.status_code == 422
    executor.open_order.assert_not_called()


@pytest.mark.parametrize(
    "payload", [{}, {"stop_loss": None}, {"stop_loss": 0}, {"take_profit": -1}]
)
async def test_invalid_modify_request(execution_client, payload):
    client, executor = execution_client
    response = await client.post("/execution/modify/12345", json=payload)
    assert response.status_code == 422
    executor.modify_order.assert_not_called()


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("open_order", "/execution/open", PAYLOAD),
        ("close_order", "/execution/close/987654", None),
        ("modify_order", "/execution/modify/987654", {"stop_loss": 2950}),
    ],
)
def test_all_executor_exceptions_are_500(api, method, path, payload):
    app, executor = api
    getattr(executor, method).side_effect = HTTPException(status_code=409, detail="Broker conflict")
    response = TestClient(app).post(path, json=payload)
    assert response.status_code == 500
    assert "Broker conflict" in response.json()["detail"]


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("close_order", "/execution/close/987654", None),
        ("modify_order", "/execution/modify/987654", {"stop_loss": 2950}),
    ],
)
def test_close_modify_reject_explicit_failure(api, method, path, payload):
    app, executor = api
    getattr(executor, method).return_value = SimpleNamespace(success=False, error="Broker rejected")
    response = TestClient(app).post(path, json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Broker rejected"


@pytest.mark.parametrize("tick", [None, {}, {"ask": None}, {"ask": 0}, {"ask": "NaN"}])
def test_open_without_price_does_not_execute(api, tick):
    app, executor = api
    app.state.market_data.get_tick.return_value = tick
    response = TestClient(app).post("/execution/open", json=PAYLOAD)
    assert response.status_code == 400
    executor.open_order.assert_not_called()


def test_open_sell_uses_bid(api):
    app, executor = api
    response = TestClient(app).post("/execution/open", json={**PAYLOAD, "direction": "sell"})
    assert response.status_code == 200
    assert executor.open_order.await_args.args[0].entry_price == Decimal("2999")


def test_paper_executor_complete_lifecycle(api):
    from arty_trading.modules.execution.paper_executor import PaperOrderExecutor

    app, _ = api
    executor = PaperOrderExecutor()
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)
    opened = client.post("/execution/open", json=PAYLOAD)
    assert opened.status_code == 200
    ticket = opened.json()["ticket"]
    assert executor.get_trade_by_ticket(ticket) is not None
    modified = client.post(f"/execution/modify/{ticket}", json={"stop_loss": 2950})
    assert modified.status_code == 200
    assert executor.get_trade_by_ticket(ticket).stop_loss == Decimal("2950")
    closed = client.post(f"/execution/close/{ticket}")
    assert closed.status_code == 200
    assert executor.open_trades_count == 0
    assert executor.closed_trades_count == 1
