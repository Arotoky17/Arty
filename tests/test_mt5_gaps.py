import numpy as np
import pandas as pd

from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import resample_closed_bars
from arty_trading.validation.mt5_gaps import classify, closed_mask, current_gap_segment


def test_fast_calendar_mask_matches_scalar_calendar_across_dst_and_holidays():
    calendar = MarketCalendar()
    rng = np.random.default_rng(9)
    origin = pd.Timestamp("2020-01-01", tz="UTC")
    index = pd.DatetimeIndex(
        origin + pd.to_timedelta(rng.integers(0, 7 * 366 * 1440, 3000), unit="min")
    )
    expected = [calendar.state(stamp.to_pydatetime())["non_tradable"] for stamp in index]
    assert closed_mask(index, calendar).tolist() == expected


def test_gap_classes_threshold_and_derived_consumer_segments():
    grid = pd.date_range("2020-01-06T12:00Z", "2020-01-06T14:00Z", freq="min", inclusive="left")
    removed = np.zeros(len(grid), dtype=bool)
    removed[1] = True
    removed[5:8] = True
    removed[20:35] = True
    removed[40:56] = True
    stamps = grid[~removed].as_unit("ms").asi8
    report = classify(stamps, start=grid[0], end=grid[-1] + pd.Timedelta(minutes=1))
    totals = report["totals"]
    assert totals["isolated_open_minute"]["missing_minutes"] == 1
    assert totals["short_open_gap_2_15"]["missing_minutes"] == 18
    assert totals["large_open_gap_gt15"]["missing_minutes"] == 16
    assert report["policy"]["non_tradable_intervals"][0]["non_tradable"]
    frame = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=grid[~removed]
    )
    frame.attrs["gap_policy"] = report["policy"]
    bars = resample_closed_bars(frame, 5)
    assert bars.loc["2020-01-06T12:55Z", "non_tradable"]
    assert bars.loc["2020-01-06T12:55Z", "entry_blocked"]
    prefix = current_gap_segment(bars, pd.Timestamp("2020-01-06T13:20Z"))
    assert prefix.index.min() == pd.Timestamp("2020-01-06T13:00Z")
    assert prefix.available_at.max() <= pd.Timestamp("2020-01-06T13:20Z")


def test_expected_closed_gap_is_not_an_open_market_outage():
    grid = pd.date_range("2020-01-06T21:59Z", "2020-01-06T23:02Z", freq="min", inclusive="left")
    stamps = grid[[0, 61, 62]].as_unit("ms").asi8
    report = classify(stamps)
    assert report["totals"]["expected_closed_market"]["missing_minutes"] == 60
    assert report["totals"]["large_open_gap_gt15"]["intervals"] == 0


def test_gap_bar_flags_block_fills_and_are_filtered_from_atr_detectors():
    from decimal import Decimal

    from arty_trading.core.entities import Candle
    from arty_trading.core.enums import TimeFrame
    from arty_trading.utils.helpers import calculate_atr
    from arty_trading.validation.market_calendar import entry_allowed, tradable_candles

    args = {
        "symbol": "XAUUSD",
        "timeframe": TimeFrame.M5,
        "open": Decimal(100),
        "high": Decimal(101),
        "low": Decimal(99),
        "close": Decimal(100),
    }
    good = Candle(**args, time=pd.Timestamp("2020-01-06T12:00Z").to_pydatetime())
    blocked = Candle(
        **args,
        time=pd.Timestamp("2020-01-06T12:05Z").to_pydatetime(),
        non_tradable=True,
        entry_blocked=True,
    )
    assert not entry_allowed(blocked)
    assert tradable_candles([good, blocked]) == [good]
    assert calculate_atr([good, blocked], period=1) == 0


def test_mt5_reader_attaches_gap_overlay_and_checks_its_hash(tmp_path):
    import hashlib
    import json

    import pytest

    from arty_trading.validation.market_data import index_utc, source_frames

    policy = {
        "non_tradable_intervals": [
            {"start_utc": "2020-01-06T12:40:00Z", "end_exclusive": "2020-01-06T12:56:00Z"}
        ]
    }
    gap_file = tmp_path / "gap_policy.json"
    gap_file.write_text(json.dumps(policy))
    (tmp_path / "provenance.json").write_text(
        json.dumps(
            {
                "provider": "mt5_csv",
                "ask_origin": "reconstructed_ask",
                "gap_policy_sha256": hashlib.sha256(gap_file.read_bytes()).hexdigest(),
            }
        )
    )
    stamps = pd.date_range("2020-01-06T12:56Z", periods=4, freq="min").as_unit("ms").asi8
    for side in ("bid", "ask"):
        directory = tmp_path / "xauusd" / side
        directory.mkdir(parents=True)
        pd.DataFrame(
            {
                "timestamp": stamps,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1,
            }
        ).to_csv(directory / "2020-01-06.csv", index=False)
    frames, provenance = source_frames(tmp_path, "XAUUSD")
    bars = resample_closed_bars(index_utc(frames["bid"], "ms"), 5)
    assert bars.iloc[0].non_tradable
    assert provenance["allowed_for_reference_cost_model"] is False
    gap_file.write_text('{"non_tradable_intervals": []}')
    with pytest.raises(ValueError, match="checksum"):
        source_frames(tmp_path, "XAUUSD")
