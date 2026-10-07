"""Read-only local MT5/Dukascopy bid alignment; no strategy or network."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from arty_trading.config.operational import load_config
from arty_trading.validation.dukascopy_import import decode_m1


def lag_scan(mt5: pd.Series, duka: pd.Series, start: pd.Timestamp, end: pd.Timestamp):
    """Positive lag moves MT5 timestamps later. Select by minute-return correlation."""
    grid = pd.date_range(
        start - pd.Timedelta(minutes=120),
        end + pd.Timedelta(minutes=120),
        freq="min",
        inclusive="left",
    )
    left = mt5.reindex(grid).to_numpy(dtype=float)
    right = duka.reindex(grid).to_numpy(dtype=float)
    n = int((end - start).total_seconds() // 60)
    target = right[120 : 120 + n]
    scans = []
    for lag in range(-120, 121):
        aligned = left[120 - lag : 120 - lag + n]
        valid = np.isfinite(aligned) & np.isfinite(target)
        pairs = int(valid.sum())
        if pairs < 300:
            continue
        diff = aligned[valid] - target[valid]
        bias = float(np.median(diff))
        returns_valid = valid[1:] & valid[:-1]
        a = np.diff(aligned)[returns_valid]
        b = np.diff(target)[returns_valid]
        correlation = None
        if len(a) >= 100 and np.std(a) > 0 and np.std(b) > 0:
            correlation = float(np.corrcoef(a, b)[0, 1])
        scans.append(
            {
                "lag_minutes": lag,
                "matched_minutes": pairs,
                "median_signed_price_diff_usd": bias,
                "median_abs_price_diff_usd": float(np.median(np.abs(diff))),
                "median_bias_removed_abs_diff_usd": float(np.median(np.abs(diff - bias))),
                "minute_returns_correlation": correlation,
                "consecutive_return_pairs": len(a),
            }
        )
    ranked = sorted(
        (row for row in scans if row["minute_returns_correlation"] is not None),
        key=lambda row: row["minute_returns_correlation"],
        reverse=True,
    )
    if len(ranked) < 2:
        return {
            "start_utc": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "status": "insufficient_overlap",
            "optimal": None,
            "scans": scans,
        }
    best, second = ranked[:2]
    margin = best["minute_returns_correlation"] - second["minute_returns_correlation"]
    clear = best["minute_returns_correlation"] >= 0.5 and margin >= 0.1
    price_best = min(scans, key=lambda row: row["median_bias_removed_abs_diff_usd"])
    return {
        "start_utc": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "status": "clear" if clear else "ambiguous",
        "optimal": best,
        "runner_up": second,
        "correlation_margin": margin,
        "zero_lag": next((row for row in scans if row["lag_minutes"] == 0), None),
        "price_residual_optimal_lag": price_best["lag_minutes"],
        "scans": scans,
    }


def compare_local(source: Path, raw_root: Path):
    with source.open(encoding="utf-8-sig") as stream:
        separator = csv.Sniffer().sniff(stream.read(4096), delimiters=",;\t").delimiter
    frame = pd.read_csv(source, sep=separator)
    frame.columns = [name.strip("<>").upper() for name in frame.columns]
    local = pd.DatetimeIndex(
        pd.to_datetime(frame.DATE + " " + frame.TIME, format="%Y.%m.%d %H:%M:%S")
    )
    utc = local.tz_localize("Europe/Athens", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    mt5 = pd.Series(frame.CLOSE.to_numpy(dtype=float), index=utc)
    excluded_dst = int(mt5.index.isna().sum())
    mt5 = mt5.loc[~mt5.index.isna()]
    duplicates = int(mt5.index.duplicated().sum())
    mt5 = mt5.loc[~mt5.index.duplicated()].sort_index()
    candidates = {}
    for extension, directory in (("bi5", "bi5"), ("csv", "m1")):
        for path in (raw_root / "xauusd/bid" / directory).glob(f"*.{extension}"):
            day = path.stem.split("_asof_")[0]
            if "2020-01-01" <= day < "2021-01-01":
                candidates[day] = path
    parts = []
    sources = []
    cfg = load_config("data_import.yaml")
    for day, path in sorted(candidates.items()):
        payload = path.read_bytes()
        sources.append({"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()})
        if path.suffix == ".bi5":
            payload, _ = decode_m1(
                payload,
                datetime.fromisoformat(day + "T00:00:00+00:00"),
                datetime(2021, 1, 1, tzinfo=UTC),
                cfg,
            )
        part = pd.read_csv(io.BytesIO(payload), usecols=["timestamp", "close"])
        parts.append(
            pd.Series(
                part.close.to_numpy(), index=pd.to_datetime(part.timestamp, unit="ms", utc=True)
            )
        )
    if not parts:
        raise ValueError("No imported local 2020 Dukascopy bid bars")
    duka = pd.concat(parts).sort_index()
    if duka.index.duplicated().any():
        raise ValueError("Duplicate Dukascopy minutes in chosen source snapshots")
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = min(pd.Timestamp("2021-01-01", tz="UTC"), duka.index.max() + pd.Timedelta(minutes=1))
    monthly = {}
    for opened in pd.date_range(start, end, freq="MS", inclusive="left"):
        closed = min(opened + pd.offsets.MonthBegin(1), end)
        monthly[opened.strftime("%Y-%m")] = lag_scan(mt5, duka, opened, closed)
    weekly = {}
    opened = start - pd.Timedelta(days=start.dayofweek)
    while opened < end:
        closed = min(opened + pd.Timedelta(days=7), end)
        weekly[opened.strftime("%Y-%m-%d")] = lag_scan(mt5, duka, max(opened, start), closed)
        opened += pd.Timedelta(days=7)
    daily = {}
    # Scan every available UTC day to localize divergences; sparse days stay ambiguous.
    for opened in pd.date_range(start, end, freq="D", inclusive="left"):
        daily[opened.strftime("%Y-%m-%d")] = lag_scan(
            mt5, duka, opened, min(opened + pd.Timedelta(days=1), end)
        )
    divergent = [
        day
        for day, row in daily.items()
        if row["status"] == "clear" and abs(row["optimal"]["lag_minutes"]) > 1
    ]
    ambiguous = [day for day, row in daily.items() if row["status"] == "ambiguous"]
    confirmed = not divergent and all(
        row["status"] == "clear" and abs(row["optimal"]["lag_minutes"]) <= 1
        for row in monthly.values()
    )
    # A monthly result cannot certify missing/sparse individual trading dates.
    if ambiguous:
        confirmed = False
    transition_weeks = {
        key: row
        for key, row in weekly.items()
        if ("2020-03-02" <= key <= "2020-03-30") or ("2020-10-19" <= key <= "2020-11-09")
    }
    return {
        "server_rule": "Europe/Athens",
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "dukascopy_sources": sources,
        "start_utc": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "last_dukascopy_bar_utc": duka.index.max().isoformat(),
        "mt5_dst_excluded": excluded_dst,
        "mt5_duplicates": duplicates,
        "monthly": monthly,
        "weekly": weekly,
        "transition_weeks": transition_weeks,
        "daily": daily,
        "divergent_days": divergent,
        "ambiguous_days": ambiguous,
        "insufficient_overlap_days": [
            day for day, row in daily.items() if row["status"] == "insufficient_overlap"
        ],
        "dst_eu_confirmed_2020_available_overlap": confirmed,
        "method": {
            "lag_definition": "MT5 timestamp + lag = Dukascopy timestamp",
            "search_minutes": [-120, 120],
            "selection": "maximum minute-return Pearson correlation",
            "clear": "correlation >=0.5 and margin over second lag >=0.1",
            "timezone_tolerance_minutes": 1,
            "minimum_samples": "300 close pairs and 100 consecutive minute-return pairs",
            "price_diff": "MT5 minus Dukascopy, USD per ounce; signed and absolute medians",
            "limitations": [
                "Heuristic thresholds, not statistical proof",
                "No inference about dates absent from Dukascopy",
                "Sparse days remain unavailable",
                "A one-minute difference can reflect quote/bar construction, not DST",
            ],
        },
        "network": False,
        "backtest_run": False,
        "raw_modified": False,
    }
