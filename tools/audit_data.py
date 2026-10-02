"""Monthly read-only CSV audit; never executes a backtest."""

import argparse
from pathlib import Path

from arty_trading.validation.data_audit import run_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument(
        "--ticks", type=Path, nargs="+", help="Optional timestamp/bid/ask tick CSVs"
    )
    parser.add_argument("--output", type=Path, default=Path("reports/data_audit"))
    args = parser.parse_args()
    report = run_audit(args.data, args.symbol, args.output, args.ticks)
    print(
        f"Audit written to {args.output}; {len(report['coverage_missing_months'])} missing months"
    )


if __name__ == "__main__":
    main()
