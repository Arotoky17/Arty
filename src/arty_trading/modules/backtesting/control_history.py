"""Read-only MT5 historical snapshots; never manufacture unavailable market data."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_history(
    raw: dict[str, Any],
    symbol: str,
    start: datetime,
    end: datetime,
) -> dict[TimeFrame, list[Candle]]:
    if raw.get("source") not in ("mt5", "mt5_export", "csv") or raw.get("symbol") != symbol:
        raise ValueError("Expected a real MT5 snapshot for the requested symbol")
    history: dict[TimeFrame, list[Candle]] = {}
    for tf in (TimeFrame.M5, TimeFrame.H1, TimeFrame.H4):
        rows = raw.get("candles", {}).get(tf.value, [])
        candles: list[Candle] = []
        for row in rows:
            if "spread" not in row:
                raise ValueError("Historical spread is required for realistic execution")
            date = datetime.fromtimestamp(int(row["time"]), UTC)
            if start <= date < end:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=tf,
                        time=date,
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=int(row.get("tick_volume", row.get("volume", 0))),
                        spread=int(row["spread"]),
                        non_tradable=bool(row.get("non_tradable", False)),
                        entry_blocked=bool(row.get("entry_blocked", False)),
                    )
                )
        if not candles:
            raise ValueError(f"No real {tf.value} history in the requested period")
        if any(a.time >= b.time for a, b in zip(candles, candles[1:])):
            raise ValueError(f"Unordered or duplicate {tf.value} timestamps")
        if candles[0].time > start + timedelta(days=5) or candles[-1].time < end - timedelta(
            days=5
        ):
            raise ValueError(f"Incomplete {tf.value} period coverage")
        if any(b.time - a.time > timedelta(days=7) for a, b in zip(candles, candles[1:])):
            raise ValueError(f"Historical {tf.value} gap exceeds seven days")
        history[tf] = candles
    return history


def obtain_history(
    config: dict[str, Any],
    symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], dict[TimeFrame, list[Candle]]]:
    from arty_trading.validation.split import HOLDOUT_LOADING, DataSplit, HoldoutAccessError
    from arty_trading.validation.trial_registry import TrialRegistry

    split = DataSplit(TrialRegistry())
    if HOLDOUT_LOADING.get():
        if not split.holdout[0] <= start < end <= split.holdout[1]:
            raise HoldoutAccessError("Requested period is outside holdout")
    else:
        split.assert_period_development(start, end)
    path = config.get("history_file")
    if config.get("source") == "csv":
        from arty_trading.modules.backtesting.csv_history import load_csv_history

        raw = load_csv_history(Path(config["history_directory"]), float(config["broker"]["point"]))
    elif path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        import MetaTrader5 as mt5  # noqa: N813 - broker convention

        arguments = {"timeout": 10000}
        terminal = config.get("terminal_path")
        initialized = (
            mt5.initialize(terminal, **arguments) if terminal else mt5.initialize(**arguments)
        )
        if not initialized:
            raise RuntimeError(
                f"MT5 history unavailable: {mt5.last_error()}. "
                "Open the connected terminal or set history_file to a real MT5 JSON export."
            )
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"MT5 symbol {symbol} is unavailable")
        raw = {
            "source": "mt5",
            "symbol": symbol,
            "candles": {},
            "broker": {
                "contract_size": float(info.trade_contract_size),
                "point": float(info.point),
                "volume_min": float(info.volume_min),
                "volume_step": float(info.volume_step),
            },
        }
        for tf, code in (
            (TimeFrame.M5, mt5.TIMEFRAME_M5),
            (TimeFrame.H1, mt5.TIMEFRAME_H1),
            (TimeFrame.H4, mt5.TIMEFRAME_H4),
        ):
            rates = mt5.copy_rates_range(symbol, code, start, end)
            if rates is None:
                raise RuntimeError(f"MT5 {tf.value} history unavailable: {mt5.last_error()}")
            raw["candles"][tf.value] = [
                {name: rate[name].item() for name in rates.dtype.names} for rate in rates
            ]
        # Keep the exact input snapshot for subsequent offline reproduction.
        destination = Path("data/history") / f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}_mt5.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    return raw, validate_history(raw, symbol, start, end)
