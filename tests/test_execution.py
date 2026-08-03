"""Tests de l'exécuteur d'ordres."""

from decimal import Decimal

import pytest

from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.execution import OrderExecutor


def make_signal(symbol="EURUSD", direction=Direction.BUY, entry=1.0800, sl=1.0780, tp=1.0840):
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(sl)),
        take_profit=Decimal(str(tp)),
        confidence=0.7,
        strategy_name="Test",
        timeframe=TimeFrame.H1,
        justification="Test signal",
        smc_concepts=["BOS"],
    )


class TestOrderExecutorInitialization:
    def test_mock_mode_by_default(self):
        executor = OrderExecutor(mock_mode=True)
        assert executor.is_mock_mode is True

    def test_trailing_stop_config(self):
        executor = OrderExecutor(mock_mode=True, trailing_stop_pips=20)
        assert executor._trailing_stop_pips == 20

    def test_break_even_config(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=10)
        assert executor._break_even_pips == 10


class TestOpenOrder:
    @pytest.mark.asyncio
    async def test_open_buy_order(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal(direction=Direction.BUY)
        trade = await executor.open_order(signal, 0.1)
        assert trade.symbol == "EURUSD"
        assert trade.direction == Direction.BUY
        assert trade.entry_price == Decimal("1.0800")
        assert trade.stop_loss == Decimal("1.0780")
        assert trade.take_profit == Decimal("1.0840")
        assert trade.volume == Decimal("0.1")
        assert trade.ticket is not None
        assert trade.is_open is True

    @pytest.mark.asyncio
    async def test_open_sell_order(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal(direction=Direction.SELL, entry=1.0800, sl=1.0820, tp=1.0760)
        trade = await executor.open_order(signal, 0.2)
        assert trade.direction == Direction.SELL
        assert trade.entry_price == Decimal("1.0800")
        assert trade.stop_loss == Decimal("1.0820")
        assert trade.take_profit == Decimal("1.0760")
        assert trade.volume == Decimal("0.2")

    @pytest.mark.asyncio
    async def test_open_order_assigns_ticket(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade1 = await executor.open_order(signal, 0.1)
        trade2 = await executor.open_order(signal, 0.1)
        assert trade1.ticket != trade2.ticket

    @pytest.mark.asyncio
    async def test_open_order_preserves_signal_id(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        assert trade.signal_id == signal.id

    @pytest.mark.asyncio
    async def test_open_order_preserves_strategy_name(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        assert trade.strategy_name == signal.strategy_name


class TestCloseOrder:
    @pytest.mark.asyncio
    async def test_close_order(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        closed = await executor.close_order(trade)
        assert closed.close_price is not None
        assert closed.profit is not None

    @pytest.mark.asyncio
    async def test_close_order_removes_from_mock_trades(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        assert trade.ticket in executor._mock_trades
        await executor.close_order(trade)
        assert trade.ticket not in executor._mock_trades


class TestModifyOrder:
    @pytest.mark.asyncio
    async def test_modify_stop_loss(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        modified = await executor.modify_order(trade, stop_loss=1.0770)
        assert modified.stop_loss == Decimal("1.0770")

    @pytest.mark.asyncio
    async def test_modify_take_profit(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        modified = await executor.modify_order(trade, take_profit=1.0850)
        assert modified.take_profit == Decimal("1.0850")

    @pytest.mark.asyncio
    async def test_modify_both(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        modified = await executor.modify_order(trade, stop_loss=1.0770, take_profit=1.0850)
        assert modified.stop_loss == Decimal("1.0770")
        assert modified.take_profit == Decimal("1.0850")

    @pytest.mark.asyncio
    async def test_modify_none_unchanged(self):
        executor = OrderExecutor(mock_mode=True)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        modified = await executor.modify_order(trade)
        assert modified.stop_loss == Decimal("1.0780")
        assert modified.take_profit == Decimal("1.0840")


class TestTrailingStop:
    @pytest.mark.asyncio
    async def test_trailing_stop_buy(self):
        executor = OrderExecutor(mock_mode=True, trailing_stop_pips=10)
        signal = make_signal(entry=1.0800, sl=1.0780)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_trailing_stop(trade, current_price=1.0820)
        assert result is not None
        assert float(result.stop_loss) > 1.0780

    @pytest.mark.asyncio
    async def test_trailing_stop_sell(self):
        executor = OrderExecutor(mock_mode=True, trailing_stop_pips=10)
        signal = make_signal(direction=Direction.SELL, entry=1.0800, sl=1.0820, tp=1.0760)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_trailing_stop(trade, current_price=1.0780)
        assert result is not None
        assert float(result.stop_loss) < 1.0820

    @pytest.mark.asyncio
    async def test_trailing_stop_disabled(self):
        executor = OrderExecutor(mock_mode=True, trailing_stop_pips=0)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_trailing_stop(trade, current_price=1.0820)
        assert result is None

    @pytest.mark.asyncio
    async def test_trailing_stop_no_move_backward(self):
        executor = OrderExecutor(mock_mode=True, trailing_stop_pips=10)
        signal = make_signal(entry=1.0800, sl=1.0780)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_trailing_stop(trade, current_price=1.0790)
        assert result is None


class TestBreakEven:
    @pytest.mark.asyncio
    async def test_break_even_buy(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=15)
        signal = make_signal(entry=1.0800, sl=1.0780)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_break_even(trade, current_price=1.0820)
        assert result is not None
        assert float(result.stop_loss) == 1.0800

    @pytest.mark.asyncio
    async def test_break_even_sell(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=15)
        signal = make_signal(direction=Direction.SELL, entry=1.0800, sl=1.0820, tp=1.0760)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_break_even(trade, current_price=1.0780)
        assert result is not None
        assert float(result.stop_loss) == 1.0800

    @pytest.mark.asyncio
    async def test_break_even_disabled(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=0)
        signal = make_signal()
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_break_even(trade, current_price=1.0820)
        assert result is None

    @pytest.mark.asyncio
    async def test_break_even_not_reached(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=30)
        signal = make_signal(entry=1.0800, sl=1.0780)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_break_even(trade, current_price=1.0810)
        assert result is None

    @pytest.mark.asyncio
    async def test_break_even_already_at_entry(self):
        executor = OrderExecutor(mock_mode=True, break_even_pips=15)
        signal = make_signal(entry=1.0800, sl=1.0800)
        trade = await executor.open_order(signal, 0.1)
        result = await executor.apply_break_even(trade, current_price=1.0820)
        assert result is None