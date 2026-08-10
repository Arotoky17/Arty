"""Tests de l'API FastAPI."""

import pytest
from httpx import ASGITransport, AsyncClient

from arty_trading.api.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_health_check(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["bot"] == "Arty"
    assert data["name"] == "Arty"
    assert data["trading_mode"] == "analysis"
    assert data["live_trading_enabled"] is False


@pytest.mark.asyncio
async def test_get_symbols(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/config/symbols")
    assert response.status_code == 200
    data = response.json()
    assert "EURUSD" in data["symbols"]
    assert data["timeframe"] == "M5"


@pytest.mark.asyncio
async def test_get_risk_config(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/config/risk")
    assert response.status_code == 200
    data = response.json()
    assert data["risk_per_trade"] == 0.01
    assert data["max_open_positions"] == 3
