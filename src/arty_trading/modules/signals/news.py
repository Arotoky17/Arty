"""Calendrier économique local et filtre des événements à impact élevé."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arty_trading.config.settings import NewsSettings


@dataclass(frozen=True)
class EconomicEvent:
    """Événement normalisé issu d'un fournisseur ou d'un fichier calendrier."""

    time: datetime
    title: str
    currencies: tuple[str, ...]
    impact: str = "high"


class EconomicCalendar:
    """Charge un calendrier JSON et détermine les fenêtres d'interdiction."""

    def __init__(self, settings: NewsSettings) -> None:
        self._settings = settings

    def is_blocked(self, symbol: str, now: datetime | None = None) -> bool:
        """True pendant la fenêtre d'un événement rouge pertinent au symbole."""
        if not self._settings.enabled:
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        before = timedelta(minutes=self._settings.block_minutes_before)
        after = timedelta(minutes=self._settings.block_minutes_after)
        currencies = {symbol[:3].upper(), symbol[3:6].upper()}
        return any(
            event.impact.lower() == "high"
            and currencies.intersection(event.currencies)
            and event.time - before <= current <= event.time + after
            for event in self._events()
        )

    def _events(self) -> list[EconomicEvent]:
        path = Path(self._settings.calendar_file)
        if not path.is_file():
            return []
        try:
            raw_events = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        events: list[EconomicEvent] = []
        for item in raw_events:
            try:
                event_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
                events.append(
                    EconomicEvent(
                        time=event_time.astimezone(UTC),
                        title=str(item.get("title", "")),
                        currencies=tuple(currency.upper() for currency in item.get("currencies", [])),
                        impact=str(item.get("impact", "high")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return events
