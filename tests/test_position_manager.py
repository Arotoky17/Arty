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
    actions = manager.evaluate(trade, Decimal("1.1040"))
    assert any(a.reason == "take_profit" for a in actions)


async def test_paper_executor_reduces_the_position_for_partial_tp() -> None:
    executor = PaperOrderExecutor()
    trade = _trade()
    trade.ticket = 50001
    executor._open_trades[trade.ticket] = trade  # état contrôlé du simulateur
    closed = await executor.close_partial_order(trade, 0.5)
    assert closed is not None
    assert closed.volume == Decimal("0.05")
    assert trade.volume == Decimal("0.05")


# =============================================================================
# Tests gestion des gains — Break-even, Partial TP, Trailing
# =============================================================================


def _sell_trade() -> Trade:
    return Trade(
        symbol="EURUSD",
        direction=Direction.SELL,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.1010"),
        take_profit=Decimal("1.0960"),
        volume=Decimal("0.10"),
    )


def test_no_break_even_below_threshold() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1009"))
    assert not any(a.reason == "break_even" for a in actions)


def test_break_even_at_exactly_one_r() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1010"))
    assert any(a.reason == "break_even" for a in actions)


def test_break_even_only_once() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    actions = manager.evaluate(trade, Decimal("1.1020"))
    assert not any(a.reason == "break_even" for a in actions)


def test_partial_tp_at_1_5_r() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1015"))
    assert any(a.kind == "partial_close" for a in actions)


def test_trailing_activated_at_1_5_r() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1015"))
    assert any(a.reason == "trailing_stop" for a in actions)


def test_trailing_improves_sl_only() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    actions = manager.evaluate(trade, Decimal("1.1030"))
    trailing_actions = [a for a in actions if a.reason == "trailing_stop"]
    assert len(trailing_actions) == 1
    assert trailing_actions[0].stop_loss > trade.stop_loss


def test_buy_trailing_correct() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    actions = manager.evaluate(trade, Decimal("1.1030"))
    trailing = [a for a in actions if a.reason == "trailing_stop"]
    assert len(trailing) == 1
    assert trailing[0].stop_loss > trade.entry_price


def test_sell_trailing_correct() -> None:
    manager = PositionManager(PositionSettings())
    trade = _sell_trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("0.9990"))
    manager.evaluate(trade, Decimal("0.9985"))
    actions = manager.evaluate(trade, Decimal("0.9970"))
    trailing = [a for a in actions if a.reason == "trailing_stop"]
    assert len(trailing) == 1
    assert trailing[0].stop_loss < trade.entry_price


def test_trailing_never_worsens_sl() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    manager.evaluate(trade, Decimal("1.1020"))
    actions = manager.evaluate(trade, Decimal("1.0995"))
    trailing = [a for a in actions if a.reason == "trailing_stop"]
    assert len(trailing) == 0


def test_partial_tp_triggers_only_once() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1015"))
    actions = manager.evaluate(trade, Decimal("1.1020"))
    assert not any(a.kind == "partial_close" for a in actions)


def test_final_tp_at_2_r_closes_remainder() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    actions = manager.evaluate(trade, Decimal("1.1040"))
    assert any(a.reason == "take_profit" for a in actions)


def test_retrace_after_1_5_r_keeps_protection() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    trailing_actions_1 = manager.evaluate(trade, Decimal("1.1015"))
    assert any(a.reason == "trailing_stop" for a in trailing_actions_1)
    new_sl = [a.stop_loss for a in trailing_actions_1 if a.reason == "trailing_stop"][0]
    assert new_sl > trade.stop_loss
    actions_after_retrace = manager.evaluate(trade, Decimal("1.1005"))
    assert not any(a.reason == "stop_loss" for a in actions_after_retrace)


def test_initial_risk_unchanged() -> None:
    manager = PositionManager(PositionSettings())
    trade = _trade()
    manager.register(trade)
    initial_risk = manager._states[str(trade.id)].initial_risk
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    manager.evaluate(trade, Decimal("1.1030"))
    assert manager._states[str(trade.id)].initial_risk == initial_risk
