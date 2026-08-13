"""Tests de caractérisation des garde-fous d'exécution (application.execution_guards).

Extraits de ``application/trading_engine.py`` sans changement de comportement.
Ces tests figent les valeurs de retour et la logique de chaque branche.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from arty_trading.application.execution_guards import (
    final_gate_before_execution,
    revalidate_before_execution,
)
from arty_trading.core.entities import Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.decision.market_context import MarketContext


def _signal(direction: Direction = Direction.BUY, entry: str = "1.1000",
            sl: str = "1.0950", tp: str = "1.1100") -> Signal:
    """Signal avec un R/R par défaut de 2.0 (sl 50, tp 100)."""
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        confidence=0.8,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.M5,
    )


def _context(master_trend: str = "bullish", regime: str = "unknown",
             spread: int = 5, candles=None, smc_data=None) -> MarketContext:
    return MarketContext(
        symbol="EURUSD",
        timestamp=datetime(2024, 1, 2, 8, 0, tzinfo=UTC),
        master_trend=master_trend,
        regime=regime,
        spread=spread,
        ltf_candles=candles or [],
        ltf_smc_data=smc_data or [],
    )


def _settings(supported: list[str] | None = None, profile=None) -> MagicMock:
    settings = MagicMock()
    settings.supported_symbols = supported if supported is not None else ["EURUSD", "XAUUSD"]
    settings.get_instrument_profile.return_value = profile
    return settings


# ---------------------------------------------------------------------------
# revalidate_before_execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidation_bearish_blocks_buy() -> None:
    assert await revalidate_before_execution("EURUSD", _signal(Direction.BUY), _context("bearish")) is False


@pytest.mark.asyncio
async def test_revalidation_bullish_blocks_sell() -> None:
    assert await revalidate_before_execution("EURUSD", _signal(Direction.SELL), _context("bullish")) is False


@pytest.mark.asyncio
async def test_revalidation_neutral_blocks_all() -> None:
    assert await revalidate_before_execution("EURUSD", _signal(Direction.BUY), _context("neutral")) is False


@pytest.mark.asyncio
async def test_revalidation_aligned_pass() -> None:
    assert await revalidate_before_execution("EURUSD", _signal(Direction.BUY), _context("bullish")) is True
    assert await revalidate_before_execution("EURUSD", _signal(Direction.SELL), _context("bearish")) is True


# ---------------------------------------------------------------------------
# final_gate_before_execution
# ---------------------------------------------------------------------------


class _Profile:
    min_risk_reward = 2.0
    max_spread_points = 30
    max_zone_age_bars = 20
    retest_atr_mult = 1.0


@pytest.mark.asyncio
async def test_final_gate_unsupported_symbol() -> None:
    settings = _settings(supported=["EURUSD"])
    r = await final_gate_before_execution("GBPUSD", _signal(), _context("bullish"), settings)
    assert r is False


@pytest.mark.asyncio
async def test_final_gate_sl_or_tp_equals_entry() -> None:
    settings = _settings(profile=_Profile())
    sig = _signal(entry="1.1000", sl="1.1000", tp="1.1100")
    r = await final_gate_before_execution("EURUSD", sig, _context("bullish"), settings)
    assert r is False


@pytest.mark.asyncio
async def test_final_gate_rr_below_min() -> None:
    settings = _settings(profile=_Profile())
    # RR = 5/100 = 0.05 < 2.0
    sig = _signal(entry="1.1000", sl="1.0995", tp="1.1000")
    r = await final_gate_before_execution("EURUSD", sig, _context("bullish"), settings)
    assert r is False


@pytest.mark.asyncio
async def test_final_gate_spread_too_high() -> None:
    settings = _settings(profile=_Profile())
    r = await final_gate_before_execution("EURUSD", _signal(), _context("bullish", spread=50), settings)
    assert r is False


@pytest.mark.asyncio
async def test_final_gate_aligned_valid_pass() -> None:
    settings = _settings(profile=_Profile())
    r = await final_gate_before_execution("EURUSD", _signal(), _context("bullish"), settings)
    assert r is True