"""Verify holdout trend/range coverage from OHLC only, with explicit authorization."""

import argparse
from pathlib import Path

from arty_trading.validation.regime_audit import run_regime_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=Path("reports/holdout_market_audit"))
    parser.add_argument("--allow-holdout-market-audit", action="store_true")
    args = parser.parse_args()
    report = run_regime_audit(
        args.data, args.symbol, args.output, enabled=args.allow_holdout_market_audit
    )
    print(f"Holdout regime coverage: {report['status']}; no P&L inspected")


if __name__ == "__main__":
    main()
