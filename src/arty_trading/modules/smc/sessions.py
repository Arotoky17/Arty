"""
Détecteur de sessions de trading ICT/SMC.

Concepts ICT
------------
ICT distingue plusieurs sessions et "Kill Zones" qui ont des propriétés
statistiques particulières :

- **Asian Session** : 17:00 – 07:00 UTC (range souvent étroit, couvre minuit)
- **London Session** : 07:00 – 12:00 UTC (volatilité, cassures)
- **New York Session** : 12:00 – 17:00 UTC (liquidité, continuation)
- **London/NY Overlap** : 12:00 – 17:00 UTC (chevauchement)
- **London Kill Zone** : 07:00 – 10:00 UTC (zone de cassure privilégiée)
- **New York Kill Zone** : 12:00 – 15:00 UTC (zone de cassure privilégiée)

Le détecteur associe chaque bougie à sa session et identifie les Kill Zones.
Aucune fonction ici n'ouvre de trade : le détecteur retourne uniquement des
informations de marché.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept, TradingSession
from arty_trading.modules.smc.base import BaseDetector, SMCDetection


@dataclass(frozen=True)
class SessionWindow:
    """Fenêtre horaire d'une session (en UTC)."""

    name: str
    session: TradingSession
    start: time
    end: time
    is_kill_zone: bool = False


# Définition des sessions ICT (heures UTC)
# Les sessions couvrent maintenant l'ensemble des 24h sans trou horaire.
# Asia couvre le passage de minuit : 17:00 -> 07:00 UTC.
_DEFAULT_SESSIONS: tuple[SessionWindow, ...] = (
    SessionWindow("asian", TradingSession.ASIA, time(17, 0), time(7, 0)),
    SessionWindow("london", TradingSession.LONDON, time(7, 0), time(12, 0)),
    SessionWindow(
        "london_kill_zone",
        TradingSession.LONDON,
        time(7, 0),
        time(10, 0),
        is_kill_zone=True,
    ),
    SessionWindow("new_york", TradingSession.NEW_YORK, time(12, 0), time(17, 0)),
    SessionWindow(
        "new_york_kill_zone",
        TradingSession.NEW_YORK,
        time(12, 0),
        time(15, 0),
        is_kill_zone=True,
    ),
    SessionWindow(
        "london_ny_overlap",
        TradingSession.OVERLAP_LONDON_NY,
        time(12, 0),
        time(17, 0),
    ),
)


def _to_utc(dt: datetime) -> datetime:
    """Convertit un datetime en UTC s'il est naïf ou conscient."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _time_in_window(t: time, start: time, end: time) -> bool:
    """Vérifie si un time est dans [start, end). Gère le passage à minuit."""
    if start <= end:
        return start <= t < end
    # Passage à minuit (ex: 22:00 -> 02:00)
    return t >= start or t < end


class SessionDetector(BaseDetector):
    """
    Détecteur de sessions de trading ICT/SMC.

    Associe chaque bougie à sa session (Asia, London, New York, overlap) et
    identifie les Kill Zones (London KZ, New York KZ).
    """

    def __init__(
        self,
        enabled: bool = True,
        sessions: tuple[SessionWindow, ...] | None = None,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            sessions: Liste personnalisée de sessions. Utilise les sessions ICT
                par défaut si None.
        """
        super().__init__(enabled=enabled)
        self._sessions = sessions if sessions is not None else _DEFAULT_SESSIONS

    @property
    def name(self) -> str:
        return "sessions"

    @property
    def sessions(self) -> tuple[SessionWindow, ...]:
        """Retourne la liste des sessions configurées."""
        return self._sessions

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les sessions et Kill Zones pour chaque bougie.

        Pour chaque bougie, on émet une détection SESSION (et KILL_ZONE si la
        bougie tombe dans une Kill Zone). Les doublons de session (ex: London
        et London KZ) sont émis séparément pour garder l'information complète.

        Args:
            candles: Liste des bougies (du plus ancien au plus récent)

        Returns:
            Liste des détections de sessions
        """
        from arty_trading.validation.market_calendar import tradable_candles

        candles = tradable_candles(candles)
        if not self._enabled or not candles:
            return []

        detections: list[SMCDetection] = []

        for i, candle in enumerate(candles):
            utc_time = _to_utc(candle.time)
            candle_time = utc_time.time()

            matched_any = False
            matched_kill_zone = False

            for sw in self._sessions:
                if not _time_in_window(candle_time, sw.start, sw.end):
                    continue

                matched_any = True

                if sw.is_kill_zone:
                    matched_kill_zone = True
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.KILL_ZONE,
                            direction="neutral",
                            price=candle.close,
                            index=i,
                            details={
                                "session": sw.session.value,
                                "kill_zone_name": sw.name,
                                "start": sw.start.isoformat(),
                                "end": sw.end.isoformat(),
                                "candle_time": utc_time.isoformat(),
                            },
                        )
                    )

            if matched_any:
                # Déterminer la session principale (non kill zone) la plus précise
                primary = self._primary_session(candle_time)
                detections.append(
                    SMCDetection(
                        concept=SMCConcept.SESSION,
                        direction="neutral",
                        price=candle.close,
                        index=i,
                        details={
                            "session": primary.session.value,
                            "session_name": primary.name,
                            "start": primary.start.isoformat(),
                            "end": primary.end.isoformat(),
                            "candle_time": utc_time.isoformat(),
                            "in_kill_zone": matched_kill_zone,
                        },
                    )
                )

        return detections

    def _primary_session(self, t: time) -> SessionWindow:
        """
        Retourne la session principale (non kill zone) pour une heure donnée.

        Args:
            t: Heure UTC

        Returns:
            La SessionWindow principale correspondante
        """
        for sw in self._sessions:
            if sw.is_kill_zone:
                continue
            if _time_in_window(t, sw.start, sw.end):
                return sw
        # Fallback : Asian par défaut
        return self._sessions[0]

    def get_session_for_time(self, dt: datetime) -> SessionWindow | None:
        """
        Retourne la session principale pour un datetime donné.

        Args:
            dt: Datetime à vérifier

        Returns:
            La SessionWindow correspondante ou None
        """
        utc_time = _to_utc(dt)
        t = utc_time.time()
        for sw in self._sessions:
            if sw.is_kill_zone:
                continue
            if _time_in_window(t, sw.start, sw.end):
                return sw
        return None

    def is_kill_zone(self, dt: datetime) -> bool:
        """
        Vérifie si un datetime tombe dans une Kill Zone.

        Args:
            dt: Datetime à vérifier

        Returns:
            True si le datetime est dans une Kill Zone
        """
        utc_time = _to_utc(dt)
        t = utc_time.time()
        for sw in self._sessions:
            if sw.is_kill_zone and _time_in_window(t, sw.start, sw.end):
                return True
        return False