"""Append-only daily Dukascopy M1 acquisition. Never imports the backtest engine."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import lzma
import math
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from arty_trading.config.operational import load_config


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_import_period(split: dict[str, Any], now: datetime) -> None:
    """Reject a future holdout before the first network request or raw-file write."""
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("Import cutoff must be explicit UTC")
    row = split["holdout"]
    for key in ("start", "end"):
        if row[key] is not None and datetime.fromisoformat(row[key]) > now:
            raise ValueError(f"Holdout {key} contains future dates; import refused")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_manifest(path: Path, *, recover: bool = False) -> list[dict[str, Any]]:
    """Seal a torn final write with an appended checksum-labelled recovery marker."""
    if not path.exists():
        return []
    payload = path.read_bytes()
    lines = payload.splitlines()
    records = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            recovered = False
            if index + 1 < len(lines):
                try:
                    marker = json.loads(lines[index + 1])
                    recovered = marker.get("status") == "journal_recovery" and (
                        marker.get("fragment_sha256") == digest(line)
                    )
                except json.JSONDecodeError:
                    pass
            if recovered:
                continue
            if recover and index == len(lines) - 1:
                marker = {
                    "status": "journal_recovery",
                    "fragment_sha256": digest(line),
                    "timestamp": utc_now().isoformat(),
                }
                with path.open("ab") as stream:
                    if not payload.endswith(b"\n"):
                        stream.write(b"\n")
                    stream.write(json.dumps(marker).encode() + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                records.append(marker)
            else:
                raise ValueError("Corrupt manifest without a verified recovery marker") from None
    if recover and payload and not payload.endswith(b"\n"):
        if records[-1].get("status") != "journal_recovery":
            with path.open("ab") as stream:
                stream.write(b"\n")
    return records


def publish_immutable(path: Path, payload: bytes, staging: Path) -> str:
    """Publish a complete file atomically; never replace an existing raw object."""
    sha = digest(payload)
    if path.exists():
        if digest(path.read_bytes()) != sha:
            raise ValueError(f"Append-only conflict / checksum mismatch: {path}")
        return sha
    path.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    temporary = staging / f"{sha}.{os.getpid()}.part"
    # Staging is disposable; immutable destination files are never truncated.
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if digest(path.read_bytes()) != sha:
            raise ValueError(f"Concurrent append-only conflict: {path}") from None
    finally:
        temporary.unlink(missing_ok=True)
    return sha


def decode_m1(
    payload: bytes, day: datetime, cutoff: datetime, cfg: dict[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    """BI5 big-endian: seconds, open, close, low, high, volume; zero-based URL month."""
    unpacker = struct.Struct(cfg["record_format"])
    decoded = lzma.decompress(payload) if payload else b""
    if len(decoded) % unpacker.size:
        raise ValueError("Truncated Dukascopy candle record")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    count = 0
    previous = -1
    last = None
    divisor = cfg["price_divisor"]
    for values in unpacker.iter_unpack(decoded):
        row = dict(zip(cfg["record_fields"], values, strict=True))
        seconds = row["seconds"]
        if not 0 <= seconds < cfg["day_seconds"] or seconds % cfg["source_bar_seconds"]:
            raise ValueError("Invalid/non-minute Dukascopy timestamp")
        if seconds <= previous:
            raise ValueError("Duplicate or unordered Dukascopy records")
        previous = seconds
        if not math.isfinite(row["volume"]) or row["volume"] < 0:
            raise ValueError("Invalid Dukascopy volume")
        opened = day + timedelta(seconds=seconds)
        closed = opened + timedelta(seconds=cfg["source_bar_seconds"])
        if closed > cutoff:
            # Preserve source BI5, but never expose an unfinished/future M1 candle.
            continue
        prices = {name: row[name] / divisor for name in ("open", "high", "low", "close")}
        if not (
            0
            < prices["low"]
            <= min(prices["open"], prices["close"])
            <= max(prices["open"], prices["close"])
            <= prices["high"]
        ):
            raise ValueError("Invalid Dukascopy OHLC bounds / price divisor")
        writer.writerow(
            [
                int(opened.timestamp() * 1000),
                *[prices[k] for k in ("open", "high", "low", "close")],
                row["volume"],
            ]
        )
        count += 1
        last = closed.isoformat()
    return buffer.getvalue().encode(), {"rows": count, "last_closed_at": last}


def fetch_bytes(url: str, cfg: dict[str, Any]) -> bytes | None:
    for attempt in range(cfg["max_attempts"]):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Arty-data-audit/1.0"})
            with urllib.request.urlopen(request, timeout=cfg["timeout_seconds"]) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None  # Journal unavailability; never assume missing means market closed.
            if error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt + 1 == cfg["max_attempts"]:
                raise
            wait = cfg["retry_seconds"] * (attempt + 1)
            header = error.headers.get("Retry-After", "")
            if header.isdigit():
                wait = max(wait, int(header))
            if wait > cfg["max_retry_after_seconds"]:
                raise RuntimeError(
                    "Provider requests long backoff; stop and resume later"
                ) from error
            time.sleep(wait)
        except (TimeoutError, urllib.error.URLError):
            if attempt + 1 == cfg["max_attempts"]:
                raise
            time.sleep(cfg["retry_seconds"] * (attempt + 1))
    raise RuntimeError("Unreachable download retry state")


def fetch_day(
    day: datetime, side: str, cutoff: datetime, output: Path, cfg: dict[str, Any]
) -> dict[str, Any]:
    symbol = cfg["symbol"]
    url = (
        f"{cfg['endpoint']}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/"
        f"{side.upper()}_candles_min_1.bi5"
    )
    stem = day.date().isoformat()
    if day.date() == cutoff.date():
        stem += "_asof_" + cutoff.strftime("%Y%m%dT%H%M%SZ")
    binary = output / symbol.lower() / side / "bi5" / f"{stem}.bi5"
    target = output / symbol.lower() / side / "m1" / f"{stem}.csv"
    cached = binary.exists()
    payload = binary.read_bytes() if cached else fetch_bytes(url, cfg)
    entry = {
        "date": day.date().isoformat(),
        "side": side,
        "url": url,
        "retrieved_at": utc_now().isoformat(),
        "snapshot_cutoff": cutoff.isoformat(),
    }
    if payload is None:
        return {**entry, "status": "not_available", "rows": 0}
    csv_bytes, info = decode_m1(payload, day, cutoff, cfg)
    staging = Path(cfg["staging"])
    binary_sha = publish_immutable(binary, payload, staging)
    csv_sha = publish_immutable(target, csv_bytes, staging)
    return {
        **entry,
        **info,
        "status": "verified",
        "cached": cached,
        "binary_path": str(binary),
        "binary_sha256": binary_sha,
        "path": str(target),
        "sha256": csv_sha,
    }


def freeze_available_end(
    split_path: Path, entries: list[dict[str, Any]], cutoff: datetime
) -> datetime | None:
    """Use actual common timestamps, not file names, scheduled dates or one-sided data."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in entries:
        if row["status"] == "verified" and row["rows"]:
            key = (row["date"], row["side"])
            if key not in latest or row["snapshot_cutoff"] > latest[key]["snapshot_cutoff"]:
                latest[key] = row
    common_dates = sorted({d for d, s in latest if (d, "bid") in latest and (d, "ask") in latest})
    last = None
    for day in reversed(common_dates):
        sides = []
        for side in ("bid", "ask"):
            entry = latest[(day, side)]
            path = Path(entry["path"])
            if digest(path.read_bytes()) != entry["sha256"]:
                raise ValueError(f"Imported checksum changed: {path}")
            with path.open(newline="", encoding="utf-8") as stream:
                sides.append({int(row["timestamp"]) for row in csv.DictReader(stream)})
        common = sides[0] & sides[1]
        if common:
            seconds = load_config("data_import.yaml")["source_bar_seconds"]
            last = datetime.fromtimestamp(max(common) / 1000, UTC) + timedelta(seconds=seconds)
            break
    split = yaml.safe_load(split_path.read_text(encoding="utf-8"))
    if last is not None and last > cutoff:
        raise ValueError("Imported holdout would contain future candles")
    start = datetime.fromisoformat(split["holdout"]["start"])
    split["holdout"]["end"] = last.isoformat() if last is not None and last > start else None
    split["holdout"]["status"] = (
        "frozen_from_import" if last and last > start else "unavailable_until_import"
    )
    split["holdout"]["end_policy"] = "last_available_common_closed_m1_bar"
    split["availability"] = {
        "snapshot_utc": cutoff.isoformat(),
        "last_common_closed_bar": last.isoformat() if last else None,
    }
    validate_import_period(split, cutoff)
    split_path.write_text(yaml.safe_dump(split, sort_keys=False), encoding="utf-8")
    return last


def run_fetch(output: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    cfg = load_config("data_import.yaml")
    cutoff = (now or utc_now()).replace(second=0, microsecond=0)
    split = load_config("split.yaml")
    validate_import_period(split, cutoff)
    start = datetime.fromisoformat(cfg["start"])
    if start > cutoff:
        raise ValueError("Import start is in the future")
    output = output or Path(cfg["output"])
    if output.resolve() == Path("data/historical").resolve():
        raise ValueError("Import must not write into the existing historical source directory")
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(cfg["staging"])
    staging.mkdir(parents=True, exist_ok=True)
    manifest = output / "import_manifest.jsonl"
    # An OS-backed SQLite lock releases on interruption; no stale lock to delete.
    with sqlite3.connect(staging / "download_lock.sqlite", timeout=1) as lock:
        lock.execute("CREATE TABLE IF NOT EXISTS importer_lock (id INTEGER)")
        lock.commit()
        lock.execute("BEGIN EXCLUSIVE")
        entries = read_manifest(manifest, recover=True)
        days: list[tuple[datetime, str]] = []
        day = start
        while day <= cutoff:
            days.extend((day, side) for side in cfg["sides"])
            day += timedelta(days=1)
        for entry in entries:
            if entry["status"] == "verified":
                for path_key, hash_key in (("path", "sha256"), ("binary_path", "binary_sha256")):
                    path = Path(entry[path_key])
                    if not path.exists() or digest(path.read_bytes()) != entry[hash_key]:
                        raise ValueError(f"Imported object missing or checksum changed: {path}")
        # Existing objects are decoded/rechecked by fetch_day on every resume.
        failures = []
        with (
            manifest.open("a", encoding="utf-8") as journal,
            ThreadPoolExecutor(max_workers=cfg["workers"]) as pool,
        ):
            futures = {
                pool.submit(fetch_day, day, side, cutoff, output, cfg): (day, side)
                for day, side in days
            }
            for number, future in enumerate(as_completed(futures), 1):
                try:
                    entry = future.result()
                except Exception as error:
                    day, side = futures[future]
                    entry = {
                        "date": day.date().isoformat(),
                        "side": side,
                        "status": "error",
                        "error": str(error),
                        "retrieved_at": utc_now().isoformat(),
                    }
                    failures.append(entry)
                    # Cancel pending requests: do not hammer an unavailable provider.
                    for pending in futures:
                        pending.cancel()
                journal.write(json.dumps(entry) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                entries.append(entry)
                if failures:
                    break
                if number % cfg["progress_every"] == 0:
                    print(
                        f"Dukascopy: {number}/{len(days)} daily sides verified/journaled",
                        flush=True,
                    )
        verified = [entry for entry in entries if entry["status"] == "verified"]
        unavailable = [entry for entry in entries if entry["status"] == "not_available"]
        if not failures and not any(entry["rows"] for entry in verified):
            failures.append({"status": "error", "error": "No M1 source rows available"})
        summary = {
            "cutoff_utc": cutoff.isoformat(),
            "start_utc": start.isoformat(),
            "backtest_run": False,
            "holdout_loaded_into_engine": False,
            "failures": failures,
            "status": "failed" if failures else "downloaded",
            "manifest": str(manifest),
            "verified_daily_sides": len(verified),
            "unavailable_daily_sides": len(unavailable),
            "coverage_validated": False,
        }
        if not failures:
            from arty_trading.config.operational import CONFIG_ROOT

            last = freeze_available_end(CONFIG_ROOT / "split.yaml", entries, cutoff)
            summary["last_common_closed_bar"] = last.isoformat() if last else None
            validation_path = CONFIG_ROOT / "validation.yaml"
            validation = yaml.safe_load(validation_path.read_text(encoding="utf-8"))
            validation["audit"]["expected_end"] = cutoff.isoformat()
            validation_path.write_text(
                yaml.safe_dump(validation, sort_keys=False), encoding="utf-8"
            )
        report = Path(cfg["report_output"])
        report.mkdir(parents=True, exist_ok=True)
        (report / "import_status.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if failures:
            raise RuntimeError(f"Dukascopy import incomplete; resume later: {failures[0]['error']}")
        return summary
