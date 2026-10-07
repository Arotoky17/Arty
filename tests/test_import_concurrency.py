"""Bounded offline downloads, one writer, shared provider cooldown, zero-I/O resume."""

import io
import json
import lzma
import struct
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arty_trading.config.operational import load_config
from arty_trading.validation import dukascopy_import as importer

DAY = datetime(2024, 1, 1, tzinfo=UTC)
PAYLOAD = lzma.compress(struct.pack(">5If", 0, 2000000, 2001000, 1999000, 2002000, 1.0))


class Response(io.BytesIO):
    def __init__(self):
        super().__init__(PAYLOAD)
        self.headers = {"Content-Length": str(len(PAYLOAD))}


def config(tmp_path, workers=1):
    cfg = load_config("data_import.yaml")
    cfg.update(
        start=DAY.isoformat(),
        workers=workers,
        staging=str(tmp_path / "stage"),
        report_output=str(tmp_path / "report"),
        request_delay_min_seconds=0,
        request_delay_max_seconds=0,
    )
    return cfg


def run(tmp_path, cfg, days=4, **kwargs):
    return importer.run_fetch(
        tmp_path / "raw",
        cfg=cfg,
        now=datetime(2026, 10, 5, tzinfo=UTC),
        until=DAY + timedelta(days=days),
        **kwargs,
    )


def test_four_downloads_overlap_but_writes_and_manifest_order_are_serial(tmp_path, monkeypatch):
    cfg = config(tmp_path, 4)
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active, peak = 0, 0
    writer = threading.get_ident()
    published = []
    original_publish = importer.publish_immutable
    original_open = Path.open

    def open_checked(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if any(flag in mode for flag in "wa+") and path.suffix in {".part", ".jsonl"}:
            assert threading.get_ident() == writer
        return original_open(path, *args, **kwargs)

    def download(request, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        # Make the first file finish later than the other files in its batch.
        threading.Event().wait(0.015 if "BID" in request.full_url else 0.001)
        with lock:
            active -= 1
        return Response()

    def publish(path, payload, staging):
        assert threading.get_ident() == writer
        published.append(path)
        return original_publish(path, payload, staging)

    monkeypatch.setattr(importer.urllib.request, "urlopen", download)
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    monkeypatch.setattr(importer, "publish_immutable", publish)
    monkeypatch.setattr(Path, "open", open_checked)
    summary = run(tmp_path, cfg)
    rows = importer.read_manifest(tmp_path / "raw" / "import_manifest.jsonl")
    files = [r for r in rows if r["status"] == "verified"]
    assert peak == 4
    assert len(published) == len(set(published)) == 16
    assert [(r["date"], r["side"]) for r in files] == [
        ((DAY + timedelta(days=i)).date().isoformat(), side)
        for i in range(4)
        for side in ("bid", "ask")
    ]
    assert summary["bid_ask_pairing_validated"]


def test_resume_has_zero_raw_io_checksums_or_validation_and_is_idempotent(tmp_path, monkeypatch):
    cfg = config(tmp_path, 4)
    monkeypatch.setattr(importer.urllib.request, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    run(tmp_path, cfg)
    manifest = tmp_path / "raw" / "import_manifest.jsonl"
    before = manifest.read_bytes()
    original_open = Path.open

    def no_raw_io(path, *args, **kwargs):
        assert path.suffix not in {".bi5", ".csv", ".part"}
        return original_open(path, *args, **kwargs)

    read = MagicMock(wraps=importer.read_manifest)
    index = MagicMock(wraps=importer.index_manifest)
    monkeypatch.setattr(Path, "open", no_raw_io)
    monkeypatch.setattr(importer, "read_manifest", read)
    monkeypatch.setattr(importer, "index_manifest", index)
    for name in ("digest", "decode_m1", "publish_immutable", "fetch_bytes"):
        monkeypatch.setattr(importer, name, MagicMock(side_effect=AssertionError(name)))
    summary = run(tmp_path, cfg, max_runtime=1e-9)
    assert summary["reused_daily_sides"] == 8
    assert summary["bid_ask_pairing_validated"]
    assert not summary["runtime_exhausted"]
    assert read.call_count == index.call_count == 1
    assert manifest.read_bytes() == before


def test_legacy_manifest_is_trusted_without_claiming_unrecorded_pair_validation(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    cfg["start"] = (DAY + timedelta(days=2)).isoformat()
    monkeypatch.setattr(importer.urllib.request, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    run(tmp_path, cfg, days=3)
    manifest = tmp_path / "raw" / "import_manifest.jsonl"
    rows = [r for r in importer.read_manifest(manifest) if r["status"] == "verified"]
    for row in rows:
        row.pop("validation_signature")
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(importer, "digest", MagicMock(side_effect=AssertionError("checksum")))
    monkeypatch.setattr(importer, "decode_m1", MagicMock(side_effect=AssertionError("decode")))
    summary = run(tmp_path, cfg, days=3)
    assert summary["reused_daily_sides"] == 2
    assert not summary["bid_ask_pairing_validated"]
    assert summary["pairing_audit_required"] == ["2024-01-03"]


def test_runtime_budget_excludes_resume_indexing(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    clock = [0.0]
    original_index = importer.index_manifest

    def slow_index(entries):
        clock[0] += 100
        return original_index(entries)

    monkeypatch.setattr(importer.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    monkeypatch.setattr(importer, "index_manifest", slow_index)
    opener = MagicMock(side_effect=lambda *a, **k: Response())
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    summary = run(tmp_path, cfg, days=1, max_runtime=1)
    assert opener.call_count == 2
    assert not summary["runtime_exhausted"]


def test_runtime_expiry_during_delay_does_not_start_a_request(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.update(request_delay_min_seconds=2, request_delay_max_seconds=2)
    clock = [0.0]
    monkeypatch.setattr(importer.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        importer.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    opener = MagicMock(side_effect=AssertionError("request after deadline"))
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    summary = run(tmp_path, cfg, days=1, max_runtime=1)
    assert summary["runtime_exhausted"]
    opener.assert_not_called()
    assert not list((tmp_path / "raw").rglob("*.bi5"))


@pytest.mark.parametrize("code", [429, 503])
def test_retry_after_from_one_worker_delays_other_workers(tmp_path, monkeypatch, code):
    cfg = config(tmp_path, 4)
    cfg.update(max_attempts=1, retry_base_seconds=5, retry_jitter_fraction=0)
    clock, sleeps, request_times = [0.0], [], []
    gate = importer.RequestGate()
    cfg["_request_gate"] = gate

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def opener(*args, **kwargs):
        request_times.append(clock[0])
        if len(request_times) == 1:
            raise urllib.error.HTTPError(
                "https://mock.test", code, "busy", {"Retry-After": "20"}, io.BytesIO()
            )
        return Response()

    monkeypatch.setattr(importer.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(importer.time, "sleep", sleep)
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    rejected, release = threading.Event(), threading.Event()

    def first_worker():
        try:
            return importer.fetch_bytes("https://mock.test/a", cfg)
        except urllib.error.HTTPError:
            rejected.set()
            assert release.wait(timeout=5)
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_worker)
        assert rejected.wait(timeout=5)
        try:
            assert pool.submit(importer.fetch_bytes, "https://mock.test/b", cfg).result() == PAYLOAD
        finally:
            release.set()
        with pytest.raises(urllib.error.HTTPError):
            first.result()
    assert request_times == [0, 20]
    assert sleeps == [0, 0, 20]


def test_excessive_retry_after_stops_all_workers_without_shortening_it(tmp_path, monkeypatch):
    cfg = config(tmp_path, 4)
    cfg.update(max_attempts=1, file_attempts=1, backoff_cap_seconds=60)
    gate = importer.RequestGate()
    cfg["_request_gate"] = gate
    opener = MagicMock(
        side_effect=urllib.error.HTTPError(
            "https://mock.test", 429, "busy", {"Retry-After": "120"}, io.BytesIO()
        )
    )
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    monkeypatch.setattr(importer.time, "sleep", MagicMock())
    with pytest.raises(importer.RuntimeExpiredError):
        importer.fetch_bytes("https://mock.test/a", cfg)
    assert gate.provider_pause
    with pytest.raises(importer.RuntimeExpiredError):
        importer.fetch_bytes("https://mock.test/b", cfg)
    assert opener.call_count == 1
