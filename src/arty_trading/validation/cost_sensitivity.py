"""Post-hoc cost stress on a fixed fill/size/price path; no new backtest runs."""

from __future__ import annotations

from typing import Any

from arty_trading.config.operational import load_config


def drawdown(equity: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def cost_sensitivity(
    # Each ticket: realized net money, round-trip cost money, initial risk money.
    trades: list[tuple[float, float, float]],
    equity: list[float],
    accrued_costs: list[float],
    multipliers: list[float],
) -> list[dict[str, Any]]:
    if len(equity) != len(accrued_costs):
        raise ValueError("Equity and accrued costs must have matching timestamps")
    scenarios = []
    cfg = load_config("execution.yaml")["cost"]
    for factor in multipliers:
        profits = [net - (factor - 1) * cost for net, cost, _ in trades]
        gains = sum(p for p in profits if p > 0)
        losses = -sum(p for p in profits if p < 0)
        returns = [(net - (factor - 1) * cost) / risk for net, cost, risk in trades if risk > 0]
        stressed_equity = [
            value - (factor - 1) * cost for value, cost in zip(equity, accrued_costs)
        ]
        scenarios.append(
            {
                "cost_multiplier": factor,
                "n_trades": len(trades),
                "expectancy_r": sum(returns) / len(returns) if returns else 0.0,
                "profit_factor": gains / losses if losses else (None if gains else 0.0),
                "profit_factor_status": "no_losses" if gains and not losses else "defined",
                "net_profit": sum(profits),
                "max_drawdown": drawdown(stressed_equity),
                "final_equity": stressed_equity[-1] if stressed_equity else None,
                "method": "fixed_path_repricing_no_resizing_or_signal_rerun",
                "fragile": (
                    factor == cfg["fragility_multiplier"]
                    and bool(returns)
                    and sum(returns) / len(returns) < cfg["fragility_expectancy_r"]
                ),
            }
        )
    return scenarios
