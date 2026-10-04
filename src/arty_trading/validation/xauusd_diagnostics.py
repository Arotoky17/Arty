"""Descriptive diagnostics on completed runs; never optimize detection parameters."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from arty_trading.config.operational import definitions, load_config
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.risk_limits import permitted_volume
from arty_trading.utils.helpers import calculate_atr, get_pip_size, pip_value
from arty_trading.validation.market_calendar import tradable_candles
from arty_trading.validation.market_data import resample_closed_bars
from arty_trading.validation.monthly_regimes import confirmed_structure


def news_blocked(at: datetime, cost: CostModel, enabled: bool) -> bool:
    if not enabled:
        return False
    cfg = load_config("diagnostics.yaml")["news_exclusion"]
    return any(
        datetime.fromisoformat(event["time_utc"]) - timedelta(minutes=cfg["before_minutes"])
        <= at
        <= datetime.fromisoformat(event["time_utc"]) + timedelta(minutes=cfg["after_minutes"])
        for event in cost.news_events
    )


def summarize(
    rows: list[dict[str, Any]],
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    if not rows:
        return {"n_trades": 0, "expectancy_r": None, "bootstrap_ci95_r": None}
    cfg = load_config("setup1_preregistration.yaml")["validation"]
    frame = pd.DataFrame(rows)
    at = pd.to_datetime(frame.entry_time, utc=True)
    weeks = at.dt.tz_localize(None).dt.to_period("W-SUN")
    groups = frame.groupby(weeks).r.agg(["sum", "count"])
    week_start = pd.Timestamp(start).tz_localize(None) if start else weeks.min().start_time
    week_end = pd.Timestamp(end).tz_localize(None) if end else weeks.max().start_time
    groups = groups.reindex(pd.period_range(week_start, week_end, freq="W-SUN"), fill_value=0)
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    indices = rng.integers(0, len(groups), size=(cfg["bootstrap_replicates"], len(groups)))
    sums = groups["sum"].to_numpy()[indices].sum(axis=1)
    counts = groups["count"].to_numpy()[indices].sum(axis=1)
    alpha = (1 - cfg["bootstrap_confidence"]) / 2
    defined = counts > 0
    interval = np.quantile(sums[defined] / counts[defined], [alpha, 1 - alpha])
    return {
        "n_trades": len(rows),
        "expectancy_r": float(frame.r.mean()),
        "bootstrap_ci95_r": interval.tolist(),
        "weekly_blocks": len(groups),
        "undefined_zero_trade_replicates": int((~defined).sum()),
        "ci_limitation": "Few independent weeks yield unreliable intervals"
        if len(groups) < 2
        else None,
    }


def diagnostics(
    rows: list[dict[str, Any]],
    candles: list[Any],
    cost: CostModel,
    balance: float,
    risk_fraction: float,
    symbol: str,
) -> dict[str, Any]:
    if symbol != "XAUUSD":
        return {"status": "not_applicable", "symbol": symbol}
    cfg = load_config("diagnostics.yaml")
    active = tradable_candles(candles)
    frame = pd.DataFrame(
        [
            {
                "time": c.time,
                **{name: float(getattr(c, name)) for name in ("open", "high", "low", "close")},
            }
            for c in active
        ]
    )
    labels = pd.Series(dtype="object")
    if not frame.empty:
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("time"), utc=True))
        duration = active[0].timeframe.value
        source_minutes = load_config("validation.yaml")["audit"]["timeframes"].get(duration, 1)
        if source_minutes <= definitions()["regime_audit"]["structure_timeframe_minutes"]:
            h4 = resample_closed_bars(
                frame, definitions()["regime_audit"]["structure_timeframe_minutes"], source_minutes
            )
            labels = confirmed_structure(h4, definitions()["swing"]["window"])
    enriched = []
    for original in rows:
        row = dict(original)
        at = pd.Timestamp(row["entry_time"])
        closed = labels.loc[labels.index <= at] if not labels.empty else labels
        row["regime"] = str(closed.iloc[-1]) if not closed.empty else "warmup"
        row["year"] = str(at.year)
        row["session"] = next(
            (name for name, hours in cfg["session_hours_utc"].items() if at.hour in hours),
            "unclassified",
        )
        row["news_excluded"] = news_blocked(at.to_pydatetime(), cost, True)
        enriched.append(row)
    breakdown = {}
    start = active[0].time if active else None
    end = active[-1].time if active else None
    for field in ("year", "regime", "direction", "session"):
        keys = sorted({str(row[field]) for row in enriched})
        breakdown[field] = {
            key: summarize([row for row in enriched if str(row[field]) == key], start, end)
            for key in keys
        }
    benchmark: dict[str, Any] = {"status": "insufficient_atr_warmup"}
    period = definitions()["atr_period"]
    if len(active) > period:
        entry = active[period]
        atr = float(calculate_atr(active[: period + 1], period))
        if atr > 0:
            risk = balance * risk_fraction
            unit_distance = atr * cfg["benchmark"]["risk_atr_multiple"]
            volume = risk / (unit_distance / float(get_pip_size(symbol)) * float(pip_value(symbol)))
            limits: dict[str, Any] = {}
            if symbol == "XAUUSD":
                volume, limits = permitted_volume(
                    volume, float(entry.close), float(entry.close) - unit_distance, atr, balance,
                )
                risk = unit_distance * volume * definitions()["instrument_units"]["XAUUSD"][
                    "contract_ounces_per_lot"
                ]
            exit_bar = active[-1]
            net = (float(exit_bar.close - entry.close) / float(get_pip_size(symbol))) * float(
                pip_value(symbol)
            ) * volume - cost.charge(
                volume, float(pip_value(symbol)), entry.time, exit_bar.time,
                entry_kind="market", entry_price=float(entry.close),
                exit_price=float(exit_bar.close),
            )
            benchmark = {
                "status": "descriptive",
                "initial_risk_money": risk,
                "net_r": net / risk if risk else None,
                "execution_limits": limits,
                "entry_time": entry.time.isoformat(),
                "exit_time": exit_bar.time.isoformat(),
                "risk_definition": (
                    "Same initial cash risk; ATR reference distance, no executed stop"
                ),
                "period_limitation": "After ATR warmup; risk ceilings applied, no swap model",
            }
    kept = [row for row in enriched if not row["news_excluded"]]
    return {
        "status": "descriptive_only",
        "aggregate": summarize(enriched, start, end),
        "breakdown": breakdown,
        "benchmark_buy_hold": benchmark,
        "news_comparison": {
            "without_exclusion": summarize(enriched, start, end),
            "fixed_path_exclusion": summarize(kept, start, end),
            "method": "descriptive subset; NOT an independent strategy rerun",
            "actual_variant_status": (
                "requires separately authorized run using same engine and news_exclusion=True"
            ),
            "window_minutes": cfg["news_exclusion"],
        },
        "parameter_selection_allowed": False,
    }
