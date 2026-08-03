"""Tests du moteur de backtesting."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.backtesting import BacktestEngine, BacktestStats, calculate_stats


def make_candle(
    index=0,
    open_price=1.0800,
    high=1.0810,
    low=1.0790,
    close=1.0805,
    volume=1000,
    symbol="EURUSD",
):
    """Crée une bougie de test."""
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, 0, index, tzinfo=timezone.utc),
        open=Decimal(str(open_price)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def make_candles(n=50, start=1.0800):
    """Crée une liste de bougies avec un trend haussier."""
    candles = []
    price = start
    for i in range(n):
        if i % 3 == 0:
            high = price + 0.0020
            low = price - 0.0010
            close = price + 0.0015
        else:
            high = price + 0.0010
            low = price - 0.0015
            close = price - 0.0005
        candles.append(make_candle(i, price, high, low, close))
        price = close
    return candles


def make_trade(
    direction=Direction.BUY,
    entry=1.0800,
    sl=1.0780,
    tp=1.0840,
    volume=0.1,
    profit=Decimal("10"),
):
    """Crée un trade fermé pour les tests de statistiques."""
    trade = Trade(
        symbol="EURUSD",
        direction=direction,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(sl)),
        take_profit=Decimal(str(tp)),
        volume=Decimal(str(volume)),
        strategy_name="Test",
        ticket=1,
    )
    trade.close_price = Decimal(str(entry))
    trade.profit = profit
    return trade


class TestBacktestEngineInitialization:
    def test_default_init(self):
        engine = BacktestEngine()
        assert engine.balance == Decimal("10000")
        assert engine.equity == Decimal("10000")
        assert len(engine.trades) == 0
        assert len(engine.open_trades) == 0
        assert len(engine.equity_curve) == 1

    def test_custom_balance(self):
        engine = BacktestEngine(initial_balance=Decimal("5000"))
        assert engine.balance == Decimal("5000")

    def test_custom_risk(self):
        engine = BacktestEngine(risk_per_trade=0.02)
        assert engine._risk_per_trade == 0.02


class TestBacktestRun:
    def test_run_empty_candles(self):
        engine = BacktestEngine()
        stats = engine.run([])
        assert stats.total_trades == 0
        assert stats.final_balance == Decimal("10000")

    def test_run_no_generator(self):
        """Test sans générateur de signaux — juste vérifier que ça ne plante pas."""
        candles = make_candles(30)
        engine = BacktestEngine()
        stats = engine.run(candles)
        assert stats.total_trades == 0
        assert stats.final_balance == Decimal("10000")
        assert len(engine.equity_curve) == 31  # 1 initial + 30 bougies

    def test_run_with_generator(self):
        """Test avec un générateur mock."""
        candles = make_candles(50)
        engine = BacktestEngine()

        class MockGenerator:
            def generate_best(self, candles, smc_data):
                if len(candles) < 5:
                    return None
                return Signal(
                    symbol="EURUSD",
                    signal_type=SignalType.BUY,
                    direction=Direction.BUY,
                    entry_price=candles[-1].close,
                    stop_loss=candles[-1].close - Decimal("0.0020"),
                    take_profit=candles[-1].close + Decimal("0.0040"),
                    confidence=0.7,
                    strategy_name="Test",
                    timeframe=TimeFrame.H1,
                    justification="Mock",
                    smc_concepts=["BOS"],
                )

        class MockDetector:
            def detect_sync(self, candles, symbol):
                return [{"concept": "BOS", "direction": "bullish"}]

        stats = engine.run(candles, signal_generator=MockGenerator(), smc_detector=MockDetector())
        assert stats.total_trades > 0
        assert stats.initial_balance == Decimal("10000")
        assert stats.final_balance != Decimal("10000")  # Le solde a changé

    def test_equity_curve_grows(self):
        candles = make_candles(30)
        engine = BacktestEngine()
        engine.run(candles)
        assert len(engine.equity_curve) == 31


class TestTradeManagement:
    def test_close_on_tp_buy(self):
        """Test qu'un trade BUY se ferme au TP."""
        engine = BacktestEngine()
        trade = Trade(
            symbol="EURUSD",
            direction=Direction.BUY,
            entry_price=Decimal("1.0800"),
            stop_loss=Decimal("1.0780"),
            take_profit=Decimal("1.0840"),
            volume=Decimal("0.1"),
            strategy_name="Test",
            ticket=1,
        )
        engine._open_trades.append(trade)
        engine._trades.append(trade)

        candle = make_candle(high=1.0850, low=1.0790, close=1.0845)
        engine._check_open_trades(candle)

        assert len(engine.open_trades) == 0
        assert trade.close_price == Decimal("1.0840")
        assert trade.profit is not None
        assert trade.profit > 0

    def test_close_on_sl_buy(self):
        """Test qu'un trade BUY se ferme au SL."""
        engine = BacktestEngine()
        trade = Trade(
            symbol="EURUSD",
            direction=Direction.BUY,
            entry_price=Decimal("1.0800"),
            stop_loss=Decimal("1.0780"),
            take_profit=Decimal("1.0840"),
            volume=Decimal("0.1"),
            strategy_name="Test",
            ticket=1,
        )
        engine._open_trades.append(trade)
        engine._trades.append(trade)

        candle = make_candle(high=1.0810, low=1.0770, close=1.0785)
        engine._check_open_trades(candle)

        assert len(engine.open_trades) == 0
        assert trade.close_price == Decimal("1.0780")
        assert trade.profit is not None
        assert trade.profit < 0

    def test_close_on_tp_sell(self):
        """Test qu'un trade SELL se ferme au TP."""
        engine = BacktestEngine()
        trade = Trade(
            symbol="EURUSD",
            direction=Direction.SELL,
            entry_price=Decimal("1.0800"),
            stop_loss=Decimal("1.0820"),
            take_profit=Decimal("1.0760"),
            volume=Decimal("0.1"),
            strategy_name="Test",
            ticket=1,
        )
        engine._open_trades.append(trade)
        engine._trades.append(trade)

        candle = make_candle(high=1.0810, low=1.0750, close=1.0765)
        engine._check_open_trades(candle)

        assert len(engine.open_trades) == 0
        assert trade.close_price == Decimal("1.0760")
        assert trade.profit is not None
        assert trade.profit > 0

    def test_no_close_if_no_hit(self):
        """Test qu'un trade reste ouvert si ni SL ni TP n'est touché."""
        engine = BacktestEngine()
        trade = Trade(
            symbol="EURUSD",
            direction=Direction.BUY,
            entry_price=Decimal("1.0800"),
            stop_loss=Decimal("1.0780"),
            take_profit=Decimal("1.0840"),
            volume=Decimal("0.1"),
            strategy_name="Test",
            ticket=1,
        )
        engine._open_trades.append(trade)

        candle = make_candle(high=1.0810, low=1.0790, close=1.0805)
        engine._check_open_trades(candle)

        assert len(engine.open_trades) == 1


class TestStatistics:
    def test_empty_stats(self):
        """Test les statistiques avec aucun trade."""
        stats = calculate_stats([], [Decimal("10000")], Decimal("10000"))
        assert stats.total_trades == 0
        assert stats.winning_trades == 0
        assert stats.losing_trades == 0
        assert stats.win_rate == 0.0
        assert stats.final_balance == Decimal("10000")

    def test_winning_trade(self):
        """Test les statistiques avec un trade gagnant."""
        trades = [make_trade(profit=Decimal("50"))]
        equity = [Decimal("10000"), Decimal("10050")]
        stats = calculate_stats(trades, equity, Decimal("10000"))
        assert stats.total_trades == 1
        assert stats.winning_trades == 1
        assert stats.losing_trades == 0
        assert stats.win_rate == 1.0
        assert stats.gross_profit == Decimal("50")
        assert stats.total_profit == Decimal("50")
        assert stats.final_balance == Decimal("10050")

    def test_losing_trade(self):
        """Test les statistiques avec un trade perdant."""
        trades = [make_trade(profit=Decimal("-30"))]
        equity = [Decimal("10000"), Decimal("9970")]
        stats = calculate_stats(trades, equity, Decimal("10000"))
        assert stats.total_trades == 1
        assert stats.winning_trades == 0
        assert stats.losing_trades == 1
        assert stats.win_rate == 0.0
        assert stats.gross_loss == Decimal("30")
        assert stats.total_profit == Decimal("-30")

    def test_mixed_trades(self):
        """Test avec des trades gagnants et perdants."""
        trades = [
            make_trade(profit=Decimal("50")),
            make_trade(profit=Decimal("-30")),
            make_trade(profit=Decimal("20")),
            make_trade(profit=Decimal("-10")),
        ]
        equity = [Decimal("10000"), Decimal("10050"), Decimal("10020"), Decimal("10040"), Decimal("10030")]
        stats = calculate_stats(trades, equity, Decimal("10000"))
        assert stats.total_trades == 4
        assert stats.winning_trades == 2
        assert stats.losing_trades == 2
        assert stats.win_rate == 0.5
        assert stats.gross_profit == Decimal("70")
        assert stats.gross_loss == Decimal("40")
        assert stats.total_profit == Decimal("30")
        assert stats.profit_factor == 70 / 40

    def test_profit_factor_no_losses(self):
        """Test le profit factor quand il n'y a pas de pertes."""
        trades = [make_trade(profit=Decimal("50"))]
        stats = calculate_stats(trades, [Decimal("10000"), Decimal("10050")], Decimal("10000"))
        assert stats.profit_factor == float("inf")

    def test_drawdown_calculation(self):
        """Test le calcul du drawdown maximum."""
        equity = [
            Decimal("10000"),
            Decimal("10500"),
            Decimal("10200"),
            Decimal("10300"),
            Decimal("10100"),
        ]
        trades = [make_trade(profit=Decimal("100"))]
        stats = calculate_stats(trades, equity, Decimal("10000"))
        assert stats.max_drawdown_amount == Decimal("400")
        assert stats.max_drawdown > 0

    def test_consecutive_wins_losses(self):
        """Test le comptage des séries de gains/pertes."""
        trades = [
            make_trade(profit=Decimal("10")),
            make_trade(profit=Decimal("10")),
            make_trade(profit=Decimal("10")),
            make_trade(profit=Decimal("-10")),
            make_trade(profit=Decimal("-10")),
        ]
        stats = calculate_stats(trades, [Decimal("10000")] * 6, Decimal("10000"))
        assert stats.max_consecutive_wins == 3
        assert stats.max_consecutive_losses == 2

    def test_sharpe_ratio(self):
        """Test le calcul du Sharpe ratio."""
        trades = [
            make_trade(profit=Decimal("10")),
            make_trade(profit=Decimal("20")),
            make_trade(profit=Decimal("-5")),
            make_trade(profit=Decimal("15")),
        ]
        stats = calculate_stats(trades, [Decimal("10000")] * 5, Decimal("10000"))
        assert stats.sharpe_ratio != 0

    def test_expectancy(self):
        """Test le calcul de l'expectancy."""
        trades = [
            make_trade(profit=Decimal("50")),
            make_trade(profit=Decimal("-30")),
        ]
        stats = calculate_stats(trades, [Decimal("10000"), Decimal("10050"), Decimal("10020")], Decimal("10000"))
        assert stats.expectancy == Decimal("10")

    def test_total_return_pct(self):
        """Test le pourcentage de retour."""
        trades = [make_trade(profit=Decimal("500"))]
        stats = calculate_stats(trades, [Decimal("10000"), Decimal("10500")], Decimal("10000"))
        assert stats.total_return_pct == 5.0


class TestGetSummary:
    def test_summary(self):
        engine = BacktestEngine(initial_balance=Decimal("5000"))
        summary = engine.get_summary()
        assert summary["initial_balance"] == "5000"
        assert summary["final_balance"] == "5000"
        assert summary["total_trades"] == 0
        assert summary["open_trades"] == 0
        assert summary["profit"] == "0"