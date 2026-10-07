import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from arty_trading.validation.mt5_csv import (
    collect_mt5_bars,
    convert_mt5,
    reconstructed_ask_forbidden,
)
from arty_trading.validation.mt5_quality import compare_duka, reconstruct, spread_audit


def test_reconstruct_preserves_bid_and_forbids_reference_calibration(tmp_path):
    source = tmp_path / "mt5.csv"
    source.write_text(
        "Date,Time,Open,High,Low,Close,TickVol,Vol,Spread\n"
        "2020.01.06,12:00:00,100,101,99,100,10,0,20\n"
        "2020.01.06,12:01:00,100,101,99,100,11,0,30\n"
    )
    root = tmp_path / "converted"
    convert_mt5(
        collect_mt5_bars(source), zone_name="Europe/Athens", point=None, output=root, bid_only=True
    )
    bid_path = root / "xauusd/bid/2020-01-06.csv"
    before = bid_path.read_bytes()
    bid, spreads, report = reconstruct(source, root, tmp_path / "report")
    ask = pd.read_csv(root / "xauusd/ask/2020-01-06.csv")
    assert ask.close.tolist() == [100.2, 100.3]
    assert bid_path.read_bytes() == before
    assert report["bid_files_unchanged"] == 1
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["point"] == "0.01"
    assert provenance["broker_specification"]["contract_oz"] == 100
    with pytest.raises(ValueError, match="reference cost model"):
        reconstructed_ask_forbidden(provenance)
    entries = [
        json.loads(line) for line in (root / "conversion_manifest.jsonl").read_text().splitlines()
    ]
    assert all(row["ask_origin"] == "reconstructed_ask" for row in entries)
    assert hashlib.sha256(bid_path.read_bytes()).hexdigest() == entries[0]["sha256"]


def test_spread_flags_do_not_remove_outliers_or_constant_runs():
    index = pd.date_range("2020-01-06", periods=500, freq="min", tz="UTC").as_unit("ms").asi8
    values = np.full(500, 0.2)
    values[-2:] = [0, 10]
    report, frame = spread_audit(pd.Series(values, index=index))
    assert report["outliers"]["count"] == 1
    assert report["zero_spread_bars"] == 1
    assert len(report["constant_runs_240_minutes"]) == 1
    assert len(report["constant_days_95_percent"]) == 1
    assert len(frame) == 500


def test_comparison_reports_median_p95_and_both_spreads(tmp_path):
    path = tmp_path / "xauusd/bid/m1"
    path.mkdir(parents=True)
    index = pd.date_range("2020-01-06", periods=4, freq="min", tz="UTC").as_unit("ms").asi8
    pd.DataFrame({"timestamp": index, "close": [100, 100, 100, 100]}).to_csv(
        path / "2020-01-06.csv", index=False
    )
    asks = tmp_path / "xauusd/ask/m1"
    asks.mkdir(parents=True)
    pd.DataFrame({"timestamp": index, "close": [100.5] * 4}).to_csv(
        asks / "2020-01-06.csv", index=False
    )
    bid = pd.DataFrame({"close": [100, 101, 102, 103]}, index=index)
    report = compare_duka(bid, pd.Series(0.2, index=index), tmp_path)
    assert report["overall"]["bid_abs_price_diff_usd"]["median"] == 1.5
    assert report["overall"]["bid_abs_price_diff_usd"]["p95"] == pytest.approx(2.85)
    assert report["overall"]["mt5_spread_usd"]["median"] == 0.2
    assert report["overall"]["duka_spread_usd"]["median"] == 0.5
