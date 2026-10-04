"""Data ingestion and calendar controls, without strategy simulation."""

import lzma
import struct
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from arty_trading.config.operational import load_config
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.validation.data_audit import bar_outlier_metrics
from arty_trading.validation.dukascopy_import import (
    decode_m1,
    fetch_day,
    freeze_available_end,
    publish_immutable,
    read_manifest,
    validate_import_period,
)
from arty_trading.validation.market_data import index_utc, source_frames
from arty_trading.validation.monthly_regimes import confirmed_structure, sustained_presence
from arty_trading.validation.news_calendar import read_news_calendar


def test_flat_padding_does_not_decay_audit_atr_or_hide_absolute_spikes() -> None:
    prices = [100.0] * 20 + [100.0] * 200 + [100.5, 150.0]
    frame = pd.DataFrame({name: prices for name in ("open", "high", "low", "close")})
    frame.loc[:19, "high"] += 1
    frame.loc[:19, "low"] -= 1
    frame.index = (
        pd.date_range("2024-01-05T21:40Z", periods=20, freq="min")
        .append(pd.date_range("2024-01-06T00:00Z", periods=200, freq="min"))
        .append(pd.date_range("2024-01-07T23:00Z", periods=2, freq="min"))
    )
    metrics = bar_outlier_metrics(frame, load_config("validation.yaml")["audit"])
    assert metrics.iloc[-2].legacy_outlier
    assert not metrics.iloc[-2].outlier
    assert metrics.iloc[-1].outlier
    assert metrics.iloc[-1].absolute_return_flag
    prefix = bar_outlier_metrics(frame.iloc[:-1], load_config("validation.yaml")["audit"])
    pd.testing.assert_frame_equal(prefix, metrics.iloc[:-1])


def test_existing_2024_grid_flags_are_padding_driven_without_raw_mutation() -> None:
    frames, provenance = source_frames(Path("data/historical"), "XAUUSD")
    cfg = load_config("validation.yaml")["audit"]
    for side, expected in (("bid", 135), ("ask", 136)):
        frame = index_utc(frames[side], "ms")
        metrics = bar_outlier_metrics(frame, cfg)
        assert int(metrics.legacy_outlier.sum()) == expected
        assert int(metrics.outlier.sum()) == 12
        # Preserve the seven candidates from the previous flat-clock rule;
        # regular market hours additionally expose five unverified holiday cases.
        old_atr = (
            metrics.true_range.loc[~metrics.unchanged_flat]
            .shift()
            .ewm(alpha=1 / load_config("definitions.yaml")["atr_period"], adjust=False)
            .mean()
            .reindex(metrics.index)
            .ffill()
        )
        before = (old_atr > 0) & (metrics.true_range > cfg["spike_atr_multiple"] * old_atr)
        assert int(before.sum()) == 7
        assert not (before & ~metrics.outlier).any()
        assert int(metrics.non_tradable.sum()) == 86700
        assert int(metrics.forward_fill_candidate.sum()) == 84959
        assert not metrics.absolute_return_flag.any()
        assert not metrics.absolute_range_flag.any()
    assert all(item["matches"] for item in provenance["manifest_checks"])


def test_import_refuses_future_holdout_even_before_any_download() -> None:
    split = deepcopy(load_config("split.yaml"))
    split["holdout"]["end"] = "2027-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="future"):
        validate_import_period(split, datetime(2026, 10, 2, tzinfo=UTC))
    split["holdout"]["end"] = None
    validate_import_period(split, datetime(2026, 10, 2, tzinfo=UTC))


def test_dukascopy_decoder_field_order_scale_and_closed_bar_cutoff() -> None:
    cfg = load_config("data_import.yaml")
    day = datetime(2024, 1, 2, tzinfo=UTC)
    raw = b"".join(
        struct.pack(">5If", second, 2000000, 2001000, 1999000, 2002000, 3.0)
        for second in (0, 60, 120)
    )
    payload, info = decode_m1(lzma.compress(raw), day, day + timedelta(minutes=2), cfg)
    import io

    frame = pd.read_csv(io.BytesIO(payload))
    assert info["rows"] == 2
    assert frame.iloc[0][["open", "high", "low", "close"]].tolist() == [2000, 2002, 1999, 2001]
    assert frame.timestamp.tolist() == [
        int(day.timestamp() * 1000),
        int((day + timedelta(minutes=1)).timestamp() * 1000),
    ]
    with pytest.raises(ValueError, match="Truncated"):
        decode_m1(lzma.compress(raw[:-1]), day, day + timedelta(days=1), cfg)


def test_append_only_publication_resumes_and_rejects_changed_objects(tmp_path: Path) -> None:
    target = tmp_path / "raw" / "day.bi5"
    staging = tmp_path / "staging"
    sha = publish_immutable(target, b"original", staging)
    assert publish_immutable(target, b"original", staging) == sha
    with pytest.raises(ValueError, match="Append-only"):
        publish_immutable(target, b"replacement", staging)
    assert target.read_bytes() == b"original"


def test_daily_import_resumes_without_network_and_keeps_raw_checksums(
    tmp_path: Path, monkeypatch
) -> None:
    import arty_trading.validation.dukascopy_import as importer

    cfg = load_config("data_import.yaml")
    cfg["staging"] = str(tmp_path / "staging")
    payload = lzma.compress(struct.pack(">5If", 0, 2000000, 2001000, 1999000, 2002000, 1.0))
    calls = []

    def download(url, config):
        calls.append(url)
        return payload

    monkeypatch.setattr(importer, "fetch_bytes", download)
    day = datetime(2024, 1, 2, tzinfo=UTC)
    first = fetch_day(day, "bid", day + timedelta(days=1), tmp_path / "raw", cfg)
    second = fetch_day(day, "bid", day + timedelta(days=1), tmp_path / "raw", cfg)
    assert len(calls) == 1
    assert "/2024/00/02/" in calls[0]
    assert second["cached"]
    assert second["sha256"] == first["sha256"]
    assert second["binary_sha256"] == first["binary_sha256"]


def test_import_future_guard_precedes_network_and_raw_writes(tmp_path: Path, monkeypatch) -> None:
    import arty_trading.validation.dukascopy_import as importer

    original = importer.load_config
    split = original("split.yaml")
    split["holdout"]["end"] = "2027-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        importer, "load_config", lambda name: split if name == "split.yaml" else original(name)
    )
    monkeypatch.setattr(importer, "fetch_bytes", lambda *args: pytest.fail("network called"))
    with pytest.raises(ValueError, match="future"):
        importer.run_fetch(tmp_path / "raw", now=datetime(2026, 10, 2, tzinfo=UTC))
    assert not (tmp_path / "raw").exists()


def test_holdout_end_uses_actual_common_closed_timestamp_not_latest_one_sided(
    tmp_path: Path,
) -> None:
    import hashlib

    import yaml

    day = datetime(2026, 10, 1, tzinfo=UTC)
    entries = []
    for side, offsets in (("bid", [0, 60, 120]), ("ask", [0, 60])):
        path = tmp_path / f"{side}.csv"
        path.write_text(
            "timestamp\n"
            + "\n".join(str(int(day.timestamp() * 1000) + x * 1000) for x in offsets)
            + "\n"
        )
        entries.append(
            {
                "status": "verified",
                "rows": len(offsets),
                "date": "2026-10-01",
                "side": side,
                "snapshot_cutoff": day.isoformat(),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    split_path = tmp_path / "split.yaml"
    split_path.write_text(yaml.safe_dump(load_config("split.yaml")))
    cutoff = day + timedelta(hours=1)
    last = freeze_available_end(split_path, entries, cutoff)
    assert last == day + timedelta(minutes=2)
    assert yaml.safe_load(split_path.read_text())["holdout"]["end"] == last.isoformat()
    with pytest.raises(ValueError, match="future"):
        freeze_available_end(split_path, entries, day + timedelta(minutes=1))


def test_news_calendar_has_sources_dst_and_unscheduled_fomc() -> None:
    rows = read_news_calendar(Path("config/news_calendar.csv"), ["NFP", "FOMC", "CPI"])
    assert len(rows) == 224
    assert all(row["source_url"] and row["time_utc"].endswith("+00:00") for row in rows)
    fomc = {row["time_utc"] for row in rows if row["type"] == "FOMC"}
    assert "2020-03-03T15:00:00+00:00" in fomc
    assert "2020-03-15T21:00:00+00:00" in fomc
    assert "2020-03-23T12:00:00+00:00" in fomc
    assert any(
        row["type"] == "NFP" and row["time_utc"] == "2024-01-05T13:30:00+00:00" for row in rows
    )
    assert any(
        row["type"] == "NFP" and row["time_utc"] == "2024-05-03T12:30:00+00:00" for row in rows
    )
    model = CostModel(**load_config("execution.yaml")["cost"])
    event = datetime(2024, 5, 3, 12, 30, tzinfo=UTC)
    regular = datetime(2024, 5, 3, 12, 0, tzinfo=UTC)
    assert model.spread_at(event) == pytest.approx(model.spread_at(regular) * 1.5)


def test_structure_pivots_cannot_see_future_bars() -> None:
    times = pd.date_range("2024-01-01", periods=80, freq="4h", tz="UTC")
    prices = [100 + i * 0.1 + (i % 7 - 3) ** 2 for i in range(80)]
    frame = pd.DataFrame(
        {
            "high": [x + 1 for x in prices],
            "low": [x - 1 for x in prices],
            "available_at": times + pd.Timedelta(hours=4),
        },
        index=times,
    )
    full = confirmed_structure(frame, 2)
    prefix = confirmed_structure(frame.iloc[:45], 2)
    pd.testing.assert_series_equal(prefix, full.iloc[:45])
    altered = frame.copy()
    altered.iloc[45:, altered.columns.get_loc("high")] = 99999
    pd.testing.assert_series_equal(full.iloc[:45], confirmed_structure(altered, 2).iloc[:45])


def test_regime_presence_requires_sustained_trend_and_range() -> None:
    labels = pd.Series(
        ["trend_up"] * 4 + ["transition"] + ["range"] * 4,
        index=pd.date_range("2024-01-01", periods=9, tz="UTC"),
    )
    assert sustained_presence(labels, 3)["contains_both_observed"]
    assert not sustained_presence(labels, 5)["contains_both_observed"]


def test_unavailable_holdout_does_not_call_loader_or_consume_access(tmp_path: Path) -> None:
    from arty_trading.validation.split import DataSplit, HoldoutAccessError
    from arty_trading.validation.trial_registry import TrialRegistry

    registry = TrialRegistry(tmp_path / "trials.sqlite")
    config = load_config("split.yaml")
    config["holdout"]["end"] = None
    split = DataSplit(registry, config)
    with pytest.raises(HoldoutAccessError, match="unavailable"):
        split.load_holdout(
            "setup", "reason", enabled=True, loader=lambda: pytest.fail("loader called")
        )
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0] == 0


def test_torn_manifest_is_recovered_by_appending_without_changing_original_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.jsonl"
    original = b'{"status": "error", "date": "2020-01-01"}\n{"status": "ver'
    path.write_bytes(original)
    entries = read_manifest(path, recover=True)
    assert path.read_bytes().startswith(original)
    assert entries[-1]["status"] == "journal_recovery"
    assert read_manifest(path) == entries
    assert read_manifest(path, recover=True) == entries


def test_unmarked_mid_journal_corruption_is_not_silently_ignored(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_bytes(b'bad record\n{"status": "error"}\n')
    with pytest.raises(ValueError, match="Corrupt manifest"):
        read_manifest(path, recover=True)


def test_all_missing_downloads_cannot_report_a_successful_import(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    import arty_trading.validation.dukascopy_import as importer

    original = importer.load_config
    cfg = original("data_import.yaml")
    cfg.update(
        {
            "start": "2026-10-02T00:00:00+00:00",
            "staging": str(tmp_path / "staging"),
            "report_output": str(tmp_path / "report"),
        }
    )
    split = original("split.yaml")
    split["holdout"]["end"] = None
    monkeypatch.setattr(
        importer, "load_config", lambda name: cfg if name == "data_import.yaml" else split
    )
    monkeypatch.setattr(importer, "fetch_bytes", lambda *args: None)
    with pytest.raises(RuntimeError, match="No M1 source rows"):
        importer.run_fetch(tmp_path / "raw", now=datetime(2026, 10, 2, 1, tzinfo=UTC))
    report = json.loads((tmp_path / "report" / "import_status.json").read_text())
    assert report["status"] == "failed"
    assert report["unavailable_daily_sides"] == 2
    assert not report["coverage_validated"]
def test_import_progress_separates_expected_closed_days_from_missing_files() -> None:
    """Weekends and configured holidays must not be reported as absent files."""
    from tools.import_progress import _coverage, _expected_closed_days

    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 8, tzinfo=UTC)
    closed = _expected_closed_days(start, end)
    assert "2020-01-01" in closed  # configured recurring holiday (01-01)
    assert "2020-01-02" in closed  # configured recurring holiday (01-02)
    assert "2020-01-04" in closed  # Saturday: fully outside market hours
    assert "2020-01-03" not in closed  # Thursday
    assert "2020-01-05" not in closed  # Sunday reopens 18:00 New York
    assert "2020-01-06" not in closed  # Monday

    # Every expected-open day of the window is imported.
    covered = {"2020-01-03", "2020-01-05", "2020-01-06", "2020-01-07"}
    report = _coverage(start, end, covered, closed)
    assert report["expected_open_days"] == 4
    assert report["expected_closed_days"] == 3
    assert report["covered_days"] == 4
    assert report["missing_days"] == 0
    assert report["verdict"] == "complet"

    # A genuinely open day without a file is still reported as an absence.
    report = _coverage(start, end, covered - {"2020-01-06"}, closed)
    assert report["expected_open_days"] == 4
    assert report["missing_days"] == 1
    assert report["missing_examples"] == ["2020-01-06"]
    assert report["verdict"] == "incomplet"

    # A closed day that nonetheless has a validated file is informative, not an anomaly.
    report = _coverage(start, end, covered | {"2020-01-04"}, closed)
    assert report["expected_closed_days"] == 3
    assert report["closed_but_present_days"] == 1
    assert report["closed_but_present_examples"] == ["2020-01-04"]
    assert report["missing_days"] == 0
    assert report["verdict"] == "complet"
