"""Tests du moteur de backtesting (Phase 8bis).

Génère des données historiques synthétiques pour EURUSD et XAUUSD,
lance le backtest, et rapporte les métriques de performance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.signals.validator import ALL_CONDITIONS
from arty_trading.modules.strategies import SMCTrendStrategy
from arty_trading.utils.helpers import get_pip_size, pip_value


def make_backtest_candle(idx: int, symbol: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx * 5),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=1000,
        spread=3,
    )


def make_eurusd_candles(n: int = 200) -> list[Candle]:
    """Génère des bougies M5 synthétiques pour EURUSD (tendance haussière avec pullbacks)."""
    candles = []
    base = 1.0800
    for i in range(n):
        o = base + (i * 0.0005)
        c = o + 0.0010
        h = c + 0.0005
        l = o - 0.0003
        candles.append(make_backtest_candle(i, "EURUSD", o, h, l, c))
        if i % 10 == 9:
            base = float(candles[-1].close) - 0.0015
    return candles


def make_xauusd_candles(n: int = 200) -> list[Candle]:
    """Génère des bougies M5 synthétiques pour XAUUSD (tendance haussière avec pullbacks)."""
    candles = []
    base = 2000.0
    for i in range(n):
        o = base + (i * 0.5)
        c = o + 1.0
        h = c + 0.5
        l = o - 0.3
        candles.append(make_backtest_candle(i, "XAUUSD", o, h, l, c))
        if i % 10 == 9:
            base = float(candles[-1].close) - 1.5
    return candles


class TestBacktestEngine:
    """Tests du backtest sur EURUSD et XAUUSD."""

    @pytest.mark.asyncio
    async def test_backtest_eurusd(self):
        candles = make_eurusd_candles(200)
        detector = SMCDetector()
        strategy = SMCTrendStrategy()
        validator = SignalValidator(min_risk_reward=2.0, max_spread=30)
        generator = SignalGenerator(
            min_confidence=0.7,
            active_strategy="SMC Trend Following",
            strategies=[strategy],
            validator=validator,
        )

        engine = BacktestEngine(
            initial_balance=Decimal("10000"),
            risk_per_trade=0.01,
            symbol="EURUSD",
        )
        stats = await engine.run_async(candles, signal_generator=generator, smc_detector=detector)

        print(
            f"BACKTEST EURUSD | trades={stats.total_trades} | "
            f"win_rate={stats.win_rate:.2%} | profit_factor={stats.profit_factor:.2f} | "
            f"max_dd={stats.max_drawdown:.2%} | final_balance={stats.final_balance}"
        )
        assert isinstance(stats.total_trades, int)

    @pytest.mark.asyncio
    async def test_backtest_xauusd(self):
        candles = make_xauusd_candles(200)
        detector = SMCDetector()
        strategy = SMCTrendStrategy()
        validator = SignalValidator(min_risk_reward=2.0, max_spread=200)
        generator = SignalGenerator(
            min_confidence=0.7,
            active_strategy="SMC Trend Following",
            strategies=[strategy],
            validator=validator,
        )

        engine = BacktestEngine(
            initial_balance=Decimal("10000"),
            risk_per_trade=0.01,
            symbol="XAUUSD",
        )
        stats = await engine.run_async(candles, signal_generator=generator, smc_detector=detector)

        print(
            f"BACKTEST XAUUSD | trades={stats.total_trades} | "
            f"win_rate={stats.win_rate:.2%} | profit_factor={stats.profit_factor:.2f} | "
            f"max_dd={stats.max_drawdown:.2%} | final_balance={stats.final_balance}"
        )
        assert isinstance(stats.total_trades, int)

    def test_pip_values_per_symbol(self):
        """Vérifie que les pip values sont corrects pour EURUSD et XAUUSD."""
        eurusd_pip = pip_value("EURUSD", lot_size=1.0)
        xauusd_pip = pip_value("XAUUSD", lot_size=1.0)

        assert eurusd_pip == 10.0
        assert xauusd_pip == 1.0

    def test_pip_sizes_per_symbol(self):
        """Vérifie que les pip sizes sont corrects pour EURUSD et XAUUSD."""
        from arty_trading.utils.helpers import get_pip_size

        assert get_pip_size("EURUSD") == Decimal("0.0001")
        assert get_pip_size("XAUUSD") == Decimal("0.01")
