"""
Gestionnaire de notifications - Orchestre tous les notificateurs.

Permet d'envoyer une notification a plusieurs canaux simultanement.
"""

from __future__ import annotations

from typing import Any

from arty_trading.core.enums import LogCategory
from arty_trading.infrastructure.notifications.base import BaseNotifier, Notification
from arty_trading.infrastructure.notifications.discord import DiscordNotifier
from arty_trading.infrastructure.notifications.email import EmailNotifier
from arty_trading.infrastructure.notifications.telegram import TelegramNotifier
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.NOTIFICATION)


class NotificationManager:
    """
    Gestionnaire centralise des notifications.

    Orchestre Telegram, Discord et Email.
    """

    def __init__(self) -> None:
        self._notifiers: dict[str, BaseNotifier] = {}

    def add_notifier(self, notifier: BaseNotifier) -> None:
        """Ajoute un notificateur."""
        self._notifiers[notifier.name] = notifier
        logger.info("Notificateur ajoute | %s | active=%s", notifier.name, notifier.enabled)

    def remove_notifier(self, name: str) -> None:
        """Retire un notificateur."""
        if name in self._notifiers:
            del self._notifiers[name]

    @property
    def notifiers(self) -> dict[str, BaseNotifier]:
        """Retourne les notificateurs."""
        return self._notifiers

    def get_notifier(self, name: str) -> BaseNotifier | None:
        """Retourne un notificateur par nom."""
        return self._notifiers.get(name)

    @classmethod
    def from_settings(cls, settings: Any) -> NotificationManager:
        """Cree un manager depuis les parametres de configuration."""
        manager = cls()

        # Telegram
        telegram = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
        manager.add_notifier(telegram)

        # Discord
        discord = DiscordNotifier(
            webhook_url=settings.discord_webhook_url,
        )
        manager.add_notifier(discord)

        # Email
        email = EmailNotifier(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,
            smtp_password=settings.smtp_password,
            recipient_email=settings.notification_email,
        )
        manager.add_notifier(email)

        return manager

    async def send(self, notification: Notification) -> dict[str, bool]:
        """
        Envoie une notification a tous les notificateurs actives.

        Returns:
            Dictionnaire {notifier_name: success}
        """
        results: dict[str, bool] = {}

        for name, notifier in self._notifiers.items():
            if not notifier.enabled:
                results[name] = False
                continue
            try:
                success = await notifier.send(notification)
                results[name] = success
            except Exception as exc:
                logger.error("Erreur notificateur %s | %s", name, exc)
                results[name] = False

        logger.info(
            "Notification envoyee | %s | resultats=%s",
            notification.title,
            results,
        )

        return results

    async def send_signal_notification(self, signal_data: dict) -> dict[str, bool]:
        """Envoie une notification de signal."""
        direction_emoji = "🟢" if signal_data.get("direction") == "buy" else "🔴"
        notif = Notification(
            title=f"{direction_emoji} Signal {signal_data.get('symbol', '')} {signal_data.get('direction', '')}",
            message=(
                f"Symbole: {signal_data.get('symbol', 'N/A')}\n"
                f"Direction: {signal_data.get('direction', 'N/A')}\n"
                f"Entree: {signal_data.get('entry_price', 'N/A')}\n"
                f"SL: {signal_data.get('stop_loss', 'N/A')}\n"
                f"TP: {signal_data.get('take_profit', 'N/A')}\n"
                f"Confiance: {signal_data.get('confidence', 'N/A')}\n"
                f"Strategie: {signal_data.get('strategy_name', 'N/A')}"
            ),
            level="info",
        )
        return await self.send(notif)

    async def send_trade_notification(self, trade_data: dict) -> dict[str, bool]:
        """Envoie une notification de trade ouvert/ferme."""
        action = trade_data.get("action", "opened")
        result = trade_data.get("result", "")
        level = "success" if result == "win" else "warning" if result == "loss" else "info"

        notif = Notification(
            title=f"Trade {action} | {trade_data.get('symbol', '')}",
            message=(
                f"Symbole: {trade_data.get('symbol', 'N/A')}\n"
                f"Direction: {trade_data.get('direction', 'N/A')}\n"
                f"Volume: {trade_data.get('volume', 'N/A')}\n"
                f"Entree: {trade_data.get('entry_price', 'N/A')}\n"
                f"Resultat: {result or 'En cours'}\n"
                f"Profit: {trade_data.get('profit', 'N/A')}"
            ),
            level=level,
        )
        return await self.send(notif)

    async def send_risk_alert(self, alert_data: dict) -> dict[str, bool]:
        """Envoie une alerte de risque."""
        notif = Notification(
            title=f"⚠️ Alerte de risque | {alert_data.get('type', '')}",
            message=alert_data.get("message", "Alerte de risque detectee"),
            level="warning",
        )
        return await self.send(notif)

    async def send_critical(self, title: str, message: str) -> dict[str, bool]:
        """Envoie une alerte critique (erreur, déconnexion, drawdown...)."""
        notif = Notification(title=title, message=message, level="error")
        return await self.send(notif)

    async def send_daily_report(self, summary: dict) -> dict[str, bool]:
        """Envoie un rapport journalier simple à partir du résumé de stats."""
        lines = [
            f"Analyses: {summary.get('total_analyses', 0)}",
            f"Signaux: {summary.get('total_signals', 0)}",
            f"Trades: {summary.get('total_trades', 0)}",
        ]
        wins = summary.get("winning_trades", 0)
        losses = summary.get("losing_trades", 0)
        total = wins + losses
        win_rate = summary.get("win_rate", 0.0)
        lines.append(f"Win rate: {win_rate:.1%}" if total else "Win rate: N/A")
        lines.append(f"Profit: {summary.get('total_profit', 0)}")
        notif = Notification(
            title="📊 Rapport journalier",
            message="\n".join(lines),
            level="info",
        )
        return await self.send(notif)

    async def test_all(self) -> dict[str, bool]:
        """Teste tous les notificateurs."""
        results = {}
        for name, notifier in self._notifiers.items():
            if not notifier.enabled:
                results[name] = False
                continue
            results[name] = await notifier.test_connection()
        return results

    def get_status(self) -> dict:
        """Retourne le statut de tous les notificateurs."""
        return {
            name: {
                "enabled": n.enabled,
                "name": n.name,
            }
            for name, n in self._notifiers.items()
        }
