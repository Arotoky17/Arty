"""Tests du module IA Assistant (Phase 10)."""

import pytest

from arty_trading.modules.ai import AIAssistant, AIMessage, AIResponse
from arty_trading.modules.ai.base import AIProvider
from arty_trading.modules.ai.openai_provider import OpenAIProvider
from arty_trading.modules.ai.anthropic_provider import AnthropicProvider


class TestAIMessage:
    def test_create_message(self):
        msg = AIMessage(role="user", content="Bonjour")
        assert msg.role == "user"
        assert msg.content == "Bonjour"

    def test_create_assistant_message(self):
        msg = AIMessage(role="assistant", content="Bonjour !")
        assert msg.role == "assistant"
        assert msg.content == "Bonjour !"


class TestAIResponse:
    def test_create_response(self):
        resp = AIResponse(content="Reponse", model="gpt-4o-mini")
        assert resp.content == "Reponse"
        assert resp.model == "gpt-4o-mini"
        assert resp.usage == {}

    def test_create_response_with_usage(self):
        resp = AIResponse(
            content="Reponse",
            model="gpt-4o-mini",
            usage={"total_tokens": 100},
        )
        assert resp.usage["total_tokens"] == 100


class TestOpenAIProvider:
    def test_provider_name(self):
        provider = OpenAIProvider(api_key="test-key")
        assert provider.name == "openai"

    def test_provider_model(self):
        provider = OpenAIProvider(api_key="test-key", model="gpt-4o")
        assert provider.model == "gpt-4o"

    def test_provider_no_api_key(self):
        provider = OpenAIProvider(api_key="")
        assert provider.name == "openai"

    @pytest.mark.asyncio
    async def test_chat_no_api_key(self):
        provider = OpenAIProvider(api_key="")
        response = await provider.chat([AIMessage(role="user", content="test")])
        assert "non configuree" in response.content or "Erreur" in response.content


class TestAnthropicProvider:
    def test_provider_name(self):
        provider = AnthropicProvider(api_key="test-key")
        assert provider.name == "anthropic"

    def test_provider_model(self):
        provider = AnthropicProvider(api_key="test-key", model="claude-3-5-sonnet-20241022")
        assert provider.model == "claude-3-5-sonnet-20241022"

    def test_provider_no_api_key(self):
        provider = AnthropicProvider(api_key="")
        assert provider.name == "anthropic"

    @pytest.mark.asyncio
    async def test_chat_no_api_key(self):
        provider = AnthropicProvider(api_key="")
        response = await provider.chat([AIMessage(role="user", content="test")])
        assert "non configuree" in response.content or "Erreur" in response.content


class TestAIAssistant:
    def test_create_assistant_no_provider(self):
        assistant = AIAssistant(provider=None)
        assert assistant.provider is None
        assert len(assistant.history) == 0

    def test_create_assistant_with_provider(self):
        provider = OpenAIProvider(api_key="test-key")
        assistant = AIAssistant(provider=provider)
        assert assistant.provider is not None
        assert assistant.provider.name == "openai"

    def test_clear_history(self):
        assistant = AIAssistant(provider=None)
        assistant._history.append(AIMessage(role="user", content="test"))
        assert len(assistant.history) == 1
        assistant.clear_history()
        assert len(assistant.history) == 0

    @pytest.mark.asyncio
    async def test_chat_no_provider(self):
        assistant = AIAssistant(provider=None)
        response = await assistant.chat("Bonjour")
        assert "non configure" in response.content
        assert response.model == "none"

    @pytest.mark.asyncio
    async def test_chat_with_provider_no_key(self):
        provider = OpenAIProvider(api_key="")
        assistant = AIAssistant(provider=provider)
        response = await assistant.chat("Bonjour")
        assert "non configuree" in response.content or "Erreur" in response.content

    @pytest.mark.asyncio
    async def test_explain_signal(self):
        provider = OpenAIProvider(api_key="")
        assistant = AIAssistant(provider=provider)
        response = await assistant.explain_signal({
            "symbol": "EURUSD",
            "direction": "buy",
            "entry_price": 1.0800,
            "stop_loss": 1.0780,
            "take_profit": 1.0840,
            "confidence": 0.8,
            "strategy_name": "SMC Trend",
        })
        assert response.content != ""

    @pytest.mark.asyncio
    async def test_analyze_trade(self):
        provider = OpenAIProvider(api_key="")
        assistant = AIAssistant(provider=provider)
        response = await assistant.analyze_trade({
            "symbol": "EURUSD",
            "direction": "buy",
            "entry_price": 1.0800,
            "exit_price": 1.0840,
            "profit": 40,
            "result": "win",
        })
        assert response.content != ""

    @pytest.mark.asyncio
    async def test_performance_summary(self):
        provider = OpenAIProvider(api_key="")
        assistant = AIAssistant(provider=provider)
        response = await assistant.performance_summary({
            "total_trades": 100,
            "winning_trades": 60,
            "losing_trades": 40,
            "win_rate": 60,
            "profit_factor": 1.5,
        })
        assert response.content != ""

    @pytest.mark.asyncio
    async def test_suggest_improvements(self):
        provider = OpenAIProvider(api_key="")
        assistant = AIAssistant(provider=provider)
        response = await assistant.suggest_improvements({
            "active_strategies": "SMC Trend, Breakout",
            "risk_per_trade": 0.01,
        })
        assert response.content != ""

    def test_from_settings(self, monkeypatch):
        """Test la creation depuis les settings."""
        from arty_trading.config import get_settings

        monkeypatch.setenv("AI_PROVIDER", "openai")
        get_settings.cache_clear()
        settings = get_settings()
        assistant = AIAssistant.from_settings(settings.ai)
        assert assistant.provider is not None
        assert assistant.provider.name in ("openai", "anthropic")
        get_settings.cache_clear()


class TestAIRoutes:
    """Tests des routes API IA."""

    @pytest.mark.asyncio
    async def test_ai_status(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ai/status")
        assert response.status_code == 200
        data = response.json()
        assert "configured" in data
        assert "provider" in data
        assert "model" in data

    @pytest.mark.asyncio
    async def test_ai_chat(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/ai/chat", json={"message": "Bonjour"})
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "model" in data

    @pytest.mark.asyncio
    async def test_ai_explain_signal(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/ai/explain/signal", json={
                "symbol": "EURUSD",
                "direction": "buy",
                "entry_price": 1.0800,
                "stop_loss": 1.0780,
                "take_profit": 1.0840,
                "confidence": 0.8,
                "strategy_name": "SMC Trend",
            })
        assert response.status_code == 200
        data = response.json()
        assert "response" in data

    @pytest.mark.asyncio
    async def test_ai_clear_history(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/ai/clear-history")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["history_length"] == 0
