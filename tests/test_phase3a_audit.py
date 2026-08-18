"""Tests de diagnostic Phase 3A — retTest et Premium/Discount.

Ces tests valident que l'instrumentation expose la cause exacte du rejet
sans modifier le comportement de trading.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.smc.premium_discount import (
    premium_discount_diagnostic,
)
from arty_trading.utils.helpers import (
    retest_still_valid,
    retest_still_valid_detailed,
)

# =============================================================================
# Helpers
# =============================================================================


def _candle(
    idx: int,
    symbol: str = "EURUSD",
    o: float = 1.0800,
    h: float = 1.0810,
    low: float = 1.0790,
    c: float = 1.0805,
    vol: int = 100,
    spread: int = 3,
) -> Candle:
    """Crée une bougie M5 de test."""
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 1, 8, idx, tzinfo=UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=vol,
        spread=spread,
    )


def _make_retest_candles(
    n: int = 25,
    fvg_index: int = 22,
) -> tuple[list[Candle], list[dict]]:
    """Génère des bougies M5 avec un FVG bullish à fvg_index.

    Le dernier candle clôture juste au-dessus du fond de la zone, bullish,
    de sorte que le retest est valide.
    """
    zone_bottom = 1.0810
    zone_top = 1.0820

    # Les bougies 0 à fvg_index-1 : tendance baissière puis reversal (création du FVG)
    candles: list[Candle] = []
    price = 1.0850
    for i in range(fvg_index):
        # Baisse progressive
        o = price
        c = price - 0.0005
        h = o + 0.0002
        low = c - 0.0001
        candles.append(_candle(i, o=o, h=h, low=low, c=c))
        price = float(c)

    # Candle à fvg_index : forte hausse qui crée le FVG bullish (gap 1.0810-1.0820)
    candles.append(
        _candle(fvg_index, o=1.0805, h=1.0820, low=1.0803, c=1.0812)
    )

    # Candle fvg_index+1 : baisse qui crée un trou dans la zone (gap bottom hauteur)
    # On veut que le gap_bottom = 1.0810
    candles.append(
        _candle(fvg_index + 1, o=1.0802, h=1.0810, low=1.0800, c=1.0804)
    )

    # Candles intermédiaires : consolidation proche de la zone
    price = 1.0804
    for i in range(fvg_index + 2, n - 1):
        o = price
        c = price + 0.0001
        h = c + 0.0002
        low = o - 0.0002
        candles.append(_candle(i, o=o, h=h, low=low, c=c))
        price = float(c)

    # Dernière bougie : bullish, clôture juste au-dessus du fond de zone
    candles.append(
        _candle(n - 1, o=1.0810, h=1.0813, low=1.0808, c=zone_bottom + 0.0002)
    )

    smc_data = [
        {
            "concept": "fair_value_gap",
            "direction": "bullish",
            "price": 1.0815,
            "index": fvg_index,
            "details": {
                "gap_top": zone_top,
                "gap_bottom": zone_bottom,
                "gap_size": 0.0010,
                "mitigation_count": 0,
                "filled": False,
            },
        }
    ]

    return candles, smc_data


def _make_pd_candles_bullish(
    n: int = 25,
) -> tuple[list[Candle], list[dict]]:
    """Bougies avec swing high/low clairs, prix en discount (BUY OK)."""
    candles: list[Candle] = []
    high_val = 1.0860
    low_val = 1.0780
    midpoint = (high_val + low_val) / 2  # 1.0820

    # 20 candles formant la range haute-basse
    for i in range(n - 1):
        o = low_val + (i / n) * (high_val - low_val) * 0.3
        c = o + 0.0005
        h = high_val
        low = low_val
        candles.append(_candle(i, o=o, h=h, low=low, c=c))
        high_val = float(c) + 0.0002
        low_val = float(c) - 0.0002

    # Dernière bougie: en dessous du midpoint (discount)
    last_close = midpoint - 0.01
    candles.append(
        _candle(
            n - 1,
            o=last_close + 0.001,
            h=last_close + 0.002,
            low=last_close - 0.001,
            c=last_close,
        )
    )

    smc_data = []

    return candles, smc_data


# =============================================================================
# Tests retest_still_valid_detailed — raisons exactes
# =============================================================================


class TestRetestDetailed:
    def test_valid_retest_returns_pass(self):
        """Retest valide → reason PASS, valid True."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is True
        assert result.reason == "PASS"

    def test_no_zones_in_direction_returns_true(self):
        """Aucune zone dans la direction → valid True, reason informative."""
        candles = _make_retest_candles(n=25, fvg_index=20)[0]

        result = retest_still_valid_detailed(
            candles, [], "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        # Comportement identique à l'originale : ne bloque pas si pas de zone
        assert result.valid is True
        assert result.reason == "NO_ZONES_IN_DIRECTION"

    def test_zone_too_old(self):
        """Zone trop ancienne → ZONE_TOO_OLD."""
        candles, smc = _make_retest_candles(n=30, fvg_index=2)
        # max_age_bars = 5, age = 30-1-2 = 27 > 5

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=5, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ZONE_TOO_OLD"
        assert result.zones_in_direction > 0
        assert result.zone_age_bars is not None
        assert result.zone_age_bars > 5

    def test_zone_too_far(self):
        """Zone trop éloignée du prix → ZONE_TOO_FAR."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)
        # Last candle close = zone_bottom + 0.0002 = 1.0812
        # Zone mid = 1.0815, distance ~0.0003
        # Set last candle far away
        last_idx = len(candles) - 1
        far_price = 1.0500
        candles[last_idx] = _candle(
            last_idx,
            o=far_price + 0.002,
            h=far_price + 0.003,
            low=far_price - 0.001,
            c=far_price,
        )

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ZONE_TOO_FAR"
        assert result.distance_to_zone is not None
        assert result.distance_to_zone > result.max_distance

    def test_no_retest_confirmation(self):
        """Zone fraîche et proche mais dernière bougie ne confirme pas le rejet."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)
        # Replace last candle: bearish (close < open), close >= bottom but not bullish
        # zone_mid = 1.0815, zone_bottom = 1.0810
        # close=1.0814 is close to mid AND >= bottom, but close < open → not bullish
        last_idx = len(candles) - 1
        candles[last_idx] = _candle(
            last_idx,
            o=1.0816,
            h=1.0818,
            low=1.0812,
            c=1.0814,  # close >= bottom but bearish → NO_CONFIRMATION
        )

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "NO_RETEST_CONFIRMATION"
        assert result.zones_in_direction > 0

    def test_atr_zero_returns_false(self):
        """ATR = 0 (données plates) → ATR_ZERO."""
        candles = []
        for i in range(20):
            candles.append(_candle(i, o=1.0800, h=1.0800, low=1.0800, c=1.0800))
        smc = [
            {
                "concept": "fair_value_gap",
                "direction": "bullish",
                "price": 1.0810,
                "index": 18,
                "details": {
                    "gap_top": 1.0820,
                    "gap_bottom": 1.0810,
                    "gap_size": 0.0010,
                },
            }
        ]

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ATR_ZERO"

    def test_detailed_matches_original_bool(self):
        """La version détaillée produit le même booléen que la version originale."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)

        original_result = retest_still_valid(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )
        detailed_result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert original_result == detailed_result.valid

    def test_detailed_matches_original_too_old(self):
        """Même résultat booléen pour ZONE_TOO_OLD."""
        candles, smc = _make_retest_candles(n=30, fvg_index=2)

        original_result = retest_still_valid(
            candles, smc, "bullish", max_age_bars=5, max_distance_atr_mult=1.0
        )
        detailed_result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=5, max_distance_atr_mult=1.0
        )

        assert original_result == detailed_result.valid
        assert detailed_result.valid is False

    def test_detailed_matches_original_too_far(self):
        """Même résultat booléen pour ZONE_TOO_FAR."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)
        last_idx = len(candles) - 1
        far_price = 1.0500
        candles[last_idx] = _candle(
            last_idx,
            o=far_price + 0.002,
            h=far_price + 0.003,
            low=far_price - 0.001,
            c=far_price,
        )

        original_result = retest_still_valid(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )
        detailed_result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert original_result == detailed_result.valid
        assert detailed_result.valid is False

    def test_detailed_matches_original_no_confirmation(self):
        """Même résultat booléen pour NO_RETEST_CONFIRMATION."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)
        last_idx = len(candles) - 1
        candles[last_idx] = _candle(
            last_idx,
            o=1.0816,
            h=1.0818,
            low=1.0812,
            c=1.0814,  # bearish + close >= bottom → not confirmed
        )

        original_result = retest_still_valid(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )
        detailed_result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert original_result == detailed_result.valid
        assert detailed_result.valid is False

    def test_zone_too_old_detail_present(self):
        """ZONE_TOO_OLD → le détail d'âge est exposé."""
        candles, smc = _make_retest_candles(n=25, fvg_index=3)
        # age = 25-1-3 = 21 > 20

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ZONE_TOO_OLD"
        assert result.zone_age_bars is not None
        assert result.zone_type is not None
        assert result.atr > 0
        assert result.max_zone_age_bars == 20

    def test_zone_too_far_detail_present(self):
        """ZONE_TOO_FAR → le détail de distance est exposé."""
        candles, smc = _make_retest_candles(n=25, fvg_index=20)
        last_idx = len(candles) - 1
        far_price = 1.0500
        candles[last_idx] = _candle(
            last_idx,
            o=far_price + 0.002,
            h=far_price + 0.003,
            low=far_price - 0.001,
            c=far_price,
        )

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ZONE_TOO_FAR"
        assert result.distance_to_zone is not None
        assert result.max_distance > 0
        assert result.zone_type == "fair_value_gap"

    def test_bearish_retest_valid(self):
        """Retest bearish valide."""
        n = 25
        zone_top = 1.0845
        zone_bottom = 1.0830
        zone_mid = (zone_top + zone_bottom) / 2  # 1.08375

        candles: list[Candle] = []
        price = 1.0780
        for i in range(n):
            o = price
            c = price + 0.0008
            h = c + 0.0015
            low = o - 0.0003
            candles.append(_candle(i, o=o, h=h, low=low, c=c))
            price = float(c)

        # Dernière bougie: bearish, clôture juste en dessous de zone_top
        candles[-1] = _candle(
            n - 1,
            o=zone_top + 0.0002,
            h=zone_top + 0.001,
            low=zone_mid - 0.001,
            c=zone_top - 0.0002,
        )
        # close = 1.0843 <= zone_top (1.0845) ✓
        # distance = |1.0843 - 1.08375| = 0.00055, max_distance ~0.0025 → close enough ✓
        # is_bullish: close(1.0843) < open(1.0847) → bearish ✓

        smc = [
            {
                "concept": "order_block",
                "direction": "bearish",
                "price": 1.0840,
                "index": 22,
                "details": {
                    "ob_top": zone_top,
                    "ob_bottom": zone_bottom,
                    "mitigation_count": 0,
                },
            }
        ]

        result = retest_still_valid_detailed(
            candles, smc, "bearish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is True
        assert result.reason == "PASS"

    def test_multiple_zones_one_fails_all_reason(self):
        """Multiples zones : si toutes trop anciennes, reason=ZONE_TOO_OLD."""
        candles, _ = _make_retest_candles(n=30, fvg_index=2)
        smc = [
            {
                "concept": "fair_value_gap",
                "direction": "bullish",
                "price": 1.0810,
                "index": 1,  # age = 28 > 20
                "details": {"gap_top": 1.0820, "gap_bottom": 1.0810, "gap_size": 0.0010},
            },
            {
                "concept": "order_block",
                "direction": "bullish",
                "price": 1.0800,
                "index": 3,  # age = 26 > 20
                "details": {"ob_top": 1.0810, "ob_bottom": 1.0795, "mitigation_count": 0},
            },
        ]

        result = retest_still_valid_detailed(
            candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
        )

        assert result.valid is False
        assert result.reason == "ZONE_TOO_OLD"
        assert result.zones_in_direction == 2


# =============================================================================
# Tests premium_discount_diagnostic
# =============================================================================


class TestPremiumDiscountDiagnostic:
    def _pd_smc_data(self, location: str = "discount") -> list[dict]:
        """Prépare un smc_data avec une détection premium_discount."""
        swing_high = 1.0860
        swing_low = 1.0780
        range_size = swing_high - swing_low
        equilibrium = swing_low + range_size / 2  # 1.0820

        if location == "premium":
            current_price = 1.0840  # au-dessus du midpoint
        else:
            current_price = 1.0800  # en dessous du midpoint

        return [
            {
                "concept": "premium_discount",
                "direction": "neutral",
                "price": equilibrium,
                "index": 20,
                "details": {
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                    "range_size": range_size,
                    "equilibrium": equilibrium,
                    "premium_start": equilibrium,
                    "premium_end": swing_high,
                    "discount_start": swing_low,
                    "discount_end": equilibrium,
                    "current_price": current_price,
                    "current_zone": location,
                },
            }
        ]

    def _pd_candles(self, close_price: float = 1.0800) -> list[Candle]:
        """Candles simples pour le diagnostic PD (les valeurs viennent de smc_data)."""
        candles: list[Candle] = []
        for i in range(25):
            o = 1.0800 + i * 0.0001
            c = o + 0.0002
            h = c + 0.0001
            low = o - 0.0001
            candles.append(_candle(i, o=o, h=h, c=c, low=low))
        # Dernière bougie avec le close_price souhaité
        candles[-1] = _candle(
            24, o=close_price, h=close_price + 0.0002, low=close_price - 0.0002, c=close_price
        )
        return candles

    def test_premium_discount_values_exposed(self):
        """Toutes les valeurs du calcul sont présentes dans le diagnostic."""
        candles = self._pd_candles(close_price=1.0800)
        smc = self._pd_smc_data(location="discount")

        result = premium_discount_diagnostic(
            candles, smc, "bearish", symbol="XAUUSD", timeframe="M5"
        )

        # Vérification stricte des champs exposés
        assert result.symbol == "XAUUSD"
        assert result.direction == "bearish"
        assert result.timeframe == "M5"
        assert result.swing_high == 1.0860
        assert result.swing_low == 1.0780
        assert result.range_size == pytest.approx(1.0860 - 1.0780)
        assert result.range_high == result.swing_high
        assert result.range_low == result.swing_low
        assert result.midpoint_50 == pytest.approx(1.0820)
        assert result.current_price == pytest.approx(1.0800)
        assert result.location == "discount"
        assert result.expected_location == "premium"
        assert result.in_correct_zone is False
        assert result.distance_to_midpoint == pytest.approx(abs(1.0800 - 1.0820))
        assert result.pct_from_midpoint >= 0

    def test_premium_discount_invalid_for_buy_in_premium(self):
        """Prix en premium → invalide pour BUY."""
        candles = self._pd_candles(close_price=1.0840)
        smc = self._pd_smc_data(location="premium")

        result = premium_discount_diagnostic(
            candles, smc, "bullish", symbol="EURUSD", timeframe="M5"
        )

        assert result.swing_high > 0
        assert result.swing_low > 0
        assert result.range_size > 0
        assert result.midpoint_50 > 0
        assert result.current_price > 0
        assert result.location == "premium"
        assert result.expected_location == "discount"
        assert result.in_correct_zone is False
        assert result.valid is False

    def test_premium_discount_insufficient_data(self):
        """Données insuffisantes → diagnostic invalide avec reason."""
        candles = [_candle(0)]

        result = premium_discount_diagnostic(
            candles, [], "bullish", symbol="EURUSD"
        )

        assert result.valid is False
        assert result.reason == "INSUFFICIENT_DATA"

    def test_premium_discount_to_dict(self):
        """La sérialisation to_dict fonctionne."""
        candles = self._pd_candles()
        smc = self._pd_smc_data(location="discount")

        result = premium_discount_diagnostic(
            candles, smc, "bullish", symbol="EURUSD", timeframe="M5"
        )

        d = result.to_dict()
        assert "valid" in d
        assert "reason" in d
        assert "symbol" in d
        assert "direction" in d
        assert "swing_high" in d
        assert "swing_low" in d
        assert "range_high" in d
        assert "range_low" in d
        assert "midpoint_50" in d
        assert "current_price" in d
        assert "location" in d
        assert "expected_location" in d
        assert "in_correct_zone" in d

    def test_buy_in_discount_valid(self):
        """BUY avec prix en discount → valide."""
        candles = self._pd_candles(close_price=1.0800)
        smc = self._pd_smc_data(location="discount")

        result = premium_discount_diagnostic(
            candles, smc, "bullish", symbol="EURUSD", timeframe="M5"
        )

        assert result.location == "discount"
        assert result.expected_location == "discount"
        assert result.in_correct_zone is True
        assert result.valid is True

    def test_sell_in_premium_valid(self):
        """SELL avec prix en premium → valide."""
        candles = self._pd_candles(close_price=1.0840)
        smc = self._pd_smc_data(location="premium")

        result = premium_discount_diagnostic(
            candles, smc, "bearish", symbol="EURUSD", timeframe="M5"
        )

        assert result.location == "premium"
        assert result.expected_location == "premium"
        assert result.in_correct_zone is True
        assert result.valid is True


# =============================================================================
# Tests d'intégration : EURUSD vs XAUUSD comparaison
# =============================================================================


class TestEurusdVsXauusdComparison:
    def test_both_symbols_diagnostic_fields_present(self):
        """Les diagnostics contiennent les champs nécessaires pour comparaison."""
        for symbol in ("EURUSD", "XAUUSD"):
            candles, smc = _make_retest_candles(n=25, fvg_index=20)

            retest = retest_still_valid_detailed(
                candles, smc, "bullish", max_age_bars=20, max_distance_atr_mult=1.0
            )

            # Retest diagnostic fields
            assert hasattr(retest, "symbol")
            assert hasattr(retest, "direction")
            assert hasattr(retest, "last_candle_time")
            assert hasattr(retest, "atr")
            assert hasattr(retest, "max_distance")
            assert hasattr(retest, "max_zone_age_bars")
            assert hasattr(retest, "retest_atr_mult")
            assert hasattr(retest, "zone_type")
            assert hasattr(retest, "zone_id")
            assert hasattr(retest, "zone_created_index")
            assert hasattr(retest, "zone_age_bars")
            assert hasattr(retest, "distance_to_zone")
            assert hasattr(retest, "zone_consumed")
            assert hasattr(retest, "zone_direction")
            assert hasattr(retest, "retest_detected")
            assert hasattr(retest, "retest_confirmed")

            # Premium/Discount diagnostic fields
            pd = premium_discount_diagnostic(
                candles, smc, "bullish", symbol=symbol, timeframe="M5"
            )
            assert hasattr(pd, "symbol")
            assert hasattr(pd, "direction")
            assert hasattr(pd, "timeframe")
            assert hasattr(pd, "swing_high")
            assert hasattr(pd, "swing_low")
            assert hasattr(pd, "range_high")
            assert hasattr(pd, "range_low")
            assert hasattr(pd, "midpoint_50")
            assert hasattr(pd, "current_price")
            assert hasattr(pd, "location")
            assert hasattr(pd, "expected_location")
            assert hasattr(pd, "in_correct_zone")
