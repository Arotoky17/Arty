"""Generate seeded detector charts and weekly frequencies on development OHLC only."""

import argparse
from pathlib import Path

from arty_trading.validation.detection_review import run_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=Path("reports/detection_review"))
    args = parser.parse_args()
    report = run_review(args.data, args.symbol, args.output)
    print({name: len(value["selected"]) for name, value in report["selections"].items()})


if __name__ == "__main__":
    main()
