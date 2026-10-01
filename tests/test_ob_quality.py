"""Tests Phase 12 — notation de qualité des Order Blocks (Grade A/B/C/D).

Couvre :
- le catalogue des grades et les comparaisons ``grade_meets_min`` ;
- la notation pondérée (displacement, hauteur de zone, mitigations, fraîcheur,
  tendance, confluences, premium/discount) ;
- le garde-fou anti look-ahead des confluences ;
- la configuration Pydantic (désactivée par défaut, surchargeable, validée) ;
- le filtre qualité appliqué à un setup (``evaluate_setup_ob_gate``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arty_trading.config.settings import OBQualitySettings, Settings
from arty_trading.modules.smc.ob_quality import (
    OB_GRADES,
    OBQualityGrade,
    assess_order_block_quality,
    evaluate_setup_ob_gate,
    grade_meets_min,
    grade_rank,
)


def ob_detection(
    direction: str = "bullish",
    index: int = 10,
    top: float = 2010.0,
    bottom: float = 2009.0,
    displacement: float = 3.0,
    mitigations: int = 0,
) -> dict:
    """Détection OB normalisée (format ``SMCDetection.to_dict()``)."""
    return {
        "concept": "order_block",
        "direction": direction,
        "index": index,
        "details": {
            "ob_top": top,
            "ob_bottom": bottom,
            "displacement_size": displacement,
            "mitigation_count": mitigations,
        },
    }


def confluence_data(direction: str = "bullish") -> list[dict]:
    """Confluences complètes : sweep + shift de structure + FVG + discount."""
    return [
        {"concept": "liquidity_sweep", "direction": direction, "index": 8, "details": {}},
        {"concept": "change_of_character", "direction": direction, "index": 9, "details": {}},
        {
            "concept": "fair_value_gap",
            "direction": direction,
            "index": 9,
            "details": {"gap_top": 2009.6, "gap_bottom": 2009.2},
        },
        {
            "concept": "premium_discount",
            "direction": "neutral",
            "index": 9,
            "details": {"equilibrium": 2010.0},
        },
    ]


class TestGradeCatalog:
    """Catalogue et comparaisons de grades."""

    def test_catalog_values(self) -> None:
        assert OB_GRADES == ("A", "B", "C", "D")
        assert [g.value for g in OBQualityGrade] == list(OB_GRADES)

    def test_rank_order(self) -> None:
        assert grade_rank("A") == 4
        assert grade_rank("B") == 3
        assert grade_rank("C") == 2
        assert grade_rank("D") == 1

    def test_rank_unknown_is_zero(self) -> None:
        assert grade_rank("X") == 0
        assert grade_rank("") == 0

    def test_rank_accepts_enum_and_str(self) -> None:
        assert grade_rank(OBQualityGrade.A) == grade_rank("a")

    def test_meets_min(self) -> None:
        assert grade_meets_min("A", "B") is True
        assert grade_meets_min("B", "B") is True
        assert grade_meets_min("C", "B") is False
        assert grade_meets_min("D", "B") is False
        assert grade_meets_min("X", "B") is False


class TestScoring:
    """Notation pondérée d'un Order Block."""

    def test_premium_ob_is_grade_a(self) -> None:
        result = assess_order_block_quality(
            ob_detection(),
            atr=1.0,
            htf_trend="bullish",
            smc_data=confluence_data(),
            bars_since_zone=1,
        )
        assert result.grade is OBQualityGrade.A
        assert result.score >= 85
        assert result.components["confluence"] > 0
        assert result.components["trend"] > 0

    def test_average_ob_is_grade_b_not_a(self) -> None:
        """Sans confluence ni premium/discount : bon OB mais pas premium."""
        result = assess_order_block_quality(
            ob_detection(), atr=1.0, htf_trend="bullish", bars_since_zone=2
        )
        assert result.grade is OBQualityGrade.B
        assert 70 <= result.score < 85

    def test_poor_ob_is_grade_d(self) -> None:
        result = assess_order_block_quality(
            ob_detection(top=2012.0, bottom=2008.0, displacement=0.1, mitigations=3),
            atr=1.0,
            htf_trend="neutral",
            bars_since_zone=40,
        )
        assert result.grade is OBQualityGrade.D
        assert result.score < 50

    def test_missing_zone_bounds_is_d(self) -> None:
        detection = {
            "concept": "order_block",
            "direction": "bullish",
            "index": 3,
            "details": {"displacement_size": 3.0},
        }
        result = assess_order_block_quality(detection, atr=1.0)
        assert result.grade is OBQualityGrade.D
        assert result.score == 0
        assert result.reasons == ("missing_zone_bounds",)

    def test_score_is_bounded_and_components_sum_to_score(self) -> None:
        result = assess_order_block_quality(
            ob_detection(),
            atr=1.0,
            htf_trend="bullish",
            smc_data=confluence_data(),
            bars_since_zone=0,
        )
        assert 0 <= result.score <= 100
        assert sum(result.components.values()) == result.score

    def test_deterministic(self) -> None:
        kwargs = {"atr": 1.0, "htf_trend": "bullish", "smc_data": confluence_data(),
                  "bars_since_zone": 3}
        first = assess_order_block_quality(ob_detection(), **kwargs)
        second = assess_order_block_quality(ob_detection(), **kwargs)
        assert first.score == second.score
        assert first.grade == second.grade
        assert first.components == second.components

    def test_counter_trend_scores_lower_than_aligned(self) -> None:
        aligned = assess_order_block_quality(
            ob_detection(), atr=1.0, htf_trend="bullish", bars_since_zone=1
        )
        opposed = assess_order_block_quality(
            ob_detection(), atr=1.0, htf_trend="bearish", bars_since_zone=1
        )
        assert opposed.score < aligned.score

    def test_no_look_ahead_on_confluence(self) -> None:
        """Un sweep postérieur à la zone ne doit pas compter comme confluence."""
        smc_data = [
            {"concept": "liquidity_sweep", "direction": "bullish", "index": 25, "details": {}},
        ]
        result = assess_order_block_quality(
            ob_detection(index=10),
            atr=1.0,
            htf_trend="bullish",
            smc_data=smc_data,
            bars_since_zone=1,
        )
        assert result.components["confluence"] == 0
        assert "confluence=none" in result.reasons

    def test_opposite_direction_confluence_ignored(self) -> None:
        smc_data = [
            {"concept": "liquidity_sweep", "direction": "bearish", "index": 8, "details": {}},
        ]
        result = assess_order_block_quality(
            ob_detection(), atr=1.0, htf_trend="bullish", smc_data=smc_data, bars_since_zone=1
        )
        assert result.components["confluence"] == 0

    def test_fvg_overlap_requires_intersection(self) -> None:
        smc_data = [
            {
                "concept": "fair_value_gap",
                "direction": "bullish",
                "index": 9,
                "details": {"gap_top": 2050.0, "gap_bottom": 2049.0},
            },
        ]
        result = assess_order_block_quality(
            ob_detection(), atr=1.0, htf_trend="bullish", smc_data=smc_data, bars_since_zone=1
        )
        assert result.components["confluence"] == 0

    def test_bearish_ob_prefers_premium(self) -> None:
        smc_data = [
            {
                "concept": "premium_discount",
                "direction": "neutral",
                "index": 9,
                "details": {"equilibrium": 2009.0},
            },
        ]
        result = assess_order_block_quality(
            ob_detection(direction="bearish"),
            atr=1.0,
            htf_trend="bearish",
            smc_data=smc_data,
            bars_since_zone=1,
        )
        # zone_mid (2009.5) > equilibrium (2009.0) → premium, correct pour un SELL.
        assert result.fractions["premium_discount"] == 1.0

    def test_to_dict_payload(self) -> None:
        result = assess_order_block_quality(ob_detection(), atr=1.0, htf_trend="bullish")
        payload = result.to_dict()
        assert payload["ob_grade"] in OB_GRADES
        assert payload["ob_quality_score"] == result.score
        assert "ob_quality_components" in payload
        assert isinstance(payload["ob_quality_reasons"], list)

    def test_unknown_atr_does_not_crash(self) -> None:
        result = assess_order_block_quality(ob_detection(), atr=0.0, htf_trend="bullish")
        assert 0 <= result.score <= 100
        assert result.fractions["displacement"] == 0.0


class TestOBQualitySettings:
    """Configuration Pydantic du filtre qualité OB."""

    def test_disabled_by_default(self) -> None:
        settings = OBQualitySettings()
        assert settings.enabled is False
        assert settings.min_grade == "B"
        assert settings.require_m5_confirmation is True

    def test_wired_into_main_settings(self) -> None:
        settings = Settings()
        assert settings.ob_quality.enabled is False
        assert settings.ob_quality.min_grade == "B"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OB_QUALITY_ENABLED", "true")
        monkeypatch.setenv("OB_MIN_GRADE", "a")
        monkeypatch.setenv("OB_M5_REQUIRE_DISPLACEMENT", "false")
        settings = OBQualitySettings()
        assert settings.enabled is True
        assert settings.min_grade == "A"
        assert settings.m5_require_displacement is False

    def test_invalid_min_grade_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OBQualitySettings(OB_MIN_GRADE="Z")

    def test_invalid_threshold_order_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OBQualitySettings(OB_GRADE_A_THRESHOLD=50, OB_GRADE_B_THRESHOLD=70)

    def test_invalid_zone_height_order_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OBQualitySettings(OB_MIN_ZONE_HEIGHT_ATR=5.0, OB_MAX_ZONE_HEIGHT_ATR=1.0)


class TestSetupGate:
    """Filtre qualité appliqué aux métadonnées d'un setup."""

    def test_setup_without_grade_is_allowed(self) -> None:
        decision = evaluate_setup_ob_gate({}, min_grade="B", require_m5_confirmation=True)
        assert decision.allowed is True
        assert decision.reason == "no_grade"

    def test_fvg_setup_is_allowed(self) -> None:
        decision = evaluate_setup_ob_gate({"setup_type": "FVG_RETRACE"}, min_grade="A")
        assert decision.allowed is True

    def test_grade_below_min_blocked(self) -> None:
        decision = evaluate_setup_ob_gate(
            {"ob_grade": "C", "ob_m5_confirmed": True}, min_grade="B"
        )
        assert decision.allowed is False
        assert decision.reason == "grade_below_min"
        assert decision.grade == "C"

    def test_missing_m5_confirmation_blocked(self) -> None:
        decision = evaluate_setup_ob_gate(
            {"ob_grade": "A", "ob_m5_confirmed": False}, min_grade="B"
        )
        assert decision.allowed is False
        assert decision.reason == "m5_confirmation_missing"

    def test_confirmed_grade_a_allowed(self) -> None:
        decision = evaluate_setup_ob_gate(
            {"ob_grade": "A", "ob_m5_confirmed": True}, min_grade="B"
        )
        assert decision.allowed is True
        assert decision.reason == "ok"

    def test_m5_confirmation_not_required(self) -> None:
        decision = evaluate_setup_ob_gate(
            {"ob_grade": "B"}, min_grade="B", require_m5_confirmation=False
        )
        assert decision.allowed is True
