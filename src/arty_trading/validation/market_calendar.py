"""XAUUSD market clock: timezone-aware schedule, immutable raw-data annotation."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from arty_trading.config.operational import load_config


def good_friday(year: int) -> date:
    """Gregorian computus (calendar arithmetic, not detection thresholds)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    value = h + weekday_offset - 7 * m + 114
    return date(year, value // 31, value % 31 + 1) - timedelta(days=2)


class MarketCalendar:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config("market_calendar.yaml")
        self.zone = ZoneInfo(self.config["timezone"])
        self.pause_start = time.fromisoformat(self.config["daily_pause_start"])
        self.pause_end = time.fromisoformat(self.config["daily_pause_end"])
        self.close_time = time.fromisoformat(self.config["weekend_close_time"])
        self.open_time = time.fromisoformat(self.config["weekend_open_time"])

    def state(self, instant: datetime) -> dict[str, Any]:
        if instant.tzinfo is None:
            raise ValueError("Market calendar requires timezone-aware timestamp")
        utc = instant.astimezone(UTC)
        local = utc.astimezone(self.zone)
        holidays = self.config.get("holiday_exclusions", {})
        if local.strftime("%m-%d") in holidays.get("recurring_month_days", []) or (
            holidays.get("good_friday", False) and local.date() == good_friday(local.year)
        ):
            return {"non_tradable": True, "entry_blocked": True, "reason": "conservative_holiday"}
        for override in self.config["holiday_overrides"]:
            if (
                datetime.fromisoformat(override["start_utc"])
                <= utc
                < datetime.fromisoformat(override["end_utc"])
            ):
                return {"non_tradable": True, "entry_blocked": True, "reason": "holiday_override"}
        day, clock = local.weekday(), local.time()
        closed_day = self.config["weekend_close_weekday"]
        open_day = self.config["weekend_open_weekday"]
        weekend = (
            closed_day < day < open_day
            or (day == closed_day and clock >= self.close_time)
            or (day == open_day and clock < self.open_time)
        )
        pause = self.pause_start <= clock < self.pause_end
        if weekend or pause:
            return {
                "non_tradable": True,
                "entry_blocked": True,
                "reason": "weekend" if weekend else "daily_pause",
            }
        reopened = datetime.combine(local.date(), self.pause_end, self.zone)
        elapsed = (utc - reopened.astimezone(UTC)).total_seconds() / 60
        blocked = 0 <= elapsed < self.config["post_reopen_minutes"]
        return {
            "non_tradable": False,
            "entry_blocked": blocked,
            "reason": ("weekend_reopen" if day == open_day else "daily_reopen")
            if blocked
            else "open",
        }

    def day_is_closed(self, day: date) -> bool:
        """True when no minute of this UTC day is tradable.

        Hourly probing is exact for the configured calendar: open windows last
        hours while the daily pause lasts one hour, so no open window is missed.
        Used to decide that a provider file is not required for this day.
        """
        probes = (
            datetime.combine(day, time.min, tzinfo=UTC) + timedelta(minutes=minute)
            for minute in range(0, 1440, 60)
        )
        return all(self.state(moment)["non_tradable"] for moment in probes)

    def annotate(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise ValueError("Market annotation requires timezone-aware DatetimeIndex")
        result = frame.copy()
        # Work by timezone-converted local clock, not the host timezone.
        local = frame.index.tz_convert(self.zone)
        minutes = local.hour * 60 + local.minute

        def clock(t: time) -> int:
            return t.hour * 60 + t.minute

        day = local.dayofweek
        weekend = (day > self.config["weekend_close_weekday"]) & (
            day < self.config["weekend_open_weekday"]
        )
        weekend |= (day == self.config["weekend_close_weekday"]) & (
            minutes >= clock(self.close_time)
        )
        weekend |= (day == self.config["weekend_open_weekday"]) & (minutes < clock(self.open_time))
        pause = (minutes >= clock(self.pause_start)) & (minutes < clock(self.pause_end))
        result["non_tradable"] = weekend | pause
        result["entry_blocked"] = result.non_tradable | (
            (minutes >= clock(self.pause_end))
            & (minutes < clock(self.pause_end) + self.config["post_reopen_minutes"])
        )
        holidays = self.config.get("holiday_exclusions", {})
        holiday_mask = local.strftime("%m-%d").isin(holidays.get("recurring_month_days", []))
        if holidays.get("good_friday", False):
            for year in set(local.year):
                holiday = good_friday(year)
                holiday_mask |= (
                    (local.year == year)
                    & (local.month == holiday.month)
                    & (local.day == holiday.day)
                )
        result.loc[holiday_mask, ["non_tradable", "entry_blocked"]] = True
        for override in self.config["holiday_overrides"]:
            mask = (result.index >= pd.Timestamp(override["start_utc"])) & (
                result.index < pd.Timestamp(override["end_utc"])
            )
            result.loc[mask, ["non_tradable", "entry_blocked"]] = True
        return result

    def session_day(self, instant: datetime, convention: str, anchor: str) -> datetime:
        if convention == "utc":
            return instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if convention != "new_york_17":
            raise ValueError("Unknown D1 convention")
        local = instant.astimezone(self.zone)
        opened = datetime.combine(local.date(), time.fromisoformat(anchor), self.zone)
        if local < opened:
            opened -= timedelta(days=1)
        return opened.astimezone(UTC)


@lru_cache(maxsize=16)
def _configured_calendar(path: Path, modified: int) -> MarketCalendar:
    return MarketCalendar(load_config(path.name))


@lru_cache(maxsize=65536)
def _closed_at(instant: datetime, path: Path, modified: int) -> bool:
    # Bounded memoization of a pure calendar decision; file revision invalidates it.
    return bool(_configured_calendar(path, modified).state(instant)["non_tradable"])


def _calendar_revision() -> tuple[Path, int]:
    from arty_trading.config.operational import CONFIG_ROOT

    path = CONFIG_ROOT / "market_calendar.yaml"
    return path, path.stat().st_mtime_ns


def entry_allowed(candle: Any) -> bool:
    if candle.non_tradable or candle.entry_blocked:
        return False
    if candle.symbol == "XAUUSD" and candle.time.tzinfo is not None:
        path, modified = _calendar_revision()
        return not _configured_calendar(path, modified).state(candle.time)["entry_blocked"]
    return True


def tradable_candles(candles: list[Any]) -> list[Any]:
    """Filter annotations; infer closures only for intraday execution bars.

    H4/D1 bars may include open observations despite starting in a pause.
    Their annotations must come from aggregation of the underlying open M1.
    """
    path, modified = _calendar_revision()
    result = []
    for candle in candles:
        closed = candle.non_tradable
        if (
            candle.symbol == "XAUUSD"
            and candle.time.tzinfo is not None
            and candle.timeframe.value in {"M1", "M5", "M15", "M30", "H1"}
        ):
            closed |= _closed_at(candle.time, path, modified)
        if not closed:
            result.append(candle)
    return result
