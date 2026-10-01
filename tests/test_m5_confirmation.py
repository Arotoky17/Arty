"""Tests Phase 12 — confirmation M5 d'une zone Order Block.

Vérifie la règle « pas de signal OB sans retest + rejet en clôture M5 » et le
caractère optionnel de l'exigence de displacement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.smc.m5_confirmation import (
    CONFIRMATION_TYPES,
    CONFIRMED_TYPES,
    evaluate_m5_confirmation,
)

ZONE_TOP = 2010.0
ZONE_BOTTOM = 2009.0
_ZONE_HEIGHT = ZONE_TOP - ZONE_BOTTOM

_t0 = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)


def mk(idx: int, o: float, h: float, low: float, c: float) -> Candle:
    """Bougie M5 déterministe."""
    return Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=_t0 + timedelta(minutes=5 * idx),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=100,
        spread=20,
    )


def flat(idx: int, price: float = 2012.0) -> Candle:
    """Bougie plate loin de la zone (aucun retest)."""
    return mk(idx, price, price + 0.2, price - 0.2, price)


def retest_and_reject(close: float, body: float = 1.2) -> list[Candle]:
    """Historique : bougies loin de la zone puis une bougie de rejet.

    La dernière bougie ouvre ``body`` sous la clôture demandée : son corps vaut
    donc ``body`` (paramétrable pour tester l'exigence de displacement).
    """
    candles = [flat(i) for i in range(4)]
    candles.append(
        mk(len(candles), close - body, close + 0.05, min(2009.5, close) - 0.05, close)
    )
    return candles


class TestInputValidation:
    """Entrées invalides → confirmation refusée, jamais d'exception."""

    def test_no_data(self) -> None:
        result = evaluate_m5_confirmation([], "bullish", ZONE_TOP, ZONE_BOTTOM)
        assert result.confirmed is False
        assert result.confirmation_type == "no_data"

    def test_invalid_direction(self) -> None:
        result = evaluate_m5_confirmation([flat(0)], "neutral", ZONE_TOP, ZONE_BOTTOM)
        assert result.confirmed is False
        assert result.confirmation_type == "invalid_direction"

    def test_invalid_zone(self) -> None:
        result = evaluate_m5_confirmation([flat(0)], "bullish", ZONE_BOTTOM, ZONE_TOP)
        assert result.confirmed is False
        assert result.confirmation_type == "invalid_zone"

    def test_all_types_are_catalogued(self) -> None:
        assert "no_retest" in CONFIRMATION_TYPES
        assert CONFIRMED_TYPES == frozenset({"rejection", "strong_rejection"})


class TestRetest:
    """La zone doit d'abord être retestée."""

    def test_no_retest_when_price_never_returns(self) -> None:
        candles = [flat(i, 2015.0) for i in range(6)]
        result = evaluate_m5_confirmation(candles, "bullish", ZONE_TOP, ZONE_BOTTOM)
        assert result.confirmed is False
        assert result.confirmation_type == "no_retest"
        assert result.zone_touched is False

    def test_retest_index_is_reported(self) -> None:
        candles = retest_and_reject(close=2010.5)
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=False
        )
        assert result.zone_touched is True
        assert result.retest_index == len(candles) - 1

    def test_lookback_excludes_old_retest(self) -> None:
        candles = [mk(0, 2009.4, 2009.6, 2009.2, 2009.5)]
        candles += [flat(i, 2015.0) for i in range(1, 6)]
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, lookback_bars=3
        )
        assert result.confirmation_type == "no_retest"


class TestRejection:
    """Rejet en clôture : force proportionnelle à la zone."""

    def test_strong_rejection_closes_beyond_zone(self) -> None:
        candles = retest_and_reject(close=ZONE_TOP + 0.4)
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=True
        )
        assert result.confirmed is True
        assert result.confirmation_type == "strong_rejection"
        assert result.rejection_ratio >= 1.0

    def test_partial_rejection_accepted(self) -> None:
        candles = retest_and_reject(close=ZONE_BOTTOM + 0.6 * _ZONE_HEIGHT)
        result = evaluate_m5_confirmation(
            candles,
            "bullish",
            ZONE_TOP,
            ZONE_BOTTOM,
            atr=1.0,
            min_rejection_ratio=0.5,
            require_displacement=True,
        )
        assert result.confirmed is True
        assert result.confirmation_type == "rejection"

    def test_weak_rejection_rejected(self) -> None:
        candles = retest_and_reject(close=ZONE_BOTTOM + 0.2 * _ZONE_HEIGHT)
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=True
        )
        assert result.confirmed is False
        assert result.confirmation_type == "weak_rejection"

    def test_wrong_direction_candle_rejected(self) -> None:
        candles = [flat(i) for i in range(4)]
        candles.append(mk(4, 2011.0, 2011.1, 2009.4, 2010.6))  # bougie baissière
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=False
        )
        assert result.confirmed is False
        assert result.confirmation_type == "no_rejection"

    def test_bearish_setup_mirrored(self) -> None:
        candles = [flat(i) for i in range(4)]
        candles.append(mk(4, 2010.6, 2010.7, 2009.2, 2008.6))
        result = evaluate_m5_confirmation(
            candles, "bearish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=True
        )
        assert result.confirmed is True
        assert result.confirmation_type == "strong_rejection"


class TestDisplacement:
    """Le displacement du rejet est optionnel mais configurable."""

    def test_small_body_rejected_when_displacement_required(self) -> None:
        candles = retest_and_reject(close=ZONE_TOP + 0.4, body=0.2)
        result = evaluate_m5_confirmation(
            candles,
            "bullish",
            ZONE_TOP,
            ZONE_BOTTOM,
            atr=1.0,
            require_displacement=True,
            displacement_atr_mult=1.0,
        )
        assert result.confirmed is False
        assert result.confirmation_type == "no_displacement"

    def test_small_body_accepted_when_displacement_not_required(self) -> None:
        candles = retest_and_reject(close=ZONE_TOP + 0.4, body=0.2)
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0, require_displacement=False
        )
        assert result.confirmed is True

    def test_body_atr_mult_computed(self) -> None:
        candles = retest_and_reject(close=ZONE_TOP + 0.4, body=1.5)
        result = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=0.5, require_displacement=False
        )
        assert result.body_atr_mult == 3.0


class TestPayload:
    """Sérialisation vers les métadonnées de setup."""

    def test_to_dict_keys(self) -> None:
        candles = retest_and_reject(close=ZONE_TOP + 0.4)
        payload = evaluate_m5_confirmation(
            candles, "bullish", ZONE_TOP, ZONE_BOTTOM, atr=1.0
        ).to_dict()
        assert payload["ob_m5_confirmed"] is True
        assert payload["ob_m5_confirmation_type"] == "strong_rejection"
        assert payload["ob_m5_zone_touched"] is True

    def test_not_confirmed_payload(self) -> None:
        payload = evaluate_m5_confirmation(
            [flat(0, 2015.0)], "bullish", ZONE_TOP, ZONE_BOTTOM
        ).to_dict()
        assert payload["ob_m5_confirmed"] is False
        assert payload["ob_m5_confirmation_type"] == "no_retest"
