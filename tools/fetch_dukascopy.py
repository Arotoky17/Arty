"""Fetch XAUUSD M1 bid/ask from 2020 through current closed UTC minute; no backtest."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from arty_trading.config.operational import load_config
from arty_trading.validation.dukascopy_import import (
    apply_request_delay,
    day_url,
    fetch_bytes,
    run_fetch,
)


def diagnose_network() -> dict:
    """Two bounded HTTPS probes, no raw writes and no holdout reads."""
    cfg = {**load_config("data_import.yaml"), "max_attempts": 1}
    start = datetime.fromisoformat(cfg["start"])
    probes = []
    for url in ("https://www.dukascopy.com/", day_url(start, "bid", cfg)):
        try:
            payload = fetch_bytes(url, cfg)
            probes.append(
                {
                    "url": url,
                    "status": "reachable" if payload else "HTTP_404",
                    "bytes": len(payload) if payload else 0,
                }
            )
        except Exception as error:
            reason = getattr(error, "reason", error)
            probes.append(
                {
                    "url": url,
                    "status": "request_failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "errno": getattr(reason, "errno", None),
                    "winerror": getattr(reason, "winerror", None),
                }
            )
    return {
        "probes": probes,
        "raw_written": False,
        "holdout_accessed": False,
        "interpretation": "Observed from this process only; no proof of provider outage",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--until",
        type=datetime.fromisoformat,
        help="Exclusive UTC end within development; never reads holdout objects",
    )
    parser.add_argument(
        "--delay",
        type=float,
        help="Base seconds between requests, with jitter (default from config: 4)",
    )
    parser.add_argument(
        "--max-runtime",
        type=float,
        help="Download budget in seconds, excluding resume indexing and optional existing audit",
    )
    parser.add_argument(
        "--file-attempts",
        type=int,
        help="Attempts per file before deferring it (default 8)",
    )
    parser.add_argument("--user-agent", help="Override the explicit User-Agent header")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent downloads (1..32); immutable publication has one writer",
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Explicitly reread/checksum existing files and certify legacy BID/ASK pairs",
    )
    parser.add_argument(
        "--csv-root",
        type=Path,
        help="Plan B: ingest external M1 CSV bid/ask exports from this directory",
    )
    parser.add_argument(
        "--diagnose-network",
        action="store_true",
        help="Probe website and a 2020 BI5 URL once each; no import",
    )
    args = parser.parse_args()
    if args.diagnose_network:
        print(json.dumps(diagnose_network(), indent=2))
        return
    if args.csv_root is not None:
        from arty_trading.validation.csv_import import run_csv_import

        print(json.dumps(run_csv_import(args.csv_root, args.output, until=args.until), indent=2))
        return
    cfg = load_config("data_import.yaml")
    if not 1 <= args.workers <= 32:
        raise SystemExit("--workers must be between 1 and 32")
    cfg["workers"] = args.workers
    if args.delay is not None:
        cfg = apply_request_delay(cfg, args.delay)
    if args.file_attempts is not None:
        if args.file_attempts < 1:
            raise SystemExit("--file-attempts must be a positive integer")
        cfg = {**cfg, "file_attempts": args.file_attempts}
    if args.user_agent:
        cfg = {**cfg, "user_agent": args.user_agent}
    print(
        json.dumps(
            run_fetch(
                args.output,
                until=args.until,
                max_runtime=args.max_runtime,
                cfg=cfg,
                verify_existing=args.verify_existing,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
