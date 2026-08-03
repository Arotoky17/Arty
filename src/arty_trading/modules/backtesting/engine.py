"""
Moteur de backtesting.

Simule l'exécution de stratégies sur des données historiques
et calcule les statistiques de performance.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.backtesting.stats import BacktestStats, calculate_stats

logger = get_logger(LogCategory.BACKTEST)


class BacktestEngine:
    """
    Moteur de backtesting.

    Simule l'exécution d'une stratégie sur des données historiques.
    Parcourt les bougies une par une, génère des signaux, ouvre/ferme
    des trades virtuels, et calcule les statistiques de performance.
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("10000"),
        risk_per_trade: float = 0.01,
        spread_pips: float = 1.0,
    ) -> None:
        self._initial_balance = initial_balance
        self._risk_per_trade = risk_per_trade
        self._spread_pips = spread_pips
        self._balance = initial_balance
        self._equity = initial_balance
        self._trades: list[Trade] = []
        self._open_trades: list[Trade] = []
        self._equity_curve: list[Decimal] = [initial_balance]
        self._ticket_counter = 1

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def equity(self) -> Decimal:
        return self._equity

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def open_trades(self) -> list[Trade]:
        return list(self._open_trades)

    @property
    def equity_curve(self) -> list[Decimal]:
        return list(self._equity_curve)

    def run(
        self,
        candles: list[Candle],
        signal_generator=None,
        smc_detector=None,
        symbol: str = "EURUSD",
    ) -> BacktestStats:
        if not candles:
            return calculate_stats([], self._equity_curve, self._initial_balance)

        for i, candle in enumerate(candles):
            self._check_open_trades(candle)

            if signal_generator and smc_detector and i >= 20:
                recent_candles = candles[max(0, i - 20):i + 1]
                smc_data = smc_detector.detect_sync(recent_candles, symbol)
                signal = signal_generator.generate_best(recent_candles, smc_data)

                if signal:
                    self._open_trade_from_signal(signal, candle)

            self._update_equity(candle)
            self._equity_curve.append(self._equity)

        if self._open_trades:
            last_candle = candles[-1]
            for trade in list(self._open_trades):
                self._close_trade(trade, last_candle.close)

        return calculate_stats(self._trades, self._equity_curve, self._initial_balance)

    def _open_trade_from_signal(self, signal: Signal, candle: Candle) -> None:
        risk_amount = float(self._balance) * self._risk_per_trade
        sl_distance = abs(float(signal.entry_price - signal.stop_loss))
        if sl_distance == 0:
            return

        pip_size = 0.01 if "JPY" in signal.symbol else 0.0001
        sl_pips = sl_distance / pip_size
        if sl_pips == 0:
            return

        pip_value_per_lot = 10.0
        volume = risk_amount / (sl_pips * pip_value_per_lot)
        volume = round(volume, 2)
        if volume < 0.01:
            volume = 0.01

        trade = Trade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=Decimal(str(volume)),
            signal_id=signal.id,
            strategy_name=signal.strategy_name,
            ticket=self._ticket_counter,
        )
        self._ticket_counter += 1
        self._open_trades.append(trade)
        self._trades.append(trade)

    def _check_open_trades(self, candle: Candle) -> None:
        for trade in list(self._open_trades):
            hit_sl = False
            hit_tp = False
            close_price = float(trade.entry_price)

            if trade.direction == Direction.BUY:
                if candle.low <= float(trade.stop_loss):
                    hit_sl = True
                    close_price = float(trade.stop_loss)
                elif candle.high >= float(trade.take_profit):
                    hit_tp = True
                    close_price = float(trade.take_profit)
            else:
                if candle.high >= float(trade.stop_loss):
                    hit_sl = True
                    close_price = float(trade.stop_loss)
                elif candle.low <= float(trade.take_profit):
                    hit_tp = True
                    close_price = float(trade.take_profit)

            if hit_sl or hit_tp:
                self._close_trade(trade, Decimal(str(close_price)))

    def _close_trade(self, trade: Trade, close_price: Decimal) -> None:
        pip_size = 0.01 if "JPY" in trade.symbol else 0.0001
        if trade.direction == Direction.BUY:
            pips = (float(close_price) - float(trade.entry_price)) / pip_size
        else:
            pips = (float(trade.entry_price) - float(close_price)) / pip_size

        pip_value_per_lot = 10.0
        profit = Decimal(str(pips * pip_value_per_lot * float(trade.volume)))

        trade.close_price = close_price
        trade.profit = profit

        self._balance += profit
        self._open_trades.remove(trade)

    def _update_equity(self, candle: Candle) -> None:
        floating = Decimal("0")
        for trade in self._open_trades:
            pip_size = 0.01 if "JPY" in trade.symbol else 0.0001
            if trade.direction == Direction.BUY:
                pips = (float(candle.close) - float(trade.entry_price)) / pip_size
            else:
                pips = (float(trade.entry_price) - float(candle.close)) / pip_size
            pip_value_per_lot = 10.0
            floating += Decimal(str(pips * pip_value_per_lot * float(trade.volume)))

        self._equity = self._balance + floating

    def get_summary(self) -> dict:
        return {
            "initial_balance": str(self._initial_balance),
            "final_balance": str(self._balance),
            "total_trades": len(self._trades),
            "open_trades": len(self._open_trades),
            "profit": str(self._balance - self._initial_balance),
        }
