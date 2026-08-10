"""Tests de l'API FastAPI."""

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from arty_trading.api.main import create_app
from arty_trading.config.settings import get_settings


@pytest.fixture
def app():
    """Crée l'application avec un environnement injecté explicitement.

    Les variables sont fournies directement par le test (et non issues d'un
    fichier ``.env`` local) afin de rendre la suite indépendante de la machine.
    """
    with patch.dict(
        os.environ,
        {
            "TRADING_MODE": "analysis",
            "ALLOW_LIVE_TRADING": "false",
            "DEFAULT_SYMBOLS": "EURUSD,GBPUSD,USDJPY,XAUUSD",
            "DEFAULT_TIMEFRAME": "M5",
        },
    ):
        get_settings.cache_clear()
        yield create_app()
        get_settings.cache_clear()


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
