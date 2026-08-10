"""Tests des règles de gestion de position en R."""

from decimal import Decimal

from arty_trading.config.settings import PositionSettings
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction
from arty_trading.modules.execution import PositionManager
from arty_trading.modules.execution.paper_executor import PaperOrderExecutor


def _trade() -> Trade:
    return Trade(
        symbol="EURUSD",
        direction=Direction.BUY,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0990"),
        take_profit=Decimal("1.1040"),
        volume=Decimal("0.10"),
    )


def test_break_even_partial_and_trailing_are_triggered_once() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    at_one_r = manager.evaluate(trade, Decimal("1.1010"))
    assert at_one_r[0].reason == "break_even"
    trade.stop_loss = Decimal("1.1000")
    at_two_r = manager.evaluate(trade, Decimal("1.1020"))
    assert at_two_r[0].kind == "partial_close"
    at_three_r = manager.evaluate(trade, Decimal("1.1030"))
    assert at_three_r[0].reason == "trailing_stop"


def test_stop_or_target_requests_a_full_close() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    assert manager.evaluate(trade, Decimal("1.0990"))[0].reason == "stop_loss"
    assert manager.evaluate(trade, Decimal("1.1040"))[0].reason == "take_profit"


async def test_paper_executor_reduces_the_position_for_partial_tp() -> None:
    executor = PaperOrderExecutor()
    trade = _trade()
    trade.ticket = 50001
    executor._open_trades[trade.ticket] = trade  # état contrôlé du simulateur
    closed = await executor.close_partial_order(trade, 0.5)
    assert closed is not None
    assert closed.volume == Decimal("0.05")
    assert trade.volume == Decimal("0.05")
