"""
Module Notifications - Telegram, Discord, Email.

Envoie des notifications pour :
- Signaux generes
- Trades ouverts/fermes
- Alertes de risque
- Resume quotidien
"""

from arty_trading.infrastructure.notifications.manager import NotificationManager
from arty_trading.infrastructure.notifications.telegram import TelegramNotifier
from arty_trading.infrastructure.notifications.discord import DiscordNotifier
from arty_trading.infrastructure.notifications.email import EmailNotifier

__all__ = [
    "NotificationManager",
    "TelegramNotifier",
    "DiscordNotifier",
    "EmailNotifier",
]
