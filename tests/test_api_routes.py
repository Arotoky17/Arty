"""Tests des routes API Phase 9 — SMC, Signaux, Risque, Exécution, Backtesting."""

import pytest
from httpx import ASGITransport, AsyncClient

from arty_trading.api.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# =====================================================================
# SMC Routes
# =====================================================================


class TestSMCRoutes:
    @pytest.mark.asyncio
    async def test_list_concepts(self, client):
        response = await client.get("/smc/concepts")
        assert response.status_code == 200
        data = response.json()
        assert "concepts" in data
        assert "structure" in data["concepts"]
        assert "fair_value_gap" in data["concepts"]
        assert "order_blocks" in data["concepts"]
        assert "liquidity" in data["concepts"]
        assert "premium_discount" in data["concepts"]

    @pytest.mark.asyncio
    async def test_get_enabled_concepts(self, client):
        response = await client.get("/smc/enabled")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert isinstance(data["enabled"], list)

    @pytest.mark.asyncio
    async def test_enable_concept(self, client):
        response = await client.post("/smc/enable/structure")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "structure" in data["enabled"]

    @pytest.mark.asyncio
    async def test_disable_concept(self, client):
        # D'abord activer
        await client.post("/smc/enable/structure")
        # Puis désactiver
        response = await client.post("/smc/disable/structure")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "structure" not in data["enabled"]

    @pytest.mark.asyncio
    async def test_enable_all(self, client):
        response = await client.post("/smc/enable-all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["enabled"]) == 5

    @pytest.mark.asyncio
    async def test_disable_all(self, client):
        response = await client.post("/smc/disable-all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["enabled"]) == 0


# =====================================================================
# Signals Routes
# =====================================================================


class TestSignalsRoutes:
    @pytest.mark.asyncio
    async def test_list_strategies(self, client):
        response = await client.get("/signals/strategies")
        assert response.status_code == 200
        data = response.json()
        assert "strategies" in data
        assert len(data["strategies"]) > 0
        # Vérifier que les stratégies attendues sont présentes
        names = [s["name"] for s in data["strategies"]]
        assert "SMC Trend Following" in names
        assert "Breakout" in names

    @pytest.mark.asyncio
    async def test_get_enabled_strategies(self, client):
        response = await client.get("/signals/strategies/enabled")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert isinstance(data["enabled"], list)

    @pytest.mark.asyncio
    async def test_enable_strategy(self, client):
        response = await client.post("/signals/strategies/Breakout/enable")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Breakout" in data["enabled"]

    @pytest.mark.asyncio
    async def test_disable_strategy(self, client):
        await client.post("/signals/strategies/Breakout/enable")
        response = await client.post("/signals/strategies/Breakout/disable")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Breakout" not in data["enabled"]

    @pytest.mark.asyncio
    async def test_enable_all_strategies(self, client):
        response = await client.post("/signals/strategies/enable-all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["enabled"]) >= 6

    @pytest.mark.asyncio
    async def test_disable_all_strategies(self, client):
        response = await client.post("/signals/strategies/disable-all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["enabled"]) == 0


# =====================================================================
# Risk Routes
# =====================================================================


class TestRiskRoutes:
    @pytest.mark.asyncio
    async def test_get_risk_report(self, client):
        response = await client.get("/risk/report")
        assert response.status_code == 200
        data = response.json()
        assert "open_positions" in data
        assert "max_open_positions" in data
        assert "daily_loss" in data
        assert "consecutive_losses" in data
        assert "current_drawdown_pct" in data
        assert "max_drawdown_pct" in data
        assert "risk_per_trade_pct" in data

    @pytest.mark.asyncio
    async def test_get_risk_status(self, client):
        response = await client.get("/risk/status")
        assert response.status_code == 200
        data = response.json()
        assert "open_positions" in data
        assert "daily_loss" in data
        assert "consecutive_losses" in data
        assert "current_drawdown" in data
        assert "open_trades" in data
        assert isinstance(data["open_trades"], list)

    @pytest.mark.asyncio
    async def test_validate_signal(self, client):
        response = await client.post(
            "/risk/validate",
            json={
                "symbol": "EURUSD",
                "direction": "buy",
                "entry_price": 1.0800,
                "stop_loss": 1.0780,
                "take_profit": 1.0840,
                "confidence": 0.8,
                "strategy_name": "Test",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        assert "risk_report" in data


# =====================================================================
# Execution Routes
# =====================================================================


class TestExecutionRoutes:
    @pytest.mark.asyncio
    async def test_get_positions(self, client):
        response = await client.get("/execution/positions")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "positions" in data
        assert isinstance(data["positions"], list)

    @pytest.mark.asyncio
    async def test_get_history(self, client):
        response = await client.get("/execution/history")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "trades" in data

    @pytest.mark.asyncio
    async def test_open_trade(self, client):
        response = await client.post(
            "/execution/open",
            json={
                "symbol": "EURUSD",
                "direction": "buy",
                "volume": 0.1,
                "stop_loss": 1.0780,
                "take_profit": 1.0840,
                "strategy_name": "Manual",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_close_trade(self, client):
        response = await client.post("/execution/close/12345")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["ticket"] == 12345

    @pytest.mark.asyncio
    async def test_modify_trade(self, client):
        response = await client.post(
            "/execution/modify/12345",
            json={"stop_loss": 1.0770, "take_profit": 1.0850},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["ticket"] == 12345


# =====================================================================
# Backtesting Routes
# =====================================================================


class TestBacktestingRoutes:
    @pytest.mark.asyncio
    async def test_backtest_info(self, client):
        response = await client.get("/backtesting/info")
        assert response.status_code == 200
        data = response.json()
        assert data["engine"] == "BacktestEngine"
        assert "features" in data
        assert "default_params" in data
        assert data["default_params"]["initial_balance"] == 10000