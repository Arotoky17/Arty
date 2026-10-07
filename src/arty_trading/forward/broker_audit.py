"""Broker audit for the forward demo.

Compares the demo broker's M1 candles with the Dukascopy raw store (timezone,
symbol, sessions and level differences) and records the XAUUSD symbol
specification the forward run actually relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from arty_trading.config.operational import definitions, load_config
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import index_utc, source_frames


def symbol_specification(raw_symbol_info: Any, account_leverage: int = 0) -> dict[str, Any]:
    """Contract size, commission, spread, leverage and volume grid actually used."""
    def pick(name: str, default: Any = None) -> Any:
        value = getattr(raw_symbol_info, name, None)
        return default if value is None else value

    digits = int(pick("digits", 2))
    point = float(pick("point", 0.01))
    spread_points = int(pick("spread", 0) or 0)
    return {
        "symbol": str(pick("name", "XAUUSD")),
        "digits": digits,
        "point": point,
        "trade_mode": pick("trade_mode"),
        "volume_min": float(pick("volume_min", 0.01)),
        "volume_max": float(pick("volume_max", 0.01)),
        "volume_step": float(pick("volume_step", 0.01)),
        "spread_points": spread_points,
        "spread_price": round(spread_points * point, 5),
        "contract_size": float(pick("trade_contract_size", 100.0)),
        "tick_value": float(pick("trade_tick_value", 1.0)),
        "tick_size": float(pick("trade_tick_size", point)),
        "filling_mode": pick("filling_mode"),
        "stops_level_points": int(pick("trade_stops_level", 0) or 0),
        "freeze_level_points": int(pick("trade_freeze_level", 0) or 0),
        "margin_initial": float(pick("margin_initial", 0.0) or 0.0),
        "margin_maintenance": float(pick("margin_maintenance", 0.0) or 0.0),
        "session_deals": pick("session_deals"),
        "session_buy": pick("session_buy"),
        "session_sell": pick("session_sell"),
        "account_leverage": int(account_leverage),
        "configured_contract_ounces_per_lot": float(
            definitions()["instrument_units"]["XAUUSD"]["contract_ounces_per_lot"]
        ),
        "configured_pip_usd": float(definitions()["instrument_units"]["XAUUSD"]["pip_usd"]),
    }


def compare_m1(
    broker_frame: pd.DataFrame,
    dukascopy_frame: pd.DataFrame,
    *,
    price_tolerance: float = 0.05,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Compare broker M1 candles with the Dukascopy raw store."""
    broker = index_utc(broker_frame.copy(), "ms").sort_index()
    reference = index_utc(dukascopy_frame.copy(), "ms").sort_index()
    if max_rows:
        reference = reference.tail(max_rows)
    common = broker.index.intersection(reference.index)
    broker_only = broker.index.difference(reference.index)
    reference_only = reference.index.difference(broker.index)
    deltas = (
        (broker.loc[common, "close"] - reference.loc[common, "close"]).abs()
        if len(common)
        else pd.Series(dtype=float)
    )
    outside = deltas[deltas > price_tolerance] if len(deltas) else deltas
    calendar = MarketCalendar()
    session_check = calendar.annotate(reference.copy())
    return {
        "status": "compared",
        "price_tolerance": price_tolerance,
        "broker_rows": int(len(broker)),
        "reference_rows": int(len(reference)),
        "common_rows": int(len(common)),
        "broker_only_timestamps": int(len(broker_only)),
        "reference_only_timestamps": int(len(reference_only)),
        "max_close_delta": float(deltas.max()) if len(deltas) else None,
        "mean_close_delta": float(deltas.mean()) if len(deltas) else None,
        "rows_beyond_tolerance": int(len(outside)),
        "non_tradable_rows_reference": int(session_check.non_tradable.sum()),
        "entry_blocked_rows_reference": int(session_check.entry_blocked.sum()),
        "consistent": bool(len(outside) == 0 and len(broker_only) == 0),
        "interpretation": (
            "Level agreement only; the broker feed is the execution reference, "
            "Dukascopy remains the research reference"
        ),
    }


def build_audit(
    broker_frame: pd.DataFrame,
    directory: str,
    symbol: str = "XAUUSD",
    side: str = "bid",
    raw_symbol_info: Any = None,
    account_leverage: int = 0,
    price_tolerance: float = 0.05,
) -> dict[str, Any]:
    """Full broker audit: symbol specification plus candle comparison."""
    frames, provenance = source_frames(Path(directory), symbol)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "reference": "dukascopy_raw",
        "provenance_files": len(provenance["files"]),
        "candles": compare_m1(broker_frame, frames[side], price_tolerance=price_tolerance),
        "symbol_specification": symbol_specification(raw_symbol_info, account_leverage),
        "model_reference": {
            "commission_per_lot_side": load_config("execution.yaml")["cost"][
                "commission_per_lot_side"
            ],
            "spread_mode": load_config("execution.yaml")["cost"]["spread_mode"],
        },
        "pnl_inspected": False,
    }
