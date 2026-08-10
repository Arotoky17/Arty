"""Tests du SignalValidator et de l'intégration avec le SignalGenerator."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingSession
from arty_trading.modules.signals import SignalGenerator, SignalValidator, ValidationResult
from arty_trading.modules.signals.validator import (
    ALL_CONDITIONS,
    COND_BOS,
    COND_CHOCH,
    COND_FVG,
    COND_HTF_TREND,
    COND_LIQUIDITY_SWEEP,
    COND_NEWS,
    COND_ORDER_BLOCK,
    COND_PREMIUM_DISCOUNT,
    COND_RR,
    COND_SESSION,
    COND_SPREAD,
)


# =============================================================================
# Helpers
# =============================================================================


def make_candle(idx, o, h, l, c, volume=100, spread=3):
    """Crée une bougie de test."""
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, 8, idx, tzinfo=timezone.utc),  # 08:xx UTC = London session
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
        spread=spread,
    )


def make_candles(n=20, spread=3):
    """Crée une liste de bougies en tendance haussière."""
    candles = []
    base = 1.0800
    i = 0
    while i < n:
        for _ in range(3):
            if i >= n:
                break
            o = base + i * 0.0010
            c = base + (i + 1) * 0.0010
            h = c + 0.0008
            l = o - 0.0002
            candles.append(make_candle(i, o, h, l, c, spread=spread))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0015
        h = float(o) + 0.0003
        l = c - 0.0003
        candles.append(make_candle(i, o, h, l, c, spread=spread))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0010
        h = float(o) + 0.0002
        l = c - 0.0005
        candles.append(make_candle(i, o, h, l, c, spread=spread))
        i += 1
    return candles


def make_full_bullish_smc_data():
    """Données SMC avec toutes les conditions bullish valides."""
    return [
        {"concept": "break_of_structure", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 6, "details": {"gap_size": 0.0005}},
        {"concept": "order_block", "direction": "bullish", "price": 1.0795, "index": 4, "details": {"mitigated": False}},
        {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0790, "index": 7, "details": {"type": "buy_side_liquidity_grab"}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0810, "index": 8, "details": {"current_zone": "discount"}},
    ]


def make_full_bearish_smc_data():
    """Données SMC avec toutes les conditions bearish valides."""
    return [
        {"concept": "break_of_structure", "direction": "bearish", "price": 1.0790, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bearish", "price": 1.0800, "index": 6, "details": {"gap_size": 0.0005}},
        {"concept": "order_block", "direction": "bearish", "price": 1.0815, "index": 4, "details": {"mitigated": False}},
        {"concept": "liquidity_sweep", "direction": "bearish", "price": 1.0820, "index": 7, "details": {"type": "sell_side_liquidity_grab"}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0800, "index": 8, "details": {"current_zone": "premium"}},
    ]


def make_buy_signal(confidence=0.9, rr=2.0):
    """Crée un signal BUY de test."""
    entry = Decimal("1.0810")
    risk = Decimal("0.0020")
    reward = risk * Decimal(str(rr))
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=entry,
        stop_loss=entry - risk,
        take_profit=entry + reward,
        confidence=confidence,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.H1,
        justification="Test signal",
    )


def make_sell_signal(confidence=0.9, rr=2.0):
    """Crée un signal SELL de test."""
    entry = Decimal("1.0810")
    risk = Decimal("0.0020")
    reward = risk * Decimal(str(rr))
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.SELL,
        direction=Direction.SELL,
        entry_price=entry,
        stop_loss=entry + risk,
        take_profit=entry - reward,
        confidence=confidence,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.H1,
        justification="Test signal",
    )


# =============================================================================
# Tests du ValidationResult
# =============================================================================


class TestValidationResult:
    def test_creation(self):
        result = ValidationResult(
            is_valid=True,
            score=1.0,
            failed_conditions=[],
            explanation="OK",
        )
        assert result.is_valid is True
        assert result.score == 1.0
        assert result.failed_conditions == []
        assert result.explanation == "OK"

    def test_to_dict(self):
        result = ValidationResult(
            is_valid=False,
            score=0.5,
            failed_conditions=["bos_valid"],
            explanation="Failed",
            checked_conditions={"bos_valid": False},
            details={"bos_valid": "No BOS found"},
        )
        d = result.to_dict()
        assert d["is_valid"] is False
        assert d["score"] == 0.5
        assert d["failed_conditions"] == ["bos_valid"]
        assert d["explanation"] == "Failed"
        assert d["checked_conditions"] == {"bos_valid": False}
        assert d["details"]["bos_valid"] == "No BOS found"


# =============================================================================
# Tests du SignalValidator
# =============================================================================


class TestSignalValidatorInit:
    def test_default_initialization(self):
        v = SignalValidator()
        assert v.min_risk_reward == 1.5
        assert v.max_spread == 20
        assert v.require_htf_alignment is True
        assert v.require_news_filter is True
        assert TradingSession.LONDON in v.authorized_sessions
        assert TradingSession.NEW_YORK in v.authorized_sessions

    def test_custom_initialization(self):
        v = SignalValidator(
            min_risk_reward=2.0,
            max_spread=10,
            require_htf_alignment=False,
            require_news_filter=False,
        )
        assert v.min_risk_reward == 2.0
        assert v.max_spread == 10
        assert v.require_htf_alignment is False
        assert v.require_news_filter is False

    def test_custom_sessions(self):
        sessions = [TradingSession.LONDON]
        v = SignalValidator(authorized_sessions=sessions)
        assert v.authorized_sessions == [TradingSession.LONDON]

    def test_setters(self):
        v = SignalValidator()
        v.min_risk_reward = 3.0
        v.max_spread = 15
        assert v.min_risk_reward == 3.0
        assert v.max_spread == 15


class TestAllConditions:
    def test_all_conditions_count(self):
        assert len(ALL_CONDITIONS) == 11

    def test_all_conditions_names(self):
        expected = {
            COND_HTF_TREND,
            COND_BOS,
            COND_CHOCH,
            COND_ORDER_BLOCK,
            COND_FVG,
            COND_LIQUIDITY_SWEEP,
            COND_PREMIUM_DISCOUNT,
            COND_SESSION,
            COND_SPREAD,
            COND_NEWS,
            COND_RR,
        }
        assert set(ALL_CONDITIONS) == expected


# =============================================================================
# Tests de validation complète
# =============================================================================


class TestFullValidation:
    def test_valid_bullish_signal(self):
        """Toutes les conditions sont validées pour un signal BUY."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert isinstance(result, ValidationResult)
        assert result.is_valid is True
        assert result.score == 1.0
        assert result.failed_conditions == []
        assert "VALIDÉ" in result.explanation

    def test_valid_bearish_signal(self):
        """Toutes les conditions sont validées pour un signal SELL."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_sell_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bearish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is True
        assert result.score == 1.0
        assert result.failed_conditions == []

    def test_invalid_missing_bos(self):
        """Signal rejeté si pas de BOS."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        # Retirer le BOS
        smc_data = [d for d in smc_data if d["concept"] != "break_of_structure"]

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_BOS in result.failed_conditions
        assert result.score < 1.0

    def test_invalid_missing_fvg(self):
        """Signal rejeté si pas de FVG."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        smc_data = [d for d in smc_data if d["concept"] != "fair_value_gap"]

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_FVG in result.failed_conditions

    def test_invalid_missing_order_block(self):
        """Signal rejeté si pas d'Order Block."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        smc_data = [d for d in smc_data if d["concept"] != "order_block"]

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_ORDER_BLOCK in result.failed_conditions

    def test_invalid_missing_liquidity_sweep(self):
        """Signal rejeté si pas de Liquidity Sweep."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        smc_data = [d for d in smc_data if d["concept"] != "liquidity_sweep"]

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_LIQUIDITY_SWEEP in result.failed_conditions

    def test_invalid_wrong_premium_discount(self):
        """Signal BUY rejeté si prix en zone premium au lieu de discount."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        # Changer la zone en premium
        for d in smc_data:
            if d["concept"] == "premium_discount":
                d["details"]["current_zone"] = "premium"

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_PREMIUM_DISCOUNT in result.failed_conditions

    def test_invalid_high_spread(self):
        """Signal rejeté si spread trop élevé."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=10)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=15)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_SPREAD in result.failed_conditions

    def test_invalid_low_rr(self):
        """Signal rejeté si R/R insuffisant."""
        validator = SignalValidator(min_risk_reward=3.0, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_RR in result.failed_conditions

    def test_invalid_news_filter(self):
        """Signal rejeté si news à impact élevé."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data, has_high_impact_news=True)

        assert result.is_valid is False
        assert COND_NEWS in result.failed_conditions

    def test_invalid_htf_trend_misaligned(self):
        """Signal rejeté si tendance HTF non alignée."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        # Tendance HTF bearish alors que le signal est BUY
        result = validator.validate(signal, candles, smc_data, htf_trend="bearish")

        assert result.is_valid is False
        assert COND_HTF_TREND in result.failed_conditions

    def test_valid_htf_trend_explicit(self):
        """Tendance HTF explicite alignée."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data, htf_trend="bullish")

        assert result.is_valid is True
        assert COND_HTF_TREND not in result.failed_conditions

    def test_valid_htf_smc_data(self):
        """Tendance HTF dérivée des données SMC HTF."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        htf_smc_data = [
            {"concept": "break_of_structure", "direction": "bullish", "price": 1.0850, "index": 3, "details": {}},
        ]

        result = validator.validate(signal, candles, smc_data, htf_smc_data=htf_smc_data)

        assert result.is_valid is True
        assert COND_HTF_TREND not in result.failed_conditions

    def test_invalid_htf_smc_data_misaligned(self):
        """Tendance HTF dérivée des données SMC HTF non alignée."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        htf_smc_data = [
            {"concept": "break_of_structure", "direction": "bearish", "price": 1.0750, "index": 3, "details": {}},
        ]

        result = validator.validate(signal, candles, smc_data, htf_smc_data=htf_smc_data)

        assert result.is_valid is False
        assert COND_HTF_TREND in result.failed_conditions

    def test_htf_alignment_disabled(self):
        """La vérification HTF peut être désactivée."""
        validator = SignalValidator(require_htf_alignment=False, min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        # Même avec une tendance HTF bearish, le signal est validé
        result = validator.validate(signal, candles, smc_data, htf_trend="bearish")

        assert COND_HTF_TREND not in result.failed_conditions
        assert result.checked_conditions[COND_HTF_TREND] is True

    def test_news_filter_disabled(self):
        """Le filtre de news peut être désactivé."""
        validator = SignalValidator(require_news_filter=False, min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data, has_high_impact_news=True)

        assert COND_NEWS not in result.failed_conditions
        assert result.checked_conditions[COND_NEWS] is True

    def test_choch_contradictory(self):
        """CHoCH dans la direction opposée invalide le signal."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        smc_data.append({
            "concept": "change_of_character",
            "direction": "bearish",
            "price": 1.0790,
            "index": 9,
            "details": {},
        })

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_CHOCH in result.failed_conditions

    def test_choch_same_direction_ok(self):
        """CHoCH dans la direction du signal est valide."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        smc_data.append({
            "concept": "change_of_character",
            "direction": "bullish",
            "price": 1.0820,
            "index": 9,
            "details": {},
        })

        result = validator.validate(signal, candles, smc_data)

        assert COND_CHOCH not in result.failed_conditions
        assert result.checked_conditions[COND_CHOCH] is True

    def test_no_choch_ok(self):
        """Pas de CHoCH = pas de conflit = valide."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert COND_CHOCH not in result.failed_conditions
        assert result.checked_conditions[COND_CHOCH] is True

    def test_score_partial(self):
        """Le score est correct pour des conditions partielles."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()
        # Retirer FVG et OB → 2 conditions échouent sur 11
        smc_data = [d for d in smc_data if d["concept"] not in ("fair_value_gap", "order_block")]

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert len(result.failed_conditions) == 2
        # score = 9/11 ≈ 0.8182
        assert 0.8 < result.score < 0.82

    def test_checked_conditions_complete(self):
        """Toutes les conditions sont vérifiées et présentes dans checked_conditions."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        for cond in ALL_CONDITIONS:
            assert cond in result.checked_conditions
            assert cond in result.details

    def test_empty_smc_data(self):
        """Données SMC vides → plusieurs conditions échouent."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)

        result = validator.validate(signal, candles, [])

        assert result.is_valid is False
        assert COND_BOS in result.failed_conditions
        assert COND_FVG in result.failed_conditions
        assert COND_ORDER_BLOCK in result.failed_conditions
        assert COND_LIQUIDITY_SWEEP in result.failed_conditions
        assert COND_PREMIUM_DISCOUNT in result.failed_conditions
        assert COND_HTF_TREND in result.failed_conditions

    def test_empty_candles(self):
        """Bougies vides → session échoue."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, [], smc_data)

        assert result.is_valid is False
        assert COND_SESSION in result.failed_conditions

    def test_spread_from_param(self):
        """Le spread peut être passé en paramètre."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        # spread=25 > max_spread=20 → échec
        result = validator.validate(signal, candles, smc_data, spread=25)

        assert result.is_valid is False
        assert COND_SPREAD in result.failed_conditions

    def test_premium_discount_individual_zones(self):
        """Vérification avec les détections PREMIUM et DISCOUNT individuelles."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = [
            {"concept": "break_of_structure", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
            {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 6, "details": {"gap_size": 0.0005}},
            {"concept": "order_block", "direction": "bullish", "price": 1.0795, "index": 4, "details": {"mitigated": False}},
            {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0790, "index": 7, "details": {"type": "buy_side_liquidity_grab"}},
            {"concept": "discount", "direction": "bullish", "price": 1.0810, "index": 8, "details": {"in_discount": True}},
        ]

        result = validator.validate(signal, candles, smc_data)

        assert COND_PREMIUM_DISCOUNT not in result.failed_conditions


# =============================================================================
# Tests de session
# =============================================================================


class TestSessionCheck:
    def test_london_session_ok(self):
        """Bougies à 08:xx UTC = session de Londres → autorisée."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert COND_SESSION not in result.failed_conditions

    def test_asia_session_not_authorized(self):
        """Bougies à 02:xx UTC = session asiatique → non autorisée par défaut."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        signal = make_buy_signal(rr=2.0)
        # Créer des bougies à 02:xx UTC (session asiatique)
        candles = []
        for i in range(20):
            candles.append(Candle(
                symbol="EURUSD",
                timeframe=TimeFrame.H1,
                time=datetime(2024, 1, 1, 2, i, tzinfo=timezone.utc),
                open=Decimal("1.0800"),
                high=Decimal("1.0810"),
                low=Decimal("1.0790"),
                close=Decimal("1.0805"),
                volume=100,
                spread=3,
            ))
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert result.is_valid is False
        assert COND_SESSION in result.failed_conditions

    def test_asia_session_authorized_when_configured(self):
        """Session asiatique autorisée si configurée."""
        validator = SignalValidator(
            min_risk_reward=1.5,
            max_spread=20,
            authorized_sessions=[TradingSession.ASIA],
        )
        signal = make_buy_signal(rr=2.0)
        candles = []
        for i in range(20):
            candles.append(Candle(
                symbol="EURUSD",
                timeframe=TimeFrame.H1,
                time=datetime(2024, 1, 1, 2, i, tzinfo=timezone.utc),
                open=Decimal("1.0800"),
                high=Decimal("1.0810"),
                low=Decimal("1.0790"),
                close=Decimal("1.0805"),
                volume=100,
                spread=3,
            ))
        smc_data = make_full_bullish_smc_data()

        result = validator.validate(signal, candles, smc_data)

        assert COND_SESSION not in result.failed_conditions


# =============================================================================
# Tests d'intégration avec le SignalGenerator
# =============================================================================


class TestSignalGeneratorIntegration:
    def test_generator_without_validator(self):
        """Sans validateur, le générateur fonctionne comme avant."""
        gen = SignalGenerator(min_confidence=0.1)
        assert gen.validator is None
        assert gen.last_validation is None

    def test_generator_with_validator(self):
        """Le générateur peut être configuré avec un validateur."""
        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        assert gen.validator is not None
        assert gen.validator is validator

    @pytest.mark.asyncio
    async def test_generate_with_valid_validator(self):
        """Le signal est retourné si le validateur l'accepte."""
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signal = await gen.generate(candles, smc_data)

        assert signal is not None
        assert gen.last_validation is not None
        assert gen.last_validation.is_valid is True

    @pytest.mark.asyncio
    async def test_generate_with_invalid_validator(self):
        """Le signal est rejeté si le validateur le refuse (R/R trop élevé)."""
        validator = SignalValidator(min_risk_reward=10.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signal = await gen.generate(candles, smc_data)

        assert signal is None
        assert gen.last_validation is not None
        assert gen.last_validation.is_valid is False
        assert COND_RR in gen.last_validation.failed_conditions

    @pytest.mark.asyncio
    async def test_generate_with_news_filter(self):
        """Le signal est rejeté s'il y a des news à impact élevé."""
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signal = await gen.generate(candles, smc_data, has_high_impact_news=True)

        assert signal is None
        assert gen.last_validation is not None
        assert COND_NEWS in gen.last_validation.failed_conditions

    @pytest.mark.asyncio
    async def test_generate_with_htf_trend(self):
        """Le signal est rejeté si la tendance HTF est non alignée."""
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signal = await gen.generate(candles, smc_data, htf_trend="bearish")

        assert signal is None
        assert gen.last_validation is not None
        assert COND_HTF_TREND in gen.last_validation.failed_conditions

    @pytest.mark.asyncio
    async def test_generate_without_validator_backward_compatible(self):
        """Sans validateur, le générateur fonctionne comme avant (backward compatible)."""
        gen = SignalGenerator(min_confidence=0.1)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signal = await gen.generate(candles, smc_data)

        assert signal is not None
        assert gen.last_validation is None

    def test_validate_method_without_validator(self):
        """La méthode validate retourne None sans validateur."""
        gen = SignalGenerator(min_confidence=0.1)
        signal = make_buy_signal()
        candles = make_candles(20)
        smc_data = make_full_bullish_smc_data()

        result = gen.validate(signal, candles, smc_data)
        assert result is None

    def test_validate_method_with_validator(self):
        """La méthode validate délègue au validateur."""
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        signal = make_buy_signal(rr=2.0)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        result = gen.validate(signal, candles, smc_data)
        assert result is not None
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_generate_all_with_validator(self):
        """generate_all filtre les signaux via le validateur."""
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signals = await gen.generate_all(candles, smc_data)

        # Tous les signaux retournés sont validés
        for s in signals:
            assert s is not None

    @pytest.mark.asyncio
    async def test_generate_all_with_strict_validator(self):
        """generate_all avec un validateur strict rejette tout."""
        validator = SignalValidator(min_risk_reward=100.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        candles = make_candles(20, spread=3)
        smc_data = make_full_bullish_smc_data()

        signals = await gen.generate_all(candles, smc_data)

        assert signals == []