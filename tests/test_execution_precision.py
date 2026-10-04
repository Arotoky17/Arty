"""USD execution, risk ceilings and shared causal New York daily context."""

from datetime import UTC, datetime
from decimal import Decimal
from random import Random

import pandas as pd
import pytest

from arty_trading.config.operational import definitions, load_config
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.fill_model import FillModel
from arty_trading.modules.execution.risk_limits import permitted_volume
from arty_trading.validation.daily_context import daily_candles, previous_day_levels
from arty_trading.validation.market_calendar import MarketCalendar, tradable_candles
from arty_trading.validation.market_data import resample_closed_bars
from arty_trading.validation.trial_registry import TrialRegistry
from tools.statistical_power import planning_power


def test_usd_slippage_separates_limit_and_market_stop() -> None:
    cfg = load_config("execution.yaml")["cost"]
    model = CostModel(
        **{
            **cfg,
            "spread_mode": "configured",
            "spread_unit": "pips",
            "spread_pips": 0,
            "hourly_spread_pips": {},
            "session_hours_utc": {},
            "session_spread_pips": {},
            "commission_per_lot_side": 0,
            "news_calendar_path": None,
        }
    )
    assert model.pip_usd == definitions()["instrument_units"]["XAUUSD"]["pip_usd"] == 0.01
    assert model.slippage_price("limit") == 0.1
    assert model.slippage_price("stop") == model.slippage_price("market") == 0.3
    # A limit ENTRY adds no slippage; only the exit leg is charged.
    assert model.entry_slippage_price("limit") == 0.0
    assert model.entry_slippage_price("market") == model.entry_slippage_price("stop") == 0.3
    assert model.charge(1, 1, None, None, exit_kind="stop") == pytest.approx(30)
    assert model.charge(1, 1, None, None, exit_kind="limit") == pytest.approx(10)


def test_limit_entry_pays_only_the_crossing_not_extra_slippage() -> None:
    """The 0.10 USD limit-entry excursion is priced once, in the fill crossing."""
    fill = FillModel(**load_config("execution.yaml")["fill"])
    assert fill.crossing_usd == 0.10  # the crossing carries the adverse move
    cfg = load_config("execution.yaml")["cost"]
    model = CostModel(
        **{
            **cfg,
            "spread_mode": "configured",
            "spread_unit": "pips",
            "spread_pips": 0,
            "hourly_spread_pips": {},
            "session_hours_utc": {},
            "session_spread_pips": {},
            "commission_per_lot_side": 0,
            "news_calendar_path": None,
        }
    )
    # Limit entry leg is free; a market exit charges only its own 0.30 USD.
    assert model.entry_slippage_price("limit") == 0.0
    assert model.charge(
        1, 1, None, None, entry_kind="limit", exit_kind="market"
    ) == pytest.approx(30)
    # A market entry still pays its own slippage, so the two legs stay distinct.
    assert model.charge(
        1, 1, None, None, entry_kind="market", exit_kind="market"
    ) == pytest.approx(60)


def test_fill_reference_and_stress_have_unambiguous_usd_units() -> None:
    model = FillModel(**load_config("execution.yaml")["fill"])
    assert model.crossing_pips * 0.01 == model.crossing_usd == 0.10
    assert model.stress_crossing_usd == 0.30
    assert model.stop_on_fill_bar is True


def execution_fixture() -> tuple[Signal, Candle]:
    signal = Signal(
        symbol="XAUUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1999"),
        take_profit=Decimal("2002"),
        confidence=1.0,
        strategy_name="synthetic_unit",
        timeframe=TimeFrame.M5,
        justification="Synthetic execution test",
        metadata={"atr_at_bos": 2.0},
    )
    candle = Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 4, 12, tzinfo=UTC),
        open=Decimal("2000"),
        high=Decimal("2003"),
        low=Decimal("1998"),
        close=Decimal("2001"),
    )
    return signal, candle


def test_stress_crossing_rejects_a_reference_only_fill() -> None:
    signal, candle = execution_fixture()
    candle = candle.model_copy(update={"low": Decimal("1999.80")})
    cfg = load_config("execution.yaml")["fill"]
    assert FillModel(**cfg).fills(signal, candle, 0.01, Random(0))
    assert not FillModel(**{**cfg, "crossing_usd": cfg["stress_crossing_usd"]}).fills(
        signal, candle, 0.01, Random(0)
    )


def test_fill_bar_can_stop_out_and_never_take_profit(tmp_path) -> None:
    signal, candle = execution_fixture()
    engine = BacktestEngine(
        symbol="XAUUSD",
        registry=TrialRegistry(tmp_path / "synthetic.sqlite"),
        cost_model=CostModel(0, 0, 0),
    )
    engine._open_trade_from_signal(signal, candle)
    assert len(engine.trades) == 1
    engine._check_open_trades(candle)
    trade = engine.trades[0]
    assert not trade.is_open
    assert trade.close_price == signal.stop_loss


def test_normalized_spread_scales_with_price() -> None:
    model = CostModel(spread_unit="fraction_price", normalized_hours={7: 0.0001})
    at = datetime(2024, 1, 4, 7, tzinfo=UTC)
    assert model.spread_at(at, 2000) == pytest.approx(20)
    assert model.spread_at(at, 4000) == pytest.approx(40)
    model = CostModel(
        spread_unit="fraction_price", normalized_hours={7: 0.0001}, development_end_year=2025
    )
    with pytest.raises(ValueError, match="annual"):
        model.spread_at(datetime(2026, 1, 5, 7, tzinfo=UTC), 2000)


def test_minimum_stop_and_aggregate_risk_ceiling() -> None:
    assert permitted_volume(1, 2000, 1999.01, 2, 10000)[0] == 0
    volume, audit = permitted_volume(1, 2000, 1999, 2, 10000)
    assert volume == pytest.approx(0.5)
    assert audit["gross_leverage_after"] <= 10
    assert audit["margin_fraction_after"] <= 0.5
    assert audit["initial_risk_usd"] == pytest.approx(50)
    assert permitted_volume(1, 2000, 1999, 2, 10000, 100000, 5000)[0] == 0
    assert permitted_volume(1, 2000, 1999, float("nan"), 10000)[0] == 0


def test_volume_cap_is_reported_when_a_ceiling_binds() -> None:
    # Risk request 1.0 lot but only 0.5 lot fits under gross leverage and margin.
    volume, audit = permitted_volume(1, 2000, 1999, 2, 10000)
    assert volume == pytest.approx(0.5)
    assert audit["requested_volume"] == 1
    assert audit["capped"] is True
    assert audit["capped_by_leverage"] is True
    assert audit["capped_by_margin"] is True
    # Request below both ceilings: no cap reported.
    _, small = permitted_volume(0.1, 2000, 1999, 2, 10000)
    assert small["capped"] is False
    assert small["capped_by_leverage"] is False
    assert small["capped_by_margin"] is False


@pytest.mark.parametrize(
    "start,end,hours",
    [
        ("2024-03-09T22:00Z", "2024-03-10T21:00Z", 23),
        ("2024-11-02T21:00Z", "2024-11-03T22:00Z", 25),
    ],
)
def test_new_york_daily_close_tracks_dst(start: str, end: str, hours: int) -> None:
    index = pd.date_range(start, end, freq="h", inclusive="left")
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=index)
    bars = resample_closed_bars(frame, 1440, 60)
    assert len(bars) == 1
    assert bars.available_at.iloc[0] == pd.Timestamp(end)
    assert bars.observations.iloc[0] == hours


def test_pdh_and_pdl_use_the_same_closed_daily_series() -> None:
    index = pd.date_range("2024-03-11T21:00Z", "2024-03-12T21:00Z", freq="h", inclusive="left")
    frame = pd.DataFrame({"open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0}, index=index)
    at = datetime(2024, 3, 12, 21, tzinfo=UTC)
    candles = daily_candles(frame, "XAUUSD", 60, at)
    assert previous_day_levels(candles, datetime(2024, 3, 12, 20, tzinfo=UTC))["pdh"] is None
    assert previous_day_levels(candles, at)["pdh"] == Decimal("103.0")
    assert previous_day_levels(candles, at)["pdl"] == Decimal("98.0")


@pytest.mark.parametrize(
    "day", ["2024-12-24", "2024-12-26", "2024-12-31", "2024-01-02", "2024-03-29"]
)
def test_requested_full_day_holiday_exclusions(day: str) -> None:
    index = pd.DatetimeIndex([pd.Timestamp(day + "T15:00Z")])
    frame = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0]}, index=index
    )
    assert MarketCalendar().annotate(frame).non_tradable.all()


def test_power_is_labelled_as_an_assumption_not_measured_trades() -> None:
    report = planning_power()
    assert report["pnl_inspected"] is False
    assert report["estimates"]["dev"]["minimum_detectable_expectancy_r"] == pytest.approx(
        0.376, abs=0.001
    )
    assert report["estimates"]["holdout"]["minimum_detectable_expectancy_r"] == pytest.approx(
        0.594, abs=0.001
    )


def test_calendar_cache_invalidates_on_configuration_revision(tmp_path, monkeypatch) -> None:
    import os

    import yaml

    from arty_trading.config import operational

    cfg = load_config("market_calendar.yaml")
    path = tmp_path / "market_calendar.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(operational, "CONFIG_ROOT", tmp_path)
    _, candle = execution_fixture()
    candle = candle.model_copy(update={"time": datetime(2024, 1, 2, 12, tzinfo=UTC)})
    assert not tradable_candles([candle])
    old = path.stat().st_mtime_ns
    cfg["holiday_exclusions"]["recurring_month_days"] = []
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    os.utime(path, ns=(old + 1_000_000, old + 1_000_000))
    assert tradable_candles([candle]) == [candle]


def test_atr_warmup_requires_all_fourteen_true_ranges() -> None:
    from datetime import timedelta

    from arty_trading.config.operational import operational_atr

    _, candle = execution_fixture()
    period = definitions()["atr_period"]
    bars = [
        candle.model_copy(update={"time": candle.time + timedelta(minutes=5 * i)})
        for i in range(period + 1)
    ]
    assert operational_atr(bars[:-1]) == 0
    assert operational_atr(bars) > 0
