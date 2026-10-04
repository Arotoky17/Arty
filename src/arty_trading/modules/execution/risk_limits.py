"""XAUUSD minimum structural stop and aggregate notional/margin ceilings."""

from __future__ import annotations

import math
from typing import Any

from arty_trading.config.operational import definitions, load_config


def permitted_volume(
    requested: float,
    entry: float,
    stop: float,
    atr: float,
    equity: float,
    existing_notional: float = 0.0,
    existing_margin: float = 0.0,
) -> tuple[float, dict[str, Any]]:
    cfg = load_config("execution.yaml")["risk_limits"]
    if (
        any(
            not math.isfinite(x)
            for x in (
                requested,
                entry,
                stop,
                atr,
                equity,
                existing_notional,
                existing_margin,
            )
        )
        or min(existing_notional, existing_margin) < 0
    ):
        return 0.0, {"reason": "invalid_risk_inputs"}
    if (
        any(
            not math.isfinite(cfg[key]) or cfg[key] <= 0
            for key in (
                "maximum_gross_leverage",
                "broker_margin_leverage",
                "maximum_margin_fraction",
                "volume_step",
                "minimum_volume",
            )
        )
        or cfg["maximum_margin_fraction"] > 1
    ):
        raise ValueError("Invalid leverage/margin configuration")
    units = definitions()["instrument_units"]["XAUUSD"]["contract_ounces_per_lot"]
    distance = abs(entry - stop)
    if atr <= 0 or distance < definitions()["execution_constraints"]["minimum_stop_atr"] * atr:
        return 0.0, {"reason": "minimum_stop_atr_or_warmup"}
    if min(equity, entry, requested) <= 0:
        return 0.0, {"reason": "invalid_risk_inputs"}
    per_lot = entry * units
    by_leverage = max(0.0, equity * cfg["maximum_gross_leverage"] - existing_notional) / per_lot
    by_margin = (
        max(0.0, equity * cfg["maximum_margin_fraction"] - existing_margin)
        * cfg["broker_margin_leverage"]
        / per_lot
    )
    # The risk-based request is capped when a ceiling binds strictly below it.
    leverage_binding = by_leverage < requested
    margin_binding = by_margin < requested
    volume = (
        math.floor(min(requested, by_leverage, by_margin) / cfg["volume_step"]) * cfg["volume_step"]
    )
    if volume < cfg["minimum_volume"]:
        return 0.0, {"reason": "leverage_margin_or_minimum_volume"}
    return volume, {
        "requested_volume": float(requested),
        "capped": leverage_binding or margin_binding,
        "capped_by_leverage": leverage_binding,
        "capped_by_margin": margin_binding,
        "initial_risk_usd": distance * units * volume,
        "notional_usd": volume * per_lot,
        "gross_leverage_after": (existing_notional + volume * per_lot) / equity,
        "margin_usd": volume * per_lot / cfg["broker_margin_leverage"],
        "margin_fraction_after": (
            existing_margin + volume * per_lot / cfg["broker_margin_leverage"]
        )
        / equity,
    }
