"""Monthly causal D1 ADX and confirmed H4 swing structure, market data only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from arty_trading.config.operational import definitions, load_config
from arty_trading.validation.daily_context import daily_bars
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import (
    OHLC,
    index_utc,
    resample_closed_bars,
    safe_output,
    source_frames,
    write_report,
)
from arty_trading.validation.regime_audit import adx


def confirmed_structure(bars: pd.DataFrame, window: int) -> pd.Series:
    """HH+HL or LH+LL, otherwise range; pivots first usable after right-window close."""
    if window < 1:
        raise ValueError("Structure swing window must be positive")
    highs: list[float] = []
    lows: list[float] = []
    labels = []
    high = bars.high.to_numpy()
    low = bars.low.to_numpy()
    for closed in range(len(bars)):
        pivot = closed - window
        if pivot >= window:
            left = pivot - window
            right = closed + 1
            if np.argmax(high[left:right]) == window:
                highs.append(float(high[pivot]))
            if np.argmin(low[left:right]) == window:
                lows.append(float(low[pivot]))
        label = "warmup"
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                label = "trend_up"
            elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                label = "trend_down"
            else:
                label = "range"
        labels.append(label)
    return pd.Series(labels, index=bars.available_at, name="structure")


def sustained_presence(labels: pd.Series, minimum: int) -> dict[str, Any]:
    episodes: dict[str, list[dict[str, Any]]] = {"trend": [], "range": []}
    normalized = labels.replace({"trend_up": "trend", "trend_down": "trend"})
    for _, group in normalized.groupby(normalized.ne(normalized.shift()).cumsum()):
        category = str(group.iloc[0])
        if category in episodes and len(group) >= minimum:
            episodes[category].append(
                {
                    "start": group.index[0].isoformat(),
                    "end": group.index[-1].isoformat(),
                    "bars": len(group),
                }
            )
    return {"contains_both_observed": all(episodes.values()), "episodes": episodes}


def run_monthly_regimes(
    directory: Path,
    symbol: str,
    output: Path,
    *,
    enabled: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not enabled:
        raise ValueError("Explicit --allow-holdout-market-audit required")
    safe_output(directory, output)
    cutoff = pd.Timestamp(now or datetime.now(UTC))
    cfg = definitions()["regime_audit"]
    split = load_config("split.yaml")
    frames, provenance = source_frames(directory, symbol)
    bid, ask = (index_utc(frames[side], "ms") for side in ("bid", "ask"))
    for frame in (bid, ask):
        if (
            frame.index.hasnans
            or not frame.index.is_unique
            or not frame.index.is_monotonic_increasing
        ):
            raise ValueError("Regime audit requires ordered unique UTC observations")
        if (frame.index + pd.Timedelta(minutes=1) > cutoff).any():
            raise ValueError("Regime input contains unfinished/future observations")
    common = bid.index.intersection(ask.index)
    bid, ask = bid.loc[common], ask.loc[common]
    active = bid.loc[~MarketCalendar().annotate(bid).non_tradable, OHLC]
    daily = daily_bars(bid, 1, cutoff.to_pydatetime())
    if definitions()["daily_bars"]["convention"] == "new_york_17":
        weekdays = daily.available_at.dt.tz_convert(MarketCalendar().zone).dt.dayofweek
    else:
        weekdays = daily.index.dayofweek
    daily = daily.loc[weekdays.isin(cfg["daily_weekdays"])]
    values = adx(daily, cfg["adx_period"])
    daily_labels = pd.Series("transition", index=daily.available_at)
    daily_labels.loc[daily.available_at[values.ge(cfg["trend_min_adx"])]] = "trend"
    daily_labels.loc[daily.available_at[values.le(cfg["range_max_adx"])]] = "range"
    daily_labels.loc[daily.available_at[values.isna()]] = "warmup"
    adx_by_close = pd.Series(values.to_numpy(), index=daily.available_at)
    htf = resample_closed_bars(active, cfg["structure_timeframe_minutes"])
    structure = confirmed_structure(htf, definitions()["swing"]["window"])
    monthly = []
    periods: dict[str, dict[str, Any]] = {}
    for name, section in (("development", split["development"]), ("holdout", split["holdout"])):
        start = pd.Timestamp(section["start"])
        end = pd.Timestamp(section["end"]) if section["end"] else None
        if end is None:
            periods[name] = {
                "status": "unavailable_until_import",
                "contains_both": None,
                "reason": "No common closed bid/ask holdout data",
                "end": None,
            }
            continue
        if end > cutoff:
            raise ValueError("Regime period contains future dates")
        observed = active.loc[(active.index >= start) & (active.index < end)]
        expected = pd.date_range(start.replace(day=1).normalize(), end, freq="MS", inclusive="left")
        present = set(observed.index.strftime("%Y-%m"))
        missing = [
            month.strftime("%Y-%m") for month in expected if month.strftime("%Y-%m") not in present
        ]
        d1 = daily_labels.loc[(daily_labels.index >= start) & (daily_labels.index <= end)]
        h4 = structure.loc[(structure.index >= start) & (structure.index <= end)]
        adx_presence = sustained_presence(d1, cfg["minimum_daily_episode_bars"])
        structure_presence = sustained_presence(h4, cfg["minimum_structure_episode_bars"])
        complete = not missing and not observed.empty
        # Missing months are sufficient to disprove complete coverage, but their
        # absence is not enough: also require actual first/last common observations.
        complete = bool(
            complete
            and bid.index.min() <= start
            and bid.index.max() + pd.Timedelta(minutes=1) >= end
        )
        both = (
            adx_presence["contains_both_observed"] and structure_presence["contains_both_observed"]
        )
        periods[name] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "status": "confirmed"
            if complete and both
            else ("not_confirmed" if complete else "incomplete_data"),
            "coverage_complete": complete,
            "missing_months": missing,
            "contains_both": bool(both) if complete else None,
            "adx_d1": adx_presence,
            "structure_htf": structure_presence,
        }
        for month in expected:
            key = month.strftime("%Y-%m")
            day_mask = d1.index.strftime("%Y-%m") == key
            htf_mask = h4.index.strftime("%Y-%m") == key
            sample = adx_by_close.reindex(d1.index)[day_mask].dropna()
            row = {
                "period": name,
                "month": key,
                "data_present": key in present,
                "d1_bars": int(day_mask.sum()),
                "adx_mean": float(sample.mean()) if len(sample) else None,
                "adx_min": float(sample.min()) if len(sample) else None,
                "adx_max": float(sample.max()) if len(sample) else None,
            }
            row.update({"adx_" + str(k): int(v) for k, v in d1[day_mask].value_counts().items()})
            row.update(
                {"structure_" + str(k): int(v) for k, v in h4[htf_mask].value_counts().items()}
            )
            monthly.append(row)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "pnl_inspected": False,
        "holdout_setup_access_consumed": False,
        "definitions": cfg,
        "frozen_split": split,
        "provenance": provenance,
        "periods": periods,
        "months": monthly,
        "methods": [
            "Shared closed NY17 D1 for Wilder ADX, PDH/PDL and D1 structural bias",
            "H4 UTC confirmed fractals: HH+HL / LH+LL; otherwise range",
            "Labels attributed to bar availability/close, not opening timestamp",
            "Past market warmup may cross period start; no future prices used",
        ],
    }
    write_report(output / "monthly_regimes.json", report)
    pd.DataFrame(monthly).to_csv(output / "monthly_regimes.csv", index=False)
    with (output / "market_only_access.jsonl").open("a", encoding="utf-8") as journal:
        import json

        journal.write(
            json.dumps(
                {
                    "timestamp": report["generated_at"],
                    "purpose": "D1_ADX_HTF_structure_market_only",
                    "pnl_inspected": False,
                }
            )
            + "\n"
        )
    return report
