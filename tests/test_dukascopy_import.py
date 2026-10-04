"""Offline network and immutable importer regression tests; no terminal/backtest."""

import importlib
import io
import json
import lzma
import py_compile
import ssl
import struct
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import arty_trading.validation.dukascopy_import as importer
from arty_trading.config.operational import load_config

DAY = datetime(2024, 1, 4, tzinfo=UTC)


def bi5(seconds=(0,), price=2000000):
    return lzma.compress(
        b"".join(
            struct.pack(">5If", at, price, price + 1000, price - 1000, price + 2000, 1.0)
            for at in seconds
        )
    )


@pytest.fixture
def network(monkeypatch):
    cfg = load_config("data_import.yaml")
    # Deterministic waits: no inter-request delay and a fixed backoff schedule.
    cfg.update(
        request_delay_min_seconds=0,
        request_delay_max_seconds=0,
        retry_base_seconds=5,
        backoff_cap_seconds=60,
        retry_jitter_fraction=0.0,
    )
    sleep = MagicMock()
    opener = MagicMock()
    monkeypatch.setattr(importer.time, "sleep", sleep)
    monkeypatch.setattr(importer.urllib.request, "urlopen", opener)
    return cfg, opener, sleep


def response(payload):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = payload
    result.headers = {"Content-Length": str(len(payload))}
    return result


def http_error(code, header=""):
    return urllib.error.HTTPError(
        "https://example.test", code, "fixture", {"Retry-After": header}, io.BytesIO()
    )


def test_syntax_and_module_import(tmp_path):
    py_compile.compile(importer.__file__, cfile=str(tmp_path / "importer.pyc"), doraise=True)
    assert importlib.import_module(importer.__name__) is importer


def test_https_success_preserves_timeout_and_default_tls(network):
    cfg, opener, _ = network
    payload = bi5()
    opener.return_value = response(payload)
    assert importer.fetch_bytes("https://example.test", cfg) == payload
    assert opener.call_count == 1
    assert opener.call_args.kwargs == {"timeout": cfg["timeout_seconds"]}


def test_404_is_unavailable_and_not_retried(network):
    cfg, opener, sleep = network
    opener.side_effect = http_error(404)
    assert importer.fetch_bytes("https://example.test", cfg) is None
    assert opener.call_count == 1
    assert [call.args[0] for call in sleep.call_args_list] == [0]


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_temporary_http_retries_exactly_configured_maximum(network, code):
    cfg, opener, sleep = network
    opener.side_effect = [http_error(code) for _ in range(cfg["max_attempts"])]
    with pytest.raises(urllib.error.HTTPError):
        importer.fetch_bytes("https://example.test", cfg)
    assert opener.call_count == cfg["max_attempts"]
    assert [call.args[0] for call in sleep.call_args_list] == [0, 5, 0, 10, 0]


@pytest.mark.parametrize("code", [400, 401, 403, 410])
def test_permanent_http_does_not_retry(network, code):
    cfg, opener, _ = network
    opener.side_effect = http_error(code)
    with pytest.raises(urllib.error.HTTPError):
        importer.fetch_bytes("https://example.test", cfg)
    assert opener.call_count == 1


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timeout"),
        ConnectionResetError("reset"),
        urllib.error.URLError(TimeoutError("WinError 10060")),
    ],
)
def test_connection_failures_retry_then_preserve_original_error(network, error):
    cfg, opener, sleep = network
    opener.side_effect = error
    with pytest.raises(type(error)):
        importer.fetch_bytes("https://example.test", cfg)
    assert opener.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list if call.args[0]] == [5, 10]


def test_tls_verification_failure_is_not_bypassed(network):
    cfg, opener, _ = network
    opener.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate"))
    with pytest.raises(urllib.error.URLError):
        importer.fetch_bytes("https://example.test", cfg)
    assert opener.call_count == 1


@pytest.mark.parametrize("header", ["garbage", "NaN", "inf", "-20"])
def test_invalid_retry_after_falls_back_to_backoff(network, header):
    cfg, opener, sleep = network
    opener.side_effect = [http_error(503, header), response(bi5())]
    assert importer.fetch_bytes("https://example.test", cfg)
    assert [call.args[0] for call in sleep.call_args_list] == [0, 5, 0]


def test_retry_after_excessive_stops_without_shortening_provider_wait(network):
    cfg, opener, sleep = network
    opener.side_effect = http_error(429, "120")
    with pytest.raises(RuntimeError, match="exceeds cap"):
        importer.fetch_bytes("https://example.test", cfg)
    assert opener.call_count == 1
    assert [call.args[0] for call in sleep.call_args_list] == [0]


def test_retry_after_date_and_backoff_cap(monkeypatch):
    cfg = load_config("data_import.yaml")
    monkeypatch.setattr(importer, "utc_now", lambda: DAY)
    assert importer.retry_delay(cfg, 0, "42") == 42
    # An explicit provider wait is honoured and never shortened by the jitter.
    assert importer.retry_delay(cfg, 0, "Thu, 04 Jan 2024 00:00:30 GMT") >= 30
    assert importer.retry_delay(cfg, 10000) == cfg["backoff_cap_seconds"]


def test_partial_http_read_retries_and_discards_partial_bytes(network):
    cfg, opener, _ = network
    partial = response(b"partial")
    partial.headers["Content-Length"] = "100"
    opener.side_effect = [partial, response(bi5())]
    assert importer.fetch_bytes("https://example.test", cfg) == bi5()
    assert opener.call_count == 2


@pytest.mark.parametrize(
    "payload",
    [b"", b"not lzma", lzma.compress(b""), lzma.compress(b"short"), bi5()[:-1], bi5() + b"junk"],
)
def test_invalid_bi5_never_publishes_a_verified_day(tmp_path, monkeypatch, payload):
    cfg = load_config("data_import.yaml")
    cfg["staging"] = str(tmp_path / "staging")
    monkeypatch.setattr(importer, "fetch_bytes", lambda *args: payload)
    with pytest.raises(ValueError):
        importer.fetch_day(DAY, "bid", DAY + timedelta(days=1), tmp_path / "raw", cfg)
    assert not list((tmp_path / "raw").rglob("*.csv"))
    assert not list((tmp_path / "raw").rglob("*.bi5"))


def test_decoder_field_order_prices_and_future_cutoff():
    cfg = load_config("data_import.yaml")
    payload, info = importer.decode_m1(bi5((0, 60, 120)), DAY, DAY + timedelta(minutes=2), cfg)
    rows = list(importer.csv.DictReader(io.StringIO(payload.decode())))
    assert info["rows"] == 2
    assert [rows[0][key] for key in ("open", "high", "low", "close")] == [
        "2000.0",
        "2002.0",
        "1999.0",
        "2001.0",
    ]
    assert importer.day_url(DAY, "ask", cfg).endswith("/2024/00/04/ASK_candles_min_1.bi5")
    assert "/11/" in importer.day_url(DAY.replace(month=12), "bid", cfg)


def isolated_run(tmp_path, monkeypatch):
    original = importer.load_config
    cfg = original("data_import.yaml")
    cfg.update(
        start=DAY.isoformat(),
        staging=str(tmp_path / "staging"),
        report_output=str(tmp_path / "report"),
    )
    monkeypatch.setattr(importer.time, "sleep", MagicMock())  # never wait in tests
    monkeypatch.setattr(
        importer, "load_config", lambda name: cfg if name == "data_import.yaml" else original(name)
    )
    return cfg


def test_failing_side_is_deferred_and_never_blocks_the_next_file(tmp_path, monkeypatch):
    isolated_run(tmp_path, monkeypatch)
    urls = []

    def failed(url, cfg):
        urls.append(url)
        if "BID" in url:
            raise urllib.error.URLError(TimeoutError("WinError 10060"))
        return bi5()

    monkeypatch.setattr(importer, "fetch_bytes", failed)
    output = tmp_path / "raw"
    until = DAY + timedelta(days=1)
    with pytest.raises(RuntimeError, match="Required daily resources unavailable"):
        importer.run_fetch(output, now=datetime(2026, 10, 3, tzinfo=UTC), until=until)
    report = json.loads((tmp_path / "report" / "import_status.json").read_text())
    # The bid failure did not stop the ask file from being fetched and verified.
    assert report["verified_daily_sides"] == 1
    assert [(row["side"], row["status"]) for row in report["deferred_files"]] == [("bid", "failed")]
    assert report["missing_files"] == [{"date": "2024-01-04", "side": "bid"}]
    assert report["resume_required"] is True
    assert any("ASK" in url for url in urls)
    files = {str(path): path.read_bytes() for path in output.rglob("*.bi5")}
    urls.clear()
    monkeypatch.setattr(importer, "fetch_bytes", lambda url, cfg: urls.append(url) or bi5())
    summary = importer.run_fetch(output, now=datetime(2026, 10, 3, tzinfo=UTC), until=until)
    # Only the deferred side is re-requested; the verified one is served from cache.
    assert len(urls) == 1 and "BID" in urls[0]
    assert summary["bid_ask_pairing_validated"]
    assert summary["scope"] == "development_only"
    assert not summary["coverage_validated"]
    assert all(Path(path).read_bytes() == content for path, content in files.items())
    assert not list((tmp_path / "staging").glob("*.part"))


@pytest.mark.parametrize("ask", [None, bi5((60,)), bi5(price=1990000)])
def test_missing_or_misaligned_ask_cannot_finalize_import(tmp_path, monkeypatch, ask):
    isolated_run(tmp_path, monkeypatch)
    monkeypatch.setattr(importer, "fetch_bytes", lambda url, cfg: ask if "ASK" in url else bi5())
    monkeypatch.setattr(
        importer, "freeze_available_end", lambda *args: pytest.fail("freeze called")
    )
    with pytest.raises(RuntimeError):
        importer.run_fetch(
            tmp_path / "raw", now=datetime(2026, 10, 3, tzinfo=UTC), until=DAY + timedelta(days=1)
        )
    report = json.loads((tmp_path / "report" / "import_status.json").read_text())
    assert report["status"] == "failed"
    assert not report["bid_ask_pairing_validated"]


def test_until_guard_precedes_any_download(tmp_path, monkeypatch):
    isolated_run(tmp_path, monkeypatch)
    monkeypatch.setattr(importer, "fetch_bytes", lambda *args: pytest.fail("network called"))
    with pytest.raises(ValueError, match="development"):
        importer.run_fetch(
            tmp_path / "raw",
            now=datetime(2026, 1, 4, tzinfo=UTC),
            until=datetime(2025, 1, 2, tzinfo=UTC),
        )
    assert not (tmp_path / "raw").exists()
def test_expected_closed_day_without_provider_file_is_journaled_not_failed(tmp_path, monkeypatch):
    """A Saturday with no provider file is expected, never a retryable error."""
    isolated_run(tmp_path, monkeypatch)
    saturday = "/00/06/"

    def provider(url, cfg):
        return None if saturday in url else bi5()

    monkeypatch.setattr(importer, "fetch_bytes", provider)
    output = tmp_path / "raw"
    summary = importer.run_fetch(
        output, now=datetime(2026, 10, 3, tzinfo=UTC), until=datetime(2024, 1, 8, tzinfo=UTC)
    )
    assert summary["status"] == "downloaded"
    assert summary["expected_closed_days"] == 1  # 2024-01-06 is a Saturday
    assert summary["empty_expected_daily_sides"] == 2  # bid and ask
    assert summary["missing_files"] == []
    assert summary["bid_ask_pairing_validated"] is True
    journal = [
        json.loads(line)
        for line in (output / "import_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    empty = [row for row in journal if row.get("status") == "empty_expected"]
    assert {row["date"] for row in empty} == {"2024-01-06"}
    assert {row["side"] for row in empty} == {"bid", "ask"}
    assert all(row["rows"] == 0 for row in empty)


def test_open_day_without_provider_file_is_still_a_failure(tmp_path, monkeypatch):
    """Only a market day without a file blocks the import."""
    isolated_run(tmp_path, monkeypatch)
    monkeypatch.setattr(importer, "fetch_bytes", lambda url, cfg: None if "BID" in url else bi5())
    with pytest.raises(RuntimeError, match="Required daily resources unavailable"):
        importer.run_fetch(
            tmp_path / "raw",
            now=datetime(2026, 10, 3, tzinfo=UTC),
            until=DAY + timedelta(days=1),
        )
    report = json.loads((tmp_path / "report" / "import_status.json").read_text())
    assert report["status"] == "failed"
    assert report["missing_files"] == [{"date": "2024-01-04", "side": "bid"}]
    assert report["empty_expected_daily_sides"] == 0
def test_long_backoff_defers_one_file_after_n_attempts(tmp_path, monkeypatch):
    """503/timeout exhaust file_attempts, then defer instead of aborting the run."""
    cfg = load_config("data_import.yaml")
    cfg["staging"] = str(tmp_path / "staging")
    sleep = MagicMock()
    monkeypatch.setattr(importer.time, "sleep", sleep)
    calls = []

    def always_busy(url, settings):
        calls.append(url)
        raise urllib.error.HTTPError(url, 503, "busy", {"Retry-After": ""}, io.BytesIO())

    monkeypatch.setattr(importer, "fetch_bytes", always_busy)
    entry = importer.fetch_day_with_backoff(
        DAY, "bid", DAY + timedelta(days=1), tmp_path / "raw", {**cfg, "file_attempts": 3}
    )
    assert entry["status"] == "failed"
    assert entry["attempts"] == 3
    assert entry["deferred_for_resume"] is True
    assert (entry["date"], entry["side"]) == ("2024-01-04", "bid")
    assert len(calls) == 3
    assert sleep.call_count == 2  # a wait between attempts, none after the last
    waits = [call.args[0] for call in sleep.call_args_list]
    assert waits[0] >= cfg["retry_base_seconds"] * 0.75
    assert waits[1] > waits[0]  # exponential growth between file attempts


def test_retry_delay_is_long_bounded_and_jittered():
    cfg = load_config("data_import.yaml")
    samples = [importer.retry_delay(cfg, 0) for _ in range(50)]
    assert min(samples) >= cfg["retry_base_seconds"] * 0.75
    assert max(samples) <= cfg["retry_base_seconds"] * 1.25
    assert len(set(samples)) > 1  # jitter is real, not a constant
    assert importer.retry_delay(cfg, 10000) == cfg["backoff_cap_seconds"]
    assert cfg["retry_base_seconds"] >= 30
    assert cfg["backoff_cap_seconds"] >= 900
    assert cfg["file_attempts"] == 8


def test_max_runtime_bounds_the_session_without_failing(tmp_path, monkeypatch):
    isolated_run(tmp_path, monkeypatch)
    monkeypatch.setattr(importer, "fetch_bytes", lambda url, cfg: bi5())
    summary = importer.run_fetch(
        tmp_path / "raw",
        now=datetime(2026, 10, 3, tzinfo=UTC),
        until=DAY + timedelta(days=3),
        max_runtime=1e-9,
    )
    assert summary["status"] == "runtime_budget_exhausted"
    assert summary["runtime_exhausted"] is True
    assert summary["resume_required"] is True
    assert summary["failures"] == []
    assert summary["verified_daily_sides"] == 0
    assert summary["missing_files"]


def test_throughput_journal_reports_debit_and_error_rate():
    started = importer.time.monotonic() - 3600.0
    rate = importer.throughput(verified=4, attempted=6, started=started, sides=2)
    assert rate["verified_sides"] == 4
    assert rate["attempted_sides"] == 6
    assert rate["days_per_hour"] == pytest.approx(2.0, rel=0.01)
    assert rate["error_rate"] == pytest.approx(2 / 6)
    assert rate["elapsed_seconds"] > 3599


def test_user_agent_is_explicit_and_delay_is_configurable(network):
    cfg, opener, _ = network
    opener.return_value = response(bi5())
    assert importer.fetch_bytes("https://example.test", {**cfg, "user_agent": "Arty-test/9.9"})
    assert opener.call_args.args[0].get_header("User-agent") == "Arty-test/9.9"
    tuned = importer.apply_request_delay(cfg, 4.0)
    assert tuned["request_delay_base_seconds"] == 4.0
    assert tuned["request_delay_min_seconds"] == pytest.approx(3.0)
    assert tuned["request_delay_max_seconds"] == pytest.approx(5.0)
    assert tuned["request_delay_min_seconds"] <= tuned["request_delay_max_seconds"]
def write_m1_csv(path, day, rows, close_shift=0.0):
    opened = int(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp() * 1000)
    lines = ["timestamp,open,high,low,close,volume"]
    for index in range(rows):
        stamp = opened + index * 60_000
        price = 2000.0 + index * 0.1 + close_shift
        lines.append(f"{stamp},{price:.3f},{price + 0.5:.3f},{price - 0.5:.3f},{price:.3f},7")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_external_csv_import_publishes_append_only_with_same_manifest(tmp_path):
    """Plan B: external M1 CSV exports land in the same store, checksums and journal."""
    from arty_trading.validation.csv_import import run_csv_import

    root = tmp_path / "external"
    write_m1_csv(root / "bid" / "2024-01-04.csv", "2024-01-04", 5)
    write_m1_csv(root / "ask" / "2024-01-04.csv", "2024-01-04", 5, close_shift=0.2)
    cfg = load_config("data_import.yaml")
    cfg.update(
        start="2024-01-04T00:00:00+00:00",
        output=str(tmp_path / "raw"),
        staging=str(tmp_path / "staging"),
        report_output=str(tmp_path / "report"),
    )
    summary = run_csv_import(
        root,
        tmp_path / "raw",
        cfg=cfg,
        now=datetime(2026, 10, 3, tzinfo=UTC),
        until=datetime(2024, 1, 5, tzinfo=UTC),
    )
    assert summary["provider"] == "external_csv"
    assert summary["verified_daily_sides"] == 2
    assert summary["missing_files"] == []
    assert summary["deferred_files"] == []
    assert summary["status"] == "downloaded"
    published = sorted((tmp_path / "raw" / "xauusd" / "bid" / "m1").glob("*.csv"))
    assert [row.name for row in published] == ["2024-01-04.csv"]
    journal = [
        json.loads(line)
        for line in (tmp_path / "raw" / "import_manifest.jsonl").read_text().splitlines()
    ]
    assert {row["status"] for row in journal} == {"verified"}
    assert {row["provider"] for row in journal} == {"external_csv"}
    assert all(len(row["sha256"]) == 64 and row["rows"] == 5 for row in journal)
    # Re-running republishes identical bytes: append-only, never replaced.
    again = run_csv_import(
        root,
        tmp_path / "raw",
        cfg=cfg,
        now=datetime(2026, 10, 3, tzinfo=UTC),
        until=datetime(2024, 1, 5, tzinfo=UTC),
    )
    assert again["status"] == "downloaded"


def test_external_csv_import_rejects_bad_rows_as_deferred(tmp_path):
    """The same audit rejects malformed rows instead of publishing them."""
    from arty_trading.validation.csv_import import run_csv_import

    root = tmp_path / "external"
    write_m1_csv(root / "bid" / "2024-01-04.csv", "2024-01-04", 3)
    (root / "ask").mkdir(parents=True, exist_ok=True)
    (root / "ask" / "2024-01-04.csv").write_text(
        "timestamp,open,high,low,close,volume\n2024-01-04T00:00:00Z,1,2,0,1,1\n",
        encoding="utf-8",
    )
    cfg = load_config("data_import.yaml")
    cfg.update(
        start="2024-01-04T00:00:00+00:00",
        output=str(tmp_path / "raw"),
        staging=str(tmp_path / "staging"),
        report_output=str(tmp_path / "report"),
    )
    summary = run_csv_import(
        root,
        tmp_path / "raw",
        cfg=cfg,
        now=datetime(2026, 10, 3, tzinfo=UTC),
        until=datetime(2024, 1, 5, tzinfo=UTC),
    )
    assert summary["verified_daily_sides"] == 1
    assert any(row["status"] == "rejected" for row in summary["deferred_files"])
    assert summary["resume_required"] is True
    assert summary["status"] == "failed"
