"""Limit execution only on bars subsequent to order submission."""

import math
import random
from dataclasses import dataclass

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction
from arty_trading.validation.market_calendar import entry_allowed


@dataclass(frozen=True)
class FillModel:
    mode: str
    crossing_pips: float
    probability: float
    seed: int
    expiry_bars: int
    crossing_usd: float | None = None
    stress_crossing_usd: float | None = None
    stop_on_fill_bar: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"touched", "crossed"}:
            raise ValueError("Unknown fill mode")
        if not math.isfinite(self.crossing_pips) or self.crossing_pips < 0:
            raise ValueError("Invalid crossing distance")
        if not 0 <= self.probability <= 1 or self.expiry_bars < 1:
            raise ValueError("Invalid fill probability or expiry")
        if any(
            value is not None and (not math.isfinite(value) or value < 0)
            for value in (self.crossing_usd, self.stress_crossing_usd)
        ):
            raise ValueError("Invalid USD crossing distance")

    def fills(self, signal: Signal, candle: Candle, pip_size: float, rng: random.Random) -> bool:
        if not entry_allowed(candle):
            return False
        distance = self.crossing_pips * pip_size if self.mode == "crossed" else 0
        if signal.symbol == "XAUUSD" and self.crossing_usd is not None and self.mode == "crossed":
            distance = self.crossing_usd
        price = float(signal.entry_price)
        eligible = (
            float(candle.low) <= price - distance
            if signal.direction == Direction.BUY
            else float(candle.high) >= price + distance
        )
        return eligible and rng.random() < self.probability
