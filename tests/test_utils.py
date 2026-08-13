"""Tests des utilitaires."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytz

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame, TradingSession
from arty_trading.utils.helpers import (
    calculate_atr_sliding,
    calculate_pips,
    round_price,
)
from arty_trading.utils.sessions import get_active_session, is_kill_zone, is_session_active

UTC = pytz.UTC


def _volatile_candles(n: int) -> list[Candle]:
    """Bougies identiques au jeu utilisé pour figer ``DecisionEngine._atr``."""
    o = Decimal("1.1000")
    c = Decimal("1.1000")
    out: list[Candle] = []
    for i in range(n):
        out.append(
            Candle(
                symbol="EURUSD",
                timeframe=TimeFrame.M5,
                time=datetime(2024, 1, 2, 8, i, tzinfo=timezone.utc),
                open=o,
                high=o + Decimal("0.0020") + Decimal(i) * Decimal("0.0005"),
                low=o - Decimal("0.0010"),
                close=c + Decimal(i) * Decimal("0.0004"),
                volume=100,
                spread=3,
            )
        )
        o = c + Decimal(i) * Decimal("0.0004")
        c = o
    return out


class TestHelpers:
    def test_round_price_eurusd(self):
        assert round_price(1.123456, "EURUSD") == round_price(1.12346, "EURUSD")

    def test_calculate_pips_eurusd(self):
        pips = calculate_pips("EURUSD", 1.1000, 1.1050)
        assert pips == pytest.approx(50.0)

    def test_calculate_pips_usdjpy(self):
        pips = calculate_pips("USDJPY", 150.00, 150.50)
        assert pips == pytest.approx(50.0)

    def test_calculate_atr_sliding_matches_decision_atr(self):
        """Fige la valeur du ATR glissant (identique à DecisionEngine._atr)."""
        assert calculate_atr_sliding(_volatile_candles(2), 1) == Decimal("0.0035")
        assert calculate_atr_sliding(_volatile_candles(6), 2) == Decimal("0.00525")
        assert calculate_atr_sliding(_volatile_candles(8), 14) == Decimal("0.0045")


class TestSessions:
    def test_london_session_active(self):
        # 10:00 UTC = Londres actif
        dt = UTC.localize(datetime(2026, 8, 2, 10, 0))
        assert is_session_active(TradingSession.LONDON, dt) is True

    def test_asia_session_inactive_at_london_time(self):
        dt = UTC.localize(datetime(2026, 8, 2, 10, 0))
        assert is_session_active(TradingSession.ASIA, dt) is False

    def test_kill_zone_london_open(self):
        dt = UTC.localize(datetime(2026, 8, 2, 8, 30))
        assert is_kill_zone(dt) is True

    def test_get_active_session(self):
        dt = UTC.localize(datetime(2026, 8, 2, 13, 0))
        session = get_active_session(dt)
        assert session in (TradingSession.LONDON, TradingSession.NEW_YORK, TradingSession.OVERLAP_LONDON_NY)
