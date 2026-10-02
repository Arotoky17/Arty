"""Purge, market audits, causal HTF availability and cost stress, without a baseline run."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from arty_trading.config.operational import load_config
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.validation.cost_sensitivity import cost_sensitivity
from arty_trading.validation.data_audit import audit_frame, audit_ticks, resampling_audit
from arty_trading.validation.detection_regression import compare_events
from arty_trading.validation.market_data import (
    available_bars,
    resample_closed_bars,
    safe_output,
    write_report,
)
from arty_trading.validation.preregistration import configuration_sha256
from arty_trading.validation.regime_audit import adx, classify_regimes, run_regime_audit
from arty_trading.validation.split import DataSplit, HoldoutAccessError
from arty_trading.validation.trial_registry import TrialRegistry


def frame(count: int = 121) -> pd.DataFrame:
    times = pd.date_range("2024-01-02", periods=count, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": times.as_unit("ms").asi8,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        },
        index=times,
    )


def test_frozen_purge_is_ten_business_days_and_rejected(tmp_path: Path) -> None:
    split = DataSplit(TrialRegistry(tmp_path / "registry.sqlite"))
    assert split.purge == (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 16, tzinfo=UTC))
    assert split.holdout[0] == split.purge[1]
    with pytest.raises(HoldoutAccessError):
        split.assert_period_development(split.purge[0], split.purge[1])
    broken = deepcopy(split.config)
    broken["purge"]["business_days"] = 9
    with pytest.raises(ValueError, match="business-day"):
        DataSplit(split.registry, broken)


def test_htf_unusable_before_close_and_prefix_invariant() -> None:
    source = frame()
    bars = resample_closed_bars(source, 60)
    assert len(bars) == 2
    assert available_bars(bars, source.index[0] + pd.Timedelta(minutes=59)).empty
    assert len(available_bars(bars, source.index[0] + pd.Timedelta(minutes=60))) == 1
    prefix = resample_closed_bars(source.iloc[:61], 60)
    assert prefix.iloc[0][["open", "high", "low", "close"]].equals(
        bars.iloc[0][["open", "high", "low", "close"]]
    )
    assert resampling_audit(source, load_config("validation.yaml")["audit"])["status"] == "passed"


def test_resampling_future_tail_does_not_change_closed_prices() -> None:
    source = frame()
    modified = source.copy()
    modified.iloc[60:, modified.columns.get_loc("high")] = 900.0
    before = resample_closed_bars(source, 60)
    after = resample_closed_bars(modified, 60)
    assert before.iloc[0].equals(after.iloc[0])
    assert resample_closed_bars(source.iloc[:59], 60).empty


def test_audit_detects_missing_duplicate_invalid_and_outlier_without_mutation() -> None:
    raw = frame(30).reset_index(drop=True)
    raw = raw.drop(index=4).reset_index(drop=True)
    raw = pd.concat([raw, raw.iloc[[3]]], ignore_index=True)
    raw.loc[10, ["open", "high", "low", "close"]] = [200.0, 201.0, 199.0, 200.0]
    raw.loc[12, "low"] = -1
    saved = raw.copy(deep=True)
    report, _ = audit_frame(raw, load_config("validation.yaml")["audit"])
    month = report["months"]["2024-01"]
    assert month["duplicates"] == 1
    assert month["invalid_ohlc"] == 1
    assert month["outlier_m1_bars"] >= 1
    assert month["missing_minutes"] > 0
    pd.testing.assert_frame_equal(raw, saved)


def test_audit_flags_wrong_timestamp_unit_and_timezone_strings() -> None:
    raw = frame(3).reset_index(drop=True)
    raw["timestamp"] = ["2024-01-02T00:00:00+03:00", "2024-01-02T00:01:00", "broken"]
    report, _ = audit_frame(raw, load_config("validation.yaml")["audit"])
    assert report["invalid_timestamps"] == 1
    assert report["non_numeric_timestamp_rows"] == 3
    assert report["non_utc_offsets"] == 1
    assert report["naive_datetime_strings"] == 1


def test_raw_output_directory_is_protected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_output(tmp_path, tmp_path / "derived")


def test_report_accepts_detector_decimal_metadata(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    write_report(path, {"body": Decimal("0.125")})
    assert json.loads(path.read_text())["body"] == 0.125


def test_adx_is_causal_and_distinguishes_trend_and_range() -> None:
    index = pd.date_range("2025-02-01", periods=300, freq="1h", tz="UTC")
    close = np.arange(300, dtype=float) + 100
    trending = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close}, index=index)
    values = adx(trending, 14)
    assert values.dropna().iloc[-1] == pytest.approx(100)
    pd.testing.assert_series_equal(values.iloc[:100], adx(trending.iloc[:100], 14))
    range_frame = pd.DataFrame({"high": 101.0, "low": 99.0, "close": 100.0}, index=index)
    assert adx(range_frame, 14).dropna().iloc[-1] == 0
    cfg = load_config("validation.yaml")["regime"]
    assert classify_regimes(trending, cfg)["episodes"]["trend"]
    assert classify_regimes(range_frame, cfg)["episodes"]["range"]


def test_regime_audit_requires_explicit_market_only_flag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Explicit"):
        run_regime_audit(tmp_path / "raw", "XAUUSD", tmp_path / "report", enabled=False)


def test_session_and_major_news_spread_uses_scheduled_utc() -> None:
    model = CostModel(
        1,
        0,
        0,
        session_hours_utc={"london": [7, 8, 9]},
        session_spread_pips={"london": 2},
        major_news_types=["CPI"],
        news_spread_multiplier=2,
        news_before_minutes=15,
        news_after_minutes=15,
        news_events=[{"type": "CPI", "time_utc": "2024-01-02T08:30:00+00:00"}],
    )
    assert model.spread_at(datetime(2024, 1, 2, 7, tzinfo=UTC)) == 2
    assert model.spread_at(datetime(2024, 1, 2, 8, 15, tzinfo=UTC)) == 4
    assert model.spread_at(datetime(2024, 1, 2, 8, 46, tzinfo=UTC)) == 2
    assert (
        model.charge(
            1, 10, datetime(2024, 1, 2, 7, tzinfo=UTC), datetime(2024, 1, 2, 8, 30, tzinfo=UTC)
        )
        == 30
    )
    assert CostModel(1, 0, 0).assumptions()["news_calendar_status"] == "missing"


def test_tick_audit_distinguishes_duplicate_records_from_same_millisecond() -> None:
    timestamp = int(datetime(2024, 1, 2, tzinfo=UTC).timestamp() * 1000)
    raw = pd.DataFrame(
        {
            "timestamp": [timestamp, timestamp, timestamp, timestamp + 1],
            "bid": [100, 100.1, 100.1, 200],
            "ask": [100.2, 100.3, 100.3, 199],
        }
    )
    original = raw.copy(deep=True)
    report = audit_ticks(raw, load_config("validation.yaml")["audit"])
    month = report["months"]["2024-01"]
    assert month["duplicates"] == 1
    assert month["invalid_quotes"] == 1
    assert month["outlier_ticks"] >= 1
    pd.testing.assert_frame_equal(raw, original)


def test_non_utc_news_schedule_rejected() -> None:
    with pytest.raises(ValueError, match="UTC"):
        CostModel(
            1,
            0,
            0,
            major_news_types=["NFP"],
            news_events=[{"type": "NFP", "time_utc": "2024-01-05T13:30:00"}],
        )


def test_sensitivity_monotone_and_no_extra_trials() -> None:
    scenarios = cost_sensitivity(
        [(80, 20, 100), (-120, 20, 100)], [1000, 1080, 960], [0, 20, 40], [1, 1.5, 2]
    )
    assert [row["net_profit"] for row in scenarios] == [-40, -60, -80]
    assert [row["n_trades"] for row in scenarios] == [2, 2, 2]
    assert scenarios[0]["expectancy_r"] == pytest.approx(-0.2)
    assert scenarios[2]["expectancy_r"] == pytest.approx(-0.4)


def test_undocumented_difference_is_never_silenced() -> None:
    before = [
        {
            "category": "FVG",
            "origin_index": 3,
            "direction": "bullish",
            "price": 100,
            "details": {"gap_size_atr": 1, "new_secret_rule": 1},
        }
    ]
    after = deepcopy(before)
    after[0]["details"] = {"gap_size_atr": 2, "new_secret_rule": 2}
    report = compare_events(before, after, load_config("detection_changes.yaml")["rules"])
    assert report["status"] == "needs_review"
    assert report["undocumented_differences"][0]["undocumented_fields"] == ["new_secret_rule"]


@pytest.mark.asyncio
async def test_setup1_cannot_run_without_data_and_user_approval(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.sqlite")
    engine = BacktestEngine(
        registry=registry, setup_id=load_config("setup1_preregistration.yaml")["setup_id"]
    )
    with pytest.raises(PermissionError, match="blocked"):
        await engine.run_async([])
    assert registry.get_n_trials() == 0
    assert len(configuration_sha256()) == 64
