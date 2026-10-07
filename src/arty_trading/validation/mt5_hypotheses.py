"""Offline comparison of three explicit MT5 server clock policies.

Scores measure timestamp agreement, never trading outcomes. Lower is better.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.mt5_csv import resolve_local
from arty_trading.validation.news_calendar import read_news_calendar

HYPOTHESES = ("eet_eu_dst", "eet_us_dst", "utc_plus_3_fixed")


def to_utc(local: datetime, hypothesis: str):
    if hypothesis == "eet_eu_dst":
        return resolve_local(local, ZoneInfo("Europe/Athens"))
    if hypothesis == "eet_us_dst":
        # Server = New York wall clock + 7h, i.e. UTC+2/+3 on US DST dates.
        return resolve_local(local - timedelta(hours=7), ZoneInfo("America/New_York"))
    if hypothesis == "utc_plus_3_fixed":
        return (local - timedelta(hours=3)).replace(tzinfo=UTC), None
    raise ValueError(f"Unknown server clock hypothesis: {hypothesis}")


def to_local(utc: datetime, hypothesis: str):
    if hypothesis == "eet_eu_dst":
        return utc.astimezone(ZoneInfo("Europe/Athens")).replace(tzinfo=None)
    if hypothesis == "eet_us_dst":
        return utc.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None) + timedelta(
            hours=7
        )
    if hypothesis == "utc_plus_3_fixed":
        return utc.replace(tzinfo=None) + timedelta(hours=3)
    raise ValueError(hypothesis)


def transition_sensitive(local: datetime):
    # Includes March/October/November divergence and 7 days around any change.
    for day in range(-7, 8):
        probe = local.replace(hour=12, minute=0) + timedelta(days=day)
        offsets = [
            (probe - to_utc(probe, name)[0].replace(tzinfo=None)).total_seconds()
            for name in HYPOTHESES[:2]
        ]
        if offsets[0] != offsets[1]:
            return True
    return False


def score_records(records):
    components = {}
    for kind in ("pause", "reopen", "news"):
        values = [min(abs(row["delta_minutes"]), 60) for row in records if row["kind"] == kind]
        components[kind] = {
            "samples": len(values),
            "mean_capped_abs_minutes": sum(values) / len(values) if values else None,
        }
    weights = {"pause": 0.35, "reopen": 0.35, "news": 0.30}
    available = [kind for kind in weights if components[kind]["samples"]]
    score = (
        (
            sum(weights[kind] * components[kind]["mean_capped_abs_minutes"] for kind in available)
            / sum(weights[kind] for kind in available)
        )
        if available
        else None
    )
    return {"score_minutes": score, "components": components}


def select_year(rows):
    ranked = sorted(
        (row for row in rows if row["score_minutes"] is not None),
        key=lambda row: row["score_minutes"],
    )
    if len(ranked) < 3:
        return {"selected": None, "reason": "insufficient_scored_hypotheses"}
    best, second = ranked[:2]
    margin = second["score_minutes"] - best["score_minutes"]
    transition = best["transition"]["components"]
    news = best["all"]["components"]["news"]
    enough = (
        transition["pause"]["samples"] >= 5
        and transition["reopen"]["samples"] >= 5
        and news["samples"] >= 5
    )
    selected = (
        best["hypothesis"] if enough and margin >= 10 and best["score_minutes"] <= 15 else None
    )
    return {
        "selected": selected,
        "best": best["hypothesis"],
        "margin_minutes": margin,
        "reason": "clear_margin" if selected else "ambiguous_or_insufficient_transition_evidence",
    }


def compare_hypotheses(source: Path, news_path: Path):
    with source.open(encoding="utf-8-sig") as stream:
        separator = csv.Sniffer().sniff(stream.read(4096), delimiters=",;\t").delimiter
    frame = pd.read_csv(source, sep=separator)
    frame.columns = [column.strip("<>").upper() for column in frame.columns]
    frame.index = pd.DatetimeIndex(
        pd.to_datetime(frame.DATE + " " + frame.TIME, format="%Y.%m.%d %H:%M:%S")
    )
    duplicates = int(frame.index.duplicated().sum())
    frame = frame.loc[~frame.index.duplicated()].sort_index()
    stamps = frame.index
    # Select longest eligible intra-day gap independently of the hypotheses.
    pauses = {}
    for previous, current in zip(stamps[:-1], stamps[1:], strict=True):
        missing = (current - previous).total_seconds() / 60 - 1
        if 5 <= missing <= 180 and previous.dayofweek < 5:
            key = previous.date()
            if key not in pauses or missing > pauses[key][2]:
                pauses[key] = (
                    previous.to_pydatetime() + timedelta(minutes=1),
                    current.to_pydatetime(),
                    missing,
                )
    events = read_news_calendar(news_path, ["NFP", "FOMC", "CPI"])
    calendar = MarketCalendar()
    records = {name: [] for name in HYPOTHESES}
    ambiguities = []
    for hypothesis in HYPOTHESES:
        for start, end, missing in pauses.values():
            sensitive = transition_sensitive(start)
            for kind, local, expected_time in (
                ("pause", start, calendar.pause_start),
                ("reopen", end, calendar.pause_end),
            ):
                utc, issue = to_utc(local, hypothesis)
                if issue:
                    ambiguities.append(
                        {"hypothesis": hypothesis, "local": str(local), "kind": issue}
                    )
                    continue
                market_local = utc.astimezone(calendar.zone)
                expected = datetime.combine(market_local.date(), expected_time, calendar.zone)
                records[hypothesis].append(
                    {
                        "year": start.year,
                        "hypothesis": hypothesis,
                        "kind": kind,
                        "server_local": local.isoformat(),
                        "utc": utc.isoformat(),
                        "delta_minutes": (utc - expected.astimezone(UTC)).total_seconds() / 60,
                        "gap_missing_minutes": missing,
                        "transition_sensitive": sensitive,
                    }
                )
        for event in events:
            if event["type"] not in {"NFP", "FOMC"}:
                continue
            utc = datetime.fromisoformat(event["time_utc"])
            local = to_local(utc, hypothesis)
            window = frame.loc[local - timedelta(minutes=90) : local + timedelta(minutes=90)]
            if window.empty:
                continue
            peaks = window.loc[window.TICKVOL == window.TICKVOL.max()]
            if len(peaks) != 1:
                ambiguities.append(
                    {
                        "hypothesis": hypothesis,
                        "event_utc": utc.isoformat(),
                        "kind": "tied_volume_peaks",
                        "peak_count": len(peaks),
                    }
                )
                continue
            peak_local = peaks.index[0].to_pydatetime()
            peak_utc, issue = to_utc(peak_local, hypothesis)
            if issue:
                continue
            records[hypothesis].append(
                {
                    "year": utc.year,
                    "hypothesis": hypothesis,
                    "kind": "news",
                    "event_type": event["type"],
                    "event_utc": utc.isoformat(),
                    "server_local": peak_local.isoformat(),
                    "utc": peak_utc.isoformat(),
                    "tick_volume": int(peaks.TICKVOL.iloc[0]),
                    "delta_minutes": (peak_utc - utc).total_seconds() / 60,
                    "transition_sensitive": transition_sensitive(local),
                }
            )
    years = {}
    for year in sorted(set(stamps.year)):
        rows = []
        for hypothesis in HYPOTHESES:
            sample = [row for row in records[hypothesis] if row["year"] == year]
            all_score = score_records(sample)
            transition = score_records([row for row in sample if row["transition_sensitive"]])
            score = (
                (all_score["score_minutes"] + transition["score_minutes"]) / 2
                if all_score["score_minutes"] is not None
                and transition["score_minutes"] is not None
                else None
            )
            rows.append(
                {
                    "hypothesis": hypothesis,
                    "score_minutes": score,
                    "all": all_score,
                    "transition": transition,
                    "transition_by_month": {
                        str(month): score_records(
                            [
                                row
                                for row in sample
                                if row["transition_sensitive"]
                                and datetime.fromisoformat(row["server_local"]).month == month
                            ]
                        )
                        for month in (2, 3, 4, 10, 11)
                    },
                }
            )
        years[str(year)] = {"scores": rows, "decision": select_year(rows)}
    decisions = [row["decision"]["selected"] for row in years.values()]
    selected = (
        decisions[0]
        if decisions and all(value == decisions[0] and value for value in decisions)
        else None
    )
    return {
        "source": str(source),
        "source_rows": len(frame),
        "duplicates": duplicates,
        "years": years,
        "selected_hypothesis": selected,
        "ambiguous_years": [year for year, row in years.items() if not row["decision"]["selected"]],
        "mixed_policy_years": len(set(value for value in decisions if value)) > 1,
        "records": records,
        "ambiguities": ambiguities,
        "method": {
            "lower_is_better": True,
            "residual_cap_minutes": 60,
            "weights": {"pause": 0.35, "reopen": 0.35, "news": 0.30},
            "annual_score": "half all evidence + half transition-sensitive evidence",
            "clear_selection": "margin >=10; best <=15; >=5 transition gaps; >=5 annual news",
            "transition_sensitive": "EU/US offset divergence within +/-7 days",
            "pause_detection": "largest 5..180 missing-minute gap per weekday server date",
            "news_window_minutes": 90,
            "limitations": [
                "Scores are diagnostics, not statistical proof",
                "Peaks may lag news",
                "Broker pause duration can differ from reference calendar",
                "2026 incomplete; autumn DST transitions not yet observed",
            ],
        },
        "network": False,
        "backtest_run": False,
        "split_yaml_modified": False,
    }
