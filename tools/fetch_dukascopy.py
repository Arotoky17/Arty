"""Fetch XAUUSD M1 bid/ask from 2020 through current closed UTC minute; no backtest."""

import argparse
from pathlib import Path

from arty_trading.validation.dukascopy_import import run_fetch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    print(run_fetch(args.output))


if __name__ == "__main__":
    main()
