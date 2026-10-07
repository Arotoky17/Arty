"""Analyze an existing SpreadLogger CSV offline; never connects to a broker."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from arty_trading.validation.broker_spreads import BrokerSpreadHypothesis, analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spread-broker-reel", type=float, help="Independent floor in USD/oz")
    parser.add_argument("--evidence", help="Independent broker cost evidence; required with floor")
    args = parser.parse_args()
    raw = Path("data/raw").resolve()
    output = args.output.resolve()
    if output == args.input.resolve() or output == raw or raw in output.parents:
        parser.error("Output must be separate from source and data/raw")
    result = analyze(pd.read_csv(args.input))
    result["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    if args.spread_broker_reel is not None:
        hypothesis = BrokerSpreadHypothesis(args.spread_broker_reel, args.evidence or "")
        result["hypothesis_not_activated"] = {
            "spread_broker_reel_usd_per_oz": hypothesis.spread_broker_reel_usd_per_oz,
            "evidence": hypothesis.evidence,
            "stress_multipliers": [1, 1.5, 2],
            "rule": "max(reconstructed_spread, independent_floor) * stress; charge once",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
