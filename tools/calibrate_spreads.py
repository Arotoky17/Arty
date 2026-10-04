"""Calibrate UTC hourly close-quote spreads on development only; no strategy/P&L."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from arty_trading.config.operational import load_config
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import index_utc, safe_output, source_frames, write_report


def calibrate(
    directory: Path,
    output: Path,
    *,
    allow_partial: bool = False,
    effective_year: int | None = None,
    allow_holdout_quotes: bool = False,
) -> dict:
    safe_output(directory, output)
    raw, provenance = source_frames(directory, "XAUUSD")
    bid, ask = (index_utc(raw[side], "ms") for side in ("bid", "ask"))
    split = load_config("split.yaml")["development"]
    partition = "development"
    if effective_year is not None:
        if not allow_holdout_quotes:
            raise PermissionError("Annual recalibration requires --allow-holdout-quotes")
        import pandas as pd

        if effective_year <= pd.Timestamp(split["end"]).year:
            raise ValueError("First holdout year uses the full development calibration")
        split = {
            "start": f"{effective_year - 1}-01-01T00:00:00+00:00",
            "end": f"{effective_year}-01-01T00:00:00+00:00",
        }
        partition = "previous_year_quotes_only"
    import pandas as pd

    if pd.Timestamp(split["end"]) > pd.Timestamp.now(tz="UTC"):
        raise ValueError("Calibration cannot use a future period")
    bid = bid.loc[(bid.index >= split["start"]) & (bid.index < split["end"])]
    if bid.empty:
        raise ValueError("No quotes in calibration period")
    ask = ask.reindex(bid.index)
    if ask.close.isna().any() or (ask.close < bid.close).any():
        raise ValueError("Calibration requires aligned nonnegative bid/ask spreads")
    expected = pd.date_range(split["start"], split["end"], freq="MS", inclusive="left")
    missing = sorted(set(expected.strftime("%Y-%m")) - set(bid.index.strftime("%Y-%m")))
    complete = not missing and (
        bid.index.min() <= pd.Timestamp(split["start"])
        and bid.index.max() + pd.Timedelta(minutes=1) >= pd.Timestamp(split["end"])
    )
    if not complete and not allow_partial:
        raise ValueError("Entire calibration period required; diagnostic only with --allow-partial")
    bid = bid.loc[~MarketCalendar().annotate(bid).entry_blocked]
    cfg = load_config("execution.yaml")["cost"]
    model = CostModel(**{**cfg, "spread_mode": "configured"})
    from arty_trading.utils.helpers import get_pip_size

    spreads = (ask.close.reindex(bid.index) - bid.close) / float(get_pip_size("XAUUSD"))
    # Exclude configured major-news windows before applying a separate markup.
    import pandas as pd

    excluded = pd.Series(False, index=spreads.index)
    for event in model.news_events:
        at = pd.Timestamp(event["time_utc"])
        excluded |= (spreads.index >= at - pd.Timedelta(minutes=model.news_before_minutes)) & (
            spreads.index <= at + pd.Timedelta(minutes=model.news_after_minutes)
        )
    spreads = spreads.loc[~excluded]
    mid = (ask.close.reindex(spreads.index) + bid.close.reindex(spreads.index)) / 2
    fractions = spreads * cfg["pip_usd"] / mid
    hours = {}
    for hour, values in spreads.groupby(spreads.index.hour):
        assumed = model.spread_at(values.index[0].to_pydatetime())
        hours[int(hour)] = {
            "observations": len(values),
            "median_pips": float(values.quantile(cfg["calibration_quantiles"]["median"])),
            "p75_pips": float(values.quantile(cfg["calibration_quantiles"]["p75"])),
            "p75_fraction_price": float(
                fractions.reindex(values.index).quantile(cfg["calibration_quantiles"]["p75"])
            ),
            "p90_pips": float(values.quantile(cfg["calibration_quantiles"]["p90"])),
            "previous_assumed_pips": assumed,
            "p75_minus_assumed_pips": float(values.quantile(cfg["calibration_quantiles"]["p75"]))
            - assumed,
        }
    if len(hours) != 24:
        raise ValueError("Calibration requires observations for all 24 UTC hours")
    report = {
        "partition": partition,
        "effective_year": effective_year,
        "spread_unit": "fraction_price",
        "pip_usd": cfg["pip_usd"],
        "coverage_complete": bool(complete),
        "missing_months": missing,
        "calibration_start": split["start"],
        "calibration_end_exclusive": split["end"],
        "annual_policy": "Previous calendar year quotes only; freeze before evaluated run",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "instrument": "XAUUSD",
        "hours": hours,
        "sample_start_utc": bid.index.min().isoformat(),
        "sample_end_utc": bid.index.max().isoformat(),
        "limitation": (
            "M1 close quote spreads, not simultaneous executable ticks; see coverage_complete"
        ),
        "news_minutes_excluded": int(excluded.sum()),
        "provenance": provenance,
        "pnl_inspected": False,
        "raw_data_modified": False,
    }
    write_report(output / "spread_calibration.json", report)
    (output / "spread_calibration.yaml").write_text(yaml.safe_dump(report), encoding="utf-8")
    with (output / "calibration_access.jsonl").open("a", encoding="utf-8") as journal:
        journal.write(
            json.dumps(
                {
                    "timestamp": report["generated_at_utc"],
                    "effective_year": effective_year,
                    "partition": partition,
                    "pnl_inspected": False,
                    "purpose": "spread_quotes_only",
                    "coverage_complete": bool(complete),
                }
            )
            + "\n"
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--output", type=Path, default=Path("reports/data_readiness/spreads"))
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--effective-year", type=int)
    parser.add_argument("--allow-holdout-quotes", action="store_true")
    args = parser.parse_args()
    print(
        calibrate(
            args.data,
            args.output,
            allow_partial=args.allow_partial,
            effective_year=args.effective_year,
            allow_holdout_quotes=args.allow_holdout_quotes,
        )["hours"]
    )
