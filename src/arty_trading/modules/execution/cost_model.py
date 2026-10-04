"""Round-trip costs in account currency, with UTC hourly spreads."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostModel:
    spread_pips: float = 0.0
    slippage_pips: float = 0.0  # Legacy explicit-injection compatibility only.
    commission_per_lot_side: float = 0.0
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
    spread_mode: str = "configured"
    spread_calibration_path: str | None = None
    broker_spread_multiplier: float = 1.0
    fragility_multiplier: float = 1.5
    fragility_expectancy_r: float = 0.0
    calibration_quantiles: dict[str, float] = field(default_factory=dict)
    pip_usd: float = 0.01
    # Limit-ENTRY slippage (USD). Zero once the fill crossing already prices the
    # same adverse excursion, to avoid double counting.
    entry_limit_slippage_usd: float | None = None
    # Limit (take-profit) EXIT slippage in USD.
    limit_slippage_usd: float | None = None
    market_stop_slippage_usd: float | None = None
    slippage_status: str = "legacy_or_explicit"
    spread_unit: str = "pips"
    annual_calibration_paths: dict[int, str] = field(default_factory=dict)
    annual_recalibration_policy: str = "previous_calendar_year_quotes_only_frozen_before_run"
    normalized_hours: dict[int, float] = field(default_factory=dict)
    annual_normalized_hours: dict[int, dict[int, float]] = field(default_factory=dict)
    development_end_year: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.pip_usd) or self.pip_usd <= 0:
            raise ValueError("Pip USD unit must be finite and positive")
        if self.spread_unit not in {"pips", "fraction_price"}:
            raise ValueError("Unknown spread unit")
        if self.spread_mode not in {"configured", "calibrated_p75"}:
            raise ValueError("Unknown spread calibration mode")
        if not math.isfinite(self.broker_spread_multiplier) or self.broker_spread_multiplier <= 0:
            raise ValueError("Broker spread multiplier must be positive")
        if self.spread_mode == "calibrated_p75":
            from arty_trading.config.operational import load_config

            if not self.spread_calibration_path:
                raise ValueError("Calibrated spread requires a calibration file")
            calibrated = load_config(self.spread_calibration_path)
            if calibrated.get("calibration_end_exclusive"):
                object.__setattr__(self, "development_end_year", datetime.fromisoformat(
                    calibrated["calibration_end_exclusive"]
                ).year)
            if calibrated["partition"] != "development":
                raise ValueError("Spread calibration must use development data only")
            object.__setattr__(
                self,
                "hourly_spread_pips",
                {
                    int(hour): float(row["p75_pips"]) * self.broker_spread_multiplier
                    for hour, row in calibrated["hours"].items()
                },
            )
            object.__setattr__(self, "normalized_hours", {
                int(hour): float(row["p75_fraction_price"]) * self.broker_spread_multiplier
                for hour, row in calibrated["hours"].items() if "p75_fraction_price" in row
            })
            annual = {}
            for year, calibration_path in self.annual_calibration_paths.items():
                sample = load_config(calibration_path)
                if not sample.get("coverage_complete") or sample.get("pnl_inspected") is not False:
                    raise ValueError("Annual calibration requires complete quotes-only coverage")
                if datetime.fromisoformat(sample["calibration_end_exclusive"]) > datetime(
                    int(year), 1, 1, tzinfo=UTC
                ):
                    raise ValueError("Annual calibration cannot use future-year quotes")
                annual[int(year)] = {
                    int(hour): float(row["p75_fraction_price"]) * self.broker_spread_multiplier
                    for hour, row in sample["hours"].items()
                }
            object.__setattr__(self, "annual_normalized_hours", annual)
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
            *self.normalized_hours.values(),
            *[value for group in self.annual_normalized_hours.values() for value in group.values()],
            *[
                x
                for x in (
                    self.entry_limit_slippage_usd,
                    self.limit_slippage_usd,
                    self.market_stop_slippage_usd,
                )
                if x is not None
            ],
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

    def spread_at(self, time: datetime | None, price: float | None = None) -> float:
        if time is None:
            return self.spread_pips
        time = time.astimezone(UTC)
        spread = self.spread_pips
        for session, hours in self.session_hours_utc.items():
            if time.hour in hours:
                spread = self.session_spread_pips[session]
                break
        spread = self.hourly_spread_pips.get(time.hour, spread)
        if self.spread_unit == "fraction_price" and price is not None:
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Normalized spread requires a finite positive price")
            if (
                self.development_end_year is not None
                and time.year > self.development_end_year
                and time.year not in self.annual_normalized_hours
            ):
                raise ValueError("Missing annual quotes-only spread recalibration")
            normalized = self.annual_normalized_hours.get(time.year, self.normalized_hours)
            if time.hour not in normalized:
                raise ValueError("Normalized spread calibration missing for UTC hour")
            spread = normalized[time.hour] * price / self.pip_usd
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
            "spread_mode": self.spread_mode,
            "spread_calibration_path": self.spread_calibration_path,
            "broker_spread_multiplier": self.broker_spread_multiplier,
            "pip_unit": "XAUUSD 1 pip = 0.01 USD per ounce",
            "entry_limit_slippage_usd": self.entry_limit_slippage_usd,
            "limit_slippage_usd": self.limit_slippage_usd,
            "market_stop_slippage_usd": self.market_stop_slippage_usd,
            "slippage_status": self.slippage_status,
            "spread_unit": self.spread_unit,
            "annual_recalibration_policy": self.annual_recalibration_policy,
            "annual_calibration_paths": self.annual_calibration_paths,
        }

    def slippage_price(self, order_kind: str, symbol: str = "XAUUSD") -> float:
        if order_kind not in {"limit", "market", "stop"}:
            raise ValueError("Unknown execution order kind")
        value = self.limit_slippage_usd if order_kind == "limit" else self.market_stop_slippage_usd
        if value is not None:
            return value
        from arty_trading.utils.helpers import get_pip_size

        return self.slippage_pips * float(get_pip_size(symbol))

    def entry_slippage_price(self, order_kind: str, symbol: str = "XAUUSD") -> float:
        """Entry-leg slippage. A limit entry uses its dedicated USD value (0.0)."""
        if order_kind not in {"limit", "market", "stop"}:
            raise ValueError("Unknown execution order kind")
        if order_kind == "limit":
            value = self.entry_limit_slippage_usd
            if value is None:
                value = self.limit_slippage_usd
        else:
            value = self.market_stop_slippage_usd
        if value is not None:
            return value
        from arty_trading.utils.helpers import get_pip_size

        return self.slippage_pips * float(get_pip_size(symbol))

    def charge(
        self,
        volume: float,
        pip_value: float,
        entry_time: datetime | None,
        exit_time: datetime | None,
        *,
        entry_kind: str = "limit",
        exit_kind: str = "market",
        entry_price: float | None = None,
        exit_price: float | None = None,
    ) -> float:
        spread = (
            self.spread_at(entry_time, entry_price)
            + self.spread_at(exit_time or entry_time, exit_price or entry_price)
        ) / 2
        if (
            self.limit_slippage_usd is None
            and self.market_stop_slippage_usd is None
            and self.entry_limit_slippage_usd is None
        ):
            slippage = 2 * self.slippage_pips
        else:
            slippage = (
                self.entry_slippage_price(entry_kind) + self.slippage_price(exit_kind)
            ) / self.pip_usd
        return volume * ((spread + slippage) * pip_value + 2 * self.commission_per_lot_side)
