"""Offline performance regressions preserve validation and SHA-256 guarantees."""

import json
import lzma
import struct
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from arty_trading.config.operational import load_config
from arty_trading.validation import dukascopy_import as importer
from tools import import_progress

DAY = datetime(2024, 1, 4, tzinfo=UTC)


@pytest.fixture
def session(tmp_path, monkeypatch):
    cfg = load_config("data_import.yaml")
    cfg.update(
        start=DAY.isoformat(),
        staging=str(tmp_path / "staging"),
        report_output=str(tmp_path / "report"),
    )
    payload = lzma.compress(struct.pack(">5If", 0, 2000000, 2001000, 1999000, 2002000, 1.0))
    download = MagicMock(return_value=payload)
    monkeypatch.setattr(importer, "fetch_bytes", download)
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    output = tmp_path / "raw"

    def run(until=DAY + timedelta(days=1), **kwargs):
        return importer.run_fetch(
            output, now=datetime(2026, 10, 5, tzinfo=UTC), until=until, cfg=cfg, **kwargs
        )

    run()
    return cfg, output, run, download


def test_resume_checks_each_object_once_without_duplicate_work(session, monkeypatch):
    _, output, run, download = session
    manifest = output / "import_manifest.jsonl"
    entries = importer.read_manifest(manifest)
    verified = [r for r in entries if r["status"] == "verified"]
    with manifest.open("a", encoding="utf-8") as stream:
        for _ in range(10):
            for row in verified:
                stream.write(json.dumps(row) + "\n")
    digest = MagicMock(wraps=importer.digest)
    read = MagicMock(wraps=importer.read_manifest)
    monkeypatch.setattr(importer, "digest", digest)
    monkeypatch.setattr(importer, "read_manifest", read)
    monkeypatch.setattr(importer, "decode_m1", MagicMock(side_effect=AssertionError("decode")))
    monkeypatch.setattr(
        importer, "publish_immutable", MagicMock(side_effect=AssertionError("publish"))
    )
    download.reset_mock()
    summary = run()
    assert summary["reused_daily_sides"] == 2
    assert summary["bid_ask_pairing_validated"]
    assert digest.call_count == 0
    assert read.call_count == 1
    download.assert_not_called()
    after = importer.read_manifest(manifest)
    assert sum(r["status"] == "verified" for r in after) == 22
    assert len(after) == len(entries) + 20  # An ordinary no-op resume writes nothing.


@pytest.mark.parametrize("key", ["path", "binary_path"])
def test_reuse_still_rejects_corrupt_objects(session, key):
    _, output, run, _ = session
    entry = next(
        r
        for r in importer.read_manifest(output / "import_manifest.jsonl")
        if r["status"] == "verified"
    )
    from pathlib import Path

    Path(entry[key]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum changed"):
        run(verify_existing=True)


def test_deduplication_does_not_hide_contradictory_manifest_hashes(session):
    _, output, run, _ = session
    manifest = output / "import_manifest.jsonl"
    entry = next(r for r in importer.read_manifest(manifest) if r["status"] == "verified")
    entry["sha256"] = "0" * 64
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
    with pytest.raises(ValueError, match="checksum changed"):
        run()


@pytest.mark.parametrize("legacy", [False, True])
def test_changed_rules_or_legacy_entries_require_validation(session, monkeypatch, legacy):
    cfg, output, run, _ = session
    manifest = output / "import_manifest.jsonl"
    if legacy:
        entries = importer.read_manifest(manifest)
        for row in entries:
            row.pop("validation_signature", None)
        manifest.write_text("".join(json.dumps(row) + "\n" for row in entries), encoding="utf-8")
    else:
        cfg["source_bar_seconds"] = 120
    decode = MagicMock(wraps=importer.decode_m1)
    monkeypatch.setattr(importer, "decode_m1", decode)
    assert run()["reused_daily_sides"] == (2 if legacy else 0)
    assert decode.call_count == (0 if legacy else 2)


def test_partial_day_is_not_reused_as_complete(session):
    _, _, run, download = session
    # Even a complete object cannot substitute for a cutoff-specific snapshot.
    download.reset_mock()
    summary = run(DAY + timedelta(hours=1))
    assert summary["reused_daily_sides"] == 0
    assert download.call_count == 2


def test_success_delay_once_per_file_and_phase_journal(tmp_path, monkeypatch):
    cfg = importer.apply_request_delay(load_config("data_import.yaml"), 2)
    cfg.update(
        start=DAY.isoformat(),
        staging=str(tmp_path / "staging"),
        report_output=str(tmp_path / "report"),
        request_delay_min_seconds=2,
        request_delay_max_seconds=2,
    )
    payload = lzma.compress(struct.pack(">5If", 0, 2000000, 2001000, 1999000, 2002000, 1.0))
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = payload
    response.headers = {"Content-Length": str(len(payload))}
    opener, sleep = MagicMock(return_value=response), MagicMock()
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    monkeypatch.setattr(importer.time, "sleep", sleep)
    output = tmp_path / "raw"
    summary = importer.run_fetch(
        output, now=datetime(2026, 10, 5, tzinfo=UTC), until=DAY + timedelta(days=1), cfg=cfg
    )
    assert opener.call_count == 2
    assert [call.args[0] for call in sleep.call_args_list] == [2, 2]
    entries = importer.read_manifest(output / "import_manifest.jsonl")
    for row in entries[:2]:
        timings = row["phase_timings"]
        assert all(value >= 0 for value in timings.values())
        assert timings["bi5_decode_seconds"] > 0
        assert timings["checksum_seconds"] > 0
        assert timings["write_seconds"] > 0
        assert timings["backoff_seconds"] == 0
        phases_total = sum(v for k, v in timings.items() if k != "total_seconds")
        assert phases_total <= timings["total_seconds"]
    event = next(row for row in entries if row["status"] == "session_timing")
    assert event["manifest_append_seconds"] == summary["manifest_append_seconds"]
    assert event["phase_timings"] == summary["phase_timings"]


def test_progress_calendar_is_evaluated_once_with_same_coverage(monkeypatch):
    monkeypatch.setattr(import_progress, "read_manifest", lambda _: [])
    closed = MagicMock(wraps=import_progress._expected_closed_days)
    monkeypatch.setattr(import_progress, "_expected_closed_days", closed)
    report = import_progress.progress()
    split = load_config("split.yaml")
    start = datetime.fromisoformat(split["development"]["start"])
    end = datetime.fromisoformat(split["development"]["end"])
    assert report["development_to_2025_01_01"] == import_progress._coverage(
        start, end, set(), closed(start, end)
    )
    assert closed.call_count == 2  # One in progress, one reference computation here.
