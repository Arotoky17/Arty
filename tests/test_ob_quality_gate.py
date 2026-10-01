"""Tests Phase 12 — intégration du filtre qualité OB (setup_service + generator).

Vérifie :
- la non-régression quand ``OB_QUALITY_ENABLED`` est absent (défaut) :
  les setups Order Block sont créés exactement comme avant ;
- le filtrage à la création : un OB de grade insuffisant n'engendre AUCUN setup ;
- la propagation des métadonnées (``ob_grade``, ``ob_m5_confirmed``) ;
- le rejet d'un setup OB par le ``SignalGenerator`` (grade / confirmation M5).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.config.settings import OBQualitySettings, Settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.smc import SetupTracker

_t0 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)


def mk_m5(idx: int, o: float, h: float, low: float, c: float) -> Candle:
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


def flat_m5_series(n: int = 12, price: float = 2012.0) -> list[Candle]:
    """Bougies M5 plates, loin de toute zone (prix 2012).

    Durée volontairement courte (12 bougies) pour que les zones d'index 2-3 ne
    soient pas expirées par ``max_zone_age_bars`` (25 pour XAUUSD).
    """
    return [mk_m5(i, price, price + 0.2, price - 0.2, price) for i in range(n)]


def confirming_m5_series(n: int = 12) -> list[Candle]:
    """Bougies M5 confirmant une zone 2009-2010 : retest + rejet fort."""
    candles = [mk_m5(i, 2012.0, 2012.2, 2011.8, 2012.0) for i in range(n - 1)]
    candles.append(mk_m5(n - 1, 2009.4, 2010.8, 2009.2, 2010.7))
    return candles


def strong_ob(direction: str = "bullish", index: int = 3) -> dict:
    """OB premium : déplacement 3 ATR, zone 1 ATR, jamais mitigé + confluences."""
    details = {
        "ob_top": 2010.0,
        "ob_bottom": 2009.0,
        "displacement_size": 3.0,
        "mitigation_count": 0,
    }
    return {"concept": "order_block", "direction": direction, "index": index, "details": details}


def weak_ob(direction: str = "bullish", index: int = 3) -> dict:
    """OB médiocre : déplacement 0.1 ATR, zone 5 ATR, déjà mitigé 3 fois."""
    details = {
        "ob_top": 2014.0,
        "ob_bottom": 2009.0,
        "displacement_size": 0.1,
        "mitigation_count": 3,
    }
    return {"concept": "order_block", "direction": direction, "index": index, "details": details}


def confluence_for(direction: str = "bullish") -> list[dict]:
    return [
        {"concept": "liquidity_sweep", "direction": direction, "index": 1, "details": {}},
        {"concept": "change_of_character", "direction": direction, "index": 2, "details": {}},
        {
            "concept": "fair_value_gap",
            "direction": direction,
            "index": 2,
            "details": {"gap_top": 2009.6, "gap_bottom": 2009.2},
        },
        {
            "concept": "premium_discount",
            "direction": "neutral",
            "index": 2,
            "details": {"equilibrium": 2010.0},
        },
    ]


def fake_context(detections: list[dict], ltf_candles: list[Candle]) -> SimpleNamespace:
    return SimpleNamespace(
        master_trend="bullish",
        setup_smc_data=detections,
        setup_trend="bullish",
        ltf_smc_data=[],
        ltf_candles=ltf_candles,
        atr=1.0,
    )


def grading_settings(min_grade: str = "B") -> Settings:
    settings = Settings()
    settings.ob_quality = OBQualitySettings(enabled=True, min_grade=min_grade)
    return settings


class TestSetupServiceFiltering:
    """Filtre qualité appliqué à la création des setups (live + backtest)."""

    @staticmethod
    def _ob_setups(tracker: SetupTracker):
        return [s for s in tracker.get_active_setups("XAUUSD") if s.zone_concept == "order_block"]

    def test_disabled_by_default_creates_ob_setup(self) -> None:
        """Non-régression : sans OB_QUALITY_ENABLED, un OB crée un setup."""
        tracker = SetupTracker()
        ctx = fake_context([weak_ob()], flat_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, Settings())

        setups = self._ob_setups(tracker)
        assert len(setups) == 1
        assert setups[0].direction == Direction.BUY
        assert "ob_grade" not in setups[0].metadata

    def test_low_grade_ob_creates_no_setup(self) -> None:
        tracker = SetupTracker()
        ctx = fake_context([weak_ob()], flat_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, grading_settings("B"))
        assert self._ob_setups(tracker) == []

    def test_min_grade_a_rejects_grade_b_ob(self) -> None:
        tracker = SetupTracker()
        ctx = fake_context([strong_ob()], flat_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, grading_settings("A"))
        # Sans confluence ni premium/discount, l'OB est Grade B → refusé en Grade A.
        assert self._ob_setups(tracker) == []

    def test_high_grade_ob_created_with_metadata(self) -> None:
        tracker = SetupTracker()
        detections = [*confluence_for(), strong_ob()]
        ctx = fake_context(detections, flat_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, grading_settings("B"))

        setups = self._ob_setups(tracker)
        assert len(setups) == 1
        metadata = setups[0].metadata
        assert metadata["ob_grade"] == "A"
        assert 85 <= metadata["ob_quality_score"] <= 100
        assert "ob_quality_components" in metadata
        # Zone jamais retestée (prix M5 = 2012) → pas de confirmation M5.
        assert metadata["ob_m5_confirmed"] is False

    def test_m5_confirmation_propagated(self) -> None:
        tracker = SetupTracker()
        detections = [*confluence_for(), strong_ob()]
        ctx = fake_context(detections, confirming_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, grading_settings("B"))

        setups = self._ob_setups(tracker)
        assert len(setups) == 1
        metadata = setups[0].metadata
        assert metadata["ob_m5_confirmed"] is True
        assert metadata["ob_m5_confirmation_type"] in ("rejection", "strong_rejection")

    def test_fvg_setups_are_not_graded(self) -> None:
        tracker = SetupTracker()
        fvg = {
            "concept": "fair_value_gap",
            "direction": "bullish",
            "index": 3,
            "details": {"gap_top": 2010.0, "gap_bottom": 2009.0},
        }
        ctx = fake_context([fvg], flat_m5_series())
        update_setups_from_market_context(tracker, "XAUUSD", ctx, grading_settings("A"))

        setups = tracker.get_active_setups("XAUUSD")
        assert len(setups) == 1
        assert setups[0].zone_concept == "fair_value_gap"
        assert "ob_grade" not in setups[0].metadata


class TestGeneratorGate:
    """Rejet d'un setup OB par le SignalGenerator (grade / confirmation M5)."""

    @staticmethod
    def _ready_setup(tracker: SetupTracker, metadata: dict, confidence: float = 0.8):
        setup = tracker.create_setup(
            symbol="XAUUSD",
            direction=Direction.BUY,
            zone_concept="order_block",
            zone_index=1,
            zone_price=2009.5,
            zone_high=2010.0,
            zone_low=2009.0,
            atr=1.0,
            htf_trend="bullish",
            ttl_bars=25,
        )
        assert setup is not None
        setup.metadata.update(metadata)
        setup.confidence_estimate = confidence
        from arty_trading.modules.smc import SetupState

        setup.state = SetupState.READY
        return setup

    def _generate(self, tracker: SetupTracker, ob_quality: OBQualitySettings | None):
        generator = SignalGenerator(
            min_confidence=0.1,
            strategies=[],
            setup_tracker=tracker,
            ob_quality=ob_quality,
        )
        candles = flat_m5_series(10)
        return asyncio.run(generator.generate(candles, [], master_trend="bullish")), generator

    def test_low_grade_setup_rejected(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "D", "ob_m5_confirmed": True})
        signal, generator = self._generate(tracker, OBQualitySettings(enabled=True, min_grade="B"))
        assert signal is None
        assert generator.last_rejection_stage == "ob_quality"
        assert generator.last_rejection_reason == "grade_below_min"

    def test_missing_m5_confirmation_rejected(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "A", "ob_m5_confirmed": False})
        signal, generator = self._generate(tracker, OBQualitySettings(enabled=True, min_grade="B"))
        assert signal is None
        assert generator.last_rejection_stage == "ob_quality"
        assert generator.last_rejection_reason == "m5_confirmation_missing"

    def test_confirmed_grade_a_setup_allowed(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "A", "ob_m5_confirmed": True})
        signal, generator = self._generate(tracker, OBQualitySettings(enabled=True, min_grade="B"))
        assert signal is not None
        assert signal.direction == Direction.BUY
        assert generator.ob_quality is not None

    def test_gate_inactive_when_settings_absent(self) -> None:
        """Non-régression : sans injection de config, aucun filtrage."""
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "D", "ob_m5_confirmed": False})
        signal, _ = self._generate(tracker, None)
        assert signal is not None

    def test_gate_inactive_when_disabled(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "D", "ob_m5_confirmed": False})
        signal, _ = self._generate(tracker, OBQualitySettings(enabled=False))
        assert signal is not None

    def test_fvg_setup_without_grade_allowed(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"setup_type": "FVG_RETRACE"})
        signal, _ = self._generate(tracker, OBQualitySettings(enabled=True, min_grade="A"))
        assert signal is not None

    def test_m5_not_required_allows_unconfirmed_grade_a(self) -> None:
        tracker = SetupTracker()
        self._ready_setup(tracker, {"ob_grade": "A", "ob_m5_confirmed": False})
        signal, _ = self._generate(
            tracker,
            OBQualitySettings(enabled=True, min_grade="B", require_m5_confirmation=False),
        )
        assert signal is not None
