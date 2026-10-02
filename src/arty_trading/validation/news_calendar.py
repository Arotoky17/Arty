"""Strict UTC economic-event calendar with a per-row primary-source citation."""

import csv
from datetime import datetime, timedelta
from pathlib import Path


def read_news_calendar(path: Path, allowed_types: list[str]) -> list[dict[str, str]]:
    events = []
    seen = set()
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            time = datetime.fromisoformat(row["time_utc"])
            if time.tzinfo is None or time.utcoffset() != timedelta(0):
                raise ValueError("News calendar requires explicit UTC timestamps")
            if row["type"] not in allowed_types or not row["source_url"].startswith("https://"):
                raise ValueError("News calendar requires a configured type and source citation")
            if row["status"] not in ("released", "scheduled"):
                raise ValueError("Unknown news publication status")
            key = (row["type"], row["time_utc"])
            if key in seen:
                raise ValueError("Duplicate news event")
            seen.add(key)
            events.append(dict(row))
    if not events:
        raise ValueError("Configured news calendar is empty")
    return sorted(events, key=lambda event: event["time_utc"])
