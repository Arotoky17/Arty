"""Setup 1 launch gate: user approval plus a signed complete market-data audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arty_trading.config.operational import load_config


def configuration_sha256() -> str:
    setup = load_config("setup1_preregistration.yaml")
    payload: dict[str, Any] = {
        "setup": {key: setup[key] for key in ("setup_id", "phase", "strategy", "validation")}
    }
    payload["statistical_power"] = setup["statistical_power"]
    payload["visual_review_requirements"] = {
        key: setup["visual_review"][key] for key in ("required_types", "timeframe")
    }
    for name in (
        "definitions.yaml",
        "split.yaml",
        "execution.yaml",
        "market_calendar.yaml",
        "diagnostics.yaml",
        "spread_calibration.yaml",
        "validation.yaml",
    ):
        payload[name] = load_config(name)
    calendar = payload["execution.yaml"]["cost"].get("news_calendar_path")
    payload["annual_spread_calibrations"] = {
        str(year): load_config(path)
        for year, path in payload["execution.yaml"]["cost"].get(
            "annual_calibration_paths", {}
        ).items()
    }
    if calendar:
        from arty_trading.config.operational import CONFIG_ROOT

        path = Path(calendar)
        if not path.is_absolute():
            path = CONFIG_ROOT / path
        payload["news_calendar_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload["preregistration_markdown_sha256"] = hashlib.sha256(
        Path("preregistration.md").read_bytes()
    ).hexdigest()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def assert_setup_run_allowed(setup_id: str, *, holdout: bool = False) -> None:
    setup = load_config("setup1_preregistration.yaml")
    if setup_id != setup["setup_id"]:
        return
    approval = setup["approval"]
    data = setup["data"]
    if holdout and approval["holdout_status"] != "approved":
        raise PermissionError("Setup 1 holdout requires explicit final-phase approval")
    if (
        approval["status"] != "approved"
        or not approval["approved_by"]
        or not approval["approved_at_utc"]
        or data["status"] != "verified_2020_2026"
    ):
        raise PermissionError(
            "Setup 1 baseline blocked: user preregistration approval and 2020-2026 data required"
        )
    if approval["configuration_sha256"] != configuration_sha256():
        raise PermissionError("Setup 1 configuration changed after preregistration approval")
    calibration = load_config(load_config("execution.yaml")["cost"]["spread_calibration_path"])
    if not calibration.get("coverage_complete"):
        raise PermissionError("Setup 1 requires spread calibration on the entire development set")
    if holdout:
        from datetime import datetime

        end = load_config("split.yaml")["holdout"]["end"]
        if end:
            first = datetime.fromisoformat(calibration["calibration_end_exclusive"]).year
            paths = load_config("execution.yaml")["cost"]["annual_calibration_paths"]
            years = range(first + 1, datetime.fromisoformat(end).year + 1)
            if any(year not in paths for year in years):
                raise PermissionError(
                    "Holdout requires frozen annual quotes-only spread calibrations"
                )
    review = setup["visual_review"]
    required = load_config("validation.yaml")["review"]["samples_per_type"]
    minimum = setup["validation"]["visual_minimum_correct_fraction"]
    if (
        review["status"] != "approved"
        or review["timeframe"] != "M5"
        or any(
            review["correct_counts"].get(kind) is None
            or review["correct_counts"][kind] / required < minimum
            for kind in review["required_types"]
        )
    ):
        raise PermissionError("Setup 1 requires approved visual review >= 80 percent per type")
    path = Path(data["audit_report"])
    if (
        not path.exists()
        or hashlib.sha256(path.read_bytes()).hexdigest() != data["approved_audit_sha256"]
    ):
        raise PermissionError("Setup 1 approved data audit is missing or changed")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["coverage_missing_months"] or report["symbol"] != setup["strategy"]["symbol"]:
        raise PermissionError("Setup 1 data audit does not cover the required months")
    for source in report["provenance"]["files"]:
        path = Path(source["path"])
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise PermissionError("Setup 1 source data changed after approved audit")
