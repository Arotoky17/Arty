"""Monthly dev/holdout D1 ADX(14) and H4 structure; never calculates P&L."""

import argparse
from pathlib import Path

from arty_trading.validation.monthly_regimes import run_monthly_regimes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=Path("reports/data_readiness/regimes"))
    parser.add_argument("--allow-holdout-market-audit", action="store_true")
    args = parser.parse_args()
    report = run_monthly_regimes(
        args.data, args.symbol, args.output, enabled=args.allow_holdout_market_audit
    )
    print({name: result["status"] for name, result in report["periods"].items()})


if __name__ == "__main__":
    main()
