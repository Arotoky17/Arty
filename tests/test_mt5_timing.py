import numpy as np
import pandas as pd
import pytest

from arty_trading.validation.mt5_timing import lag_scan


@pytest.mark.parametrize("shift", [-60, 0, 60])
def test_return_alignment_recovers_signed_hour_shift_and_price_bias(shift):
    rng = np.random.default_rng(42)
    index = pd.date_range("2020-03-20", periods=1800, freq="min", tz="UTC")
    prices = 1500 + np.cumsum(rng.normal(0, .1, len(index)))
    duka = pd.Series(prices, index=index)
    mt5 = pd.Series(prices + .25, index=index + pd.Timedelta(minutes=shift))
    report = lag_scan(mt5, duka, index[180], index[-180])
    assert report["status"] == "clear"
    assert report["optimal"]["lag_minutes"] == -shift
    assert report["optimal"]["median_abs_price_diff_usd"] == pytest.approx(.25)
    assert report["optimal"]["minute_returns_correlation"] == pytest.approx(1)


def test_constant_prices_do_not_confirm_timing():
    index = pd.date_range("2020-01-01", periods=1000, freq="min", tz="UTC")
    quotes = pd.Series(1500., index=index)
    assert lag_scan(quotes, quotes, index[0], index[-1])["status"] == "insufficient_overlap"


def test_missing_minutes_are_not_bridged_as_one_minute_returns():
    rng = np.random.default_rng(5)
    index = pd.date_range("2020-01-01", periods=1000, freq="min", tz="UTC")
    quotes = pd.Series(1500 + np.cumsum(rng.normal(size=len(index))), index=index)
    quotes.iloc[400:500] = np.nan
    report = lag_scan(quotes, quotes, index[0], index[-1])
    assert report["optimal"]["lag_minutes"] == 0
    assert report["optimal"]["consecutive_return_pairs"] == 897


def test_unrelated_quotes_remain_ambiguous():
    rng = np.random.default_rng(19)
    index = pd.date_range("2020-01-01", periods=1800, freq="min", tz="UTC")
    left = pd.Series(1500 + np.cumsum(rng.normal(size=len(index))), index=index)
    right = pd.Series(1500 + np.cumsum(rng.normal(size=len(index))), index=index)
    report = lag_scan(left, right, index[180], index[-180])
    assert report["status"] == "ambiguous"
