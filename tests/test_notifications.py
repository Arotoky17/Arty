"""Tests du module Notifications (Phase 11)."""

import pytest

from arty_trading.infrastructure.notifications.base import BaseNotifier, Notification
from arty_trading.infrastructure.notifications.telegram import TelegramNotifier
from arty_trading.infrastructure.notifications.discord import DiscordNotifier
from arty_trading.infrastructure.notifications.email import EmailNotifier
from arty_trading.infrastructure.notifications.manager import NotificationManager


class TestNotification:
    def test_create_notification(self):
        notif = Notification(title="Test", message="Message de test")
        assert notif.title == "Test"
        assert notif.message == "Message de test"
        assert notif.level == "info"

    def test_create_notification_with_level(self):
        notif = Notification(title="Alerte", message="Attention", level="warning")
        assert notif.level == "warning"


class TestTelegramNotifier:
    def test_not_configured(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        assert notifier.enabled is False
        assert notifier.name == "telegram"

    def test_configured(self):
        notifier = TelegramNotifier(bot_token="123:abc", chat_id="0348896324")
        assert notifier.enabled is True
        assert notifier.name == "telegram"

    @pytest.mark.asyncio
    async def test_send_not_configured(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notif = Notification(title="Test", message="Test")
        result = await notifier.send(notif)
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_not_configured(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        result = await notifier.test_connection()
        assert result is False


class TestDiscordNotifier:
    def test_not_configured(self):
        notifier = DiscordNotifier(webhook_url="")
        assert notifier.enabled is False
        assert notifier.name == "discord"

    def test_configured(self):
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test")
        assert notifier.enabled is True

    @pytest.mark.asyncio
    async def test_send_not_configured(self):
        notifier = DiscordNotifier(webhook_url="")
        notif = Notification(title="Test", message="Test")
        result = await notifier.send(notif)
        assert result is False


class TestEmailNotifier:
    def test_not_configured(self):
        notifier = EmailNotifier()
        assert notifier.enabled is False
        assert notifier.name == "email"

    def test_configured(self):
        notifier = EmailNotifier(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="test@gmail.com",
            smtp_password="password",
            recipient_email="test@gmail.com",
        )
        assert notifier.enabled is True

    @pytest.mark.asyncio
    async def test_send_not_configured(self):
        notifier = EmailNotifier()
        notif = Notification(title="Test", message="Test")
        result = await notifier.send(notif)
        assert result is False


class TestNotificationManager:
    def test_create_empty(self):
        manager = NotificationManager()
        assert len(manager.notifiers) == 0

    def test_add_notifier(self):
        manager = NotificationManager()
        telegram = TelegramNotifier(bot_token="123:abc", chat_id="0348896324")
        manager.add_notifier(telegram)
        assert "telegram" in manager.notifiers
        assert manager.get_notifier("telegram") is not None

    def test_remove_notifier(self):
        manager = NotificationManager()
        telegram = TelegramNotifier(bot_token="123:abc", chat_id="0348896324")
        manager.add_notifier(telegram)
        assert len(manager.notifiers) == 1
        manager.remove_notifier("telegram")
        assert len(manager.notifiers) == 0

    def test_from_settings(self, monkeypatch):
        from arty_trading.config import get_settings

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "+261348896324")
        get_settings.cache_clear()
        settings = get_settings()
        manager = NotificationManager.from_settings(settings.notifications)
        assert "telegram" in manager.notifiers
        assert "discord" in manager.notifiers
        assert "email" in manager.notifiers
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_send_no_notifiers(self):
        manager = NotificationManager()
        notif = Notification(title="Test", message="Test")
        results = await manager.send(notif)
        assert results == {}

    @pytest.mark.asyncio
    async def test_send_signal_notification(self):
        manager = NotificationManager()
        results = await manager.send_signal_notification({
            "symbol": "EURUSD",
            "direction": "buy",
            "entry_price": 1.0800,
            "stop_loss": 1.0780,
            "take_profit": 1.0840,
            "confidence": 0.8,
            "strategy_name": "SMC Trend",
        })
        assert isinstance(results, dict)

    def test_get_status(self):
        manager = NotificationManager()
        telegram = TelegramNotifier(bot_token="123:abc", chat_id="0348896324")
        manager.add_notifier(telegram)
        status = manager.get_status()
        assert "telegram" in status
        assert status["telegram"]["enabled"] is True


class TestNotificationRoutes:
    """Tests des routes API notifications."""

    @pytest.mark.asyncio
    async def test_notifications_status(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/notifications/status")
        assert response.status_code == 200
        data = response.json()
        assert "telegram" in data
        assert "discord" in data
        assert "email" in data

    @pytest.mark.asyncio
    async def test_send_notification(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/notifications/send", json={
                "title": "Test",
                "message": "Message de test",
                "level": "info",
            })
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total_sent" in data

    @pytest.mark.asyncio
    async def test_send_signal_notification(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/notifications/signal", json={
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
        assert "results" in data

    @pytest.mark.asyncio
    async def test_test_notifications(self):
        from httpx import ASGITransport, AsyncClient
        from arty_trading.api.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/notifications/test")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
