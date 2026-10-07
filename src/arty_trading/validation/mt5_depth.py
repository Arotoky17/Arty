"""Exact minute coverage of the converted bid, including missing UTC days."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from arty_trading.config.operational import load_config
from arty_trading.validation.market_calendar import MarketCalendar


def minute_depth(root: Path):
    paths = sorted((root / "xauusd/bid").glob("*.csv"))
    parts = [pd.read_csv(path, usecols=["timestamp"]).timestamp.to_numpy() for path in paths]
    if not parts:
        raise ValueError("No converted bid")
    stamps = np.concatenate(parts)
    duplicates = len(stamps) - len(np.unique(stamps))
    observed = pd.DatetimeIndex(pd.to_datetime(np.unique(stamps), unit="ms", utc=True))
    cutoff = observed.max() + pd.Timedelta(minutes=1)
    split = load_config("split.yaml")
    dev_start, dev_end = (pd.Timestamp(split["development"][key]) for key in ("start", "end"))
    holdout_start = pd.Timestamp(split["holdout"]["start"])
    holdout_end = pd.Timestamp(split["holdout"]["end"]) if split["holdout"]["end"] else cutoff
    calendar = MarketCalendar()
    windows = {"development": [dev_start, dev_end], "holdout": [holdout_start, holdout_end]}
    coverage = {
        name: {
            "start_utc": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "expected_open_minutes": 0,
            "present_open_minutes": 0,
            "missing_open_minutes": 0,
            "bars_outside_market": 0,
            "provisional_end": name == "holdout" and split["holdout"]["end"] is None,
            "status": "unavailable" if end <= start else "available",
        }
        for name, (start, end) in windows.items()
    }
    years = {}
    holes = []
    for year in range(2020, cutoff.year + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), cutoff)
        grid = pd.date_range(start, end, freq="min", inclusive="left")
        opened = (~calendar.annotate(pd.DataFrame(index=grid)).non_tradable).to_numpy()
        present = grid.isin(observed)
        missing = opened & ~present
        outside = ~opened & present
        years[str(year)] = {
            "start_utc": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "expected_open_minutes": int(opened.sum()),
            "present_open_minutes": int((opened & present).sum()),
            "missing_open_minutes": int(missing.sum()),
            "bars_outside_market": int(outside.sum()),
            "observed_bid_minutes": int(present.sum()),
            "provisional_end": year == cutoff.year,
        }
        missing_index = grid[missing].as_unit("ns")
        if len(missing_index):
            breaks = np.flatnonzero(np.diff(missing_index.asi8) != 60_000_000_000) + 1
            for segment in np.split(missing_index.asi8, breaks):
                holes.append(
                    {
                        "start_utc": pd.Timestamp(segment[0], tz="UTC").isoformat(),
                        "end_exclusive": (
                            pd.Timestamp(segment[-1], tz="UTC") + pd.Timedelta(minutes=1)
                        ).isoformat(),
                        "missing_open_minutes": len(segment),
                    }
                )
        for name, (begin, finish) in windows.items():
            mask = (grid >= begin) & (grid < finish)
            coverage[name]["expected_open_minutes"] += int((opened & mask).sum())
            coverage[name]["present_open_minutes"] += int((opened & present & mask).sum())
            coverage[name]["missing_open_minutes"] += int((missing & mask).sum())
            coverage[name]["bars_outside_market"] += int((outside & mask).sum())
    return {
        "first_bid_utc": observed.min().isoformat(),
        "last_bid_utc": observed.max().isoformat(),
        "duplicate_timestamps": int(duplicates),
        "years": years,
        "coverage": coverage,
        "hole_intervals": holes,
        "hole_interval_count": len(holes),
        "calendar_caveat": "Conservative holiday exclusions, not historical broker hours",
        "split_yaml_modified": False,
        "network": False,
        "backtest_run": False,
    }
