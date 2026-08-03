"""
Générateur de signaux — fusionne les données SMC et les stratégies.
"""

from __future__ import annotations

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.strategies.base import BaseStrategy
from arty_trading.modules.strategies.strategies import (
    BreakoutStrategy,
    MomentumStrategy,
    ReversalStrategy,
    ScalpingStrategy,
    SMCTrendStrategy,
    SwingStrategy,
)

logger = get_logger(LogCategory.SIGNAL)


class SignalGenerator:
    """Générateur de signaux de trading."""

    def __init__(self, min_confidence: float = 0.5, strategies: list[BaseStrategy] | None = None) -> None:
        self._min_confidence = min_confidence
        self._strategies: dict[str, BaseStrategy] = {}
        if strategies is None:
            strategies = [
                SMCTrendStrategy(),
                BreakoutStrategy(),
                MomentumStrategy(),
                ReversalStrategy(),
                ScalpingStrategy(),
                SwingStrategy(),
            ]
        for s in strategies:
            self._strategies[s.name] = s

    @property
    def strategies(self) -> dict[str, BaseStrategy]:
        return self._strategies

    def enable_strategy(self, name: str) -> None:
        if name in self._strategies:
            self._strategies[name].enabled = True

    def disable_strategy(self, name: str) -> None:
        if name in self._strategies:
            self._strategies[name].enabled = False

    def enable_all(self) -> None:
        for s in self._strategies.values():
            s.enabled = True

    def disable_all(self) -> None:
        for s in self._strategies.values():
            s.enabled = False

    def get_enabled_strategies(self) -> list[str]:
        return [name for name, s in self._strategies.items() if s.enabled]

    async def generate(self, candles: list[Candle], smc_data: list[dict]) -> Signal | None:
        if not candles:
            return None
        signals: list[Signal] = []
        for name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue
            try:
                signal = await strategy.analyze(candles, smc_data)
                if signal is not None and signal.confidence >= self._min_confidence:
                    signals.append(signal)
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc)
        if not signals:
            return None
        return max(signals, key=lambda s: s.confidence)

    async def generate_all(self, candles: list[Candle], smc_data: list[dict]) -> list[Signal]:
        if not candles:
            return []
        signals: list[Signal] = []
        for name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue
            try:
                signal = await strategy.analyze(candles, smc_data)
                if signal is not None and signal.confidence >= self._min_confidence:
                    signals.append(signal)
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc)
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals