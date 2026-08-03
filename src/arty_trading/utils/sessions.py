"""
Utilitaires pour les sessions de trading ICT/SMC.
Gestion des sessions Asie, Londres, New York et Kill Zones.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

import pytz

from arty_trading.core.enums import TradingSession

if TYPE_CHECKING:
    from arty_trading.config.settings import SessionSettings

UTC = pytz.UTC


def _parse_time(time_str: str) -> time:
    """Parse une heure HH:MM en objet time."""
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]))


def _time_in_range(current: time, start: time, end: time) -> bool:
    """Vérifie si current est dans [start, end), gère le passage minuit."""
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def is_session_active(
    session: TradingSession,
    dt: datetime | None = None,
    settings: SessionSettings | None = None,
) -> bool:
    """
    Vérifie si une session de trading est active.

    Args:
        session: Session à vérifier
        dt: Datetime UTC (maintenant si None)
        settings: Horaires des sessions (valeurs par défaut si None)
    """
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = UTC.localize(dt)
    else:
        dt = dt.astimezone(UTC)

    current_time = dt.time()

    # Horaires par défaut ICT (UTC)
    defaults = {
        TradingSession.ASIA: ("00:00", "09:00"),
        TradingSession.LONDON: ("07:00", "16:00"),
        TradingSession.NEW_YORK: ("12:00", "21:00"),
        TradingSession.OVERLAP_LONDON_NY: ("12:00", "16:00"),
    }

    if settings:
        mapping = {
            TradingSession.ASIA: (settings.asia_start, settings.asia_end),
            TradingSession.LONDON: (settings.london_start, settings.london_end),
            TradingSession.NEW_YORK: (settings.newyork_start, settings.newyork_end),
            TradingSession.OVERLAP_LONDON_NY: ("12:00", "16:00"),
        }
        start_str, end_str = mapping[session]
    else:
        start_str, end_str = defaults[session]

    return _time_in_range(current_time, _parse_time(start_str), _parse_time(end_str))


def get_active_session(dt: datetime | None = None) -> TradingSession | None:
    """Retourne la session active ou None (hors session)."""
    for session in [
        TradingSession.OVERLAP_LONDON_NY,
        TradingSession.LONDON,
        TradingSession.NEW_YORK,
        TradingSession.ASIA,
    ]:
        if is_session_active(session, dt):
            return session
    return None


def is_kill_zone(dt: datetime | None = None) -> bool:
    """
    Kill Zones ICT : fenêtres de haute probabilité.
    - London Open Kill Zone : 07:00-10:00 UTC
    - New York Open Kill Zone : 12:00-15:00 UTC
    - London Close Kill Zone : 15:00-17:00 UTC
    """
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = UTC.localize(dt)
    else:
        dt = dt.astimezone(UTC)

    current = dt.time()
    kill_zones = [
        (time(7, 0), time(10, 0)),   # London Open
        (time(12, 0), time(15, 0)),  # NY Open
        (time(15, 0), time(17, 0)),  # London Close
    ]
    return any(_time_in_range(current, start, end) for start, end in kill_zones)
