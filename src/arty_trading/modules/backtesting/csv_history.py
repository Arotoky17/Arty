"""Read pinned Dukascopy M1 bid/ask CSVs and aggregate UTC OHLC bars."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


def read_side(paths: list[Path]) -> pd.DataFrame:
    if not paths:
        raise ValueError("Missing historical bid/ask CSV files")
    frame = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    required = ["timestamp", "open", "high", "low", "close"]
    if not set(required).issubset(frame.columns):
        raise ValueError("CSV requires timestamp (Unix milliseconds), open/high/low/close")
    if frame.empty or frame[required].isna().any().any():
        raise ValueError("Missing CSV OHLC or timestamp values")
    if not all(math.isfinite(float(value)) for value in frame[required].to_numpy().ravel()):
        raise ValueError("Non-finite CSV values")
    if frame.timestamp.duplicated().any() or not frame.timestamp.is_monotonic_increasing:
        raise ValueError("CSV timestamps must be unique and increasing")
    if (
        (frame.low > frame[["open", "close"]].min(axis=1))
        | (frame.high < frame[["open", "close"]].max(axis=1))
        | (frame.low <= 0)
    ).any():
        raise ValueError("Invalid CSV OHLC bounds")
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.timestamp, unit="ms", utc=True))
    return frame


def load_csv_history(directory: Path, point: float) -> dict[str, Any]:
    if point <= 0:
        raise ValueError("Broker point must be positive")
    manifest = json.loads((directory / "source_manifest.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = directory / Path(row["path"]).relative_to("data/historical")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Historical checksum mismatch: {path}")
    bid = read_side(sorted((directory / "xauusd/bid/m1").glob("*.csv")))
    ask = read_side(sorted((directory / "xauusd/ask/m1").glob("*.csv")))
    if not bid.index.equals(ask.index):
        raise ValueError("Bid/ask timestamps differ; history cannot be silently truncated")
    if (ask.close < bid.close).any():
        raise ValueError("Negative historical bid/ask spread")
    columns = ["open", "high", "low", "close"]
    unchanged = bid[columns].eq(bid.close.shift(), axis=0).all(axis=1) & ask[columns].eq(
        ask.close.shift(), axis=0
    ).all(axis=1)
    bid, ask = bid.loc[~unchanged], ask.loc[~unchanged]
    candles: dict[str, list[dict[str, Any]]] = {}
    aggregations = {"open": "first", "high": "max", "low": "min", "close": "last"}
    volume_column = next((key for key in ("tick_volume", "volume") if key in bid.columns), None)
    bid_aggregations = dict(aggregations)
    if volume_column is not None:
        if bid[volume_column].isna().any() or (bid[volume_column] < 0).any():
            raise ValueError("Invalid historical volume")
        bid_aggregations[volume_column] = "sum"
    for timeframe, rule in (("M5", "5min"), ("H1", "1h"), ("H4", "4h")):
        b = (
            bid.resample(rule, origin="epoch", closed="left", label="left")
            .agg(bid_aggregations)
            .dropna()
        )
        a = (
            ask.resample(rule, origin="epoch", closed="left", label="left")
            .agg(aggregations)
            .dropna()
        )
        rows: list[dict[str, Any]] = []
        for time, row in b.iterrows():
            quote = a.loc[time]
            item = {key: float(row[key]) for key in columns}
            item.update({f"ask_{key}": float(quote[key]) for key in columns})
            item.update(
                {
                    "time": int(pd.Timestamp(time).timestamp()),
                    "tick_volume": int(row[volume_column]) if volume_column is not None else 0,
                    "spread": math.ceil(max(0.0, (quote.close - row.close) / point)),
                }
            )
            rows.append(item)
        candles[timeframe] = rows
    return {
        "source": "csv",
        "symbol": "XAUUSD",
        "provider": "Dukascopy CSV mirror",
        "provenance": manifest,
        "candles": candles,
        "volume_available": volume_column is not None,
        "volume_placeholder": 0,
        "removed_unchanged_flat_minutes": int(unchanged.sum()),
    }
