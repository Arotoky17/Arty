"""Cooldown boundaries and independent risk limits with a deterministic UTC clock."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arty_trading.config.settings import RiskSettings
from arty_trading.core.entities import Signal, Trade, TradingAccount
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.risk.manager import RiskManager


def trade() -> Trade:
    return Trade(
        symbol="XAUUSD",
        direction=Direction.BUY,
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1999"),
        take_profit=Decimal("2003"),
        volume=Decimal("0.1"),
    )


def signal() -> Signal:
    return Signal(
        symbol="XAUUSD",
        direction=Direction.BUY,
        signal_type=SignalType.BUY,
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1999"),
        take_profit=Decimal("2003"),
        confidence=0.9,
        strategy_name="fixture",
        timeframe=TimeFrame.M5,
        justification="synthetic risk control",
    )


def account() -> TradingAccount:
    return TradingAccount(
        login=0,
        server="fixture",
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        free_margin=Decimal("10000"),
    )


@pytest.mark.asyncio
async def test_circuit_breaker_rearms_after_cooldown() -> None:
    now = datetime(2024, 1, 2, 12, tzinfo=UTC)
    manager = RiskManager(RiskSettings(_env_file=None), clock=lambda: now)
    for _ in range(3):
        manager.close_trade(trade(), Decimal("-10"))
    assert not await manager.validate_signal(signal(), account())
    deadline = now + timedelta(hours=24)
    manager.reset_daily()  # Midnight reset must not shorten the cooldown.
    now = deadline - timedelta(microseconds=1)
    assert manager.consecutive_loss_breaker_active()
    now = deadline
    assert await manager.validate_signal(signal(), account())
    assert manager.consecutive_losses == 0
    assert manager.get_risk_report()["consecutive_loss_breaker_until"] is None
    for _ in range(3):
        manager.close_trade(trade(), Decimal("-10"))
    assert manager.consecutive_loss_breaker_active()
    assert (
        manager.get_risk_report()["consecutive_loss_breaker_until"]
        == (now + timedelta(hours=24)).isoformat()
    )


@pytest.mark.asyncio
async def test_circuit_breaker_does_not_block_after_reset() -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    manager = RiskManager(RiskSettings(_env_file=None), clock=lambda: now)
    for _ in range(3):
        manager.close_trade(trade(), Decimal("-110"))
    now += timedelta(hours=24)
    assert not manager.consecutive_loss_breaker_active()
    # Cooldown cannot clear the monetary daily guard.
    assert not await manager.validate_signal(signal(), account())
    assert manager.last_rejection_reason == "daily_loss"
    manager.reset_daily()
    assert await manager.validate_signal(signal(), account())
    manager.update_equity(Decimal("10000"))
    manager.update_equity(Decimal("8900"))
    assert not await manager.validate_signal(signal(), account())
    assert manager.last_rejection_reason == "drawdown"


@pytest.mark.asyncio
async def test_rejection_reasons_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    manager = RiskManager(RiskSettings(_env_file=None))
    with caplog.at_level("INFO"):
        assert not await manager.validate_signal(
            signal().model_copy(update={"confidence": 0.1}), account()
        )
    assert manager.last_rejection_reason == "confidence"
    assert "reason=confidence" in caplog.text
