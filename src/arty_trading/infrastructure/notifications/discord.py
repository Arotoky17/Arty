"""
Notificateur Discord - Envoie des messages via Discord Webhook.

Configuration requise (.env) :
- DISCORD_WEBHOOK_URL : URL du webhook Discord
"""

from __future__ import annotations

import urllib.request
import json

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.infrastructure.notifications.base import BaseNotifier, Notification

logger = get_logger(LogCategory.NOTIFICATION)


class DiscordNotifier(BaseNotifier):
    """Notificateur utilisant Discord Webhook."""

    def __init__(self, webhook_url: str = "") -> None:
        super().__init__(name="discord")
        self._webhook_url = webhook_url

        if webhook_url:
            self._enabled = True
            logger.info("Discord notificateur configure")
        else:
            logger.warning("Discord non configure - webhook_url manquant")

    async def send(self, notification: Notification) -> bool:
        """Envoie un message Discord."""
        if not self._enabled:
            return False

        color_map = {
            "info": 3447003,     # bleu
            "warning": 16776960,  # jaune
            "error": 15158332,   # rouge
            "success": 3066993,  # vert
        }
        color = color_map.get(notification.level, 3447003)

        payload = {
            "embeds": [{
                "title": notification.title,
                "description": notification.message,
                "color": color,
            }]
        }

        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 204
        except Exception as exc:
            logger.error("Discord erreur envoi | %s", exc)
            return False

    async def test_connection(self) -> bool:
        """Teste la connexion au webhook Discord."""
        if not self._enabled:
            return False
        test_notif = Notification(title="Test", message="Test de connexion Arty", level="info")
        return await self.send(test_notif)
