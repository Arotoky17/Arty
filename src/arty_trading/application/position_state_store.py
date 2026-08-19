"""Persistance durable de l'état de gestion par position (reprise après redémarrage).

Conserve sur disque, par ticket MT5 : SL initial, R initial, entrée, volume
initial, paliers de partial exécutés, niveau de profit lock atteint et statut
runner. Un redémarrage ne doit jamais provoquer : double partial, reset du R,
ou recalcul d'un risque initial erroné.

Le SL MT5 courant n'est JAMAIS considéré comme le SL initial : il sert
uniquement de fallback dégradé (avec WARNING explicite) quand aucun état
persisté n'existe.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("arty_trading.position_state")

_STATE_VERSION = 1


class PositionStateStore:
    """Stocke l'état de gestion des positions ouvertes en fichiers JSON."""

    def __init__(self, directory: str = "data/position_states") -> None:
        self._directory = Path(directory)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("État position non persisté (répertoire inaccessible) | %s", exc)

    def _path(self, key: str | int) -> Path:
        return self._directory / f"{key}.json"

    def save(self, key: str | int, data: dict[str, Any]) -> None:
        """Écrit l'état d'une position (atomique par fichier temporaire)."""
        payload = {"version": _STATE_VERSION, "state": data}
        try:
            target = self._path(key)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            logger.warning("Sauvegarde état position échouée | %s | %s", key, exc)

    def load(self, key: str | int) -> dict[str, Any] | None:
        """Retourne l'état persisté d'une position, ou None."""
        try:
            target = self._path(key)
            if not target.exists():
                return None
            payload = json.loads(target.read_text(encoding="utf-8"))
            return payload.get("state")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Lecture état position échouée | %s | %s", key, exc)
            return None

    def delete(self, key: str | int) -> None:
        """Supprime l'état d'une position clôturée."""
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Suppression état position échouée | %s | %s", key, exc)
