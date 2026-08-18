"""Tests PHASE 2.1 — reproduction et correction du bug retest_still_valid.

BUG : `retest_still_valid` mesurait la distance au MILIEU de la zone au lieu
de la frontière. Pour les zones larges (grand gap FVG), le prix peut être
à la frontière de la zone (confirmation valide) mais le milieu est loin →
faux rejet ZONE_TOO_FAR.

FIX : mesurer la distance à la frontière de la zone (0 si le prix est à
l'intérieur). Aucun seuil n'est modifié.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from arty_trading.application.execution_guards import final_gate_before_execution
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.decision.market_context import MarketContext
from arty_trading.utils.helpers import (
    retest_still_valid,
    retest_still_valid_detailed,
)

# =============================================================================
# Helpers
# =============================================================================


def make_m5_candle(
    idx: int,
    symbol: str = "EURUSD",
    o: Decimal = Decimal("1.0800"),
    h: Decimal = Decimal("1.0810"),
    low_val: Decimal = Decimal("1.0790"),
    c: Decimal = Decimal("1.0805"),
    vol: int = 100,
    spread: int = 5,
) -> Candle:
    """Crée une bougie M5 de test."""
    start = datetime(2024, 6, 1, 8, 0, tzinfo=UTC) + timedelta(minutes=idx * 5)
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.M5,
        time=start,
        open=o,
        high=h,
        low=low_val,
        close=c,
        volume=vol,
        spread=spread,
    )


class _Profile:
    min_risk_reward = 2.0
    max_spread_points = 30
    max_zone_age_bars = 20
    retest_atr_mult = 1.0


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.supported_symbols = ["EURUSD", "XAUUSD"]
    settings.get_instrument_profile.return_value = _Profile()
    return settings


def _signal(
    direction: Direction = Direction.BUY,
    entry: str = "1.0800",
    sl: str = "1.0780",
    tp: str = "1.0840",
) -> Signal:
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
        justification="Test signal",
        smc_concepts=["FVG", "BOS"],
    )


def _make_wide_fvg_scenario(
    direction: Direction = Direction.BUY,
    gap_bottom_str: str = "1.0780",
    gap_top_str: str = "1.0850",
) -> tuple[list[Candle], list[dict[str, object]], Decimal, Decimal]:
    """Build candles with a wide FVG (70 pips gap) and last candle confirming.

    Returns (candles, smc_data, gap_bottom, gap_top).
    """
    gap_bottom = Decimal(gap_bottom_str)
    gap_top = Decimal(gap_top_str)
    candles: list[Candle] = []

    for i in range(20):
        base = Decimal("1.0850") + Decimal(str(i * 0.00002))
        candles.append(make_m5_candle(
            i, o=base - Decimal("0.0002"),
            h=base + Decimal("0.0003"),
            low_val=base - Decimal("0.0003"),
            c=base,
        ))

    # FVG bullish: gap from 1.0780 to 1.0850 (70 pips)
    candles.append(make_m5_candle(
        20, o=Decimal("1.0852"), h=gap_bottom,
        low_val=Decimal("1.0848"), c=Decimal("1.0848"),
    ))
    candles.append(make_m5_candle(
        21, o=Decimal("1.0846"), h=Decimal("1.0847"),
        low_val=Decimal("1.0844"), c=Decimal("1.0846"),
    ))
    candles.append(make_m5_candle(
        22, o=Decimal("1.0844"), h=Decimal("1.0848"),
        low_val=gap_top, c=Decimal("1.0846"),
    ))
    candles.append(make_m5_candle(
        23, o=Decimal("1.0844"), h=Decimal("1.0845"),
        low_val=Decimal("1.0800"), c=Decimal("1.0810"),
    ))

    dir_str = "bullish" if direction == Direction.BUY else "bearish"

    if direction == Direction.BUY:
        # Last candle: bullish, closes just above gap_bottom (inside zone)
        candles.append(make_m5_candle(
            24, o=Decimal("1.0775"), h=Decimal("1.0790"),
            low_val=Decimal("1.0770"), c=Decimal("1.0785"),
        ))
        fvg_index = 21  # i=20, index=i+1=21
    else:
        # Bearish scenario: gap from 1.0850 (bottom) to 1.0920 (top)
        # Last candle: bearish, closes just below gap_top (inside zone)
        candles.append(make_m5_candle(
            24, o=Decimal("1.0855"), h=Decimal("1.0852"),
            low_val=Decimal("1.0845"), c=Decimal("1.0848"),
        ))
        fvg_index = 21

    smc_data = [
        {
            "concept": "fair_value_gap",
            "direction": dir_str,
            "price": float(gap_bottom if direction == Direction.BUY else gap_top),
            "index": fvg_index,
            "details": {
                "gap_top": float(gap_top),
                "gap_bottom": float(gap_bottom),
                "gap_size": float(gap_top - gap_bottom),
                "mitigation_count": 0,
                "filled": False,
            },
        }
    ]
    return candles, smc_data, gap_bottom, gap_top


# =============================================================================
# BUG REPRODUCTION + FIX VERIFICATION
# =============================================================================


class TestRetestBugFix:
    """Verify the distance-to-boundary fix resolves the false rejection."""

    def test_wide_fvg_buy_price_at_boundary_passes(self) -> None:
        """BUY: wide FVG (70 pips), price at zone boundary, confirmation OK.

        Before fix: distance to midpoint (34 pips) > ATR (~14 pips) → ZONE_TOO_FAR.
        After fix: distance to boundary = 0 (price inside zone) → PASS.
        """
        candles, smc_data, gap_bottom, gap_top = _make_wide_fvg_scenario(Direction.BUY)

        result = retest_still_valid_detailed(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )

        print(f"\nvalid={result.valid}, reason={result.reason}")
        print(f"distance_to_zone={result.distance_to_zone}")
        assert result.valid is True, (
            f"BUG: valid={result.valid}, reason={result.reason}, "
            f"dist={result.distance_to_zone}"
        )

    def test_wide_fvg_sell_price_at_boundary_passes(self) -> None:
        """SELL: wide FVG (70 pips), price at zone boundary, confirmation OK."""
        candles, smc_data, gap_bottom, gap_top = _make_wide_fvg_scenario(
            Direction.SELL, "1.0850", "1.0920",
        )

        result = retest_still_valid_detailed(
            candles, smc_data, "bearish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )

        assert result.valid is True


# =============================================================================
# ÉTAPE 4 — Tests BUY/SELL symétriques (8 scénarios)
# =============================================================================


class TestRetestSymmetry:
    """Symmetric BUY/SELL tests for the Final Gate."""

    # 1. BUY + retest valide → PASS
    @pytest.mark.asyncio
    async def test_buy_valid_retest_passes(self) -> None:
        candles, smc_data, _, _ = _make_wide_fvg_scenario(Direction.BUY)
        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bullish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD", _signal(Direction.BUY), context, _settings()
        )
        assert result is True

    # 2. SELL + retest valide → PASS
    @pytest.mark.asyncio
    async def test_sell_valid_retest_passes(self) -> None:
        candles, smc_data, _, _ = _make_wide_fvg_scenario(
            Direction.SELL, "1.0850", "1.0920",
        )
        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bearish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD", _signal(Direction.SELL, entry="1.0900", sl="1.0920", tp="1.0860"),
            context, _settings(),
        )
        assert result is True

    # 3. BUY + retest expiré (>20 bars) → FAIL
    @pytest.mark.asyncio
    async def test_buy_expired_retest_fails(self) -> None:
        """FVG at index 0 with 25 candles → age = 24 > 20."""
        candles = []
        for i in range(25):
            base = Decimal("1.0850") + Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base - Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        # Make last candle bullish and inside the zone
        candles[-1] = make_m5_candle(
            24, o=Decimal("1.0770"), h=Decimal("1.0790"),
            low_val=Decimal("1.0765"), c=Decimal("1.0785"),
        )

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bullish",
            "price": 1.0780,
            "index": 0,  # age = 24 > 20
            "details": {
                "gap_top": 1.0850,
                "gap_bottom": 1.0780,
                "gap_size": 0.0070,
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bullish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD", _signal(Direction.BUY), context, _settings()
        )
        assert result is False

    # 4. SELL + retest expiré (>20 bars) → FAIL
    @pytest.mark.asyncio
    async def test_sell_expired_retest_fails(self) -> None:
        candles = []
        for i in range(25):
            base = Decimal("1.0900") - Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base + Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        candles[-1] = make_m5_candle(
            24, o=Decimal("1.0910"), h=Decimal("1.0908"),
            low_val=Decimal("1.0880"), c=Decimal("1.0885"),
        )

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bearish",
            "price": 1.0920,
            "index": 0,  # age = 24 > 20
            "details": {
                "gap_top": 1.0920,
                "gap_bottom": 1.0850,
                "gap_size": 0.0070,
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bearish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD",
            _signal(Direction.SELL, entry="1.0885", sl="1.0905", tp="1.0845"),
            context, _settings(),
        )
        assert result is False

    # 5. BUY + zone trop éloignée (> ATR) → FAIL
    @pytest.mark.asyncio
    async def test_buy_zone_too_far_fails(self) -> None:
        """Price > 1*ATR beyond zone top for bullish → too far."""
        n = 25
        candles: list[Candle] = []

        for i in range(n - 1):
            base = Decimal("1.0850") + Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base - Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        # Small tight zone at 1.0850-1.0855, price way above
        gap_bottom = Decimal("1.0850")
        gap_top = Decimal("1.0855")
        candles.append(make_m5_candle(
            24, o=Decimal("1.0860"), h=Decimal("1.0870"),
            low_val=Decimal("1.0855"), c=Decimal("1.0868"),
        ))

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bullish",
            "price": float(gap_bottom),
            "index": 23,
            "details": {
                "gap_top": float(gap_top),
                "gap_bottom": float(gap_bottom),
                "gap_size": float(gap_top - gap_bottom),
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bullish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD", _signal(Direction.BUY), context, _settings()
        )
        assert result is False

    # 6. SELL + zone trop éloignée (> ATR) → FAIL
    @pytest.mark.asyncio
    async def test_sell_zone_too_far_fails(self) -> None:
        candles: list[Candle] = []
        for i in range(24):
            base = Decimal("1.0900") - Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base + Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        gap_bottom = Decimal("1.0850")
        gap_top = Decimal("1.0855")
        candles.append(make_m5_candle(
            24, o=Decimal("1.0840"), h=Decimal("1.0845"),
            low_val=Decimal("1.0820"), c=Decimal("1.0825"),
        ))

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bearish",
            "price": float(gap_top),
            "index": 23,
            "details": {
                "gap_top": float(gap_top),
                "gap_bottom": float(gap_bottom),
                "gap_size": float(gap_top - gap_bottom),
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bearish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD",
            _signal(Direction.SELL, entry="1.0825", sl="1.0845", tp="1.0805"),
            context, _settings(),
        )
        assert result is False

    # 7. absence de zone valide (zone existe mais non confirmée) → FAIL
    @pytest.mark.asyncio
    async def test_no_valid_zone_fails(self) -> None:
        """Bullish FVG exists, fresh, close, but last candle is NOT bullish."""
        n = 25
        candles: list[Candle] = []

        for i in range(n):
            base = Decimal("1.0850") + Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base - Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        gap_bottom = Decimal("1.0780")
        gap_top = Decimal("1.0850")
        # Last candle: BEARISH (close < open), at zone boundary → not confirmed
        candles[-1] = make_m5_candle(
            24, o=Decimal("1.0790"), h=Decimal("1.0795"),
            low_val=Decimal("1.0770"), c=Decimal("1.0780"),
        )

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bullish",
            "price": float(gap_bottom),
            "index": 21,  # age = 3 (<= 20 ✓)
            "details": {
                "gap_top": float(gap_top),
                "gap_bottom": float(gap_bottom),
                "gap_size": float(gap_top - gap_bottom),
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        context = MarketContext(
            symbol="EURUSD",
            timestamp=datetime(2024, 6, 1, 9, 0, tzinfo=UTC),
            master_trend="bullish",
            regime="unknown",
            spread=5,
            ltf_candles=candles,
            ltf_smc_data=smc_data,
        )
        result = await final_gate_before_execution(
            "EURUSD", _signal(Direction.BUY), context, _settings()
        )
        assert result is False


# =============================================================================
# Verification: original vs fixed behavior consistency
# =============================================================================


class TestOriginalFunctionConsistency:
    """Ensure retest_still_valid (original) and retest_still_valid_detailed match."""

    def test_functions_consistent_valid(self) -> None:
        candles, smc_data, _, _ = _make_wide_fvg_scenario(Direction.BUY)
        original = retest_still_valid(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        detailed = retest_still_valid_detailed(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        assert original == detailed.valid

    def test_functions_consistent_too_far(self) -> None:
        """Zone too far beyond boundary should fail in both functions."""
        candles: list[Candle] = []
        for i in range(25):
            base = Decimal("1.0850") + Decimal(str(i * 0.00002))
            candles.append(make_m5_candle(
                i, o=base - Decimal("0.0002"),
                h=base + Decimal("0.0003"),
                low_val=base - Decimal("0.0003"),
                c=base,
            ))

        candles[-1] = make_m5_candle(
            24, o=Decimal("1.0860"), h=Decimal("1.0870"),
            low_val=Decimal("1.0855"), c=Decimal("1.0868"),
        )

        smc_data = [{
            "concept": "fair_value_gap",
            "direction": "bullish",
            "price": 1.0850,
            "index": 23,
            "details": {
                "gap_top": 1.0855,
                "gap_bottom": 1.0850,
                "gap_size": 0.0005,
                "mitigation_count": 0,
                "filled": False,
            },
        }]

        original = retest_still_valid(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        detailed = retest_still_valid_detailed(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        assert original == detailed.valid
        assert detailed.valid is False


# =============================================================================
# Phase 2.1 — SetupTracker integration + diagnostic instrumentation tests
# =============================================================================


class TestSetupTrackerIntegration:
    """Verify SetupTracker.create_setup() is callable with SMC-like data."""

    def test_create_setup_from_fvg_data(self) -> None:
        from arty_trading.modules.smc.setup_tracker import SetupTracker

        tracker = SetupTracker()
        setup = tracker.create_setup(
            symbol="EURUSD",
            direction=Direction.BUY,
            zone_concept="fair_value_gap",
            zone_index=42,
            zone_price=1.0810,
            zone_high=1.0820,
            zone_low=1.0800,
        )
        assert setup is not None
        assert setup.symbol == "EURUSD"
        assert setup.direction == Direction.BUY
        assert setup.zone_concept == "fair_value_gap"
        assert setup.zone_index == 42

    def test_create_setup_duplicate_prevention(self) -> None:
        from arty_trading.modules.smc.setup_tracker import SetupTracker

        tracker = SetupTracker()
        s1 = tracker.create_setup(
            symbol="EURUSD", direction=Direction.BUY,
            zone_concept="fair_value_gap", zone_index=42,
            zone_price=1.0810, zone_high=1.0820, zone_low=1.0800,
        )
        assert s1 is not None

        # Duplicate should be prevented by is_duplicate check
        assert tracker.is_duplicate("EURUSD", Direction.BUY, "fair_value_gap", 42) is True
        assert tracker.is_duplicate("EURUSD", Direction.SELL, "fair_value_gap", 42) is False

    def test_get_active_setups_returns_created(self) -> None:
        from arty_trading.modules.smc.setup_tracker import SetupTracker

        tracker = SetupTracker()
        tracker.create_setup(
            symbol="EURUSD", direction=Direction.BUY,
            zone_concept="order_block", zone_index=10,
            zone_price=1.0800, zone_high=1.0810, zone_low=1.0790,
        )
        active = tracker.get_active_setups("EURUSD")
        assert len(active) >= 1
        assert any(s.zone_concept == "order_block" for s in active)


class TestRetestDiagnosticsOnPass:
    """Phase 2.1 — instrumentation fix: zone details populated even on PASS."""

    def test_pass_populates_zone_type_and_age(self) -> None:
        candles, smc_data, _, _ = _make_wide_fvg_scenario(Direction.BUY)
        result = retest_still_valid_detailed(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        assert result.valid is True
        assert result.reason == "PASS"
        assert result.zone_type is not None, "zone_type should be populated on PASS"
        assert result.zone_id is not None, "zone_id should be populated on PASS"
        assert result.zone_age_bars is not None, "zone_age_bars should be populated on PASS"
        assert result.distance_to_zone is not None, "distance_to_zone should be populated on PASS"

    def test_pass_populated_for_bearish(self) -> None:
        """Phase 2.1 — zone diagnostics on PASS for bearish direction."""
        candles, smc_data, _, _ = _make_wide_fvg_scenario(Direction.SELL)
        result = retest_still_valid_detailed(
            candles, smc_data, "bearish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        assert result.valid is True
        assert result.reason == "PASS"
        assert result.zone_type is not None
        assert result.zone_age_bars is not None
        assert result.distance_to_zone is not None


class TestNoZonesButDataConsistency:
    """Verify both functions agree when there are zones but none match direction."""

    def test_both_return_true_when_no_zones_in_direction(self) -> None:
        """SMC data has bearish zones only, but we check bullish → should not block."""
        candles: list[Candle] = []
        for i in range(20):
            candles.append(make_m5_candle(
                i, o=Decimal("1.0800"), h=Decimal("1.0810"),
                low_val=Decimal("1.0790"), c=Decimal("1.0805"),
            ))

        # Only bearish zones in SMC data
        smc_data = [{
            "concept": "fair_value_gap", "direction": "bearish",
            "price": 1.0850, "index": 15,
            "details": {"gap_top": 1.0860, "gap_bottom": 1.0840, "gap_size": 0.0020},
        }]

        original = retest_still_valid(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )
        detailed = retest_still_valid_detailed(
            candles, smc_data, "bullish",
            max_age_bars=20, max_distance_atr_mult=1.0,
        )

        # Both should return True (no zones in direction → don't block)
        assert original is True, "Non-detailed should return True when no zones in direction"
        assert detailed.valid is True, "Detailed should return True when no zones in direction"
        assert original == detailed.valid
