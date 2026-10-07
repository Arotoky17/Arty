"""Convert MetaQuotes-Demo XAUUSD M1 bid exports to csv_import daily UTC files.

No network. No backtest. Does not modify split.yaml.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from arty_trading.config.operational import CONFIG_ROOT
from arty_trading.validation.mt5_csv import (
    DECISIONS_PENDING,
    DEFAULT_CANDIDATES,
    collect_mt5_bars,
    compare_mt5_to_dukascopy,
    convert_mt5,
    depth_report,
    detect_timezone,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("convert", "detect-timezone", "compare-hypotheses", "compare-timing")
    )
    parser.add_argument("--input", type=Path, required=True, help="MT5 CSV file or directory")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/external"),
        help="csv_import layout root (default data/external)",
    )
    parser.add_argument(
        "--server-timezone",
        help="Mandatory IANA zone for convert; optional filter label for detect-timezone",
    )
    parser.add_argument(
        "--point",
        type=Decimal,
        help="Mandatory MT5 point size for ask reconstruction (spread * point)",
    )
    parser.add_argument(
        "--bid-only", action="store_true", help="Convert bid without inventing a point or ask"
    )
    parser.add_argument(
        "--timezone-evidence", type=Path, help="Reuse an existing local timezone evidence report"
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--broker", default="MetaQuotes-Demo")
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=list(DEFAULT_CANDIDATES),
        help="IANA zones to score; none is selected automatically",
    )
    parser.add_argument("--news-calendar", type=Path, default=CONFIG_ROOT / "news_calendar.csv")
    parser.add_argument("--dukascopy-raw", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("reports/data_readiness/mt5_csv"),
    )
    args = parser.parse_args()
    if args.command == "compare-timing":
        from arty_trading.validation.mt5_timing import compare_local

        report = compare_local(args.input, args.dukascopy_raw)
        write_json(args.report_output / "timing_2020.json", report)
        print(
            json.dumps(
                {
                    "confirmed": report["dst_eu_confirmed_2020_available_overlap"],
                    "divergent_days": report["divergent_days"],
                    "ambiguous_days": report["ambiguous_days"],
                },
                indent=2,
            )
        )
        return
    if args.command == "compare-hypotheses":
        from arty_trading.validation.mt5_hypotheses import compare_hypotheses

        report = compare_hypotheses(args.input, args.news_calendar)
        write_json(args.report_output / "timezone_hypotheses.json", report)
        print(
            json.dumps(
                {"selected_hypothesis": report["selected_hypothesis"], "years": report["years"]},
                indent=2,
            )
        )
        return
    bars = collect_mt5_bars(args.input)
    reports = args.report_output
    reports.mkdir(parents=True, exist_ok=True)
    years = Counter(bar.local.year for bar in bars)
    write_json(
        reports / "source_inventory.json",
        {
            "source": str(args.input),
            "source_bars": len(bars),
            "first_server_local": min(bar.local for bar in bars).isoformat(),
            "last_server_local": max(bar.local for bar in bars).isoformat(),
            "rows_by_server_year": dict(sorted(years.items())),
            "missing_years_since_2015": [
                year for year in range(2015, max(years) + 1) if year not in years
            ],
            "utc_conversion_status": "pending_explicit_server_timezone_and_point",
            "network": False,
            "backtest_run": False,
        },
    )
    write_json(reports / "decisions_pending.json", {"decisions_pending": DECISIONS_PENDING})
    if args.command == "detect-timezone":
        evidence = detect_timezone(
            bars, candidates=tuple(args.candidates), news_path=args.news_calendar
        )
        write_json(reports / "timezone_evidence.json", evidence)
        print(
            json.dumps(
                {
                    "ambiguities": evidence["ambiguities"],
                    "ranked": evidence["ranked_without_selection"],
                },
                indent=2,
            )
        )
        return
    if not args.server_timezone:
        raise SystemExit(
            "convert requires --server-timezone (IANA). Detection does not fill this in."
        )
    if args.point is None and not args.bid_only:
        raise SystemExit("convert requires --point (MT5 point size). Do not guess digits.")
    conversion = convert_mt5(
        bars,
        zone_name=args.server_timezone,
        point=args.point,
        output=args.output,
        symbol=args.symbol,
        broker=args.broker,
        bid_only=args.bid_only,
    )
    evidence = (
        json.loads(args.timezone_evidence.read_text(encoding="utf-8"))
        if args.timezone_evidence
        else detect_timezone(bars, candidates=tuple(args.candidates), news_path=args.news_calendar)
    )
    inventory_path = reports / "source_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["utc_conversion_status"] = "converted_with_explicit_parameters"
    inventory["server_timezone"] = args.server_timezone
    inventory["point"] = None if args.point is None else str(args.point)
    write_json(inventory_path, inventory)
    if args.symbol == "XAUUSD":
        from arty_trading.validation.mt5_depth import minute_depth

        depth = minute_depth(args.output)
        write_json(reports / "minute_depth_report.json", depth)
    else:
        depth = depth_report(args.output, symbol=args.symbol)
    comparison = None
    if args.dukascopy_raw.exists():
        comparison = compare_mt5_to_dukascopy(args.output, args.dukascopy_raw, symbol=args.symbol)
        write_json(reports / "dukascopy_compare_2020.json", comparison)
    write_json(reports / "conversion_summary.json", conversion)
    write_json(reports / "timezone_evidence.json", evidence)
    write_json(reports / "depth_report.json", depth)
    write_json(
        reports / "decisions_pending.json", {"decisions_pending": conversion["decisions_pending"]}
    )
    print(
        json.dumps(
            {
                "converted_daily_sides": conversion["converted_daily_sides"],
                "dst_issues": len(conversion["dst_issues"]),
                "duplicates_skipped": len(conversion["duplicates_skipped"]),
                "ask_origin": conversion["ask_origin"],
                "timezone_ambiguities": evidence.get(
                    "ambiguities", evidence.get("ambiguous_days", [])
                ),
                "coverage": depth["coverage"],
                "dukascopy_compare_days": None
                if comparison is None
                else comparison["days_compared"],
                "split_yaml_modified": False,
                "decisions_pending": [item["id"] for item in conversion["decisions_pending"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
