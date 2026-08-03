"""Tests des entités du domaine."""

from datetime import datetime
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import Direction, SignalType, TimeFrame


class TestCandle:
    def test_bullish_candle(self):
        candle = Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.H1,
            time=datetime.utcnow(),
            open=Decimal("1.1000"),
            high=Decimal("1.1050"),
            low=Decimal("1.0990"),
            close=Decimal("1.1040"),
        )
        assert candle.is_bullish is True
        assert candle.body_size == Decimal("0.0040")

    def test_bearish_candle(self):
        candle = Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.H1,
            time=datetime.utcnow(),
            open=Decimal("1.1040"),
            high=Decimal("1.1050"),
            low=Decimal("1.0990"),
            close=Decimal("1.1000"),
        )
        assert candle.is_bullish is False


class TestSignal:
    def test_risk_reward_ratio_buy(self):
        signal = Signal(
            symbol="EURUSD",
            signal_type=SignalType.BUY,
            direction=Direction.BUY,
            entry_price=Decimal("1.1000"),
            stop_loss=Decimal("1.0950"),
            take_profit=Decimal("1.1100"),
            confidence=0.85,
            strategy_name="smc_trend",
            timeframe=TimeFrame.H1,
        )
        assert signal.risk_reward_ratio == 2.0

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            Signal(
                symbol="EURUSD",
                signal_type=SignalType.BUY,
                direction=Direction.BUY,
                entry_price=Decimal("1.1000"),
                stop_loss=Decimal("1.0950"),
                take_profit=Decimal("1.1100"),
                confidence=1.5,  # invalide
                strategy_name="test",
                timeframe=TimeFrame.H1,
            )


class TestTrade:
    def test_trade_defaults(self):
        trade = Trade(
            symbol="GBPUSD",
            direction=Direction.SELL,
            entry_price=Decimal("1.2500"),
            stop_loss=Decimal("1.2550"),
            take_profit=Decimal("1.2400"),
            volume=Decimal("0.1"),
        )
        assert trade.is_open is True
        assert trade.ticket is None
