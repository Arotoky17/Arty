"""Explicit market-only holdout inspection: OHLC/ADX, never signals or performance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from arty_trading.config.operational import load_config
from arty_trading.validation.market_data import (
    OHLC,
    index_utc,
    resample_closed_bars,
    safe_output,
    source_frames,
    write_report,
)


def wilder_mean(series: pd.Series, period: int) -> pd.Series:
    """Seed with the mean of the first period valid observations, then recurse."""
    result = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) < period:
        return result
    previous = float(valid.iloc[:period].mean())
    result.loc[valid.index[period - 1]] = previous
    for time, value in valid.iloc[period:].items():
        previous = (previous * (period - 1) + float(value)) / period
        result.loc[time] = previous
    return result


def adx(frame: pd.DataFrame, period: int) -> pd.Series:
    if period < 2:
        raise ValueError("ADX period must be at least 2")
    up = frame.high.diff()
    down = -frame.low.diff()
    positive = up.where((up > down) & (up > 0), 0.0)
    negative = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - frame.close.shift()).abs(),
            (frame.low - frame.close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if len(frame):
        tr.iloc[0] = positive.iloc[0] = negative.iloc[0] = np.nan
    atr = wilder_mean(tr, period)
    plus = 100 * wilder_mean(positive, period) / atr.replace(0, np.nan)
    minus = 100 * wilder_mean(negative, period) / atr.replace(0, np.nan)
    denominator = plus + minus
    dx = 100 * (plus - minus).abs() / denominator.replace(0, np.nan)
    dx = dx.mask(denominator.eq(0), 0.0)
    return wilder_mean(dx, period)


def classify_regimes(frame: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    values = adx(frame, cfg["adx_period"])
    labels = pd.Series("transition", index=values.index)
    labels.loc[values >= cfg["trend_min_adx"]] = "trend"
    labels.loc[values <= cfg["range_max_adx"]] = "range"
    labels.loc[values.isna()] = "warmup"
    episodes: dict[str, list[dict[str, Any]]] = {"trend": [], "range": []}
    for _, group in labels.groupby(labels.ne(labels.shift()).cumsum()):
        name = str(group.iloc[0])
        if name in episodes and len(group) >= cfg["minimum_consecutive_bars"]:
            episodes[name].append(
                {
                    "start": group.index[0].isoformat(),
                    "end": group.index[-1].isoformat(),
                    "bars": len(group),
                }
            )
    return {
        "counts": {str(k): int(v) for k, v in labels.value_counts().items()},
        "episodes": episodes,
        "contains_both": all(episodes.values()),
        "adx_method": "causal Wilder ADX with SMA seeds",
        "thresholds": cfg,
    }


def run_regime_audit(
    directory: Path, symbol: str, output: Path, *, enabled: bool
) -> dict[str, Any]:
    if not enabled:
        raise ValueError("Explicit --allow-holdout-market-audit required; this is not a P&L access")
    safe_output(directory, output)
    cfg = load_config("validation.yaml")
    split = load_config("split.yaml")
    if split["holdout"]["end"] is None:
        unavailable_report = {
            "status": "missing_data",
            "contains_both": None,
            "reason": "No imported common closed bid/ask holdout candle",
            "frozen_split": split,
            "pnl_inspected": False,
        }
        write_report(output / "holdout_regimes.json", unavailable_report)
        return unavailable_report
    start, end = (pd.Timestamp(split["holdout"][key]) for key in ("start", "end"))
    frames, provenance = source_frames(directory, symbol)
    raw = index_utc(frames["bid"], cfg["audit"]["timestamp_unit"])
    market = raw.loc[(raw.index >= start) & (raw.index < end), OHLC].copy()
    if (
        market.index.hasnans
        or not market.index.is_unique
        or not market.index.is_monotonic_increasing
    ):
        raise ValueError("Holdout market audit requires valid, ordered, unique observations")
    expected = {
        t.strftime("%Y-%m")
        for t in pd.date_range(start.normalize().replace(day=1), end, freq="MS", inclusive="left")
    }
    observed = set(market.index.strftime("%Y-%m"))
    missing = sorted(expected - observed)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "holdout_market_regimes_only",
        "symbol": symbol,
        "frozen_split": split,
        "provenance": provenance,
        "missing_months": missing,
        "rows": len(market),
        "status": "missing_data",
        "contains_both": None,
        "pnl_inspected": False,
        "holdout_setup_access_consumed": False,
    }
    if not market.empty:
        tf = cfg["regime"]["timeframe"]
        bars = resample_closed_bars(market, cfg["audit"]["timeframes"][tf])
        bars = bars.loc[bars.index.dayofweek < 5]
        report.update(classify_regimes(bars, cfg["regime"]))
        coverage_end = market.index[-1] + pd.Timedelta(minutes=cfg["audit"]["source_bar_minutes"])
        complete = not missing and market.index[0] <= start and coverage_end >= end
        report["coverage_complete"] = complete
        report["status"] = (
            ("confirmed" if report["contains_both"] else "not_confirmed")
            if complete
            else "incomplete_data"
        )
    write_report(output / "holdout_regimes.json", report)
    # Separate market-data access log: the unique strategy P&L access remains unused.
    with (output / "market_only_access.jsonl").open("a", encoding="utf-8") as stream:
        import json

        stream.write(
            json.dumps(
                {
                    "timestamp": report["generated_at"],
                    "purpose": report["purpose"],
                    "status": report["status"],
                    "pnl_inspected": False,
                }
            )
            + "\n"
        )
    return report
