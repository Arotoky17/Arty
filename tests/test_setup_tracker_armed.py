"""Tests du SetupTracker et de l'intégration ARMED/WATCHING/READY."""

from __future__ import annotations

from datetime import UTC, datetime

from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.modules.smc.setup_tracker import SetupState, SetupTracker


def _make_candle(idx: int, close: float, bullish: bool = True) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 2, 8, idx, tzinfo=UTC),
        open=close - 0.0001 if bullish else close + 0.0001,
        high=close + 0.0002,
        low=close - 0.0002,
        close=close,
        volume=100,
        spread=3,
    )


def _make_candles(prices: list[float]) -> list[Candle]:
    return [
        _make_candle(i, p, bullish=(i == 0 or p >= prices[i - 1]))
        for i, p in enumerate(prices)
    ]


class TestSetupTrackerEarlyDetection:
    """Test 1 : Setup détecté tôt → devient ARMED."""

    def test_setup_detected_becomes_armed_when_price_close(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800, 1.0805, 1.0810, 1.0815])
        smc_data = []
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        assert setup.state == SetupState.DETECTED

        transitions = tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=smc_data,
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0821,
        )
        assert setup.state == SetupState.ARMED
        assert len(transitions) == 1

    def test_setup_detected_becomes_watching_when_in_zone(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0815, 1.0816, 1.0817, 1.0818])
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        assert setup.state == SetupState.DETECTED

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0815,
        )
        assert setup.state == SetupState.WATCHING


class TestSetupTrackerWatchingToReady:
    """Test 3 : Retest valide → devient READY."""

    def test_armed_to_ready_on_confirmed_rejection(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0815, 1.0816, 1.0817, 1.0818])
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        # Force ARMED state
        setup.state = SetupState.ARMED

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0815,
        )
        assert setup.state == SetupState.READY


class TestSetupTrackerInvalidation:
    """Tests 4, 6, 7 : Invalidations."""

    def test_zone_too_far_invalidates_setup(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800, 1.0805, 1.0810, 1.0900])
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        setup.state = SetupState.ARMED

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0900,
        )
        assert setup.state == SetupState.INVALIDATED
        assert "zone_too_far" in setup.no_trade_reasons

    def test_trend_reversal_invalidates_setup(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800, 1.0805, 1.0810, 1.0815])
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        setup.state = SetupState.ARMED

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bearish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0815,
        )
        assert setup.state == SetupState.INVALIDATED
        assert "trend_reversal" in setup.no_trade_reasons

    def test_opposite_bos_invalidates_setup(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800, 1.0805, 1.0810, 1.0815])
        smc_data = [
            {
                "concept": "break_of_structure",
                "direction": "bearish",
                "price": 1.0790,
                "index": 3,
            }
        ]
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )
        setup.state = SetupState.WATCHING

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=smc_data,
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0815,
        )
        assert setup.state == SetupState.INVALIDATED
        assert "opposite_bos" in setup.no_trade_reasons


class TestSetupTrackerExpiration:
    """Test 5 : Setup trop ancien → EXPIRED."""

    def test_old_zone_expires(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800 + i * 0.0001 for i in range(30)])
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=2,
            atr=0.0010,
            htf_trend="bullish",
            ttl_bars=5,
        )
        assert setup.state == SetupState.DETECTED

        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=5,
            current_price=1.0830,
        )
        assert setup.state == SetupState.SETUP_EXPIRED


class TestSetupTrackerNoDuplicates:
    """Test 11 : Aucun doublon pour le même setup."""

    def test_duplicate_prevention(self):
        tracker = SetupTracker()
        tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_concept="fair_value_gap",
            zone_index=42,
            zone_high=1.0820,
            zone_low=1.0810,
        )
        assert tracker.is_duplicate("EURUSD", Direction.BUY, "fair_value_gap", 42) is True
        assert tracker.is_duplicate("EURUSD", Direction.SELL, "fair_value_gap", 42) is False


class TestSetupTrackerReadySignal:
    """Test 8, 9, 10, 13, 14, 15, 16 : Signal depuis setup prêt."""

    def test_get_ready_setups_returns_correct_states(self):
        tracker = SetupTracker()
        s1 = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
        )
        s1.state = SetupState.READY

        s2 = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.SELL,
            zone_high=1.0810,
            zone_low=1.0800,
            zone_concept="order_block",
            zone_index=1,
        )
        s2.state = SetupState.WATCHING

        ready = tracker.get_ready_setups("EURUSD")
        assert len(ready) == 1
        assert ready[0].setup_id == s1.setup_id

    def test_invalidated_setup_never_executed(self):
        tracker = SetupTracker()
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
        )
        setup.state = SetupState.INVALIDATED

        ready = tracker.get_ready_setups("EURUSD")
        assert len(ready) == 0

    def test_expired_setup_never_executed(self):
        tracker = SetupTracker()
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
        )
        setup.state = SetupState.SETUP_EXPIRED

        ready = tracker.get_ready_setups("EURUSD")
        assert len(ready) == 0


class TestSetupTrackerStatePersistence:
    """Test 16 : Persistance d'état entre cycles."""

    def test_state_persists_between_evaluations(self):
        tracker = SetupTracker()
        candles = _make_candles([1.0800, 1.0805, 1.0810, 1.0815, 1.0820])

        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_high=1.0820,
            zone_low=1.0810,
            zone_concept="fair_value_gap",
            zone_index=0,
            atr=0.0010,
            htf_trend="bullish",
        )

        # Cycle 1 : price approaches zone (just above)
        tracker.evaluate(
            symbol="EURUSD",
            candles=candles[:3],
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0821,
        )
        assert setup.state == SetupState.ARMED

        # Cycle 2 : price moves away
        tracker.evaluate(
            symbol="EURUSD",
            candles=candles,
            smc_data=[],
            htf_trend="bullish",
            atr=0.0010,
            max_distance_atr_mult=1.0,
            max_zone_age_bars=20,
            current_price=1.0850,
        )
        assert setup.state == SetupState.INVALIDATED
