"""Immutable quote data, separate gap exclusions and segmented derived views."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from arty_trading.validation.market_calendar import MarketCalendar, good_friday

CLASSES = (
    "expected_closed_market",
    "isolated_open_minute",
    "short_open_gap_2_15",
    "large_open_gap_gt15",
)


def closed_mask(index, calendar):
    # Same non_tradable semantics as MarketCalendar, avoiding per-minute strftime.
    local = index.tz_convert(calendar.zone)
    weekday = local.dayofweek.to_numpy()
    minute = (local.hour * 60 + local.minute).to_numpy()
    year, month, day = (getattr(local, name).to_numpy() for name in ("year", "month", "day"))

    def clock(value):
        return value.hour * 60 + value.minute

    config = calendar.config
    closed = (weekday > config["weekend_close_weekday"]) & (
        weekday < config["weekend_open_weekday"]
    )
    closed |= (weekday == config["weekend_close_weekday"]) & (minute >= clock(calendar.close_time))
    closed |= (weekday == config["weekend_open_weekday"]) & (minute < clock(calendar.open_time))
    closed |= (minute >= clock(calendar.pause_start)) & (minute < clock(calendar.pause_end))
    holidays = config.get("holiday_exclusions", {})
    for value in holidays.get("recurring_month_days", []):
        m, d = map(int, value.split("-"))
        closed |= (month == m) & (day == d)
    if holidays.get("good_friday", False):
        for value in np.unique(year):
            holiday = good_friday(int(value))
            closed |= (year == value) & (month == holiday.month) & (day == holiday.day)
    for override in config["holiday_overrides"]:
        closed |= (index >= pd.Timestamp(override["start_utc"])) & (
            index < pd.Timestamp(override["end_utc"])
        )
    return closed


def classify(stamps, start=None, end=None, calendar=None):
    calendar = calendar or MarketCalendar()
    observed = np.unique(np.asarray(stamps, dtype=np.int64))
    if not len(observed):
        raise ValueError("No quote minutes")
    first = pd.Timestamp(observed[0], unit="ms", tz="UTC")
    last = pd.Timestamp(observed[-1], unit="ms", tz="UTC")
    start = pd.Timestamp(start) if start is not None else first
    end = pd.Timestamp(end) if end is not None else last + pd.Timedelta(minutes=1)
    grid = pd.date_range(start, end, freq="min", inclusive="left")
    closed = closed_mask(grid, calendar)
    present = np.isin(grid.as_unit("ms").asi8, observed)
    label = np.full(len(grid), -1, dtype=np.int8)
    label[~present & closed] = 0
    label[~present & ~closed] = 1
    boundaries = np.r_[0, np.flatnonzero(label[1:] != label[:-1]) + 1, len(label)]
    intervals = []
    for begin, finish in zip(boundaries[:-1], boundaries[1:], strict=True):
        code = int(label[begin])
        if code < 0:
            continue
        length = int(finish - begin)
        if code == 1:
            code = 1 if length == 1 else (2 if length <= 15 else 3)
            label[begin:finish] = code
        intervals.append(
            {
                "start_utc": grid[begin].isoformat(),
                "end_exclusive": (grid[finish - 1] + pd.Timedelta(minutes=1)).isoformat(),
                "missing_minutes": length,
                "classification": CLASSES[code],
                "non_tradable": code == 3,
                "applies_to": ["ATR", "detectors", "fills"] if code == 3 else [],
            }
        )
    years = grid.year.to_numpy()
    hours = grid.hour.to_numpy()
    totals, distribution = {}, {}
    for code, name in enumerate(CLASSES):
        selected = label == code
        totals[name] = {
            "intervals": sum(row["classification"] == name for row in intervals),
            "missing_minutes": int(selected.sum()),
        }
        distribution[name] = {
            "year": {
                str(year): int((selected & (years == year)).sum()) for year in np.unique(years)
            },
            "hour_utc": {str(hour): int((selected & (hours == hour)).sum()) for hour in range(24)},
            "year_hour_utc": {
                f"{year}-{hour:02d}": int((selected & (years == year) & (hours == hour)).sum())
                for year in np.unique(years)
                for hour in range(24)
            },
        }
    policy = {
        "version": 1,
        "source_provider": "mt5_csv",
        "threshold_open_minutes_exclusive": 15,
        "non_tradable_intervals": [row for row in intervals if row["non_tradable"]],
        "consumers": ["ATR", "detectors", "fills"],
        "after_gap": "reset ATR/detector state and cancel pending fills",
        "quote_data_modified": False,
    }
    return {
        "start_utc": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "totals": totals,
        "distribution_minutes": distribution,
        "intervals": intervals,
        "observed_bars_outside_calendar": int((present & closed).sum()),
        "policy": policy,
        "calendar_caveat": "Conservative holiday exclusions, not historical broker hours",
    }


def annotate_gap_bars(frame, policy):
    """Flag every derived bar overlapping a large hole; identify independent segments."""
    result = frame.copy()
    ends = pd.to_datetime(
        [row["end_exclusive"] for row in policy["non_tradable_intervals"]], utc=True
    )
    finish = (
        result.available_at if "available_at" in result else result.index + pd.Timedelta(minutes=1)
    )
    starts = pd.to_datetime(
        [row["start_utc"] for row in policy["non_tradable_intervals"]], utc=True
    )
    order = np.argsort(ends.as_unit("ns").asi8)
    ending = ends.as_unit("ns").asi8[order]
    starting = starts.as_unit("ns").asi8[order]
    position = np.searchsorted(ending, result.index.as_unit("ns").asi8, side="right")
    blocked = np.zeros(len(result), dtype=bool)
    valid = position < len(starting)
    finish_ns = pd.DatetimeIndex(finish).as_unit("ns").asi8
    blocked[valid] = finish_ns[valid] > starting[position[valid]]
    existing = result.non_tradable.to_numpy() if "non_tradable" in result else False
    result["non_tradable"] = existing | blocked
    result["entry_blocked"] = result.non_tradable | (
        result.entry_blocked if "entry_blocked" in result else False
    )
    result["gap_segment_id"] = np.searchsorted(
        np.sort(ends.as_unit("ns").asi8), result.index.as_unit("ns").asi8, side="right"
    )
    result.attrs["gap_policy_applied"] = True
    return result


def current_gap_segment(frame, at):
    """Causal ATR/detector input; never bridge a large gap or include blocked bars."""
    if "gap_segment_id" not in frame or "non_tradable" not in frame:
        raise ValueError("Gap annotations must be applied before consuming MT5 history")
    mask = frame.index <= at
    if "available_at" in frame:
        mask &= frame.available_at <= at
    prefix = frame.loc[mask]
    if prefix.empty:
        return prefix
    segment = prefix.gap_segment_id.iloc[-1]
    return prefix.loc[(prefix.gap_segment_id == segment) & ~prefix.non_tradable].copy()


def write_gap_report(root, output):
    paths = sorted((root / "xauusd/bid").glob("*.csv"))
    stamps = np.concatenate(
        [pd.read_csv(path, usecols=["timestamp"]).timestamp.to_numpy() for path in paths]
    )
    report = classify(stamps, start=pd.Timestamp("2020-01-01", tz="UTC"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "gap_classification.json").write_text(json.dumps(report, indent=2))
    pd.DataFrame(report["intervals"]).to_csv(output / "gap_intervals.csv", index=False)
    (root / "gap_policy.json").write_text(json.dumps(report["policy"], indent=2))
    return report
