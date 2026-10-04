"""Explain historical bid/ask audit flags without importing or altering market data."""

import argparse
from pathlib import Path

from arty_trading.config.operational import CONFIG_ROOT, load_config
from arty_trading.validation.data_audit import bar_outlier_metrics
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import (
    OHLC,
    index_utc,
    safe_output,
    source_frames,
    write_report,
)
from arty_trading.validation.news_calendar import read_news_calendar


def investigate(directory: Path, output: Path) -> dict:
    safe_output(directory, output)
    cfg = load_config("validation.yaml")["audit"]
    raw, provenance = source_frames(directory, "XAUUSD")
    frames = {side: index_utc(frame, cfg["timestamp_unit"]) for side, frame in raw.items()}
    metrics = {side: bar_outlier_metrics(frame, cfg) for side, frame in frames.items()}
    times = metrics["bid"].index[metrics["bid"].legacy_outlier][:5]
    examples = []
    for time in times:
        example = {"time_utc": time.isoformat()}
        for side in ("bid", "ask"):
            row = frames[side].loc[time]
            value = metrics[side].loc[time]
            example[side] = {
                "timestamp": int(row.timestamp),
                **{name: float(row[name]) for name in OHLC},
                "true_range": float(value.true_range),
                "past_atr_grid": float(value.past_atr_grid),
                "past_atr_active": float(value.past_atr_active),
            }
        examples.append(example)
    common = frames["bid"].index.intersection(frames["ask"].index)
    summaries = {}
    remaining = []
    news = read_news_calendar(CONFIG_ROOT / "news_calendar.csv", ["NFP", "FOMC", "CPI"])
    calendar = MarketCalendar()
    for side, value in metrics.items():
        summaries[side] = {
            "legacy_count": int(value.legacy_outlier.sum()),
            "corrected_count": int(value.outlier.sum()),
            "padding_atr_false_positives": int(value.padding_atr_false_positive.sum()),
            "absolute_return_flags": int(value.absolute_return_flag.sum()),
            "absolute_range_flags": int(value.absolute_range_flag.sum()),
        }
        for time, row in value.loc[value.outlier].iterrows():
            remaining.append(
                {
                    "side": side,
                    "time_utc": time.isoformat(),
                    "true_range": float(row.true_range),
                    "spread_price": float(
                        frames["ask"].loc[time].close - frames["bid"].loc[time].close
                    ),
                    "calendar_reason": calendar.state(time.to_pydatetime())["reason"],
                    "news": [
                        event["type"] for event in news if event["time_utc"] == time.isoformat()
                    ],
                    "classification": (
                        "news"
                        if any(event["time_utc"] == time.isoformat() for event in news)
                        else (
                            calendar.state(time.to_pydatetime())["reason"]
                            if calendar.state(time.to_pydatetime())["reason"] != "open"
                            else "other"
                        )
                    ),
                }
            )
    report = {
        "criterion": {
            "return": "abs(close / previous_close - 1) > 0.02",
            "range": "(high - low) / previous_close > 0.02",
            "spike": "TR > 20 * EWM(TR.shift(1), alpha=1/14, adjust=False), ATR > 0",
            "TR": "max(high-low, abs(high-previous_close), abs(low-previous_close))",
            "combination": "OR; thresholds come from validation.yaml and definitions.yaml",
        },
        "summary": summaries,
        "raw_examples": examples,
        "remaining_candidates": remaining,
        "alignment": {
            "bid_only_timestamps": len(frames["bid"].index.difference(common)),
            "ask_only_timestamps": len(frames["ask"].index.difference(common)),
            "negative_close_spreads": int(
                (frames["ask"].loc[common].close < frames["bid"].loc[common].close).sum()
            ),
            "utc_clock": "epochs align; provider clock attestation remains unavailable",
        },
        "conclusion": (
            "Predominantly (a): unchanged flat grid rows decay ATR artificially. "
            "No observed timestamp misalignment. Remaining paired large moves are "
            "candidates, not proven corruption; compare original ticks before rejecting."
        ),
        "correction": (
            "Exclude calendar-closed bars from the ATR observation clock; "
            "retain all raw rows, old flags and absolute 2% checks. No price interpolation."
        ),
        "provenance": provenance,
        "calendar_limitation": (
            "Five extra paired candidates on US holidays (Jan15, Feb19, Mar31 after Good Friday, "
            "May27, Jun19); historic provider holiday notices unavailable. Retain flags, "
            "do not classify all quiet open bars as padding."
        ),
        "raw_data_modified": False,
        "pnl_inspected": False,
    }
    write_report(output / "investigation.json", report)
    lines = [
        "# Investigation bid/ask",
        "",
        report["conclusion"],
        "",
        str(summaries),
        "",
        "UTC | côté | open | high | low | close",
        "---|---|---:|---:|---:|---:",
    ]
    for example in examples:
        for side in ("bid", "ask"):
            prices = " | ".join(f"{example[side][name]:.3f}" for name in OHLC)
            lines.append(f"{example['time_utc']} | {side} | {prices}")
    lines += ["", report["correction"], "", "Aucun P&L ni changement des données brutes."]
    (output / "investigation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/historical"))
    parser.add_argument("--output", type=Path, default=Path("reports/data_readiness/anomalies"))
    args = parser.parse_args()
    report = investigate(args.data, args.output)
    print(report["summary"])


if __name__ == "__main__":
    main()
