"""
Notificateur Email - Envoie des emails via SMTP.

Configuration requise (.env) :
- SMTP_HOST : Serveur SMTP
- SMTP_PORT : Port (587 pour TLS)
- SMTP_USER : Utilisateur
- SMTP_PASSWORD : Mot de passe
- NOTIFICATION_EMAIL : Email de destination
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.infrastructure.notifications.base import BaseNotifier, Notification

logger = get_logger(LogCategory.NOTIFICATION)


class EmailNotifier(BaseNotifier):
    """Notificateur utilisant SMTP."""

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        recipient_email: str = "",
    ) -> None:
        super().__init__(name="email")
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._recipient_email = recipient_email

        if smtp_host and smtp_user and smtp_password and recipient_email:
            self._enabled = True
            logger.info("Email notificateur configure | %s", recipient_email)
        else:
            logger.warning("Email non configure - parametres SMTP manquants")

    async def send(self, notification: Notification) -> bool:
        """Envoie un email."""
        if not self._enabled:
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = self._recipient_email
            msg["Subject"] = f"[Arty] {notification.title}"

            body = f"{notification.message}"
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_password)
                server.send_message(msg)

            logger.info("Email envoye | %s | %s", self._recipient_email, notification.title)
            return True
        except Exception as exc:
            logger.error("Email erreur envoi | %s", exc)
            return False

    async def test_connection(self) -> bool:
        """Teste la connexion SMTP."""
        if not self._enabled:
            return False
        test_notif = Notification(title="Test", message="Test de connexion Arty", level="info")
        return await self.send(test_notif)
