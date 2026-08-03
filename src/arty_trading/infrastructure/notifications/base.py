"""
Interface de base pour les notificateurs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class Notification:
    """Notification a envoyer."""

    title: str
    message: str
    level: str = "info"  # info, warning, error, success
    data: dict[str, Any] = None


class BaseNotifier(ABC):
    """Interface abstraite pour les notificateurs."""

    def __init__(self, name: str = "base") -> None:
        self._name = name
        self._enabled = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @abstractmethod
    async def send(self, notification: Notification) -> bool:
        """
        Envoie une notification.

        Returns:
            True si envoye avec succes, False sinon
        """
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """Teste la connexion au service."""
        ...
