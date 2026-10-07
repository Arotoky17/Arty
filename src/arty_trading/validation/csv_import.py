"""Plan B: ingest external M1 bid/ask CSV exports with Dukascopy parity.

Expected per-day CSV contract (same schema, field order and audit as Dukascopy):
  * one file per UTC day and side;
  * header row exactly ``timestamp,open,high,low,close,volume``;
  * ``timestamp`` = integer Unix milliseconds, UTC, minute-aligned, strictly
    increasing, unique, and all inside the named UTC day;
  * ``open``/``high``/``low``/``close`` = finite positive prices, with
    ``low <= min(open, close) <= max(open, close) <= high``;
  * ``volume`` = non-negative integer tick volume;
  * bid quotes must not exceed ask quotes (checked pairwise after ingest).

Files are published append-only with SHA-256 and journalled with the same
manifest entry shape as the Dukascopy importer, so audits, resumes and the
hold-out freeze work identically for either provider.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arty_trading.config.operational import CONFIG_ROOT, load_config
from arty_trading.validation.dukascopy_import import (
    digest,
    freeze_available_end,
    publish_immutable,
    read_manifest,
    utc_now,
    validate_import_period,
)
from arty_trading.validation.market_calendar import MarketCalendar

FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]
DAY_MS = 86_400_000


def resolve_csv_path(root: Path, symbol: str, side: str, day: str) -> Path | None:
    """Locate the external CSV for one UTC day and side, or None."""
    for candidate in (
        root / symbol.lower() / side / f"{day}.csv",
        root / side / f"{day}.csv",
        root / f"{day}_{side}.csv",
        root / f"{day}.csv",
    ):
        if candidate.exists():
            return candidate
    for base in (root / side, root / symbol.lower() / side, root):
        if base.is_dir():
            for path in sorted(base.glob(f"*{day}*.csv")):
                return path
    return None


def read_m1_csv(payload: bytes, day: str) -> tuple[bytes, dict[str, Any]]:
    """Audit an external CSV and return canonical bytes plus provenance."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
    if reader.fieldnames != FIELDS:
        raise ValueError(f"{day}: expected header {FIELDS}, found {reader.fieldnames}")
    opened = int(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp() * 1000)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    previous: int | None = None
    rows = 0
    for row in reader:
        stamp = int(row["timestamp"])
        if stamp % 60_000:
            raise ValueError(f"{day}: timestamp not minute-aligned: {stamp}")
        if not opened <= stamp < opened + DAY_MS:
            raise ValueError(f"{day}: timestamp outside its UTC day: {stamp}")
        if previous is not None and stamp <= previous:
            raise ValueError(f"{day}: timestamps must strictly increase: {stamp}")
        previous = stamp
        prices = [float(row[name]) for name in ("open", "high", "low", "close")]
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError(f"{day}: non-finite or non-positive price")
        if prices[2] > min(prices[0], prices[3]) or prices[1] < max(prices[0], prices[3]):
            raise ValueError(f"{day}: OHLC bounds violated")
        volume = int(row["volume"])
        if volume < 0:
            raise ValueError(f"{day}: negative volume")
        writer.writerow(
            {
                "timestamp": stamp,
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": volume,
            }
        )
        rows += 1
    if not rows:
        raise ValueError(f"{day}: no M1 rows")
    return buffer.getvalue().encode(), {
        "rows": rows,
        "last_closed_at": datetime.fromtimestamp(previous / 1000, UTC).isoformat(),
    }


def _closes(path: Path) -> list[float]:
    with path.open(newline="", encoding="utf-8") as stream:
        return [float(row["close"]) for row in csv.DictReader(stream)]


def load_csv_provenance(root: Path) -> dict[str, Any]:
    path = root / "provenance.json"
    if not path.exists():
        return {"provider": "external_csv", "ask_origin": "source_ask"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CSV provenance.json must be an object")
    return payload


def dukascopy_store_entries(entries: list[dict[str, Any]]) -> bool:
    return any(
        row.get("binary_path") or "dukascopy" in str(row.get("url") or "").lower()
        for row in entries
        if row.get("status") == "verified"
    )


def _open_days(start: datetime, cutoff: datetime, calendar: MarketCalendar, sides: list[str]):
    day = start
    while day < cutoff:
        if not calendar.day_is_closed(day.date()):
            for side in sides:
                yield day, side
        day += timedelta(days=1)


def run_csv_import(
    root: Path,
    output: Path | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Import external M1 CSV exports into the same append-only raw store."""
    cfg = cfg or load_config("data_import.yaml")
    if cfg["sides"] != ["bid", "ask"]:
        raise ValueError("CSV import requires both BID and ASK sides")
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
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"CSV source directory not found: {root}")
    output = output or Path(cfg["output"])
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(cfg["staging"])
    staging.mkdir(parents=True, exist_ok=True)
    provenance = load_csv_provenance(root)
    manifest = output / "import_manifest.jsonl"
    entries = read_manifest(manifest, recover=True)
    reconstructed = provenance.get("ask_origin") == "reconstructed_ask"
    if reconstructed and dukascopy_store_entries(entries):
        raise ValueError(
            "Refusing to mix reconstructed_ask MT5 files with a Dukascopy raw store"
        )
    if reconstructed:
        (output / "provenance.json").write_text(
            json.dumps(provenance, indent=2), encoding="utf-8"
        )
    calendar = MarketCalendar()
    verified: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    day = start
    with manifest.open("a", encoding="utf-8") as journal:
        while day < cutoff:
            key = day.date().isoformat()
            if not calendar.day_is_closed(day.date()):
                published: dict[str, Path] = {}
                for side in cfg["sides"]:
                    source = resolve_csv_path(root, cfg["symbol"], side, key)
                    if source is None:
                        deferred.append({"date": key, "side": side, "status": "missing_source"})
                        continue
                    try:
                        payload, info = read_m1_csv(source.read_bytes(), key)
                    except ValueError as error:
                        deferred.append(
                            {"date": key, "side": side, "status": "rejected", "error": str(error)}
                        )
                        continue
                    target = output / cfg["symbol"].lower() / side / "m1" / f"{key}.csv"
                    sha = publish_immutable(target, payload, staging)
                    published[side] = target
                    if reconstructed:
                        quote_origin = "mt5_bid" if side == "bid" else "reconstructed_ask"
                    else:
                        quote_origin = "source_bid" if side == "bid" else "source_ask"
                    entry = {
                        "date": key,
                        "side": side,
                        "provider": provenance.get("provider", "external_csv"),
                        "quote_origin": quote_origin,
                        "ask_origin": provenance.get("ask_origin", "source_ask"),
                        "source": str(source),
                        "source_sha256": digest(source.read_bytes()),
                        "url": None,
                        "retrieved_at": utc_now().isoformat(),
                        "snapshot_cutoff": cutoff.isoformat(),
                        "rows": info["rows"],
                        "last_closed_at": info["last_closed_at"],
                        "status": "verified",
                        "cached": False,
                        "path": str(target),
                        "sha256": sha,
                        "allowed_for_reference_cost_model": not reconstructed,
                    }
                    journal.write(json.dumps(entry) + "\n")
                    journal.flush()
                    os.fsync(journal.fileno())
                    entries.append(entry)
                    verified.append(entry)
                if len(published) == len(cfg["sides"]):
                    left, right = (_closes(published[side]) for side in cfg["sides"])
                    if len(left) != len(right):
                        deferred.append(
                            {"date": key, "status": "rejected", "error": "bid/ask row mismatch"}
                        )
                    elif any(a > b for a, b in zip(left, right, strict=True)):
                        deferred.append(
                            {"date": key, "status": "rejected", "error": "bid/ask crossed quotes"}
                        )
            day += timedelta(days=1)
    validated = {(row["date"], row["side"]) for row in verified}
    missing = [
        {"date": day.date().isoformat(), "side": side}
        for day, side in _open_days(start, cutoff, calendar, cfg["sides"])
        if (day.date().isoformat(), side) not in validated
    ]
    summary: dict[str, Any] = {
        "provider": provenance.get("provider", "external_csv"),
        "ask_origin": provenance.get("ask_origin", "source_ask"),
        "allowed_for_reference_cost_model": not reconstructed,
        "source_root": str(root),
        "generated_at": utc_now().isoformat(),
        "cutoff_utc": cutoff.isoformat(),
        "start_utc": start.isoformat(),
        "verified_daily_sides": len(verified),
        "deferred_files": deferred,
        "missing_files": missing,
        "resume_required": bool(deferred) or bool(missing),
        "status": "failed" if deferred or missing else "downloaded",
        "backtest_run": False,
        "holdout_loaded_into_engine": False,
        "raw_data_modified": False,
        "pnl_inspected": False,
    }
    report = Path(cfg["report_output"])
    report.mkdir(parents=True, exist_ok=True)
    (report / "import_status_csv.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if until is None and not summary["resume_required"] and not reconstructed:
        last = freeze_available_end(CONFIG_ROOT / "split.yaml", entries, cutoff)
        summary["last_common_closed_bar"] = last.isoformat() if last else None
    summary["split_yaml_modified"] = bool(
        until is None and not summary["resume_required"] and not reconstructed
    )
    return summary