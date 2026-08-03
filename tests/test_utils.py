"""Tests des utilitaires."""

from datetime import datetime

import pytest
import pytz

from arty_trading.core.enums import TradingSession
from arty_trading.utils.helpers import calculate_pips, round_price
from arty_trading.utils.sessions import get_active_session, is_kill_zone, is_session_active

UTC = pytz.UTC


class TestHelpers:
    def test_round_price_eurusd(self):
        assert round_price(1.123456, "EURUSD") == round_price(1.12346, "EURUSD")

    def test_calculate_pips_eurusd(self):
        pips = calculate_pips("EURUSD", 1.1000, 1.1050)
        assert pips == pytest.approx(50.0)

    def test_calculate_pips_usdjpy(self):
        pips = calculate_pips("USDJPY", 150.00, 150.50)
        assert pips == pytest.approx(50.0)


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
