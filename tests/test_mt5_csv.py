import csv
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from arty_trading.validation.mt5_csv import (
    collect_mt5_bars,
    compare_mt5_to_dukascopy,
    convert_mt5,
    depth_report,
    detect_timezone,
    reconstructed_ask_forbidden,
    resolve_local,
)


def export(tmp_path, rows):
    path = tmp_path / "export.csv"
    path.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        + "\n".join(rows)
    )
    return collect_mt5_bars(path)


def row(date, clock, close="100", spread="20"):
    return f"{date}\t{clock}\t100\t101\t99\t{close}\t10\t0\t{spread}"


def test_dst_round_trip():
    zone = ZoneInfo("Europe/Athens")
    assert resolve_local(datetime(2020, 3, 29, 3, 30), zone)[1] == "nonexistent_dst"
    assert resolve_local(datetime(2020, 10, 25, 3, 30), zone)[1] == "ambiguous_dst"
    assert resolve_local(datetime(2020, 7, 1, 12), zone)[0].hour == 9


def test_conversion_provenance_duplicates_and_immutability(tmp_path):
    bars = export(tmp_path, [row("2020.01.02", "00:00"), row("2020.01.02", "00:00", "100.5")])
    output = tmp_path / "converted"
    result = convert_mt5(bars, zone_name="Europe/Athens", point=Decimal("0.01"), output=output)
    assert result["duplicates_skipped"][0]["conflicting"]
    ask = list(csv.DictReader((output / "xauusd/ask/2020-01-01.csv").open()))
    assert float(ask[0]["close"]) == 100.2
    provenance = json.loads((output / "provenance.json").read_text())
    with pytest.raises(ValueError, match="reference cost model"):
        reconstructed_ask_forbidden(provenance)
    changed = export(tmp_path, [row("2020.01.02", "00:00", "100.5")])
    with pytest.raises(ValueError, match="overwrite"):
        convert_mt5(changed, zone_name="Europe/Athens", point=Decimal("0.01"), output=output)


def test_depth_counts_cross_day_holes(tmp_path):
    bars = export(tmp_path, [row("2020.01.06", "23:59"), row("2020.01.07", "00:02")])
    output = tmp_path / "converted"
    convert_mt5(bars, zone_name="UTC", point=Decimal("0.01"), output=output)
    report = depth_report(output)
    assert report["missing_open_minutes_within_observed_span"] == 2
    assert report["coverage"]["holdout"]["status"] == "unavailable"


def test_detection_never_selects_zone(tmp_path):
    bars = export(tmp_path, [row("2020.01.02", "12:00")])
    news = tmp_path / "news.csv"
    news.write_text(
        "time_utc,type,status,event,source_url,source_local_time,verified_on_utc,note,time_confirmed\n2020-01-10T13:30:00+00:00,NFP,released,release,https://example.test,2020-01-10T13:30:00+00:00,2020-01-01,,yes\n"
    )
    report = detect_timezone(bars, candidates=("UTC",), news_path=news)
    assert report["selected_zone"] is None
    assert report["ambiguities"]


def test_compare_local_bid(tmp_path):
    bars = export(tmp_path, [row("2020.01.02", "12:00")])
    output = tmp_path / "converted"
    convert_mt5(bars, zone_name="UTC", point=Decimal("0.01"), output=output)
    duka = tmp_path / "duka/xauusd/bid/m1"
    duka.mkdir(parents=True)
    source = output / "xauusd/bid/2020-01-02.csv"
    (duka / source.name).write_bytes(source.read_bytes())
    report = compare_mt5_to_dukascopy(output, tmp_path / "duka")
    assert report["matched_minutes"] == 1
    assert report["abs_high_diff"]["max"] == 0


@pytest.mark.parametrize("spread", ["NaN", "Infinity", "-1"])
def test_invalid_spread(tmp_path, spread):
    with pytest.raises(ValueError):
        export(tmp_path, [row("2020.01.02", "12:00", spread=spread)])


def test_calibrator_rejects_origin_before_loading_quotes(tmp_path):
    from tools.calibrate_spreads import calibrate

    data = tmp_path / "data"
    data.mkdir()
    (data / "provenance.json").write_text('{"ask_origin": "reconstructed_ask"}')
    with pytest.raises(ValueError, match="reference cost model"):
        calibrate(data, tmp_path / "report", allow_partial=True)


def test_bid_only_conversion_never_invents_point_or_ask(tmp_path):
    bars = export(tmp_path, [row("2020.01.06", "12:00")])
    output = tmp_path / "converted"
    result = convert_mt5(bars, zone_name="Europe/Athens", point=None, output=output, bid_only=True)
    assert result["point"] is None
    assert result["ask_origin"] == "unavailable_pending_point"
    assert result["converted_daily_sides"] == 1
    assert not (output / "xauusd/ask").exists()
