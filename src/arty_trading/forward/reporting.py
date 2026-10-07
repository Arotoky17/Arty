"""Weekly and monthly forward-demo reports.

Reuses the shared weekly-block bootstrap so the forward interval is measured with
exactly the same method as the preregistered backtest. No verdict is issued
before the preregistered number of trades is reached.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arty_trading.config.operational import load_config


def _bucket(ts: str, period: str) -> str:
    moment = datetime.fromisoformat(ts).astimezone(UTC)
    if period == "weekly":
        iso = moment.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "monthly":
        return moment.strftime("%Y-%m")
    raise ValueError(f"Unknown reporting period: {period}")


def summarize_bucket(
    trades: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Headline metrics plus real-versus-modelled execution costs."""
    from arty_trading.validation.xauusd_diagnostics import summarize

    cfg = cfg or load_config("forward_demo.yaml")["reporting"]
    net_r = [float(row["net_r"]) for row in trades]
    gross_r = [float(row["gross_r"]) for row in trades]
    wins = [value for value in net_r if value > 0]
    losses = [-value for value in net_r if value < 0]
    profit_factor = (sum(wins) / sum(losses)) if losses else None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in net_r:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    rows = [
        {"entry_time": row["opened_at_utc"], "r": float(row["net_r"])}
        for row in trades
    ]
    bootstrap = summarize(rows)
    filled = [row for row in signals if row["status"] == "filled"]
    considered = len(signals)
    real_spreads = [float(r["spread_price"]) for r in filled if r["spread_price"] is not None]
    real_slippages = [float(r["slippage_price"]) for r in filled if r["slippage_price"] is not None]
    costs = [float(row["costs_usd"]) for row in trades]
    return {
        "trades": len(trades),
        "signals_considered": considered,
        "expectancy_net_r": (sum(net_r) / len(net_r)) if net_r else None,
        "expectancy_gross_r": (sum(gross_r) / len(gross_r)) if gross_r else None,
        "bootstrap_ci95_net_r": bootstrap.get("bootstrap_ci95_r"),
        "bootstrap_weekly_blocks": bootstrap.get("weekly_blocks"),
        "profit_factor_net": profit_factor,
        "max_drawdown_r": max_drawdown,
        "fill_rate": (len(filled) / considered) if considered else None,
        "mean_real_spread_price": (sum(real_spreads) / len(real_spreads)) if real_spreads else None,
        "mean_real_slippage_price": (
            sum(real_slippages) / len(real_slippages) if real_slippages else None
        ),
        "mean_costs_usd": (sum(costs) / len(costs)) if costs else None,
        "verdict": None,
        "verdict_status": "insufficient_trades",
    }


def model_cost_reference() -> dict[str, Any]:
    """Cost model the forward run is compared against."""
    cost = load_config("execution.yaml")["cost"]
    return {
        "limit_slippage_usd": cost.get("limit_slippage_usd"),
        "entry_limit_slippage_usd": cost.get("entry_limit_slippage_usd"),
        "market_stop_slippage_usd": cost.get("market_stop_slippage_usd"),
        "commission_per_lot_side": cost["commission_per_lot_side"],
        "spread_mode": cost["spread_mode"],
    }


def build_report(
    trades: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    period: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config("forward_demo.yaml")["reporting"]
    minimum = int(cfg["minimum_trades_for_verdict"])
    buckets: dict[str, dict[str, Any]] = {}
    for row in signals:
        buckets.setdefault(_bucket(row["ts_utc"], period), {"trades": [], "signals": []})
        buckets[_bucket(row["ts_utc"], period)]["signals"].append(row)
    for row in trades:
        key = _bucket(row["closed_at_utc"], period)
        buckets.setdefault(key, {"trades": [], "signals": []})
        buckets[key]["trades"].append(row)
    overall = summarize_bucket(trades, signals, cfg)
    total = len(trades)
    if total < minimum:
        overall["verdict"] = None
        overall["verdict_status"] = "insufficient_trades"
        overall["minimum_trades_for_verdict"] = minimum
    else:
        expectancy = overall["expectancy_net_r"] or 0.0
        profit_factor = overall["profit_factor_net"]
        overall["verdict_status"] = "evaluated"
        overall["verdict"] = (
            "criteria_met"
            if expectancy > 0 and (profit_factor is None or profit_factor > 1.2)
            else "criteria_not_met"
        )
    return {
        "period": period,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "minimum_trades_for_verdict": minimum,
        "model_cost_reference": model_cost_reference(),
        "overall": overall,
        "buckets": {
            key: summarize_bucket(value["trades"], value["signals"], cfg)
            for key, value in sorted(buckets.items())
        },
    }


def write_report(report: dict[str, Any], directory: Path, name: str) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path
