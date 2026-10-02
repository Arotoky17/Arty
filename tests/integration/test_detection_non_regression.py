"""Offline detector regression over pinned real dev data; never evaluates a strategy."""

from pathlib import Path

import pytest

from arty_trading.validation.detection_regression import run_regression


def test_six_month_xauusd_detector_non_regression(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    data = root / "data" / "historical"
    if not (data / "source_manifest.json").exists():
        pytest.skip("Pinned XAUUSD January-June 2024 source files unavailable")
    report = run_regression(data, "XAUUSD", tmp_path / "detection_regression")
    assert all(row["matches"] for row in report["provenance"]["manifest_checks"])
    assert report["pnl_inspected"] is False
    assert report["backtest_run"] is False
    assert not report["undocumented_differences"], (
        f"Undocumented differences retained in {tmp_path / 'detection_regression'}"
    )
