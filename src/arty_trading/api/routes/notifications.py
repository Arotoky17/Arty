"""
Router Notifications - Telegram, Discord, Email.

Endpoints pour envoyer des notifications et tester les canaux.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = get_logger()


class SendNotificationRequest(BaseModel):
    """Requete d'envoi de notification."""

    title: str
    message: str
    level: str = "info"


class SignalNotificationRequest(BaseModel):
    """Requete de notification de signal."""

    symbol: str
    direction: str
    entry_price: float = 0
    stop_loss: float = 0
    take_profit: float = 0
    confidence: float = 0
    strategy_name: str = ""


@router.get("/status")
async def notifications_status(request: Request) -> dict:
    """Retourne le statut des notificateurs."""
    manager = request.app.state.notification_manager
    return manager.get_status()


@router.post("/send")
async def send_notification(req: SendNotificationRequest, request: Request) -> dict:
    """Envoie une notification a tous les canaux actifs."""
    from arty_trading.infrastructure.notifications.base import Notification
    manager = request.app.state.notification_manager
    notif = Notification(title=req.title, message=req.message, level=req.level)
    results = await manager.send(notif)
    return {"results": results, "total_sent": sum(1 for v in results.values() if v)}


@router.post("/signal")
async def send_signal_notification(req: SignalNotificationRequest, request: Request) -> dict:
    """Envoie une notification de signal de trading."""
    manager = request.app.state.notification_manager
    results = await manager.send_signal_notification(req.model_dump())
    return {"results": results}


@router.post("/test")
async def test_notifications(request: Request) -> dict:
    """Teste tous les notificateurs."""
    manager = request.app.state.notification_manager
    results = await manager.test_all()
    return {"results": results}
