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
        if not paths and (directory / "provenance.json").exists():
            provider = json.loads((directory / "provenance.json").read_text()).get("provider")
            if provider == "mt5_csv":
                paths = sorted((directory / symbol.lower() / side).glob("*.csv"))
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
        gap_path = directory / "gap_policy.json"
        if gap_path.exists():
            frames[side].attrs["gap_policy"] = json.loads(gap_path.read_text(encoding="utf-8"))
    manifest_path = directory / "source_manifest.json"
    checks = []
    journal_path = directory / "import_manifest.jsonl"
    journal_entries: list[dict[str, Any]] = []
    if journal_path.exists():
        from arty_trading.validation.dukascopy_import import read_manifest

        journal_entries = read_manifest(journal_path)
        expected_paths = {
            str(Path(row["path"]).resolve()): row["sha256"]
            for row in journal_entries
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
    ask_origin = "source_ask"
    sidecar = directory / "provenance.json"
    if sidecar.exists():
        extra = json.loads(sidecar.read_text(encoding="utf-8"))
        ask_origin = extra.get("ask_origin", ask_origin)
        expected_gap = extra.get("gap_policy_sha256")
        if expected_gap:
            gap_file = directory / "gap_policy.json"
            if (
                not gap_file.exists()
                or hashlib.sha256(gap_file.read_bytes()).hexdigest() != expected_gap
            ):
                raise ValueError("MT5 gap policy checksum mismatch")
    for row in journal_entries:
        if row.get("quote_origin") == "reconstructed_ask" or row.get("ask_origin") == (
            "reconstructed_ask"
        ):
            ask_origin = "reconstructed_ask"
            break
    if ask_origin == "reconstructed_ask":
        timestamp_provenance = (
            "MT5 server-local converted to UTC; ASK reconstructed_ask from bid+spread*point; "
            "not Dukascopy; forbidden for the reference cost model"
        )
    elif journal_path.exists():
        timestamp_provenance = (
            "Dukascopy UTC day + seconds, zero-based URL month; source BI5 preserved"
        )
    else:
        timestamp_provenance = "Unix milliseconds interpreted as UTC; source offset unverified"
    return frames, {
        "files": sources,
        "manifest_checks": checks,
        "ask_origin": ask_origin,
        "allowed_for_reference_cost_model": ask_origin != "reconstructed_ask",
        "timestamp_provenance": timestamp_provenance,
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
    from arty_trading.config.operational import definitions

    daily = definitions()["daily_bars"]
    if minutes == 1440 and daily["convention"] == "new_york_17":
        from arty_trading.validation.market_calendar import MarketCalendar

        calendar = MarketCalendar()
        hour, minute = map(int, daily["new_york_anchor"].split(":"))
        local = frame.index.tz_convert(calendar.zone).tz_localize(None)
        naive_starts = local.normalize() + pd.Timedelta(hours=hour, minutes=minute)
        naive_starts = naive_starts.where(
            local >= naive_starts, naive_starts - pd.Timedelta(days=1)
        )
        starts = naive_starts.tz_localize(calendar.zone).tz_convert("UTC")
        grouped_daily = frame.groupby(starts)
        bars = grouped_daily.agg(aggregation).dropna(subset=OHLC)
        next_local = bars.index.tz_convert(calendar.zone).tz_localize(None) + pd.Timedelta(days=1)
        bars["available_at"] = next_local.tz_localize(calendar.zone).tz_convert("UTC")
        source_close = pd.Series(
            frame.index + pd.Timedelta(minutes=source_minutes), index=frame.index
        )
        bars["source_close"] = source_close.groupby(starts).max()
        bars["observations"] = grouped_daily.close.count()
        result = bars.loc[
            bars.available_at <= frame.index[-1] + pd.Timedelta(minutes=source_minutes)
        ].copy()
        return _apply_gap_overlay(result, frame)
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    bars = grouped.agg(aggregation).dropna(subset=OHLC)
    bars["available_at"] = bars.index + pd.Timedelta(minutes=minutes)
    source_close = pd.Series(frame.index + pd.Timedelta(minutes=source_minutes), index=frame.index)
    bars["source_close"] = source_close.resample(f"{minutes}min", origin="epoch").max()
    bars["observations"] = grouped.close.count()
    coverage_end = frame.index[-1] + pd.Timedelta(minutes=source_minutes)
    return _apply_gap_overlay(bars.loc[bars.available_at <= coverage_end].copy(), frame)


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


def _apply_gap_overlay(bars: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    policy = source.attrs.get("gap_policy")
    if policy:
        from arty_trading.validation.mt5_gaps import annotate_gap_bars

        return annotate_gap_bars(bars, policy)
    return bars
