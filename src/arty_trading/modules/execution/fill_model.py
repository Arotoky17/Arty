"""Limit execution only on bars subsequent to order submission."""

import math
import random
from dataclasses import dataclass

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction


@dataclass(frozen=True)
class FillModel:
    mode: str
    crossing_pips: float
    probability: float
    seed: int
    expiry_bars: int

    def __post_init__(self) -> None:
        if self.mode not in {"touched", "crossed"}:
            raise ValueError("Unknown fill mode")
        if not math.isfinite(self.crossing_pips) or self.crossing_pips < 0:
            raise ValueError("Invalid crossing distance")
        if not 0 <= self.probability <= 1 or self.expiry_bars < 1:
            raise ValueError("Invalid fill probability or expiry")

    def fills(self, signal: Signal, candle: Candle, pip_size: float, rng: random.Random) -> bool:
        distance = self.crossing_pips * pip_size if self.mode == "crossed" else 0
        price = float(signal.entry_price)
        eligible = (
            float(candle.low) <= price - distance
            if signal.direction == Direction.BUY
            else float(candle.high) >= price + distance
        )
        return eligible and rng.random() < self.probability
