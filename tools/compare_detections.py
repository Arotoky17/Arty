"""Compare six dev months against the pinned pre-refactor Git revision, no P&L."""

import argparse
import sys
from pathlib import Path

from arty_trading.validation.detection_regression import run_regression


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=Path("reports/detection_regression"))
    args = parser.parse_args()
    report = run_regression(args.data, args.symbol, args.output)
    print(f"{report['status']}: {len(report['undocumented_differences'])} undocumented differences")
    return int(bool(report["undocumented_differences"]))


if __name__ == "__main__":
    sys.exit(main())
