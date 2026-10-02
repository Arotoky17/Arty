"""Monthly market-data quality and causal resampling audit. No strategy or P&L."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from arty_trading.config.operational import definitions, load_config
from arty_trading.validation.market_data import (
    OHLC,
    available_bars,
    index_utc,
    resample_closed_bars,
    safe_output,
    source_frames,
    write_report,
)


def resampling_audit(frame: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    failures = []
    checked = 0
    if frame.empty:
        return {"status": "no_data", "probes": 0, "failures": []}
    positions = np.unique(np.linspace(0, len(frame) - 1, cfg["lookahead_probes"], dtype=int))
    for timeframe, minutes in cfg["timeframes"].items():
        full = resample_closed_bars(frame, minutes, cfg["source_bar_minutes"])
        if (full.source_close > full.available_at).any():
            failures.append({"timeframe": timeframe, "reason": "source_after_close"})
        for position in positions:
            prefix = frame.iloc[: int(position) + 1]
            cutoff = prefix.index[-1] + pd.Timedelta(minutes=cfg["source_bar_minutes"])
            partial = resample_closed_bars(prefix, minutes, cfg["source_bar_minutes"])
            expected = available_bars(full, cutoff)
            checked += 1
            if not partial[OHLC].equals(expected[OHLC]):
                failures.append(
                    {
                        "timeframe": timeframe,
                        "cutoff": cutoff.isoformat(),
                        "reason": "prefix_changed_closed_bar",
                    }
                )
    return {
        "status": "passed" if not failures else "failed",
        "probes": checked,
        "failures": failures,
        "contract": "open + timeframe <= decision_time",
        "scope": "shared production resampler; sampled prefix invariance, unit boundary tests",
    }


def bar_outlier_metrics(frame: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Keep grid flags, but freeze the volatility clock on unchanged flat bars.

    A flat OHLC equal to the preceding close is an inactivity candidate, not
    proof of an exchange closure. Never discard its raw row or suppress the
    absolute price/range checks. Every ATR uses past observations only.
    """
    previous = frame.close.shift()
    tr = pd.concat(
        [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
        axis=1,
    ).max(axis=1)
    flat = frame[OHLC].eq(previous, axis=0).all(axis=1)
    alpha = 1 / definitions()["atr_period"]
    grid_atr = tr.shift().ewm(alpha=alpha, adjust=False).mean()
    active_atr = tr.loc[~flat].shift().ewm(alpha=alpha, adjust=False).mean()
    active_atr = active_atr.reindex(frame.index).ffill()
    selected = active_atr if cfg["exclude_unchanged_flats_from_atr"] else grid_atr
    absolute_return = (frame.close / previous - 1).abs() > cfg["max_return_fraction"]
    absolute_range = (frame.high - frame.low) / previous > cfg["max_range_fraction"]
    absolute = absolute_return | absolute_range
    legacy = absolute | ((grid_atr > 0) & (tr > cfg["spike_atr_multiple"] * grid_atr))
    corrected = absolute | ((selected > 0) & (tr > cfg["spike_atr_multiple"] * selected))
    return pd.DataFrame(
        {
            "true_range": tr,
            "past_atr_grid": grid_atr,
            "past_atr_active": active_atr,
            "unchanged_flat": flat,
            "absolute_return_flag": absolute_return,
            "absolute_range_flag": absolute_range,
            "legacy_outlier": legacy,
            "outlier": corrected,
            "padding_atr_false_positive": legacy & ~corrected,
        },
        index=frame.index,
    )


def audit_frame(raw: pd.DataFrame, cfg: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    required = {"timestamp", *OHLC}
    if not required.issubset(raw.columns):
        raise ValueError("Audit needs timestamp and OHLC columns")
    numeric = raw[OHLC].apply(pd.to_numeric, errors="coerce")
    frame = index_utc(raw, cfg["timestamp_unit"])
    finite = pd.Series(np.isfinite(numeric.to_numpy()).all(axis=1), index=raw.index)
    invalid_bounds = (
        (numeric.low <= 0)
        | (numeric.high < numeric.low)
        | (numeric.low > numeric[["open", "close"]].min(axis=1))
        | (numeric.high < numeric[["open", "close"]].max(axis=1))
        | ~finite
    )
    duplicate = raw.timestamp.duplicated(keep="first")
    backwards = pd.to_numeric(raw.timestamp, errors="coerce").diff().lt(0)
    text_times = raw.timestamp.astype(str)
    explicit_offset = text_times.str.contains(r"[T ].*[+-]\d\d:?\d\d$", regex=True)
    non_utc_offset = explicit_offset & ~text_times.str.contains(r"[+]00:?00$", regex=True)
    naive_text = text_times.str.contains("T", regex=False) & ~(
        explicit_offset | text_times.str.endswith("Z")
    )
    valid_timestamp = ~frame.index.isna()
    report: dict[str, Any] = {
        "rows": len(raw),
        "invalid_timestamps": int((~valid_timestamp).sum()),
        "non_numeric_timestamp_rows": int(
            pd.to_numeric(raw.timestamp, errors="coerce").isna().sum()
        ),
        "non_utc_offsets": int(non_utc_offset.sum()),
        "naive_datetime_strings": int(naive_text.sum()),
        "timestamp_contract": (
            "Unix milliseconds / UTC; source clock cannot be proven from epochs alone"
        ),
        "tick_scope": "M1 OHLC proxies only; individual ticks are unavailable",
        "months": {},
    }
    # Derived inspection view only. Every excluded row is counted above/below.
    inspect = frame.loc[valid_timestamp].copy()
    inspect[OHLC] = numeric.loc[valid_timestamp].to_numpy()
    inspect["invalid_ohlc"] = invalid_bounds.loc[valid_timestamp].to_numpy()
    inspect["duplicate"] = duplicate.loc[valid_timestamp].to_numpy()
    inspect["backwards"] = backwards.loc[valid_timestamp].to_numpy()
    inspect["weekend"] = inspect.index.dayofweek >= 5
    inspect["outside_expected_dates"] = (inspect.index < pd.Timestamp(cfg["expected_start"])) | (
        inspect.index >= pd.Timestamp(cfg["expected_end"])
    )
    ordered = inspect.loc[~inspect.duplicate & ~inspect.invalid_ohlc].sort_index()
    metrics = bar_outlier_metrics(ordered, cfg)
    ordered[metrics.columns] = metrics
    for month, group in inspect.groupby(inspect.index.strftime("%Y-%m")):
        start = pd.Timestamp(f"{month}-01", tz="UTC")
        end = start + pd.offsets.MonthBegin(1)
        expected = pd.date_range(
            start, end, freq=f"{cfg['source_bar_minutes']}min", inclusive="left"
        )
        missing = expected.difference(group.index.unique())
        clean = ordered.loc[ordered.index.strftime("%Y-%m") == month]
        anomaly = clean.loc[clean.outlier]
        report["months"][month] = {
            "rows": len(group),
            "duplicates": int(group.duplicate.sum()),
            "unordered_rows": int(group.backwards.sum()),
            "invalid_ohlc": int(group.invalid_ohlc.sum()),
            "missing_minutes": len(missing),
            "missing_weekday_minutes": int((missing.dayofweek < 5).sum()),
            "missing_weekend_minutes": int((missing.dayofweek >= 5).sum()),
            "weekend_rows": int(group.weekend.sum()),
            "unchanged_flat_rows": int(clean.unchanged_flat.sum()),
            "weekend_flat_rows": int((clean.unchanged_flat & clean.weekend).sum()),
            "outlier_m1_bars": len(anomaly),
            "legacy_outlier_m1_bars": int(clean.legacy_outlier.sum()),
            "padding_atr_false_positives": int(clean.padding_atr_false_positive.sum()),
            "outside_expected_dates": int(group.outside_expected_dates.sum()),
            "outlier_examples": [t.isoformat() for t in anomaly.index[: cfg["example_limit"]]],
            "gap_examples": [t.isoformat() for t in missing[: cfg["example_limit"]]],
            "interpretation": (
                "Gaps/weekend quotes require exchange-calendar review; no raw corrections"
            ),
        }
    return report, ordered


def audit_ticks(raw: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """Optional bid/ask tick input; same millisecond with different quotes is legitimate."""
    if not {"timestamp", "bid", "ask"}.issubset(raw.columns):
        raise ValueError("Tick audit requires timestamp, bid and ask")
    prices = raw[["bid", "ask"]].apply(pd.to_numeric, errors="coerce")
    indexed = index_utc(raw, cfg["timestamp_unit"])
    finite = np.isfinite(prices.to_numpy()).all(axis=1)
    bad = ~finite | (prices.bid <= 0) | (prices.ask < prices.bid)
    duplicates = raw[["timestamp", "bid", "ask"]].duplicated()
    invalid_times = indexed.index.isna()
    valid = indexed.loc[~invalid_times].copy()
    valid[["bid", "ask"]] = prices.loc[~invalid_times].to_numpy()
    valid["invalid_quote"] = np.asarray(bad)[~invalid_times]
    valid["duplicate_tick"] = duplicates.loc[~invalid_times].to_numpy()
    # Sorting is confined to an inspection copy; ordering anomalies remain recorded.
    backwards = pd.to_numeric(raw.timestamp, errors="coerce").diff().lt(0)
    valid["unordered"] = backwards.loc[~invalid_times].to_numpy()
    valid = valid.sort_index()
    mid = (valid.bid + valid.ask) / 2
    returns = mid.pct_change(fill_method=None).abs()
    past = returns.shift().rolling(cfg["tick_rolling_window"]).median()
    valid["outlier_tick"] = (returns > cfg["max_return_fraction"]) | (
        (past > 0) & (returns > cfg["tick_spike_multiple"] * past)
    )
    valid["gap_candidate"] = (
        valid.index.to_series().diff().dt.total_seconds() > cfg["tick_max_gap_seconds"]
    )
    months = {}
    for month, group in valid.groupby(valid.index.strftime("%Y-%m")):
        months[month] = {
            "ticks": len(group),
            "duplicates": int(group.duplicate_tick.sum()),
            "invalid_quotes": int(group.invalid_quote.sum()),
            "unordered": int(group.unordered.sum()),
            "outlier_ticks": int(group.outlier_tick.sum()),
            "gap_candidates": int(group.gap_candidate.sum()),
            "weekend_ticks": int((group.index.dayofweek >= 5).sum()),
        }
    return {
        "status": "audited",
        "invalid_timestamps": int(invalid_times.sum()),
        "months": months,
        "interpretation": "Candidates only; gaps require market-calendar review",
    }


def run_audit(
    directory: Path, symbol: str, output: Path, tick_files: list[Path] | None = None
) -> dict[str, Any]:
    safe_output(directory, output)
    cfg = load_config("validation.yaml")["audit"]
    frames, provenance = source_frames(directory, symbol)
    reports = {}
    clean = {}
    for side, raw in frames.items():
        reports[side], clean[side] = audit_frame(raw, cfg)
        reports[side]["resampling"] = resampling_audit(clean[side], cfg)
    common = clean["bid"].index.intersection(clean["ask"].index)
    negative_spread = clean["ask"].loc[common].close < clean["bid"].loc[common].close
    monthly_crossed = negative_spread.groupby(common.strftime("%Y-%m")).sum()
    observed = set(reports["bid"]["months"]) & set(reports["ask"]["months"])
    expected_months = pd.date_range(
        cfg["expected_start"], cfg["expected_end"], freq="MS", inclusive="left"
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "mode": "read_only_market_data_audit_no_pnl",
        "config": cfg,
        "provenance": provenance,
        "sides": reports,
        "coverage_missing_months": [
            t.strftime("%Y-%m") for t in expected_months if t.strftime("%Y-%m") not in observed
        ],
        "bid_only_timestamps": len(clean["bid"].index.difference(clean["ask"].index)),
        "ask_only_timestamps": len(clean["ask"].index.difference(clean["bid"].index)),
        "negative_close_spread_by_month": {str(k): int(v) for k, v in monthly_crossed.items()},
        "source_timezone_verification": "unverified: manifest has no source-clock attestation",
        "raw_data_modified": False,
        "tick_audit": {
            "status": "not_available",
            "reason": "No tick files supplied; M1 proxies reported separately",
        },
    }
    if tick_files:
        for path in tick_files:
            safe_output(path.parent, output)
        ticks = pd.concat([pd.read_csv(path) for path in tick_files], ignore_index=True)
        report["tick_audit"] = audit_ticks(ticks, cfg)
    write_report(output / "audit_data.json", report)
    rows = [
        {"side": side, "month": month, **row}
        for side, data in reports.items()
        for month, row in data["months"].items()
    ]
    pd.DataFrame(rows).to_csv(output / "audit_data_monthly.csv", index=False)
    return report
