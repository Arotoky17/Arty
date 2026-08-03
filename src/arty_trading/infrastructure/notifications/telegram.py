"""
Notificateur Telegram - Envoie des messages via Telegram Bot API.

Configuration requise (.env) :
- TELEGRAM_BOT_TOKEN : Token du bot (obtenu via @BotFather)
- TELEGRAM_CHAT_ID : ID du chat de destination
"""

from __future__ import annotations

import urllib.request
import urllib.parse
import json

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.infrastructure.notifications.base import BaseNotifier, Notification

logger = get_logger(LogCategory.NOTIFICATION)


class TelegramNotifier(BaseNotifier):
    """Notificateur utilisant Telegram Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        super().__init__(name="telegram")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._base_url = "https://api.telegram.org/bot"

        if bot_token and chat_id:
            self._enabled = True
            logger.info("Telegram notificateur configure | chat_id=%s", chat_id)
        else:
            logger.warning("Telegram non configure - bot_token ou chat_id manquant")

    async def send(self, notification: Notification) -> bool:
        """Envoie un message Telegram."""
        if not self._enabled:
            logger.warning("Telegram non configure - notification ignoree")
            return False

        # Formater le message avec emoji selon le niveau
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅",
        }
        emoji = emoji_map.get(notification.level, "📢")

        text = f"{emoji} *{notification.title}*\n\n{notification.message}"

        try:
            url = f"{self._base_url}{self._bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }).encode()

            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())

            if result.get("ok"):
                logger.info("Telegram envoye | %s", notification.title)
                return True
            else:
                logger.error("Telegram erreur | %s", result.get("description"))
                return False
        except Exception as exc:
            logger.error("Telegram erreur envoi | %s", exc)
            return False

    async def test_connection(self) -> bool:
        """Teste la connexion au bot Telegram."""
        if not self._enabled:
            return False

        try:
            url = f"{self._base_url}{self._bot_token}/getMe"
            with urllib.request.urlopen(url, timeout=10) as resp:
                result = json.loads(resp.read())
            return result.get("ok", False)
        except Exception:
            return False
