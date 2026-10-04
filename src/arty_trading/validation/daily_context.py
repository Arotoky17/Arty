"""One causal NY/UTC D1 series for ADX, PDH/PDL and the structural HTF bias."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import resample_closed_bars


def daily_bars(frame: pd.DataFrame, source_minutes: int, cutoff: datetime) -> pd.DataFrame:
    calendar = MarketCalendar()
    active = frame.loc[~calendar.annotate(frame).non_tradable]
    bars = resample_closed_bars(active, TimeFrame.D1.minutes, source_minutes)
    if frame.empty:
        return bars
    # A source beginning partway through a session cannot define its full high/low.
    bars = bars.loc[(bars.index >= frame.index[0]) & (bars.available_at <= cutoff)]
    return bars


def daily_candles(
    frame: pd.DataFrame, symbol: str, source_minutes: int, cutoff: datetime
) -> list[Candle]:
    bars = daily_bars(frame, source_minutes, cutoff)
    return [
        Candle(
            symbol=symbol,
            timeframe=TimeFrame.D1,
            time=at.to_pydatetime(),
            available_at=row.available_at.to_pydatetime(),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=int(row.get("volume", row.get("tick_volume", 0))),
        )
        for at, row in bars.iterrows()
    ]


def candle_close_time(candle: Candle) -> datetime:
    return candle.available_at or candle.time + timedelta(minutes=candle.timeframe.minutes)


def previous_day_levels(candles: list[Candle], at: datetime) -> dict[str, Any]:
    closed = [c for c in candles if c.timeframe == TimeFrame.D1 and candle_close_time(c) <= at]
    if not closed:
        return {"pdh": None, "pdl": None, "available_at": None}
    previous = max(closed, key=candle_close_time)
    return {
        "pdh": previous.high,
        "pdl": previous.low,
        "available_at": candle_close_time(previous).isoformat(),
    }
