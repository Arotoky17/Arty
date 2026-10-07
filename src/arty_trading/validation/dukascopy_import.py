"""Append-only daily Dukascopy M1 acquisition. Never imports the backtest engine."""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import lzma
import math
import os
import random
import sqlite3
import ssl
import struct
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from arty_trading.config.operational import load_config

_timings: ContextVar[dict[str, float] | None] = ContextVar("import_timings", default=None)


class RuntimeExpiredError(Exception):
    """The download budget expired; leave uncommitted files for the next session."""


class RequestGate:
    """Session-wide provider cooldown; requests already in flight may finish."""

    def __init__(self, deadline: float | None = None):
        self.deadline = deadline
        self.cooldown_until = 0.0
        self.lock = Lock()
        self.stopped = False
        self.provider_pause = False

    def check(self) -> None:
        if self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
            raise RuntimeExpiredError

    def slow_all(self, seconds: float) -> None:
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)

    def wait(self) -> None:
        while True:
            self.check()
            with self.lock:
                remaining = self.cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            if self.deadline is not None:
                remaining = min(remaining, max(0.0, self.deadline - time.monotonic()))
            with timed_phase("backoff_seconds"):
                time.sleep(remaining)


def sleep_for(cfg: dict[str, Any], seconds: float, phase: str) -> None:
    gate = cfg.get("_request_gate")
    if gate is not None:
        gate.check()
        if gate.deadline is not None:
            seconds = min(seconds, max(0.0, gate.deadline - time.monotonic()))
    with timed_phase(phase):
        time.sleep(seconds)
    if gate is not None:
        gate.check()


@contextmanager
def download_pool(pool: ThreadPoolExecutor, gate: RequestGate) -> Iterator[None]:
    try:
        yield
    finally:
        gate.stopped = True
        pool.shutdown(wait=True, cancel_futures=True)


def index_manifest(entries: list[dict[str, Any]]) -> tuple[dict, dict]:
    """One in-memory pass; immutable committed objects are trusted on ordinary resume."""
    latest, pairs, hashes = {}, {}, {}
    for row in entries:
        if row["status"] == "pair_verified":
            pairs[row["date"]] = row
        if row["status"] != "verified":
            continue
        for path_key, hash_key in (("path", "sha256"), ("binary_path", "binary_sha256")):
            if path_key in row:
                previous = hashes.setdefault(row[path_key], row[hash_key])
                if previous != row[hash_key]:
                    raise ValueError(f"Conflicting manifest checksum changed: {row[path_key]}")
        key = (row["date"], row["side"])
        if key not in latest or row["snapshot_cutoff"] >= latest[key]["snapshot_cutoff"]:
            latest[key] = row
    return latest, pairs


@contextmanager
def timed_phase(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timings = _timings.get()
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + time.perf_counter() - started


def validation_signature(cfg: dict[str, Any]) -> dict[str, Any]:
    """Only reuse validation performed with these exact decoder rules."""
    return {
        "decoder_version": 1,
        **{
            key: cfg[key]
            for key in (
                "record_format",
                "record_fields",
                "price_divisor",
                "day_seconds",
                "source_bar_seconds",
            )
        },
    }


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
    with timed_phase("checksum_seconds"):
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
    if not payload:
        raise ValueError("Empty Dukascopy BI5 response")
    try:
        decoder = lzma.LZMADecompressor()
        with timed_phase("bi5_decode_seconds"):
            decoded = decoder.decompress(payload)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Truncated or trailing data in Dukascopy LZMA stream")
    except lzma.LZMAError as error:
        raise ValueError("Invalid or truncated Dukascopy LZMA stream") from error
    if not decoded:
        raise ValueError("Empty decoded Dukascopy day")
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
        prices = {name: row[name] / divisor for name in ("open", "high", "low", "close")}
        if not (
            0
            < prices["low"]
            <= min(prices["open"], prices["close"])
            <= max(prices["open"], prices["close"])
            <= prices["high"]
        ):
            raise ValueError("Invalid Dukascopy OHLC bounds / price divisor")
        if closed > cutoff:
            # Validate source records, but never expose an unfinished/future candle.
            continue
        writer.writerow(
            [
                int(opened.timestamp() * 1000),
                *[prices[k] for k in ("open", "high", "low", "close")],
                row["volume"],
            ]
        )
        count += 1
        last = closed.isoformat()
    if not count:
        raise ValueError("No closed M1 rows in Dukascopy response")
    return buffer.getvalue().encode(), {"rows": count, "last_closed_at": last}


def retry_delay(cfg: dict[str, Any], attempt: int, header: str = "") -> float:
    """Seconds to wait before retrying ONE file.

    Exponential from retry_base_seconds (30 s) doubling to backoff_cap_seconds
    (900 s) with symmetric jitter. An explicit Retry-After is never shortened.
    """
    jitter = float(cfg.get("retry_jitter_fraction", 0.0))
    delay = float(cfg["retry_base_seconds"]) * random.uniform(1.0 - jitter, 1.0 + jitter)
    for _ in range(attempt):
        delay *= float(cfg["retry_multiplier"])
        if delay >= cfg["backoff_cap_seconds"]:
            delay = float(cfg["backoff_cap_seconds"])
            break
    delay = min(delay, float(cfg["backoff_cap_seconds"]))
    if header:
        try:
            try:
                requested = float(header)
            except ValueError:
                deadline = parsedate_to_datetime(header)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=UTC)
                requested = (deadline.astimezone(UTC) - utc_now()).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return float(delay)  # Malformed provider header: use configured backoff.
        if not math.isfinite(requested) or requested < 0:
            return float(delay)
        # A long provider Retry-After is respected by stopping, never shortened.
        if requested > cfg["backoff_cap_seconds"]:
            raise RuntimeError("Retry-After exceeds cap; stop and resume after provider deadline")
        delay = max(delay, requested)
    return float(max(0.0, delay))


def fetch_bytes(url: str, cfg: dict[str, Any]) -> bytes | None:
    if not isinstance(cfg["max_attempts"], int) or cfg["max_attempts"] < 1:
        raise ValueError("max_attempts must be a positive integer")
    for key in (
        "timeout_seconds",
        "backoff_cap_seconds",
        "retry_base_seconds",
        "retry_multiplier",
        "request_delay_min_seconds",
        "request_delay_max_seconds",
    ):
        if not math.isfinite(cfg[key]) or cfg[key] < 0:
            raise ValueError(f"Invalid download setting: {key}")
    if (
        cfg["timeout_seconds"] <= 0
        or cfg["retry_multiplier"] < 1
        or (cfg["request_delay_min_seconds"] > cfg["request_delay_max_seconds"])
    ):
        raise ValueError("Invalid timeout, retry multiplier or request delay range")
    for attempt in range(cfg["max_attempts"]):
        try:
            gate = cfg.get("_request_gate")
            if gate is not None:
                gate.check()
            sleep_for(
                cfg,
                random.uniform(cfg["request_delay_min_seconds"], cfg["request_delay_max_seconds"]),
                "delay_seconds",
            )
            if gate is not None:
                gate.wait()
            request = urllib.request.Request(
                url, headers={"User-Agent": str(cfg.get("user_agent", "Arty-data-audit/1.0"))}
            )
            timeout = cfg["timeout_seconds"]
            if gate is not None and gate.deadline is not None:
                timeout = min(timeout, max(0.001, gate.deadline - time.monotonic()))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = bytes(response.read())
                if not payload:
                    # Emptiness is judged per day, not here; return it unchanged.
                    return b""
                length = response.headers.get("Content-Length")
                if length is not None and len(payload) != int(length):
                    raise http.client.IncompleteRead(payload, int(length) - len(payload))
                return payload
        except urllib.error.HTTPError as error:
            header = error.headers.get("Retry-After", "") if error.headers else ""
            error.close()
            if error.code == 404:
                return None  # Journal unavailability; never assume missing means market closed.
            if error.code not in (429, 500, 502, 503, 504):
                raise
            error.retry_after = header  # Honoured again by the file-level backoff.
            if gate is not None and error.code in (429, 503):
                try:
                    gate.slow_all(retry_delay(cfg, attempt, header))
                except RuntimeError:
                    # A provider deadline beyond the cap stops the whole session.
                    gate.provider_pause = True
                    gate.stopped = True
                    raise RuntimeExpiredError from None
            if attempt + 1 == cfg["max_attempts"]:
                raise
            sleep_for(cfg, retry_delay(cfg, attempt, header), "backoff_seconds")
        except (
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
            urllib.error.URLError,
        ) as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
                raise  # Never bypass TLS verification or retry a certificate defect.
            if attempt + 1 == cfg["max_attempts"]:
                raise
            sleep_for(cfg, retry_delay(cfg, attempt), "backoff_seconds")
    raise RuntimeError("Unreachable download retry state")


def apply_request_delay(cfg: dict[str, Any], base: float) -> dict[str, Any]:
    """Inter-request delay from a base value plus the configured jitter fraction."""
    result = dict(cfg)
    jitter = float(result.get("request_jitter_fraction", 0.0))
    result["request_delay_base_seconds"] = float(base)
    result["request_delay_min_seconds"] = max(0.0, float(base) * (1.0 - jitter))
    result["request_delay_max_seconds"] = float(base) * (1.0 + jitter)
    return result


def throughput(verified: int, attempted: int, started: float, sides: int = 2) -> dict[str, Any]:
    """Debit journal: days per hour and observed error rate."""
    elapsed = max(time.monotonic() - started, 1e-9)
    return {
        "elapsed_seconds": round(elapsed, 3),
        "attempted_sides": attempted,
        "verified_sides": verified,
        "days_per_hour": round((verified / max(1, sides)) / elapsed * 3600.0, 3),
        "error_rate": round((attempted - verified) / attempted, 6) if attempted else 0.0,
    }


def fetch_day_with_backoff(
    day: datetime,
    side: str,
    cutoff: datetime,
    output: Path,
    cfg: dict[str, Any],
    calendar: Any = None,
) -> dict[str, Any]:
    timings = {
        key: 0.0
        for key in (
            "network_seconds",
            "delay_seconds",
            "backoff_seconds",
            "cache_read_seconds",
            "bi5_decode_seconds",
            "validation_seconds",
            "checksum_seconds",
            "write_seconds",
        )
    }
    token = _timings.set(timings)
    started = time.perf_counter()
    try:
        entry = _fetch_day_with_backoff(day, side, cutoff, output, cfg, calendar)
        entry["phase_timings"] = {**timings, "total_seconds": time.perf_counter() - started}
        return entry
    finally:
        _timings.reset(token)


def _fetch_day_with_backoff(
    day: datetime,
    side: str,
    cutoff: datetime,
    output: Path,
    cfg: dict[str, Any],
    calendar: Any = None,
) -> dict[str, Any]:
    """Retry one file with the long backoff, then defer it and continue.

    A failing file never aborts the session: after file_attempts it is
    journalled as "failed" and listed at the end for a later resume.
    """
    attempts = max(1, int(cfg["file_attempts"]))
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fetch_day(day, side, cutoff, output, cfg, calendar)
        except RuntimeExpiredError:
            raise
        except Exception as error:  # noqa: BLE001 - one file must never stop the run
            last = error
            if attempt + 1 >= attempts:
                break
            header = getattr(error, "retry_after", "") or ""
            sleep_for(cfg, retry_delay(cfg, attempt, str(header)), "backoff_seconds")
    return {
        "date": day.date().isoformat(),
        "side": side,
        "status": "failed",
        "error": str(last),
        "error_type": type(last).__name__ if last is not None else None,
        "url": day_url(day, side, cfg),
        "endpoint": cfg["endpoint"],
        "http_status": last.code if isinstance(last, urllib.error.HTTPError) else None,
        "attempts": attempts,
        "retry_after_seen": getattr(last, "retry_after", None),
        "deferred_for_resume": True,
        "retrieved_at": utc_now().isoformat(),
    }


def day_url(day: datetime, side: str, cfg: dict[str, Any]) -> str:
    if side not in cfg["sides"] or day.tzinfo is None or day.utcoffset() != timedelta(0):
        raise ValueError("Dukascopy day requires configured BID/ASK side and explicit UTC")
    if any((day.hour, day.minute, day.second, day.microsecond)):
        raise ValueError("Dukascopy daily URL requires UTC midnight")
    return (
        f"{cfg['endpoint']}/{cfg['symbol']}/{day.year}/{day.month - 1:02d}/{day.day:02d}/"
        f"{side.upper()}_candles_min_1.bi5"
    )


def fetch_day(
    day: datetime,
    side: str,
    cutoff: datetime,
    output: Path,
    cfg: dict[str, Any],
    calendar: Any = None,
) -> dict[str, Any]:
    from arty_trading.validation.market_calendar import MarketCalendar

    if calendar is None:
        calendar = MarketCalendar()
    closed = calendar.day_is_closed(day.date())
    symbol = cfg["symbol"]
    url = day_url(day, side, cfg)
    stem = day.date().isoformat()
    if day.date() == cutoff.date():
        stem += "_asof_" + cutoff.strftime("%Y%m%dT%H%M%SZ")
    binary = output / symbol.lower() / side / "bi5" / f"{stem}.bi5"
    target = output / symbol.lower() / side / "m1" / f"{stem}.csv"
    cached = binary.exists()
    if cached:
        with timed_phase("cache_read_seconds"):
            payload = binary.read_bytes()
    else:
        timings = _timings.get()
        waits_before = (
            sum(timings.get(k, 0.0) for k in ("delay_seconds", "backoff_seconds"))
            if timings
            else 0.0
        )
        began = time.perf_counter()
        try:
            payload = fetch_bytes(url, cfg)
        finally:
            if timings is not None:
                waits = sum(timings[k] for k in ("delay_seconds", "backoff_seconds")) - waits_before
                timings["network_seconds"] += max(0.0, time.perf_counter() - began - waits)
    entry = {
        "date": day.date().isoformat(),
        "side": side,
        "url": url,
        "retrieved_at": utc_now().isoformat(),
        "snapshot_cutoff": cutoff.isoformat(),
    }
    if not payload and closed:
        # No provider file is required on a day with no tradable minute:
        # journal it as expected, never as an error to retry.
        return {
            **entry,
            "status": "empty_expected",
            "rows": 0,
            "reason": "no provider file for an expected-closed day",
        }
    if payload is None:
        return {**entry, "status": "not_available", "rows": 0}
    if not payload:
        raise ValueError("Empty Dukascopy HTTP response")
    timings = _timings.get()
    decoded_before = timings.get("bi5_decode_seconds", 0.0) if timings else 0.0
    began = time.perf_counter()
    try:
        csv_bytes, info = decode_m1(payload, day, cutoff, cfg)
    finally:
        if timings is not None:
            timings["validation_seconds"] += max(
                0.0, time.perf_counter() - began - timings["bi5_decode_seconds"] + decoded_before
            )
    staging = Path(cfg["staging"])
    if cfg.get("_prepare_only"):
        return {
            **entry,
            **info,
            "status": "verified",
            "cached": cached,
            "binary_path": str(binary),
            "path": str(target),
            "validation_signature": validation_signature(cfg),
            "_binary_payload": payload,
            "_csv_payload": csv_bytes,
        }
    checksum_before = timings.get("checksum_seconds", 0.0) if timings else 0.0
    began = time.perf_counter()
    try:
        binary_sha = publish_immutable(binary, payload, staging)
        csv_sha = publish_immutable(target, csv_bytes, staging)
    finally:
        if timings is not None:
            timings["write_seconds"] += max(
                0.0, time.perf_counter() - began - timings["checksum_seconds"] + checksum_before
            )
    return {
        **entry,
        **info,
        "status": "verified",
        "cached": cached,
        "binary_path": str(binary),
        "binary_sha256": binary_sha,
        "path": str(target),
        "sha256": csv_sha,
        "validation_signature": validation_signature(cfg),
    }


def publish_prepared(entry: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Called only by the session's writer, never by download workers."""
    if "_binary_payload" not in entry:
        return entry
    timings = entry["phase_timings"]
    token = _timings.set(timings)
    started = time.perf_counter()
    checksum_before = timings["checksum_seconds"]
    try:
        for path_key, payload_key, hash_key in (
            ("binary_path", "_binary_payload", "binary_sha256"),
            ("path", "_csv_payload", "sha256"),
        ):
            entry[hash_key] = publish_immutable(
                Path(entry[path_key]), entry.pop(payload_key), Path(cfg["staging"])
            )
    except Exception as error:  # noqa: BLE001 - preserve the per-file failure journal
        entry.pop("_binary_payload", None)
        entry.pop("_csv_payload", None)
        entry.update(
            status="failed",
            error=str(error),
            error_type=type(error).__name__,
            deferred_for_resume=True,
        )
    finally:
        elapsed = time.perf_counter() - started
        timings["write_seconds"] += max(
            0.0, elapsed - timings["checksum_seconds"] + checksum_before
        )
        timings["total_seconds"] += elapsed
        _timings.reset(token)
    return entry


def freeze_available_end(
    split_path: Path,
    entries: list[dict[str, Any]],
    cutoff: datetime,
    *,
    certified_last: datetime | None = None,
    use_certificates: bool = False,
) -> datetime | None:
    """Use actual common timestamps, not file names, scheduled dates or one-sided data."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in entries:
        if row["status"] == "verified" and row["rows"]:
            key = (row["date"], row["side"])
            if key not in latest or row["snapshot_cutoff"] > latest[key]["snapshot_cutoff"]:
                latest[key] = row
    common_dates = sorted({d for d, s in latest if (d, "bid") in latest and (d, "ask") in latest})
    last = certified_last
    for day in reversed(common_dates) if certified_last is None and not use_certificates else ():
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
    validate_import_period(split, cutoff)
    if last is not None and last > cutoff:
        raise ValueError("Imported holdout would contain future candles")
    start = datetime.fromisoformat(split["holdout"]["start"])
    frozen = (
        split["holdout"]["status"] == "frozen_from_import" and split["holdout"]["end"] is not None
    )
    if not frozen:
        split["holdout"]["end"] = last.isoformat() if last is not None and last > start else None
    if not frozen:
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


def run_fetch(
    output: Path | None = None,
    *,
    now: datetime | None = None,
    until: datetime | None = None,
    max_runtime: timedelta | float | None = None,
    cfg: dict[str, Any] | None = None,
    verify_existing: bool = False,
) -> dict[str, Any]:
    cfg = dict(cfg or load_config("data_import.yaml"))
    workers = cfg.get("workers", 1)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 32:
        raise ValueError("workers must be an integer between 1 and 32")
    if cfg["sides"] != ["bid", "ask"]:
        raise ValueError("Dukascopy import requires both configured BID and ASK sides")
    cutoff = (now or utc_now()).replace(second=0, microsecond=0)
    split = load_config("split.yaml")
    validate_import_period(split, cutoff)
    if until is not None:
        if until.tzinfo is None or until.utcoffset() != timedelta(0) or until > cutoff:
            raise ValueError("--until must be explicit UTC, not a future date")
        if until > datetime.fromisoformat(split["development"]["end"]):
            raise ValueError("Bounded import must end within development; no holdout access")
        cutoff = until
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
    with closing(sqlite3.connect(staging / "download_lock.sqlite", timeout=1)) as lock, lock:
        lock.execute("CREATE TABLE IF NOT EXISTS importer_lock (id INTEGER)")
        lock.commit()
        lock.execute("BEGIN EXCLUSIVE")
        resume_started = time.perf_counter()
        entries = read_manifest(manifest, recover=True)
        latest, pair_certificates = index_manifest(entries)
        days: list[tuple[datetime, str]] = []
        day = start
        while day < cutoff:
            days.extend((day, side) for side in cfg["sides"])
            day += timedelta(days=1)
        from arty_trading.validation.market_calendar import MarketCalendar

        calendar = MarketCalendar()
        closed_days = {
            day.date()
            for day, _ in days[:: len(cfg["sides"])]
            if calendar.day_is_closed(day.date())
        }
        checked_objects: set[tuple[str, str]] = set()
        audit_started = time.perf_counter()
        for entry in latest.values() if verify_existing else ():
            if entry["status"] == "verified":
                if (
                    until is not None
                    and datetime.fromisoformat(entry["date"]).replace(tzinfo=UTC) >= cutoff
                ):
                    continue  # Do not open any out-of-scope existing raw object.
                for path_key, hash_key in (("path", "sha256"), ("binary_path", "binary_sha256")):
                    if path_key not in entry:
                        continue  # External CSV ingest carries no binary object.
                    path = Path(entry[path_key])
                    object_key = (str(path), entry[hash_key])
                    if object_key in checked_objects:
                        continue
                    if not path.exists() or digest(path.read_bytes()) != entry[hash_key]:
                        raise ValueError(f"Imported object missing or checksum changed: {path}")
                    checked_objects.add(object_key)
        audit_seconds = time.perf_counter() - audit_started
        reusable = {}
        signature = validation_signature(cfg)
        default_signature = validation_signature(load_config("data_import.yaml"))
        for entry in latest.values():
            saved_signature = entry.get("validation_signature")
            if saved_signature != signature and not (
                saved_signature is None and signature == default_signature
            ):
                continue
            day = datetime.fromisoformat(entry["date"]).replace(tzinfo=UTC)
            # Partial-day snapshots and external CSVs must not hide a new BI5 request.
            complete_at = day + timedelta(days=1)
            if (
                complete_at > cutoff
                or datetime.fromisoformat(entry["snapshot_cutoff"]) < complete_at
            ):
                continue
            base = output / cfg["symbol"].lower() / entry["side"]
            expected_binary = str(base / "bi5" / f"{entry['date']}.bi5")
            expected_csv = str(base / "m1" / f"{entry['date']}.csv")
            # Complete external CSV imports are committed validation too.
            if (
                entry["path"] == expected_csv
                and entry.get("binary_path", expected_binary) == expected_binary
            ):
                reusable[(entry["date"], entry["side"])] = entry
        failures = []
        deferred: list[dict[str, Any]] = []
        attempted = 0
        verified_here = 0
        pending = [
            (number, day, side)
            for number, (day, side) in enumerate(days, 1)
            if (day.date().isoformat(), side) not in reusable
        ]
        reused = len(days) - len(pending)
        resume_seconds = time.perf_counter() - resume_started
        manifest_seconds = 0.0
        phase_totals: dict[str, float] = {}
        runtime_exhausted = False
        interrupted = False
        budget: float | None = None
        if max_runtime is not None:
            budget = (
                max_runtime.total_seconds()
                if isinstance(max_runtime, timedelta)
                else float(max_runtime)
            )
            if not math.isfinite(budget) or budget <= 0:
                raise ValueError("max_runtime must be positive")
        # The runtime budget starts only after resume indexing/planning/audit.
        started = time.monotonic()
        gate = RequestGate(started + budget if budget is not None else None)
        worker_cfg = {**cfg, "_prepare_only": True, "_request_gate": gate}
        changed: set[tuple[str, str]] = set()
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dukascopy")
        queue = deque()
        tasks = iter(pending)

        def submit_next() -> bool:
            nonlocal runtime_exhausted
            task = next(tasks, None)
            if task is None:
                return False
            try:
                gate.check()
            except RuntimeExpiredError:
                runtime_exhausted = not gate.provider_pause
                return False
            number, day, side = task
            queue.append(
                (
                    number,
                    pool.submit(
                        fetch_day_with_backoff, day, side, cutoff, output, worker_cfg, calendar
                    ),
                )
            )
            return True

        for _ in range(workers):
            if not submit_next():
                break
        with download_pool(pool, gate), manifest.open("a", encoding="utf-8") as journal:
            while queue:
                number, future = queue.popleft()
                attempted += 1
                # A failing file is deferred, never fatal to the remaining files.
                try:
                    entry = publish_prepared(future.result(), cfg)
                except RuntimeExpiredError:
                    runtime_exhausted = not gate.provider_pause
                    break
                except (KeyboardInterrupt, SystemExit):
                    # An interrupted session must still leave a truthful report
                    # instead of leaving the previous run's one on disk.
                    interrupted = True
                    break
                append_started = time.perf_counter()
                journal.write(json.dumps(entry) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                manifest_seconds += time.perf_counter() - append_started
                for phase, seconds in entry.get("phase_timings", {}).items():
                    phase_totals[phase] = phase_totals.get(phase, 0.0) + seconds
                entries.append(entry)
                if entry["status"] == "verified":
                    verified_here += 1
                    key = (entry["date"], entry["side"])
                    latest[key] = entry
                    changed.add(key)
                elif entry["status"] in {"failed", "error"}:
                    deferred.append(entry)
                if attempted % cfg["progress_every"] == 0:
                    rate = throughput(verified_here, attempted, started, len(cfg["sides"]))
                    print(
                        f"Dukascopy: {number}/{len(days)} sides | "
                        f"{rate['days_per_hour']:.1f} days/h | "
                        f"errors {rate['error_rate']:.1%} | deferred {len(deferred)}",
                        flush=True,
                    )
                submit_next()
            gate.stopped = True
            for _, future in queue:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
            if attempted:
                journal.write(
                    json.dumps(
                        {
                            "status": "session_timing",
                            "timestamp": utc_now().isoformat(),
                            "phase_timings": phase_totals,
                            "manifest_append_seconds": manifest_seconds,
                            "existing_checksum_seconds": audit_seconds,
                            "reused_daily_sides": reused,
                            "resume_seconds": resume_seconds,
                            "workers": workers,
                        }
                    )
                    + "\n"
                )
                journal.flush()
                os.fsync(journal.fileno())
        requested = {(day.date().isoformat(), side) for day, side in days}
        verified = [entry for key, entry in latest.items() if key in requested]
        unavailable = [entry for entry in entries if entry["status"] == "not_available"]
        empty_expected = [entry for entry in entries if entry["status"] == "empty_expected"]
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
            "phase_timings": phase_totals,
            "manifest_append_seconds": manifest_seconds,
            "existing_checksum_seconds": audit_seconds,
            "resume_seconds": resume_seconds,
            "workers": workers,
            "provider_pause": gate.provider_pause,
            "reused_daily_sides": reused,
            "verified_daily_sides": len(verified),
            "unavailable_daily_sides": len(unavailable),
            "empty_expected_daily_sides": len(empty_expected),
            "expected_closed_days": len(closed_days),
            "coverage_validated": False,
            "deferred_files": [
                {
                    "date": row["date"],
                    "side": row["side"],
                    "status": row["status"],
                    "error": row.get("error"),
                }
                for row in deferred
            ],
            "throughput": throughput(verified_here, attempted, started, len(cfg["sides"])),
            "runtime_exhausted": runtime_exhausted,
            "interrupted": interrupted,
            "resume_required": bool(deferred)
            or runtime_exhausted
            or interrupted
            or gate.provider_pause,
        }
        validated = {(entry["date"], entry["side"]) for entry in verified}
        summary["missing_files"] = [
            {"date": day.date().isoformat(), "side": side}
            for day, side in days
            if (day.date().isoformat(), side) not in validated and day.date() not in closed_days
        ]
        if summary["missing_files"] and not failures:
            failures.append(
                {
                    "status": "error",
                    "error": (
                        "Required daily resources unavailable; "
                        "HTTP 404 does not establish market closure"
                    ),
                }
            )
            summary["status"] = "failed"
        if runtime_exhausted or interrupted or gate.provider_pause:
            # A bounded or interrupted session ends by design: resumable, never a
            # hard failure, and the report must match what is actually on disk.
            failures = []
            summary["failures"] = []
            summary["status"] = (
                "provider_pause"
                if gate.provider_pause
                else "runtime_budget_exhausted"
                if runtime_exhausted
                else "interrupted"
            )
        summary["scope"] = "development_only" if until is not None else "full_requested_period"
        summary["bid_ask_pairing_validated"] = False
        summary["pairing_audit_required"] = []
        certified_last = None
        if not failures and not runtime_exhausted and not interrupted and not gate.provider_pause:
            # Compare UTC timestamps and OHLC quotes for both sides before any freeze.
            try:
                for day, _ in days[:: len(cfg["sides"])]:
                    if day.date() in closed_days:
                        continue  # No provider file is required on a closed day.
                    day_key = day.date().isoformat()
                    pair_hashes = [latest[(day_key, side)]["sha256"] for side in cfg["sides"]]
                    certificate = pair_certificates.get(day_key)
                    if (
                        certificate
                        and certificate["hashes"] == pair_hashes
                        and certificate.get("validation_signature") == signature
                    ):
                        close = datetime.fromisoformat(certificate["last_common_closed_bar"])
                        certified_last = max(certified_last or close, close)
                        continue
                    if not verify_existing and any(
                        (day_key, side) not in changed for side in cfg["sides"]
                    ):
                        # No raw reads on resume: legacy/mixed pairs require an explicit audit.
                        summary["pairing_audit_required"].append(day_key)
                        continue
                    paired = []
                    for side in cfg["sides"]:
                        with Path(latest[(day.date().isoformat(), side)]["path"]).open(
                            newline="", encoding="utf-8"
                        ) as stream:
                            paired.append(list(csv.DictReader(stream)))
                    if [r["timestamp"] for r in paired[0]] != [r["timestamp"] for r in paired[1]]:
                        raise ValueError(f"BID/ASK timestamp mismatch: {day.date()}")
                    if any(
                        float(ask[key]) < float(bid[key])
                        for bid, ask in zip(*paired, strict=True)
                        for key in ("open", "high", "low", "close")
                    ):
                        raise ValueError(f"BID/ASK crossed OHLC quotes: {day.date()}")
                    close = datetime.fromtimestamp(
                        int(paired[0][-1]["timestamp"]) / 1000, UTC
                    ) + timedelta(seconds=cfg["source_bar_seconds"])
                    certified_last = max(certified_last or close, close)
                    certificate = {
                        "status": "pair_verified",
                        "date": day_key,
                        "hashes": pair_hashes,
                        "validation_signature": signature,
                        "last_common_closed_bar": close.isoformat(),
                    }
                    with manifest.open("a", encoding="utf-8") as journal:
                        journal.write(json.dumps(certificate) + "\n")
                        journal.flush()
                        os.fsync(journal.fileno())
                summary["bid_ask_pairing_validated"] = not summary["pairing_audit_required"]
            except ValueError as error:
                failures.append({"status": "error", "error": str(error)})
                summary["status"] = "failed"
        if (
            not failures
            and not runtime_exhausted
            and not interrupted
            and until is None
            and summary["bid_ask_pairing_validated"]
        ):
            from arty_trading.config.operational import CONFIG_ROOT
            from arty_trading.validation.trial_registry import TrialRegistry

            split_before = yaml.safe_load((CONFIG_ROOT / "split.yaml").read_text(encoding="utf-8"))
            # Certificates already establish the actual common close; avoid raw reads.
            last = freeze_available_end(
                CONFIG_ROOT / "split.yaml",
                entries,
                cutoff,
                certified_last=certified_last,
                use_certificates=True,
            )
            split_after = yaml.safe_load((CONFIG_ROOT / "split.yaml").read_text(encoding="utf-8"))
            if split_before != split_after:
                TrialRegistry().record_rule_change(
                    "split.yaml",
                    split_before,
                    split_after,
                    "Freeze first common closed bid/ask holdout end after successful import",
                )
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
