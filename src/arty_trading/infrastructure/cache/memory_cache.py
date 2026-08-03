"""
Cache en mémoire avec TTL (Time-To-Live) pour les données de marché.

Fournit un cache simple et thread-safe pour éviter les appels redondants
à l'API MetaTrader 5 (récupération de bougies, spread, etc.).
"""

from __future__ import annotations

import time
from typing import Any


class CacheEntry:
    """Entrée de cache avec expiration."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        """Vérifie si l'entrée a expiré."""
        return time.monotonic() >= self.expires_at


class InMemoryCache:
    """
    Cache en mémoire avec TTL configurable par clé.

    Thread-safe via verrou asyncio (utilisé dans un contexte async).
    Nettoyage automatique des entrées expirées lors de l'accès.

    Attributes:
        _entries: Dictionnaire des entrées de cache
        _default_ttl: TTL par défaut en secondes
    """

    def __init__(self, default_ttl: float = 60.0) -> None:
        """
        Initialise le cache.

        Args:
            default_ttl: Durée de vie par défaut en secondes (60s par défaut)
        """
        self._entries: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """
        Récupère une valeur du cache.

        Args:
            key: Clé de cache

        Returns:
            La valeur mise en cache, ou None si absente/expirée
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.is_expired:
            del self._entries[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """
        Stocke une valeur dans le cache.

        Args:
            key: Clé de cache
            value: Valeur à stocker
            ttl: TTL en secondes (utilise default_ttl si None)
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._entries[key] = CacheEntry(value, effective_ttl)

    def delete(self, key: str) -> None:
        """Supprime une entrée du cache."""
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Vide entièrement le cache."""
        self._entries.clear()

    def cleanup(self) -> int:
        """
        Nettoie les entrées expirées.

        Returns:
            Nombre d'entrées supprimées
        """
        expired_keys = [k for k, v in self._entries.items() if v.is_expired]
        for key in expired_keys:
            del self._entries[key]
        return len(expired_keys)

    def __len__(self) -> int:
        """Nombre d'entrées dans le cache (incluant potentiellement les expirées)."""
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        """Vérifie si une clé existe et n'est pas expirée."""
        entry = self._entries.get(key)
        if entry is None:
            return False
        if entry.is_expired:
            del self._entries[key]
            return False
        return True