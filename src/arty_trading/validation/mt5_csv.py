"""Convert MetaQuotes MT5 M1 CSV exports into the csv_import daily UTC contract.

No network. No backtest. Server timezone is never inferred for conversion.
"""

from __future__ import annotations

import csv
import io
import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from arty_trading.config.operational import CONFIG_ROOT, load_config
from arty_trading.validation.csv_import import FIELDS, read_m1_csv
from arty_trading.validation.dukascopy_import import decode_m1, digest
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.news_calendar import read_news_calendar

MT5_FIELDS = ("Date", "Time", "Open", "High", "Low", "Close", "TickVol", "Vol", "Spread")
PAUSE_GAP_MIN = 45
PAUSE_GAP_MAX = 90
NEWS_WINDOW_MINUTES = 60
COMPARE_START = datetime(2020, 1, 1, tzinfo=UTC)
COMPARE_END = datetime(2021, 1, 1, tzinfo=UTC)
DEFAULT_CANDIDATES = (
    "Europe/Athens",
    "Europe/Helsinki",
    "Europe/Vilnius",
    "Europe/Kiev",
    "Europe/Bucharest",
    "Europe/London",
    "UTC",
    "America/New_York",
)
DECISIONS_PENDING = [
    {
        "id": "server_timezone",
        "need": "IANA timezone used by MetaQuotes-Demo historically, including DST",
        "note": "Detection reports evidence only; convert never applies a guessed zone",
    },
    {
        "id": "point",
        "need": "MT5 point size for XAUUSD on this server (spread column units)",
        "note": "Ask is bid + spread * point on every OHLC of the bar",
    },
    {
        "id": "ohlc_spread_assumption",
        "need": "Confirm applying the bar Spread to Open/High/Low/Close, not close only",
    },
    {
        "id": "volume_field",
        "need": "CSV volume uses TickVol; Vol stays in the original MT5 export",
    },
    {
        "id": "dst_ambiguous_bars",
        "need": "Ambiguous/nonexistent local minutes are skipped, never fold-picked",
    },
    {
        "id": "store_separation",
        "need": "Keep converted files under data/external, separate from Dukascopy data/raw",
    },
    {
        "id": "reference_cost_model",
        "need": "Reconstructed ask is forbidden as the reference spread-calibration source",
    },
    {
        "id": "pre_2020_research",
        "need": "Development still starts 2020-01-01; 2015-2019 is outside the split",
        "note": "split.yaml was not modified",
    },
    {
        "id": "holdout_end",
        "need": "Hold-out end stays unset until a Dukascopy freeze; MT5 does not freeze it",
    },
    {
        "id": "volume_vs_dukascopy",
        "need": "TickVol is not the same unit as Dukascopy BI5 volume; compare as separate metrics",
    },
]


@dataclass(frozen=True)
class Mt5Bar:
    local: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    spread: Decimal
    source_line: int
    source_path: str


def load_zone(name: str) -> ZoneInfo:
    if not name or name.strip() != name:
        raise ValueError("Server timezone is required (IANA name, no guessing)")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown IANA timezone: {name}") from error


def _normalize_header(name: str) -> str:
    clean = name.replace("<", "").replace(">", "").replace("\ufeff", "").strip()
    return {field.upper(): field for field in MT5_FIELDS}.get(clean.upper(), clean)


def _parse_local(date_text: str, time_text: str) -> datetime:
    date_text = date_text.strip()
    time_text = time_text.strip()
    if len(date_text) == 10 and date_text[4] in {".", "-"}:
        try:
            return datetime.fromisoformat(date_text.replace(".", "-") + "T" + time_text)
        except ValueError:
            pass
    parsed_date = None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            parsed_date = datetime.strptime(date_text, fmt)
            break
        except ValueError:
            continue
    if parsed_date is None:
        raise ValueError(f"Unrecognised MT5 date: {date_text}")
    parsed_time = None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(time_text, fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError(f"Unrecognised MT5 time: {time_text}")
    return datetime.combine(parsed_date.date(), parsed_time)


def resolve_local(naive: datetime, zone: ZoneInfo) -> tuple[datetime | None, str | None]:
    """Map a naive server timestamp to UTC. Never pick a DST fold."""
    if naive.tzinfo is not None:
        raise ValueError("MT5 timestamps must be naive server-local values")
    candidates = {
        naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        for fold in (0, 1)
        if naive.replace(tzinfo=zone, fold=fold)
        .astimezone(UTC)
        .astimezone(zone)
        .replace(tzinfo=None)
        == naive
    }
    if not candidates:
        return None, "nonexistent_dst"
    if len(candidates) > 1:
        return None, "ambiguous_dst"
    return candidates.pop(), None


def read_mt5_export(path: Path) -> list[Mt5Bar]:
    """Read one MT5 History Center CSV (Date, Time, OHLC, TickVol, Vol, Spread)."""
    raw = path.read_bytes()
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    sample = text[:4096]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = [_normalize_header(cell) for cell in next(reader)]
    except StopIteration as error:
        raise ValueError(f"{path}: empty MT5 export") from error
    if tuple(header) != MT5_FIELDS:
        raise ValueError(f"{path}: expected header {list(MT5_FIELDS)}, found {header}")
    bars: list[Mt5Bar] = []
    for index, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(MT5_FIELDS):
            raise ValueError(
                f"{path}:{index}: expected {len(MT5_FIELDS)} columns, found {len(row)}"
            )
        mapped = dict(zip(MT5_FIELDS, (cell.strip() for cell in row), strict=True))
        prices = [float(mapped[name]) for name in ("Open", "High", "Low", "Close")]
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError(f"{path}:{index}: non-finite or non-positive price")
        if prices[2] > min(prices[0], prices[3]) or prices[1] < max(prices[0], prices[3]):
            raise ValueError(f"{path}:{index}: OHLC bounds violated")
        tick_volume = int(mapped["TickVol"])
        real_volume = int(float(mapped["Vol"] or 0))
        if tick_volume < 0 or real_volume < 0:
            raise ValueError(f"{path}:{index}: negative volume")
        spread = Decimal(mapped["Spread"])
        if not spread.is_finite() or spread < 0:
            raise ValueError(f"{path}:{index}: negative spread")
        bars.append(
            Mt5Bar(
                local=_parse_local(mapped["Date"], mapped["Time"]),
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                tick_volume=tick_volume,
                real_volume=real_volume,
                spread=spread,
                source_line=index,
                source_path=str(path),
            )
        )
    if not bars:
        raise ValueError(f"{path}: no M1 rows")
    return bars


def collect_mt5_bars(source: Path) -> list[Mt5Bar]:
    if source.is_file():
        return read_mt5_export(source)
    if not source.is_dir():
        raise ValueError(f"MT5 export not found: {source}")
    files = sorted(path for path in source.rglob("*.csv") if path.is_file())
    if not files:
        raise ValueError(f"No MT5 CSV under {source}")
    bars: list[Mt5Bar] = []
    for path in files:
        bars.extend(read_mt5_export(path))
    return bars


def reconstruct_ask(bar: Mt5Bar, point: Decimal) -> dict[str, float]:
    shift = float(bar.spread * point)
    return {
        "open": bar.open + shift,
        "high": bar.high + shift,
        "low": bar.low + shift,
        "close": bar.close + shift,
    }


def _canonical_day(rows: list[dict[str, Any]], day: str) -> bytes:
    payload, _ = read_m1_csv(_encode_rows(rows), day)
    return payload


def _encode_rows(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda item: int(item["timestamp"])):
        writer.writerow(row)
    return buffer.getvalue().encode()


def convert_mt5(
    bars: list[Mt5Bar],
    *,
    zone_name: str,
    point: Decimal | None,
    output: Path,
    symbol: str = "XAUUSD",
    broker: str = "MetaQuotes-Demo",
    bid_only: bool = False,
) -> dict[str, Any]:
    """Write one csv_import file per UTC day and side. Ask is reconstructed."""
    if (point is None and not bid_only) or (
        point is not None and (point <= 0 or not point.is_finite())
    ):
        raise ValueError("point must be a positive finite decimal")
    ask_origin = "unavailable_pending_point" if bid_only else "reconstructed_ask"
    zone = load_zone(zone_name)
    issues: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_local: dict[datetime, Mt5Bar] = {}
    by_day: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"bid": [], "ask": []})
    for bar in bars:
        prior = seen_local.get(bar.local)
        if prior is not None:
            duplicates.append(
                {
                    "local": bar.local.isoformat(sep=" "),
                    "first_line": prior.source_line,
                    "first_source": prior.source_path,
                    "duplicate_source": bar.source_path,
                    "conflicting": (
                        prior.open,
                        prior.high,
                        prior.low,
                        prior.close,
                        prior.tick_volume,
                        prior.spread,
                    )
                    != (bar.open, bar.high, bar.low, bar.close, bar.tick_volume, bar.spread),
                    "duplicate_line": bar.source_line,
                }
            )
            continue
        seen_local[bar.local] = bar
        utc, issue = resolve_local(bar.local, zone)
        if utc is None:
            issues.append(
                {
                    "kind": issue,
                    "local": bar.local.isoformat(sep=" "),
                    "line": bar.source_line,
                    "source": bar.source_path,
                }
            )
            continue
        if utc.second or utc.microsecond:
            issues.append(
                {
                    "kind": "not_minute_aligned",
                    "local": bar.local.isoformat(sep=" "),
                    "utc": utc.isoformat(),
                    "line": bar.source_line,
                }
            )
            continue
        day = utc.date().isoformat()
        stamp = int(utc.timestamp() * 1000)
        bid = {
            "timestamp": stamp,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.tick_volume,
        }
        by_day[day]["bid"].append(bid)
        if not bid_only:
            ask_prices = reconstruct_ask(bar, point)
            ask = {"timestamp": stamp, "volume": bar.tick_volume, **ask_prices}
            by_day[day]["ask"].append(ask)
    output = Path(output)
    if (
        output.resolve() == Path("data/raw").resolve()
        or (output / "import_manifest.jsonl").exists()
    ):
        raise ValueError("Use a separate MT5 conversion directory, never the raw import store")
    for day, sides in by_day.items():
        for side, rows in sides.items():
            if not rows:
                continue
            target = output / symbol.lower() / side / f"{day}.csv"
            if target.exists() and target.read_bytes() != _canonical_day(rows, day):
                raise ValueError(f"Refusing to overwrite different converted data: {target}")
    written: list[dict[str, Any]] = []
    journal_path = output / "conversion_manifest.jsonl"
    output.mkdir(parents=True, exist_ok=True)
    with journal_path.open("w", encoding="utf-8") as journal:
        for day in sorted(by_day):
            for side in ("bid",) if bid_only else ("bid", "ask"):
                payload = _canonical_day(by_day[day][side], day)
                target = output / symbol.lower() / side / f"{day}.csv"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                origin = "mt5_bid" if side == "bid" else "reconstructed_ask"
                entry = {
                    "date": day,
                    "side": side,
                    "provider": "mt5_csv",
                    "quote_origin": origin,
                    "ask_origin": ask_origin,
                    "broker": broker,
                    "server_timezone": zone_name,
                    "point": None if point is None else str(point),
                    "path": str(target),
                    "sha256": digest(payload),
                    "rows": len(by_day[day][side]),
                    "status": "converted",
                    "separated_from_dukascopy": True,
                    "allowed_for_reference_cost_model": False,
                }
                journal.write(json.dumps(entry) + "\n")
                written.append(entry)
    provenance = {
        "provider": "mt5_csv",
        "broker": broker,
        "symbol": symbol,
        "server_timezone": zone_name,
        "point": None if point is None else str(point),
        "bid_origin": "mt5_bid",
        "ask_origin": ask_origin,
        "ask_reconstruction": None
        if bid_only
        else "bid + spread * point; same spread applied to OHLC",
        "bid_only": bid_only,
        "volume_field": "TickVol",
        "separated_from_dukascopy": True,
        "allowed_for_reference_cost_model": False,
        "dst_policy": "skip_ambiguous_and_nonexistent_local_minutes",
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return {
        "provider": "mt5_csv",
        "output": str(output),
        "server_timezone": zone_name,
        "point": None if point is None else str(point),
        "source_bars": len(bars),
        "converted_daily_sides": len(written),
        "utc_days": sorted(by_day),
        "duplicates_skipped": duplicates,
        "dst_issues": issues,
        "ask_origin": ask_origin,
        "allowed_for_reference_cost_model": False,
        "separated_from_dukascopy": True,
        "split_yaml_modified": False,
        "network": False,
        "backtest_run": False,
        "decisions_pending": DECISIONS_PENDING,
        "provenance": provenance,
    }


def _sorted_locals(bars: list[Mt5Bar]) -> list[datetime]:
    return sorted({bar.local.replace(second=0, microsecond=0) for bar in bars})


def daily_reopen_evidence(bars: list[Mt5Bar]) -> dict[str, Any]:
    """Longest typical weekday holes in naive server time, by year. No timezone guess."""
    stamps = _sorted_locals(bars)
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(stamps, stamps[1:], strict=False):
        missing = int((current - previous).total_seconds() // 60) - 1
        if PAUSE_GAP_MIN <= missing <= PAUSE_GAP_MAX:
            start = previous + timedelta(minutes=1)
            gaps.append(
                {
                    "local_start": start.isoformat(sep=" "),
                    "length_minutes": missing,
                    "year": start.year,
                    "clock": start.strftime("%H:%M"),
                    "weekday": start.weekday(),
                }
            )
    by_year: dict[str, Any] = {}
    ambiguities: list[dict[str, Any]] = []
    years = sorted({bar.local.year for bar in bars})
    for year in years:
        clocks = Counter(row["clock"] for row in gaps if row["year"] == year)
        ranked = clocks.most_common()
        modal = ranked[0][0] if ranked else None
        tied = []
        if ranked:
            top = ranked[0][1]
            tied = [clock for clock, count in ranked if count >= max(1, int(0.8 * top))]
            if len(tied) > 1:
                ambiguities.append(
                    {
                        "year": year,
                        "kind": "multimodal_daily_pause_clock",
                        "clocks": tied,
                        "counts": dict(ranked[:5]),
                    }
                )
        if not ranked:
            ambiguities.append({"year": year, "kind": "no_weekday_pause_gap_in_45_90_minutes"})
        by_year[str(year)] = {
            "pause_gap_samples": sum(clocks.values()),
            "modal_local_clock": modal,
            "clock_counts": dict(ranked[:8]),
            "tied_clocks": tied,
        }
    return {
        "pause_gap_minutes_accepted": [PAUSE_GAP_MIN, PAUSE_GAP_MAX],
        "years": by_year,
        "ambiguities": ambiguities,
        "sample_gaps": gaps[:20],
    }


def _peak_in_window(
    bars: list[Mt5Bar], start: datetime, end: datetime
) -> tuple[Mt5Bar | None, list[Mt5Bar]]:
    window = [bar for bar in bars if start <= bar.local <= end]
    if not window:
        return None, []
    peak_volume = max(bar.tick_volume for bar in window)
    peaks = [bar for bar in window if bar.tick_volume == peak_volume]
    return peaks[0], peaks


def news_peak_evidence(
    bars: list[Mt5Bar],
    zone_name: str,
    events: list[dict[str, str]],
) -> dict[str, Any]:
    zone = load_zone(zone_name)
    rows: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    year_deltas: dict[int, list[float]] = defaultdict(list)
    bars = sorted(bars, key=lambda bar: bar.local)
    local_stamps = [bar.local for bar in bars]
    for event in events:
        if event["type"] not in {"NFP", "FOMC"}:
            continue
        event_utc = datetime.fromisoformat(event["time_utc"])
        local_event = event_utc.astimezone(zone).replace(tzinfo=None)
        peak, peaks = _peak_in_window(
            bars[
                bisect_left(
                    local_stamps, local_event - timedelta(minutes=NEWS_WINDOW_MINUTES)
                ) : bisect_right(local_stamps, local_event + timedelta(minutes=NEWS_WINDOW_MINUTES))
            ],
            local_event - timedelta(minutes=NEWS_WINDOW_MINUTES),
            local_event + timedelta(minutes=NEWS_WINDOW_MINUTES),
        )
        if peak is None:
            ambiguities.append(
                {
                    "kind": "no_bars_in_news_window",
                    "event": event["type"],
                    "time_utc": event["time_utc"],
                    "zone": zone_name,
                }
            )
            continue
        if len(peaks) > 1:
            ambiguities.append(
                {
                    "kind": "tied_volume_peaks",
                    "event": event["type"],
                    "time_utc": event["time_utc"],
                    "zone": zone_name,
                    "locals": [item.local.isoformat(sep=" ") for item in peaks],
                }
            )
        peak_utc, issue = resolve_local(peak.local, zone)
        if peak_utc is None:
            ambiguities.append(
                {
                    "kind": issue,
                    "event": event["type"],
                    "time_utc": event["time_utc"],
                    "local": peak.local.isoformat(sep=" "),
                }
            )
            continue
        delta = (peak_utc - event_utc).total_seconds() / 60
        year_deltas[event_utc.year].append(delta)
        rows.append(
            {
                "type": event["type"],
                "event_utc": event["time_utc"],
                "peak_local": peak.local.isoformat(sep=" "),
                "peak_utc": peak_utc.isoformat(),
                "delta_minutes": delta,
                "tick_volume": peak.tick_volume,
                "tied_peaks": len(peaks),
            }
        )
    by_year = {
        str(year): {
            "events_scored": len(values),
            "median_delta_minutes": sorted(values)[len(values) // 2] if values else None,
            "abs_median_delta_minutes": sorted(abs(value) for value in values)[len(values) // 2]
            if values
            else None,
        }
        for year, values in sorted(year_deltas.items())
    }
    return {
        "zone": zone_name,
        "window_minutes": NEWS_WINDOW_MINUTES,
        "events": rows,
        "years": by_year,
        "ambiguities": ambiguities,
    }


def reopen_vs_market_calendar(bars: list[Mt5Bar], zone_name: str) -> dict[str, Any]:
    zone = load_zone(zone_name)
    calendar = MarketCalendar()
    ny = ZoneInfo("America/New_York")
    pause = time.fromisoformat(str(calendar.config["daily_pause_start"]))
    evidence = daily_reopen_evidence(bars)
    year_offsets: dict[str, Any] = {}
    ambiguities: list[dict[str, Any]] = list(evidence["ambiguities"])
    stamps = _sorted_locals(bars)
    gap_starts: list[datetime] = []
    for previous, current in zip(stamps, stamps[1:], strict=False):
        missing = int((current - previous).total_seconds() // 60) - 1
        if PAUSE_GAP_MIN <= missing <= PAUSE_GAP_MAX:
            gap_starts.append(previous + timedelta(minutes=1))
    for year in sorted({bar.local.year for bar in bars}):
        offsets: list[float] = []
        reasons: Counter[str] = Counter()
        skipped = 0
        for start in gap_starts:
            if start.year != year:
                continue
            utc, issue = resolve_local(start, zone)
            if utc is None:
                skipped += 1
                ambiguities.append({"year": year, "kind": issue, "local": start.isoformat(sep=" ")})
                continue
            local_ny = utc.astimezone(ny)
            expected = datetime.combine(local_ny.date(), pause, ny)
            offsets.append((utc - expected.astimezone(UTC)).total_seconds() / 60)
            reasons[str(calendar.state(utc)["reason"])] += 1
        unique = sorted(set(offsets))
        if len(unique) > 3:
            ambiguities.append(
                {
                    "year": year,
                    "kind": "reopen_offsets_not_stable",
                    "unique_offset_count": len(unique),
                }
            )
        year_offsets[str(year)] = {
            "samples": len(offsets),
            "dst_skipped": skipped,
            "median_offset_minutes_from_ny_pause": sorted(offsets)[len(offsets) // 2]
            if offsets
            else None,
            "unique_offsets_minutes": unique[:12],
            "calendar_reason_counts": dict(reasons),
        }
    return {
        "zone": zone_name,
        "market_calendar_timezone": calendar.config["timezone"],
        "expected_daily_pause_local": calendar.config["daily_pause_start"],
        "years": year_offsets,
        "naive_pause_clocks": evidence["years"],
        "ambiguities": ambiguities,
    }


def detect_timezone(
    bars: list[Mt5Bar],
    *,
    candidates: tuple[str, ...] = DEFAULT_CANDIDATES,
    news_path: Path | None = None,
) -> dict[str, Any]:
    """Score candidate zones. Never selects one."""
    news_path = news_path or (CONFIG_ROOT / "news_calendar.csv")
    events = read_news_calendar(news_path, ["NFP", "FOMC", "CPI"])
    naive = daily_reopen_evidence(bars)
    ranked: list[dict[str, Any]] = []
    for name in candidates:
        reopen = reopen_vs_market_calendar(bars, name)
        news = news_peak_evidence(bars, name, events)
        reopen_medians = [
            row["median_offset_minutes_from_ny_pause"]
            for row in reopen["years"].values()
            if row["median_offset_minutes_from_ny_pause"] is not None
        ]
        news_medians = [
            row["abs_median_delta_minutes"]
            for row in news["years"].values()
            if row["abs_median_delta_minutes"] is not None
        ]
        ranked.append(
            {
                "zone": name,
                "abs_median_reopen_offset_minutes": sorted(abs(value) for value in reopen_medians)[
                    len(reopen_medians) // 2
                ]
                if reopen_medians
                else None,
                "abs_median_news_delta_minutes": sorted(news_medians)[len(news_medians) // 2]
                if news_medians
                else None,
                "reopen": reopen,
                "news": {
                    "events": news["events"],
                    "years": news["years"],
                    "ambiguities": news["ambiguities"],
                    "event_count": len(news["events"]),
                },
            }
        )
    scored = [
        row
        for row in ranked
        if row["abs_median_reopen_offset_minutes"] is not None
        or row["abs_median_news_delta_minutes"] is not None
    ]

    def sort_key(row: dict[str, Any]) -> tuple[float, float]:
        reopen = row["abs_median_reopen_offset_minutes"]
        news = row["abs_median_news_delta_minutes"]
        return (
            float("inf") if reopen is None else abs(reopen),
            float("inf") if news is None else abs(news),
        )

    scored.sort(key=sort_key)
    ambiguities: list[dict[str, Any]] = list(naive["ambiguities"])
    for year in sorted({bar.local.year for bar in bars}):
        if not any(
            datetime.fromisoformat(event["time_utc"]).year == year
            and event["type"] in {"NFP", "FOMC"}
            for event in events
        ):
            ambiguities.append({"year": year, "kind": "no_NFP_FOMC_calendar_evidence"})
    for candidate in ranked:
        ambiguities.extend(candidate["reopen"]["ambiguities"])
        ambiguities.extend(candidate["news"]["ambiguities"])

    if len(scored) >= 2:
        best = sort_key(scored[0])
        tied = [row["zone"] for row in scored if sort_key(row) == best]
        close = [
            row["zone"]
            for row in scored
            if all(
                abs((sort_key(row)[index] or 0) - (best[index] or 0)) <= 5
                for index in range(2)
                if best[index] != float("inf")
            )
        ]
        if len(tied) > 1:
            ambiguities.append({"kind": "tied_best_candidate_zones", "zones": tied})
        elif len(close) > 1:
            ambiguities.append(
                {
                    "kind": "candidate_zones_within_5_minutes",
                    "zones": close,
                    "note": "Not selected; convert still requires an explicit --server-timezone",
                }
            )
    if not scored:
        ambiguities.append({"kind": "no_candidate_produced_measurable_evidence"})
    offsets_by_zone = {}
    for name in candidates:
        zone = load_zone(name)
        yearly = defaultdict(set)
        for local in {bar.local.replace(day=1, hour=12, minute=0, second=0) for bar in bars}:
            yearly[str(local.year)].add(
                int(local.replace(tzinfo=zone).utcoffset().total_seconds() / 60)
            )
        offsets_by_zone[name] = {year: sorted(values) for year, values in yearly.items()}
    return {
        "selected_zone": None,
        "yearly_candidate_utc_offsets_minutes": offsets_by_zone,
        "limitations": [
            "Volume peaks are evidence, not proof of server timezone",
            "Candidate IANA zones do not cover arbitrary historical broker timezone changes",
        ],
        "naive_daily_pause": naive["years"],
        "candidates": ranked,
        "ranked_without_selection": [
            {
                "zone": row["zone"],
                "abs_median_reopen_offset_minutes": row["abs_median_reopen_offset_minutes"],
                "abs_median_news_delta_minutes": row["abs_median_news_delta_minutes"],
            }
            for row in scored
        ],
        "ambiguities": ambiguities,
        "decisions_pending": [
            item for item in DECISIONS_PENDING if item["id"] == "server_timezone"
        ],
        "split_yaml_modified": False,
    }


def _utc_index(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["timestamp"]): row for row in rows}


def depth_report(converted_root: Path, *, symbol: str = "XAUUSD") -> dict[str, Any]:
    """Gaps, duplicates, bars outside the configured market, split coverage. Read-only."""
    split = load_config("split.yaml")
    calendar = MarketCalendar()
    holes: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    duplicates = 0
    days: dict[str, int] = {}
    first: datetime | None = None
    last: datetime | None = None
    bid_root = converted_root / symbol.lower() / "bid"
    if not bid_root.is_dir():
        raise ValueError(f"No converted bid CSVs under {converted_root}")
    import pandas as pd

    observed: set[int] = set()
    for path in sorted(bid_root.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as stream:
            stamps = [int(row["timestamp"]) for row in csv.DictReader(stream)]
        duplicates += len(stamps) - len(set(stamps))
        days[path.stem] = len(set(stamps))
        observed.update(stamps)
    if observed:
        first = datetime.fromtimestamp(min(observed) / 1000, UTC)
        last = datetime.fromtimestamp(max(observed) / 1000, UTC)
    missing_minutes = 0
    expected_minutes = 0
    cursor = first.replace(hour=0, minute=0) if first else None
    while cursor is not None and cursor <= last:
        grid = pd.date_range(cursor, periods=1440, freq="min")
        tradable = ~calendar.annotate(pd.DataFrame(index=grid)).non_tradable
        for instant, opened in zip(grid, tradable, strict=True):
            stamp = int(instant.timestamp() * 1000)
            if not first <= instant <= last:
                continue
            expected_minutes += int(opened)
            if opened and stamp not in observed:
                missing_minutes += 1
                if len(holes) < 50:
                    holes.append({"utc": instant.isoformat()})
            elif not opened and stamp in observed:
                outside.append({"utc": instant.isoformat()})
        cursor += timedelta(days=1)
    windows = {
        "development": (
            datetime.fromisoformat(split["development"]["start"]),
            datetime.fromisoformat(split["development"]["end"]),
        ),
        "development_2020_2024": (
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
        ),
        "holdout": (
            datetime.fromisoformat(split["holdout"]["start"]),
            datetime.fromisoformat(split["holdout"]["end"])
            if split["holdout"]["end"]
            else (last + timedelta(minutes=1) if last else None),
        ),
    }
    coverage: dict[str, Any] = {}
    for name, (start, end) in windows.items():
        if end is None or end <= start:
            coverage[name] = {
                "status": "unavailable",
                "reason": "holdout end is null; split.yaml was not modified",
            }
            continue
        expected = 0
        present = 0
        day = start
        while day < end:
            key = day.date().isoformat()
            if not calendar.day_is_closed(day.date()):
                expected += 1
                present += int(key in days)
            day += timedelta(days=1)
        coverage[name] = {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "provisional_end": name == "holdout" and split["holdout"]["end"] is None,
            "open_utc_days_expected": expected,
            "open_utc_days_with_bid": present,
            "missing_open_days": expected - present,
        }
    return {
        "first_utc": first.isoformat() if first else None,
        "last_utc": last.isoformat() if last else None,
        "bid_days": len(days),
        "duplicate_timestamps": duplicates,
        "missing_open_minutes_within_observed_span": missing_minutes,
        "expected_open_minutes_within_observed_span": expected_minutes,
        "holes_examples": holes[:50],
        "bars_outside_market_examples_capped": len(outside),
        "bars_outside_market_examples": outside[:50],
        "coverage": coverage,
        "holdout_end_in_split": split["holdout"]["end"],
        "split_yaml_modified": False,
        "ask_origin": json.loads((converted_root / "provenance.json").read_text()).get("ask_origin")
        if (converted_root / "provenance.json").exists()
        else "unverified",
        "allowed_for_reference_cost_model": False,
    }


def _load_dukascopy_bid_day(raw_root: Path, day: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    csv_path = raw_root / "xauusd" / "bid" / "m1" / f"{day}.csv"
    if csv_path.exists():
        reader = csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8")))
        return list(reader)
    bi5 = raw_root / "xauusd" / "bid" / "bi5" / f"{day}.bi5"
    if not bi5.exists():
        return []
    opened = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    payload, _ = decode_m1(bi5.read_bytes(), opened, datetime(2099, 1, 1, tzinfo=UTC), cfg)
    return list(csv.DictReader(io.StringIO(payload.decode())))


def compare_mt5_to_dukascopy(
    converted_root: Path,
    dukascopy_root: Path,
    *,
    start: datetime = COMPARE_START,
    end: datetime = COMPARE_END,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    """Bid-only comparison on an already-imported local Dukascopy range. No network."""
    cfg = load_config("data_import.yaml")
    close_abs: list[float] = []
    open_abs: list[float] = []
    high_abs: list[float] = []
    low_abs: list[float] = []
    volume_pairs: list[tuple[float, float]] = []
    only_mt5 = 0
    only_duka = 0
    matched = 0
    days_compared = 0
    days_missing_duka = []
    days_missing_mt5 = []
    day = start
    while day < end:
        key = day.date().isoformat()
        mt5_path = converted_root / symbol.lower() / "bid" / f"{key}.csv"
        duka_rows = _load_dukascopy_bid_day(dukascopy_root, key, cfg)
        if not mt5_path.exists():
            if duka_rows:
                days_missing_mt5.append(key)
            day += timedelta(days=1)
            continue
        mt5_rows = list(csv.DictReader(mt5_path.open(encoding="utf-8", newline="")))
        if not duka_rows:
            days_missing_duka.append(key)
            day += timedelta(days=1)
            continue
        days_compared += 1
        left = _utc_index(mt5_rows)
        right = _utc_index(duka_rows)
        only_mt5 += len(set(left) - set(right))
        only_duka += len(set(right) - set(left))
        for stamp in set(left) & set(right):
            matched += 1
            high_abs.append(abs(float(left[stamp]["high"]) - float(right[stamp]["high"])))
            low_abs.append(abs(float(left[stamp]["low"]) - float(right[stamp]["low"])))
            close_abs.append(abs(float(left[stamp]["close"]) - float(right[stamp]["close"])))
            open_abs.append(abs(float(left[stamp]["open"]) - float(right[stamp]["open"])))
            volume_pairs.append((float(left[stamp]["volume"]), float(right[stamp]["volume"])))
        day += timedelta(days=1)

    def _summary(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"count": 0, "median": None, "p90": None, "max": None}
        ordered = sorted(values)
        return {
            "count": len(ordered),
            "median": ordered[len(ordered) // 2],
            "p90": ordered[int(0.9 * (len(ordered) - 1))],
            "max": ordered[-1],
        }

    correlation = None
    if len(volume_pairs) >= 3:
        mt5_vol = [pair[0] for pair in volume_pairs]
        duka_vol = [pair[1] for pair in volume_pairs]
        mean_m = sum(mt5_vol) / len(mt5_vol)
        mean_d = sum(duka_vol) / len(duka_vol)
        cov = sum((a - mean_m) * (b - mean_d) for a, b in volume_pairs)
        var_m = sum((a - mean_m) ** 2 for a in mt5_vol)
        var_d = sum((b - mean_d) ** 2 for b in duka_vol)
        if var_m and var_d:
            correlation = cov / (var_m**0.5 * var_d**0.5)
    return {
        "period_start": start.isoformat(),
        "period_end_exclusive": end.isoformat(),
        "days_compared": days_compared,
        "days_missing_mt5": days_missing_mt5[:50],
        "days_missing_dukascopy": days_missing_duka[:50],
        "matched_minutes": matched,
        "mt5_only_minutes": only_mt5,
        "dukascopy_only_minutes": only_duka,
        "abs_close_diff": _summary(close_abs),
        "abs_open_diff": _summary(open_abs),
        "abs_high_diff": _summary(high_abs),
        "abs_low_diff": _summary(low_abs),
        "timing": "Exact UTC minute alignment; unmatched minutes counted per provider",
        "volume": {
            "units": "TickVol vs Dukascopy BI5 volume; not the same metric",
            "pairs": len(volume_pairs),
            "pearson_correlation": correlation,
        },
        "network": False,
        "backtest_run": False,
        "split_yaml_modified": False,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def reconstructed_ask_forbidden(provenance: dict[str, Any]) -> None:
    blob = json.dumps(provenance, default=str)
    if provenance.get("ask_origin") == "reconstructed_ask" or "reconstructed_ask" in blob:
        raise ValueError(
            "Reconstructed MT5 ask (bid + spread * point) cannot calibrate the reference cost model"
        )
