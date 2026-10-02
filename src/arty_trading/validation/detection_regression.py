"""Enumerate every detection difference, preserving undocumented differences for review."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arty_trading.config.operational import load_config
from arty_trading.validation.detection_review import collect_events, dev_candles, event_key
from arty_trading.validation.market_data import safe_output, write_report


def compare_events(
    before: list[dict[str, Any]], after: list[dict[str, Any]], rules: dict[str, Any]
) -> dict[str, Any]:
    old = {event_key(item): item for item in before}
    new = {event_key(item): item for item in after}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        category = key[0]
        rule = rules.get(category, {})
        if key not in old or key not in new:
            reason = rule.get("membership")
            changes.append(
                {
                    "kind": "added" if key not in old else "removed",
                    "key": list(key),
                    "documented_rule": reason,
                    "undocumented": reason is None,
                    "before": old.get(key),
                    "after": new.get(key),
                }
            )
            continue
        fields = old[key]["details"].keys() | new[key]["details"].keys()
        different = [
            field
            for field in sorted(fields)
            if old[key]["details"].get(field) != new[key]["details"].get(field)
        ]
        if different:
            unknown = [field for field in different if field not in rule.get("fields", [])]
            changes.append(
                {
                    "kind": "metadata",
                    "key": list(key),
                    "fields": different,
                    "undocumented_fields": unknown,
                    "undocumented": bool(unknown),
                    "documented_rule": rule.get("membership"),
                    "before": old[key],
                    "after": new[key],
                }
            )
    undocumented = [row for row in changes if row["undocumented"]]
    return {
        "before_count": len(before),
        "after_count": len(after),
        "before_by_type": dict(Counter(row["category"] for row in before)),
        "after_by_type": dict(Counter(row["category"] for row in after)),
        "changes_by_type": dict(Counter(row["key"][0] for row in changes)),
        "changes": changes,
        "undocumented_differences": undocumented,
        "status": "needs_review" if undocumented else "no_undocumented_differences",
        "classification_limit": (
            "Declared change coverage, not causal proof; review membership changes visually"
        ),
    }


def run_regression(directory: Path, symbol: str, output: Path) -> dict[str, Any]:
    if symbol != "XAUUSD":
        raise ValueError("Pinned pre-refactor regression is defined for XAUUSD only")
    safe_output(directory, output)
    cfg = load_config("validation.yaml")
    candles, provenance = dev_candles(directory, symbol)
    start, end = (
        datetime.fromisoformat(cfg["regression"][key])
        for key in ("reference_start", "reference_end")
    )
    candles = [c for c in candles if start <= c.time < end]
    if {c.time.strftime("%Y-%m") for c in candles} != {f"2024-{m:02d}" for m in range(1, 7)}:
        raise ValueError("Regression requires all six XAUUSD months January-June 2024")
    before, source_hashes = collect_events(candles, cfg, legacy=True, progress=True)
    after, _ = collect_events(candles, cfg, progress=True)
    report = compare_events(before, after, load_config("detection_changes.yaml")["rules"])
    report.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "period": [start.isoformat(), end.isoformat()],
            "config": cfg,
            "reference_source_hashes": source_hashes,
            "provenance": provenance,
            "reference_revision": cfg["regression"]["reference_revision"],
            "pnl_inspected": False,
            "backtest_run": False,
            "method": (
                "Same dev bars, overlapping windows, unique origin/type/direction/price; "
                "frozen reference profiles"
            ),
        }
    )
    write_report(output / "detection_regression.json", report)
    write_report(
        output / "undocumented_differences.json",
        {"differences": report["undocumented_differences"]},
    )
    lines = [
        "# XAUUSD detector non-regression",
        "",
        f"Status: {report['status']}",
        f"Before: {len(before)}; after: {len(after)}; "
        f"undocumented: {len(report['undocumented_differences'])}",
        "",
        "| Type | Before | After | Changes |",
        "|---|---:|---:|---:|",
    ]
    for category in sorted(report["before_by_type"].keys() | report["after_by_type"].keys()):
        lines.append(
            f"| {category} | {report['before_by_type'].get(category, 0)} | "
            f"{report['after_by_type'].get(category, 0)} | "
            f"{report['changes_by_type'].get(category, 0)} |"
        )
    lines += [
        "",
        "Every difference is retained in detection_regression.json.",
        "Membership rules document scope; they do not prove an individual event's cause.",
        "No P&L or backtest. Human review remains required.",
    ]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return report
