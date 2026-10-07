import pandas as pd

from arty_trading.validation.mt5_depth import minute_depth


def test_depth_reports_whole_missing_day_and_exact_minute_gap(tmp_path):
    root = tmp_path / "xauusd/bid"
    root.mkdir(parents=True)
    times = pd.to_datetime(["2020-01-06T12:00:00Z", "2020-01-06T12:02:00Z"])
    pd.DataFrame({"timestamp": times.as_unit("ms").asi8}).to_csv(
        root / "2020-01-06.csv", index=False
    )
    report = minute_depth(tmp_path)
    assert report["years"]["2020"]["present_open_minutes"] == 2
    assert any(
        hole["start_utc"] == "2020-01-06T12:01:00+00:00" and hole["missing_open_minutes"] == 1
        for hole in report["hole_intervals"]
    )
    # Jan 3 is a complete missing open day, not hidden by per-file gap checks.
    assert any(
        hole["start_utc"] <= "2020-01-03T12:00:00+00:00" < hole["end_exclusive"]
        for hole in report["hole_intervals"]
    )
    assert report["split_yaml_modified"] is False


def test_depth_counts_duplicates_without_inflating_presence(tmp_path):
    root = tmp_path / "xauusd/bid"
    root.mkdir(parents=True)
    stamp = pd.Timestamp("2020-01-06T12:00:00Z").value // 1_000_000
    pd.DataFrame({"timestamp": [stamp, stamp]}).to_csv(root / "2020-01-06.csv", index=False)
    report = minute_depth(tmp_path)
    assert report["duplicate_timestamps"] == 1
    assert report["years"]["2020"]["present_open_minutes"] == 1
