"""Read-only import coverage: verified bid/ask days and completeness verdicts.

Never runs a backtest and never writes raw data. Reports:
  * verified bid / ask / common day counts;
  * first and last covered day;
  * missing days and a "complet / incomplet" verdict, first up to the development
    end (2025-01-01) and then up to the last common closed bid/ask bar.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arty_trading.config.operational import load_config
from arty_trading.validation.dukascopy_import import read_manifest
from arty_trading.validation.market_calendar import MarketCalendar


def _latest_verified(entries: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Newest verified entry per (date, side); the manifest is append-only."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in entries:
        if row.get("status") != "verified" or not row.get("rows"):
            continue
        key = (row["date"], row["side"])
        if key not in latest or row.get("snapshot_cutoff", "") > latest[key].get(
            "snapshot_cutoff", ""
        ):
            latest[key] = row
    return latest


def _covered_days(latest: dict[tuple[str, str], dict[str, Any]]) -> tuple[set[str], set[str]]:
    bid = {day for day, side in latest if side == "bid"}
    ask = {day for day, side in latest if side == "ask"}
    return bid, ask


def _last_common_close(
    latest: dict[tuple[str, str], dict[str, Any]], common_days: set[str]
) -> datetime | None:
    """Newest timestamp present in both sides' CSVs, plus the source bar length."""
    seconds = load_config("data_import.yaml")["source_bar_seconds"]
    for day in sorted(common_days, reverse=True):
        sides: list[set[int]] = []
        present = True
        for side in ("bid", "ask"):
            path = Path(latest[(day, side)]["path"])
            if not path.exists():
                present = False
                break
            with path.open(newline="", encoding="utf-8") as stream:
                sides.append({int(row["timestamp"]) for row in csv.DictReader(stream)})
        if not present:
            continue
        common = sides[0] & sides[1]
        if common:
            return datetime.fromtimestamp(max(common) / 1000, UTC) + timedelta(seconds=seconds)
    return None


def _expected_closed_days(start: datetime, end: datetime) -> set[str]:
    """UTC days fully outside market hours, from the shared market calendar.

    Days that are entirely weekend, pause or a configured non-tradable holiday
    are expected-closed and must not be counted as missing files. A closed day
    that still carries a validated file is not an anomaly; _coverage reports it
    only as an informational counter.
    """
    if end <= start:
        return set()
    calendar = MarketCalendar()
    closed: set[str] = set()
    day = start.date()
    last = (end - timedelta(minutes=1)).date()
    while day <= last:
        if calendar.day_is_closed(day):
            closed.add(day.isoformat())
        day += timedelta(days=1)
    return closed


def _coverage(
    start: datetime, end: datetime, common_days: set[str], expected_closed: set[str]
) -> dict[str, Any]:
    if end <= start:
        return {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "expected_days": 0,
            "expected_open_days": 0,
            "expected_closed_days": 0,
            "closed_but_present_days": 0,
            "covered_days": 0,
            "missing_days": 0,
            "missing_examples": [],
            "closed_examples": [],
            "closed_but_present_examples": [],
            "verdict": "indisponible",
            "reason": "période vide (fin <= début)",
        }
    expected_open = 0
    expected_closed_count = 0
    closed_present = 0
    closed_present_examples: list[str] = []
    closed_examples: list[str] = []
    missing: list[str] = []
    day = start
    while day < end:
        key = day.date().isoformat()
        if key in expected_closed:
            expected_closed_count += 1
            if key in common_days:
                # Informational only: a closed day may legitimately carry a file.
                closed_present += 1
                if len(closed_present_examples) < 5:
                    closed_present_examples.append(key)
            if len(closed_examples) < 5:
                closed_examples.append(key)
        else:
            expected_open += 1
            if key not in common_days:
                missing.append(key)
        day += timedelta(days=1)
    return {
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "expected_days": expected_open + expected_closed_count,
        "expected_open_days": expected_open,
        "expected_closed_days": expected_closed_count,
        "closed_examples": closed_examples,
        "closed_but_present_days": closed_present,
        "closed_but_present_examples": closed_present_examples,
        "covered_days": expected_open - len(missing),
        "missing_days": len(missing),
        "missing_examples": missing[:10],
        "verdict": "complet" if not missing else "incomplet",
    }


def progress(directory: Path = Path("data/raw")) -> dict[str, Any]:
    manifest = directory / "import_manifest.jsonl"
    entries = read_manifest(manifest)
    latest = _latest_verified(entries)
    bid, ask = _covered_days(latest)
    common = bid & ask
    split = load_config("split.yaml")
    dev_start = datetime.fromisoformat(split["development"]["start"])
    dev_end = datetime.fromisoformat(split["development"]["end"])
    hold_start = datetime.fromisoformat(split["holdout"]["start"])
    last_close = _last_common_close(latest, common) if common else None
    # Compute the shared calendar range once, then select each report's interval.
    closed = _expected_closed_days(min(dev_start, hold_start), max(dev_end, last_close or dev_end))
    if last_close:
        holdout_block = _coverage(hold_start, last_close, common, closed)
        full_block = _coverage(dev_start, last_close, common, closed)
    else:
        holdout_block = {"verdict": "indisponible", "reason": "aucune clôture commune"}
        full_block = {"verdict": "indisponible", "reason": "aucune clôture commune"}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest),
        "verified_bid_days": len(bid),
        "verified_ask_days": len(ask),
        "verified_common_days": len(common),
        "first_covered_day": min(common) if common else None,
        "last_covered_day": max(common) if common else None,
        "last_common_close_utc": last_close.isoformat() if last_close else None,
        "closed_day_source": (
            "market_calendar: weekends, daily pause, configured non-tradable holidays"
        ),
        "development_to_2025_01_01": _coverage(dev_start, dev_end, common, closed),
        "holdout_to_last_common_close": holdout_block,
        "full_to_last_common_close": full_block,
        "pnl_inspected": False,
        "raw_data_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report")
    args = parser.parse_args()
    report = progress(args.data)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"Manifeste : {report['manifest']}")
    print(
        f"Jours validés  : bid={report['verified_bid_days']} "
        f"ask={report['verified_ask_days']} communs={report['verified_common_days']}"
    )
    print(
        f"Premier/dernier jour couvert : "
        f"{report['first_covered_day']} -> {report['last_covered_day']}"
    )
    print(f"Dernière clôture commune : {report['last_common_close_utc']}")
    for name in (
        "development_to_2025_01_01",
        "holdout_to_last_common_close",
        "full_to_last_common_close",
    ):
        block = report[name]
        if block.get("verdict") == "indisponible":
            print(f"[{name}] verdict=indisponible ({block.get('reason', '')})")
            continue
        print(
            f"[{name}] verdict={block['verdict']} "
            f"ouverts_attendus={block['expected_open_days']} "
            f"fermés_attendus={block['expected_closed_days']} "
            f"fermé_mais_présent={block['closed_but_present_days']} "
            f"présents={block['covered_days']} "
            f"fichiers_absents={block['missing_days']} "
            f"ex. {block['missing_examples'][:3]}"
        )


if __name__ == "__main__":
    main()
