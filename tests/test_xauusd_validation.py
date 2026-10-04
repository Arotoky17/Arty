"""XAUUSD market controls without historical strategy runs."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from arty_trading.config.operational import load_config
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.validation.cost_sensitivity import cost_sensitivity
from arty_trading.validation.dukascopy_import import retry_delay
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.trial_registry import TrialRegistry
from arty_trading.validation.xauusd_diagnostics import news_blocked, summarize


@pytest.mark.parametrize(
    "instant,closed",
    [
        ("2024-01-08T22:30:00+00:00", True),
        ("2024-07-08T21:30:00+00:00", True),
        ("2024-07-08T22:20:00+00:00", False),
        ("2024-07-06T12:00:00+00:00", True),
    ],
)
def test_market_clock_dst_and_weekend(instant, closed) -> None:
    assert MarketCalendar().state(datetime.fromisoformat(instant))["non_tradable"] == closed


def test_post_reopen_window_and_daily_anchor() -> None:
    calendar = MarketCalendar()
    assert calendar.state(datetime.fromisoformat("2024-07-08T22:19:00+00:00"))["entry_blocked"]
    assert not calendar.state(datetime.fromisoformat("2024-07-08T22:20:00+00:00"))["entry_blocked"]
    winter = calendar.session_day(
        datetime.fromisoformat("2024-01-08T23:00:00+00:00"), "new_york_17", "17:00"
    )
    summer = calendar.session_day(
        datetime.fromisoformat("2024-07-08T23:00:00+00:00"), "new_york_17", "17:00"
    )
    assert winter.hour == 22
    assert summer.hour == 21


def test_exponential_backoff_caps_and_respects_retry_after() -> None:
    cfg = load_config("data_import.yaml")
    assert cfg["workers"] == 1
    # Long jittered schedule: a 30 s base doubling up to the 15 minute cap.
    first, second = retry_delay(cfg, 0), retry_delay(cfg, 1)
    assert cfg["retry_base_seconds"] * 0.75 <= first <= cfg["retry_base_seconds"] * 1.25
    assert cfg["retry_base_seconds"] * 1.5 <= second <= cfg["retry_base_seconds"] * 2.5
    assert retry_delay(cfg, 20) == cfg["backoff_cap_seconds"]
    assert cfg["retry_base_seconds"] == 30
    assert cfg["backoff_cap_seconds"] == 900
    assert retry_delay(cfg, 0, "42") == 42
    with pytest.raises(RuntimeError, match="Retry-After"):
        retry_delay(cfg, 0, "1200")  # Beyond the cap: stop, never shorten.


def test_retry_after_http_date(monkeypatch) -> None:
    import arty_trading.validation.dukascopy_import as importer

    monkeypatch.setattr(importer, "utc_now", lambda: datetime(2026, 10, 3, tzinfo=UTC))
    # The provider wait is honoured and never shortened by the jittered schedule.
    assert retry_delay(
        load_config("data_import.yaml"), 0, "Sat, 03 Oct 2026 00:00:30 GMT"
    ) >= 30


def test_rule_changes_do_not_inflate_trial_count(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.sqlite")
    registry.record_rule_change(
        "split",
        {"holidays": ["2025-01-01"]},
        {"holidays": []},
        "User chose 10 weekdays with no holiday calendar",
    )
    assert registry.get_n_trials() == 0
    with pytest.raises(ValueError, match="reason"):
        registry.record_rule_change("scope", {}, {}, " ")
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM rule_changes").fetchone()[0] == 1


def test_calibrated_p75_spread_and_broker_scaling() -> None:
    cfg = load_config("execution.yaml")["cost"]
    model = CostModel(**cfg)
    doubled = CostModel(**{**cfg, "broker_spread_multiplier": 2.0})
    at = datetime(2024, 1, 8, 10, tzinfo=UTC)
    assert model.spread_at(at) > 30
    assert doubled.spread_at(at) == 2 * model.spread_at(at)
    with pytest.raises(ValueError, match="calibration file"):
        CostModel(**{**cfg, "spread_calibration_path": None})


def test_negative_expectancy_at_cost_1_5_is_fragile() -> None:
    scenarios = cost_sensitivity([(1.0, 4.0, 10.0)], [100.0, 101.0], [0.0, 4.0], [1, 1.5, 2])
    assert not scenarios[0]["fragile"]
    assert scenarios[1]["fragile"]
    assert scenarios[1]["expectancy_r"] == -0.1


def test_weekly_bootstrap_and_news_gate_are_deterministic() -> None:
    rows = [
        {"entry_time": "2024-01-08T10:00:00+00:00", "r": 1.0},
        {"entry_time": "2024-01-15T10:00:00+00:00", "r": -1.0},
    ]
    assert summarize(rows) == summarize(rows)
    assert summarize(rows)["expectancy_r"] == 0.0
    cost = CostModel(**load_config("execution.yaml")["cost"])
    at = datetime.fromisoformat("2024-02-13T13:30:00+00:00")
    assert news_blocked(at, cost, True)
    assert not news_blocked(at, cost, False)
    assert not news_blocked(datetime.fromisoformat("2024-02-13T14:16:00+00:00"), cost, True)


def test_trial_budget_counts_interrupted_trials(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.sqlite")
    setup = load_config("setup1_preregistration.yaml")["setup_id"]
    registry.begin(setup, {}, "M5", None, None, "dev")
    registry.begin(setup, {}, "M5", None, None, "dev")
    with pytest.raises(PermissionError, match="maximum trials"):
        registry.begin(setup, {}, "M5", None, None, "dev")
    registry.begin(setup, {}, "M5", None, None, "holdout")
    with pytest.raises(PermissionError, match="maximum trials"):
        registry.begin(setup, {}, "M5", None, None, "holdout")
    assert registry.get_n_trials(setup) == 3


def test_calendar_d1_close_has_no_dst_lookahead(monkeypatch) -> None:
    import pandas as pd

    import arty_trading.config.operational as operational
    from arty_trading.validation.market_data import resample_closed_bars

    original = operational.definitions

    def ny_definitions():
        cfg = original()
        cfg["daily_bars"]["convention"] = "new_york_17"
        return cfg

    monkeypatch.setattr(operational, "definitions", ny_definitions)
    times = pd.date_range("2024-03-09T22:00Z", "2024-03-10T20:59Z", freq="min")
    frame = pd.DataFrame({key: 100 for key in ("open", "high", "low", "close")}, index=times)
    bars = resample_closed_bars(frame, 1440)
    assert len(bars) == 1
    assert bars.iloc[0].available_at == pd.Timestamp("2024-03-10T21:00Z")
    assert resample_closed_bars(frame.iloc[:-1], 1440).empty


def test_closed_bar_corruption_remains_auditable_without_polluting_atr() -> None:
    import pandas as pd

    from arty_trading.validation.data_audit import bar_outlier_metrics

    index = pd.DatetimeIndex(["2024-01-05T21:59Z", "2024-01-06T12:00Z", "2024-01-07T23:00Z"])
    frame = pd.DataFrame(
        {key: [100, 200, 101] for key in ("open", "high", "low", "close")}, index=index
    )
    metrics = bar_outlier_metrics(frame, load_config("validation.yaml")["audit"])
    assert metrics.iloc[1].non_tradable
    assert metrics.iloc[1].outlier
    assert metrics.iloc[-1].past_atr_active == 0
