"""Real report acceptance checks and non-vacuous audit failure tests."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from arty_trading.cli import main
from arty_trading.modules.backtesting.control import ControlReplay
from arty_trading.modules.backtesting.control_audit import audit_violations, validate_report

REPORT = Path("data/backtests/baseline_post_fixes_2024H1.json")


@pytest.fixture
def real_report() -> dict[str, Any]:
    if not REPORT.exists():
        pytest.skip("Real MT5 2024-H1 history unavailable; control report not generated")
    report: dict[str, Any] = json.loads(REPORT.read_text(encoding="utf-8"))
    validate_report(report)
    return report


def test_backtest_produces_at_least_20_trades(real_report: dict[str, Any]) -> None:
    assert real_report["total_trades"] >= 20


def test_no_trade_before_ob_creation(real_report: dict[str, Any]) -> None:
    assert not audit_violations(real_report["audit_trades"])


def test_no_confirmation_from_out_of_window_event(real_report: dict[str, Any]) -> None:
    assert not audit_violations(real_report["audit_trades"])


def test_rejection_candle_touches_ob(real_report: dict[str, Any]) -> None:
    assert not audit_violations(real_report["audit_trades"])


@pytest.fixture
def valid_trade() -> dict[str, Any]:
    return {
        "ob_timestamp": "2024-01-02T10:00:00+00:00",
        "entry_timestamp": "2024-01-02T10:15:00+00:00",
        "confirmation_timestamp": "2024-01-02T10:10:00+00:00",
        "window_start": "2024-01-02T10:05:00+00:00",
        "window_end": "2024-01-02T10:10:00+00:00",
        "window_timestamps": ["2024-01-02T10:05:00+00:00", "2024-01-02T10:10:00+00:00"],
        "confirmation_valid": True,
        "confirmation_type": "rejection_candle",
        "ob_low": 2000,
        "ob_high": 2002,
        "rejection_candle_low": 2001,
        "rejection_candle_high": 2003,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("entry_timestamp", "2024-01-02T09:00:00+00:00", "entry_before_or_at_ob_creation"),
        ("confirmation_timestamp", "2024-01-02T11:00:00+00:00", "confirmation_out_of_window"),
        (
            "confirmation_timestamp",
            "2024-01-02T10:07:00+00:00",
            "confirmation_not_in_available_candles",
        ),
        ("rejection_candle_low", 2010, "no_contact_with_ob"),
        ("confirmation_valid", False, "unconfirmed_legacy_entry"),
    ],
)
def test_audit_detects_violations(
    valid_trade: dict[str, Any],
    field: str,
    value: Any,
    reason: str,
) -> None:
    assert not audit_violations([valid_trade])
    valid_trade[field] = value
    assert reason in audit_violations([valid_trade])[0]["reasons"]


def test_audit_rejects_missing_evidence() -> None:
    assert audit_violations([{}])


def test_report_rejects_synthetic_source(valid_trade: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="real MT5"):
        validate_report({"data_source": "synthetic", "audit_trades": [valid_trade]})


def test_replay_initializes_live_risk_guards() -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    replay = ControlReplay(config, {})
    assert replay.settings.risk.max_consecutive_losses == 3
    assert replay.settings.risk.max_drawdown == 0.10


def test_cli_does_not_invent_report_when_history_is_missing(tmp_path: Path) -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    config["history_file"] = str(tmp_path / "missing_history.json")
    config_path = tmp_path / "baseline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    output = tmp_path / "result.json"
    code = main(
        [
            "backtest",
            "--symbol",
            "XAUUSD",
            "--from",
            "2024-01-01",
            "--to",
            "2024-06-30",
            "--config",
            str(config_path),
            "--output",
            str(output),
        ]
    )
    assert code == 2
    assert not output.exists()
