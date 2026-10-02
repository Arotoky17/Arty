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
    for name in ("definitions.yaml", "split.yaml", "execution.yaml"):
        payload[name] = load_config(name)
    calendar = payload["execution.yaml"]["cost"].get("news_calendar_path")
    if calendar:
        from arty_trading.config.operational import CONFIG_ROOT

        path = Path(calendar)
        if not path.is_absolute():
            path = CONFIG_ROOT / path
        payload["news_calendar_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def assert_setup_run_allowed(setup_id: str) -> None:
    setup = load_config("setup1_preregistration.yaml")
    if setup_id != setup["setup_id"]:
        return
    approval = setup["approval"]
    data = setup["data"]
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
