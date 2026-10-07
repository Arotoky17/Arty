"""Prepare MT5 ask, spread comparison and gap overlays locally; no backtest."""

import argparse
import json
from pathlib import Path

from arty_trading.validation.mt5_gaps import write_gap_report
from arty_trading.validation.mt5_quality import compare_duka, reconstruct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("data/external/mt5_source/mt5_xauusd_m1.csv")
    )
    parser.add_argument("--data", type=Path, default=Path("data/external/mt5_converted"))
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/data_readiness/mt5_csv/quality")
    )
    parser.add_argument("--gaps-only", action="store_true")
    args = parser.parse_args()
    if not args.gaps_only:
        bid, spreads, _ = reconstruct(args.source, args.data, args.output)
        comparison = compare_duka(bid, spreads, args.raw)
        (args.output / "dukascopy_spread_comparison.json").write_text(
            json.dumps(comparison, indent=2)
        )
    gaps = write_gap_report(args.data, args.output)
    print(json.dumps(gaps["totals"], indent=2))


if __name__ == "__main__":
    main()
