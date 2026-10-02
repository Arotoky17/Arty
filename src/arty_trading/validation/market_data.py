"""Read-only OHLC utilities shared by audit tools and production resampling."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

OHLC = ["open", "high", "low", "close"]


def source_frames(directory: Path, symbol: str) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Keep duplicates, row ordering and invalid values for the audit to inspect."""
    frames = {}
    sources = []
    for side in ("bid", "ask"):
        paths = sorted((directory / symbol.lower() / side / "m1").glob("*.csv"))
        # Intraday source snapshots are immutable. Select only the latest snapshot
        # of each day in this derived read view, without deleting older raw files.
        snapshots: dict[str, Path] = {}
        for path in paths:
            key = path.stem.split("_asof_")[0]
            snapshots[key] = path
        paths = list(snapshots.values())
        if not paths:
            raise ValueError(f"Missing {symbol} {side} M1 CSVs")
        parts = []
        for path in paths:
            part = pd.read_csv(path)
            part["source_file"] = path.name
            parts.append(part)
            sources.append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        frames[side] = pd.concat(parts, ignore_index=True)
    manifest_path = directory / "source_manifest.json"
    checks = []
    journal_path = directory / "import_manifest.jsonl"
    if journal_path.exists():
        from arty_trading.validation.dukascopy_import import read_manifest

        entries = read_manifest(journal_path)
        expected_paths = {
            str(Path(row["path"]).resolve()): row["sha256"]
            for row in entries
            if row["status"] == "verified"
        }
        checks = [
            {
                "file": row["path"],
                "matches": expected_paths.get(str(Path(row["path"]).resolve())) == row["sha256"],
            }
            for row in sources
        ]
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {Path(row["path"]).name: row["sha256"] for row in manifest["files"]}
        checks = [
            {
                "file": Path(row["path"]).name,
                "matches": expected.get(Path(row["path"]).name) == row["sha256"],
            }
            for row in sources
        ]
    return frames, {
        "files": sources,
        "manifest_checks": checks,
        "timestamp_provenance": (
            "Dukascopy UTC day + seconds, zero-based URL month; source BI5 preserved"
            if journal_path.exists()
            else "Unix milliseconds interpreted as UTC; source offset unverified"
        ),
    }


def index_utc(frame: pd.DataFrame, unit: str) -> pd.DataFrame:
    result = frame.copy()
    result.index = pd.DatetimeIndex(
        pd.to_datetime(result.timestamp, unit=unit, utc=True, errors="coerce")
    )
    return result


def resample_closed_bars(
    frame: pd.DataFrame, minutes: int, source_minutes: int = 1
) -> pd.DataFrame:
    """Timestamps label bar OPEN; availability labels CLOSE. Exclude the unfinished tail."""
    if minutes < source_minutes or minutes % source_minutes:
        raise ValueError("Timeframes must be positive multiples of source duration")
    if not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC":
        raise ValueError("Resampling requires a UTC DatetimeIndex")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Resampling requires ordered, unique, valid timestamps")
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for column in ("tick_volume", "volume"):
        if column in frame:
            aggregation[column] = "sum"
    if frame.empty:
        return pd.DataFrame(columns=[*aggregation, "available_at", "source_close", "observations"])
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    bars = grouped.agg(aggregation).dropna(subset=OHLC)
    bars["available_at"] = bars.index + pd.Timedelta(minutes=minutes)
    source_close = pd.Series(frame.index + pd.Timedelta(minutes=source_minutes), index=frame.index)
    bars["source_close"] = source_close.resample(f"{minutes}min", origin="epoch").max()
    bars["observations"] = grouped.close.count()
    coverage_end = frame.index[-1] + pd.Timedelta(minutes=source_minutes)
    return bars.loc[bars.available_at <= coverage_end].copy()


def available_bars(bars: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    if at.tzinfo is None:
        raise ValueError("Availability cutoff requires timezone")
    return bars.loc[bars.available_at <= at].copy()


def safe_output(directory: Path, output: Path) -> None:
    raw = directory.resolve()
    target = output.resolve()
    if target == raw or raw in target.parents:
        raise ValueError("Report output must be outside the raw-data directory")
    output.mkdir(parents=True, exist_ok=True)


def write_report(path: Path, report: dict[str, Any]) -> None:
    def numeric(value: Any) -> float:
        if isinstance(value, Decimal):
            return float(value)
        raise TypeError(f"Unsupported report value: {type(value).__name__}")

    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False, default=numeric),
        encoding="utf-8",
    )
