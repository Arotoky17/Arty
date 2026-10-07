"""Offline importer benchmark: mocked HTTPS, real decode/checksums/fsync, no backtest."""

import argparse
import cProfile
import gc
import io
import json
import lzma
import pstats
import struct
import tempfile
import time
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from arty_trading.config.operational import load_config
from arty_trading.validation import dukascopy_import as importer
from tools.import_progress import progress


def benchmark(days=30, workers=1, mock_latency=0.0, cprofile=True):
    payload = lzma.compress(
        b"".join(
            struct.pack(">5If", minute * 60, 2000000, 2001000, 1999000, 2002000, 1.0)
            for minute in range(1440)
        )
    )
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = payload
    response.headers = {"Content-Length": str(len(payload))}

    def mock_download(*args, **kwargs):
        if mock_latency:
            Event().wait(mock_latency)
        return response

    start = datetime(2024, 1, 1, tzinfo=UTC)
    result = {}
    Path(".quality-cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-profile-", dir=".quality-cache") as directory:
        root = Path(directory)
        cfg = importer.apply_request_delay(load_config("data_import.yaml"), 2)
        cfg["request_delay_min_seconds"] = cfg["request_delay_max_seconds"] = 2.0
        cfg.update(
            start=start.isoformat(),
            staging=str(root / "staging"),
            report_output=str(root / "report"),
            workers=workers,
        )
        with (
            patch.object(importer.urllib.request, "urlopen", side_effect=mock_download) as opener,
            patch.object(importer.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()),
        ):
            for name in ("fresh", "resume"):
                profiler = cProfile.Profile()
                began = time.perf_counter()
                if cprofile:
                    profiler.enable()
                summary = importer.run_fetch(
                    root / "raw",
                    now=datetime(2026, 10, 5, tzinfo=UTC),
                    until=start + timedelta(days=days),
                    cfg=cfg,
                )
                if cprofile:
                    profiler.disable()
                elapsed = time.perf_counter() - began
                stats = pstats.Stats(profiler).stats if cprofile else {}
                phases = {}
                for (_, _, function), (primitive, calls, own, cumulative, _) in stats.items():
                    if function in {
                        "fetch_bytes",
                        "decode_m1",
                        "digest",
                        "publish_immutable",
                        "read_manifest",
                    } or any(part in function for part in ("fsync", "decompress")):
                        phases[function] = {
                            "calls": calls,
                            "own_seconds": own,
                            "cumulative_seconds": cumulative,
                        }
                result[name] = {
                    "seconds": elapsed,
                    "workers": workers,
                    "mock_latency_seconds": mock_latency,
                    "cprofile": cprofile,
                    "files": days * 2,
                    "seconds_per_file": elapsed / (days * 2),
                    "phases": phases,
                    "https_calls": opener.call_count,
                    "sleep_calls": sleep.call_count,
                    "requested_sleep_seconds": sum(c.args[0] for c in sleep.call_args_list),
                }
                result[name]["per_file_phase_seconds"] = {
                    key: value / (days * 2)
                    for key, value in summary.get("phase_timings", {}).items()
                }
                result[name]["manifest_append_seconds"] = summary.get("manifest_append_seconds")
                result[name]["existing_checksum_seconds"] = summary.get("existing_checksum_seconds")
                result[name]["resume_seconds"] = summary.get("resume_seconds")
                opener.reset_mock()
                sleep.reset_mock()
            gc.collect()  # SQLite context managers commit but do not close connections.
    profiler = cProfile.Profile()
    began = time.perf_counter()
    profiler.runcall(progress)
    result["progress"] = {"seconds": time.perf_counter() - began}
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(12)
    result["progress"]["profile"] = output.getvalue()
    return result


def audit_history(directory=Path("data/raw")):
    """Compare repeated versus distinct SHA-256 reads of the real local history."""
    entries = importer.read_manifest(directory / "import_manifest.jsonl")
    objects = [
        (Path(row[path_key]), row[hash_key])
        for row in entries
        if row["status"] == "verified"
        for path_key, hash_key in (("path", "sha256"), ("binary_path", "binary_sha256"))
        if path_key in row
    ]
    result = {"manifest_records": len(entries)}
    for name, workload in (("before", objects), ("after", list(dict.fromkeys(objects)))):
        began = time.perf_counter()
        for path, expected in workload:
            if not path.exists() or importer.digest(path.read_bytes()) != expected:
                raise ValueError(f"Imported object missing or checksum changed: {path}")
        result[name] = {"object_reads": len(workload), "seconds": time.perf_counter() - began}
    return result


def resume_benchmark(days=300, repetitions=10):
    """Legacy 600-file manifest with repeated entries; raw objects are intentionally absent."""
    Path(".quality-cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resume-profile-", dir=".quality-cache") as directory:
        root = Path(directory)
        output = root / "raw"
        output.mkdir()
        start = datetime(2024, 1, 1, tzinfo=UTC)
        cutoff = start + timedelta(days=days)
        cfg = load_config("data_import.yaml")
        cfg.update(
            start=start.isoformat(),
            workers=4,
            staging=str(root / "stage"),
            report_output=str(root / "report"),
        )
        rows = []
        for i in range(days):
            day = start + timedelta(days=i)
            for side in cfg["sides"]:
                base = output / "xauusd" / side
                rows.append(
                    {
                        "status": "verified",
                        "date": day.date().isoformat(),
                        "side": side,
                        "rows": 1440,
                        "snapshot_cutoff": cutoff.isoformat(),
                        "last_closed_at": (day + timedelta(days=1)).isoformat(),
                        "path": str(base / "m1" / f"{day.date()}.csv"),
                        "binary_path": str(base / "bi5" / f"{day.date()}.bi5"),
                        "sha256": "a" * 64,
                        "binary_sha256": "b" * 64,
                    }
                )
        manifest = output / "import_manifest.jsonl"
        manifest.write_text(
            "".join(json.dumps(row) + "\n" for row in rows) * repetitions, encoding="utf-8"
        )
        before = manifest.read_bytes()
        began = time.perf_counter()
        with (
            patch.object(importer, "fetch_bytes", side_effect=AssertionError("network")),
            patch.object(importer, "digest", side_effect=AssertionError("checksum")),
            patch.object(importer, "decode_m1", side_effect=AssertionError("validation")),
        ):
            summary = importer.run_fetch(
                output,
                cfg=cfg,
                until=cutoff,
                now=datetime(2026, 10, 5, tzinfo=UTC),
                max_runtime=1e-9,
            )
        elapsed = time.perf_counter() - began
        assert before == manifest.read_bytes()
        assert summary["reused_daily_sides"] == days * 2
        return {
            "seconds": elapsed,
            "resume_seconds": summary["resume_seconds"],
            "files": days * 2,
            "manifest_entries": len(rows) * repetitions,
            "network_calls": 0,
            "raw_reads": 0,
            "checksums": 0,
            "validations": 0,
            "manifest_unchanged": True,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-history", action="store_true")
    parser.add_argument("--resume-only", action="store_true")
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--mock-latency", type=float, default=0.0)
    parser.add_argument(
        "--no-cprofile",
        action="store_true",
        help="Use phase timers only for worker-count wall-clock comparisons",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = (
        resume_benchmark(args.days, args.repetitions)
        if args.resume_only
        else audit_history()
        if args.audit_history
        else benchmark(args.days, args.workers, args.mock_latency, not args.no_cprofile)
    )
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)
