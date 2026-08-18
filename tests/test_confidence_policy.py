"""
Tests de la politique adaptative confiance/RR (Phase Adaptive Confidence).

Couvre les cas obligatoires :
- confiance < 0.60 â†’ rejet low_confidence
- 0.60â€“0.69 : acceptÃ© si RR >= 2.0, rejetÃ© si RR < 2.0
- 0.70â€“0.79 : rejet si RR insuffisant
- 0.85+ : comportement existant conservÃ©
- BUY en bearish / SELL en bullish â†’ toujours rejetÃ©s (Master Direction Gate)
- SL initial / risque par trade inchangÃ©s
- Final Gate / Risk Manager toujours obligatoires (seuils inchangÃ©s)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arty_trading.config.settings import (
    InstrumentProfile,
    PositionSettings,
    RiskSettings,
)
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.signals.confidence_policy import (
    ADAPTIVE_MIN_RR,
    evaluate_confidence_policy,
)
from arty_trading.modules.signals.generator import SignalGenerator
from arty_trading.modules.strategies.base import BaseStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StubStrategy(BaseStrategy):
    """StratÃ©gie factice produisant un signal de confiance/RR paramÃ©trÃ©s."""

    name = "SMC Trend Following"  # doit correspondre Ã  la stratÃ©gie active

    def __init__(self, confidence: float, rr: float, direction: Direction = Direction.BUY) -> None:
        super().__init__()
        self._confidence = confidence
        self._rr = rr
        self._direction = direction

    async def analyze(self, candles, smc_data, htf_smc_data=None, htf_trend=None):  # type: ignore[no-untyped-def]
        entry = Decimal("1.1000")
        risk = Decimal("0.0100")
        reward = risk * Decimal(str(self._rr))
        if self._direction == Direction.BUY:
            sl, tp = entry - risk, entry + reward
        else:
            sl, tp = entry + risk, entry - reward
        return Signal(
            symbol="EURUSD",
            signal_type=SignalType.BUY if self._direction == Direction.BUY else SignalType.SELL,
            direction=self._direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            confidence=self._confidence,
            strategy_name=self.name,
            timeframe=TimeFrame.M5,
        )


def make_candles(n: int = 20) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.M5,
            time=base + timedelta(minutes=5 * i),
            open=Decimal("1.1000"),
            high=Decimal("1.1010"),
            low=Decimal("1.0990"),
            close=Decimal("1.1005"),
            volume=100,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Tests de la politique (fonction pure)
# ---------------------------------------------------------------------------

class TestConfidencePolicy:
    def test_059_rejected_low_confidence(self) -> None:
        d = evaluate_confidence_policy(0.59, 3.0)
        assert d.allowed is False
        assert d.reason == "low_confidence"
        assert d.confidence_bucket == "<0.60"

    def test_060_rr2_accepted(self) -> None:
        d = evaluate_confidence_policy(0.60, 2.0)
        assert d.allowed is True
        assert d.confidence_bucket == "0.60-0.69"
        assert d.required_rr == 2.0
        assert d.security_level == "HIGH"

    def test_060_rr15_rejected(self) -> None:
        d = evaluate_confidence_policy(0.60, 1.5)
        assert d.allowed is False
        assert d.reason == "rr_too_low"

    def test_069_rr2_accepted(self) -> None:
        assert evaluate_confidence_policy(0.69, 2.0).allowed is True

    def test_069_rr15_rejected(self) -> None:
        d = evaluate_confidence_policy(0.69, 1.5)
        assert d.allowed is False
        assert d.reason == "rr_too_low"

    def test_075_rr_insufficient_rejected(self) -> None:
        d = evaluate_confidence_policy(0.75, 1.9)
        assert d.allowed is False
        assert d.reason == "rr_too_low"
        assert d.required_rr == 2.0

    def test_075_rr2_accepted(self) -> None:
        assert evaluate_confidence_policy(0.75, 2.0).allowed is True

    def test_082_requires_rr2(self) -> None:
        assert evaluate_confidence_policy(0.82, 1.5).allowed is False
        assert evaluate_confidence_policy(0.82, 2.0).allowed is True

    def test_085_existing_behavior_preserved(self) -> None:
        d = evaluate_confidence_policy(0.85, 1.0)
        # Seuil historique franchi : autorisÃ© au niveau politique, les autres
        # gates (validator, risk manager, final gate RR>=2.0) restent inchangÃ©s.
        assert d.allowed is True
        assert d.required_rr is None
        assert d.security_level == "STANDARD"

    def test_rr1_never_auto_allowed_below_085(self) -> None:
        for conf in (0.60, 0.65, 0.70, 0.75, 0.80, 0.84):
            assert evaluate_confidence_policy(conf, 1.0).allowed is False

    def test_legacy_threshold_below_085_unchanged(self) -> None:
        # Seuil configurÃ© plus bas (tests/calibration) : comportement plat conservÃ©.
        assert evaluate_confidence_policy(0.30, 1.0, base_threshold=0.30).allowed is True
        assert evaluate_confidence_policy(0.29, 3.0, base_threshold=0.30).allowed is False

    def test_diagnostics_fields_present(self) -> None:
        d = evaluate_confidence_policy(0.64, 2.0).to_dict()
        for key in (
            "confidence",
            "confidence_threshold",
            "confidence_bucket",
            "required_rr",
            "actual_rr",
            "rr_security_level",
        ):
            assert key in d
        assert d["confidence"] == 0.64
        assert d["confidence_bucket"] == "0.60-0.69"
        assert d["required_rr"] == 2.0
        assert d["actual_rr"] == 2.0
        assert d["rr_security_level"] == "HIGH"


# ---------------------------------------------------------------------------
# Intégration SignalGenerator
# ---------------------------------------------------------------------------

class TestSignalGeneratorAdaptive:
    @pytest.mark.asyncio
    async def test_conf_065_rr2_signal_emitted(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.65, rr=2.0)],
        )
        signal = await gen.generate(make_candles(), [])
        assert signal is not None
        assert gen.last_confidence_policy is not None
        assert gen.last_confidence_policy.allowed is True

    @pytest.mark.asyncio
    async def test_conf_065_rr15_rejected(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.65, rr=1.5)],
        )
        signal = await gen.generate(make_candles(), [])
        assert signal is None
        assert gen.last_rejection_stage == "confidence"
        assert gen.last_rejection_reason == "rr_too_low"

    @pytest.mark.asyncio
    async def test_conf_055_rejected_low_confidence(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.55, rr=3.0)],
        )
        signal = await gen.generate(make_candles(), [])
        assert signal is None
        assert gen.last_rejection_reason == "low_confidence"

    @pytest.mark.asyncio
    async def test_sl_tp_and_risk_unchanged(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.65, rr=2.0)],
        )
        signal = await gen.generate(make_candles(), [])
        assert signal is not None
        # SL initial inchangÃ© (entry - 0.01), TP = 2R exactement.
        assert signal.stop_loss == Decimal("1.0900")
        assert signal.take_profit == Decimal("1.1200")
        assert signal.risk_reward_ratio == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_buy_in_bearish_always_rejected(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.95, rr=3.0)],
        )
        # Le stub Ã©met un BUY : bearish doit le rejeter mÃªme Ã  confiance >= 0.85.
        signal = await gen.generate(make_candles(), [], master_trend="bearish")
        assert signal is None
        assert gen.last_rejection_stage == "master_gate"

    @pytest.mark.asyncio
    async def test_sell_in_bullish_always_rejected(self) -> None:
        gen = SignalGenerator(
            min_confidence=0.85,
            strategies=[StubStrategy(confidence=0.65, rr=2.5, direction=Direction.SELL)],
        )
        signal = await gen.generate(make_candles(), [], master_trend="bullish")
        assert signal is None
        assert gen.last_rejection_stage == "master_gate"


# ---------------------------------------------------------------------------
# Invariants de sÃ©curitÃ© (aucune dÃ©gradation)
# ---------------------------------------------------------------------------


class TestSecurityInvariants:
    def test_final_gate_min_rr_unchanged(self) -> None:
        # Le Final Gate exige toujours RR >= 2.0 (profil instrument par dÃ©faut),
        # identique au RR requis par la politique adaptative.
        assert InstrumentProfile(symbol="EURUSD").min_risk_reward == 2.0
        assert ADAPTIVE_MIN_RR == 2.0

    def test_risk_per_trade_unchanged(self) -> None:
        risk = RiskSettings()
        assert risk.risk_per_trade == pytest.approx(0.01)
        assert risk.max_open_positions == 3

    def test_risk_manager_defaults_unchanged(self) -> None:
        from arty_trading.modules.risk.manager import RiskManager

        rm = RiskManager()
        assert rm._min_confidence == pytest.approx(0.3)
        assert rm._min_rr == pytest.approx(1.0)

    def test_existing_position_protections_reused(self) -> None:
        # Break-even Ã  +1R dÃ©jÃ  prÃ©sent dans le PositionManager existant :
        # rÃ©utilisÃ© tel quel pour sÃ©curiser les trades 0.60-0.69.
        pos = PositionSettings()
        assert pos.enable_break_even is True
        assert pos.break_even_at_r == pytest.approx(1.0)
        assert pos.enable_trailing_stop is True
        assert pos.enable_partial_tp is True

    def test_final_gate_and_risk_manager_still_in_pipeline(self) -> None:
        from arty_trading.application.execution_guards import (
            final_gate_before_execution,
        )
        from arty_trading.core.interfaces import IRiskManager

        assert callable(final_gate_before_execution)
        assert IRiskManager.validate_signal is not None

