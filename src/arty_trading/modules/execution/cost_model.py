"""Round-trip costs in account currency, with UTC hourly spreads."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostModel:
    spread_pips: float
    slippage_pips: float
    commission_per_lot_side: float
    hourly_spread_pips: dict[int, float] = field(default_factory=dict)
    session_hours_utc: dict[str, list[int]] = field(default_factory=dict)
    session_spread_pips: dict[str, float] = field(default_factory=dict)
    major_news_types: list[str] = field(default_factory=list)
    news_spread_multiplier: float = 1.0
    news_before_minutes: int = 0
    news_after_minutes: int = 0
    news_events: list[dict[str, str]] = field(default_factory=list)
    news_calendar_path: str | None = None
    sensitivity_multipliers: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.news_calendar_path:
            from arty_trading.config.operational import CONFIG_ROOT
            from arty_trading.validation.news_calendar import read_news_calendar

            path = Path(self.news_calendar_path)
            if not path.is_absolute():
                path = CONFIG_ROOT / path
            calendar = read_news_calendar(path, self.major_news_types)
            object.__setattr__(self, "news_events", [*self.news_events, *calendar])
        values = [
            self.spread_pips,
            self.slippage_pips,
            self.commission_per_lot_side,
            *self.hourly_spread_pips.values(),
            *self.session_spread_pips.values(),
        ]
        if any(not math.isfinite(x) or x < 0 for x in values):
            raise ValueError("Costs must be finite and nonnegative")
        if any(not 0 <= int(h) <= 23 for h in self.hourly_spread_pips):
            raise ValueError("Invalid UTC hour")
        hours = [hour for group in self.session_hours_utc.values() for hour in group]
        if len(hours) != len(set(hours)) or any(not 0 <= h <= 23 for h in hours):
            raise ValueError("Sessions must have unique UTC hours")
        if set(self.session_spread_pips) != set(self.session_hours_utc):
            raise ValueError("Each configured session requires a spread")
        if not math.isfinite(self.news_spread_multiplier) or self.news_spread_multiplier < 1:
            raise ValueError("News spread multiplier must be at least 1")
        if min(self.news_before_minutes, self.news_after_minutes) < 0:
            raise ValueError("Negative news window")
        if any(not math.isfinite(x) or x < 1 for x in self.sensitivity_multipliers):
            raise ValueError("Invalid sensitivity multiplier")
        for event in self.news_events:
            time = datetime.fromisoformat(event["time_utc"])
            if time.tzinfo is None or time.utcoffset() != timedelta(0):
                raise ValueError("News calendar requires explicit UTC timestamps")
            if event["type"] not in self.major_news_types:
                raise ValueError("Unconfigured major news type")

    def spread_at(self, time: datetime | None) -> float:
        if time is None:
            return self.spread_pips
        time = time.astimezone(UTC)
        spread = self.spread_pips
        for session, hours in self.session_hours_utc.items():
            if time.hour in hours:
                spread = self.session_spread_pips[session]
                break
        spread = self.hourly_spread_pips.get(time.hour, spread)
        for event in self.news_events:
            scheduled = datetime.fromisoformat(event["time_utc"])
            if (
                scheduled - timedelta(minutes=self.news_before_minutes)
                <= time
                <= (scheduled + timedelta(minutes=self.news_after_minutes))
            ):
                return spread * self.news_spread_multiplier
        return spread

    def assumptions(self) -> dict[str, Any]:
        return {
            "sessions_timezone": "UTC fixed hours; no automatic DST conversion",
            "news_calendar_status": "provided" if self.news_events else "missing",
            "news_events": len(self.news_events),
            "news_calendar_path": self.news_calendar_path,
            "news_window_minutes": [self.news_before_minutes, self.news_after_minutes],
            "news_spread_multiplier": self.news_spread_multiplier,
            "spread_convention": "half at entry plus half at exit; hourly overrides session",
        }

    def charge(
        self,
        volume: float,
        pip_value: float,
        entry_time: datetime | None,
        exit_time: datetime | None,
    ) -> float:
        spread = (self.spread_at(entry_time) + self.spread_at(exit_time or entry_time)) / 2
        return volume * (
            (spread + 2 * self.slippage_pips) * pip_value + 2 * self.commission_per_lot_side
        )
