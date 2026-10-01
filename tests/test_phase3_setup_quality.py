"""Tests Phase 3 — qualité de détection SMC et classification des setups.

Couvre :

- 3B : filtres de qualité (sweep vs mèche, displacement, micro-FVG, OB géant).
- 3D : classification des setups (pas de look-ahead).
- 3F : profil instrument XAUUSD (valeurs consommables par le moteur).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.smc.detector import SMCDetector
from arty_trading.modules.smc.fair_value_gap import FairValueGapDetector
from arty_trading.modules.smc.liquidity import LiquidityDetector
from arty_trading.modules.smc.order_blocks import OrderBlockDetector
from arty_trading.modules.smc.setup_classifier import (
    SETUP_TYPES,
    classify_setup_type,
    qualify_setup_type,
)
from arty_trading.modules.smc.setup_tracker import SetupType
from arty_trading.utils.helpers import calculate_atr, is_displacement
from arty_trading.config.settings import Settings


def mk(idx, o, h, l, c, volume=100, spread=3):
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, 0, idx, tzinfo=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
        spread=spread,
    )


def baseline_candles(n=15):
    """Bougies calmes (range ~10 pips) autour de 1.0010, avec un swing low en i=2
    (0.9990) et un swing high en i=4 (1.0022) — assez de bougies pour l'ATR(14)."""
    specs = [
        (1.0000, 1.0010, 0.9995, 1.0005),
        (1.0005, 1.0012, 1.0000, 1.0010),
        (1.0010, 1.0012, 0.9990, 1.0000),  # swing low @ 0.9990
        (1.0000, 1.0012, 0.9998, 1.0010),
        (1.0010, 1.0022, 1.0008, 1.0018),  # swing high @ 1.0022
        (1.0018, 1.0018, 1.0012, 1.0014),
        (1.0014, 1.0016, 1.0010, 1.0012),
        (1.0012, 1.0018, 1.0011, 1.0016),
        (1.0016, 1.0017, 1.0010, 1.0012),
        (1.0012, 1.0019, 1.0011, 1.0018),
        (1.0018, 1.0019, 1.0013, 1.0015),
        (1.0015, 1.0016, 1.0012, 1.0014),
        (1.0014, 1.0017, 1.0012, 1.0016),
        (1.0016, 1.0018, 1.0014, 1.0017),
        (1.0017, 1.0018, 1.0013, 1.0015),
    ]
    return [mk(i, *specs[i]) for i in range(min(n, len(specs)))]


def flat_candles(n=16):
    """Bougies plates (range ~14 pips) pour constituer un historique ATR stable."""
    return [mk(i, 1.0004, 1.0010, 0.9996, 1.0006) for i in range(n)]


class TestSweepQuality:
    """3B — sweep vs simple mèche."""

    def _detect(self, candles, **kwargs):
        detector = LiquidityDetector(**kwargs)
        return [d for d in detector.detect(candles) if d.concept.value == "liquidity_sweep"]

    def test_strong_sweep_with_displacement_detected(self):
        candles = baseline_candles()
        # Balayage du swing low + displacement haussier (close bien au-dessus).
        candles.append(mk(15, 0.9988, 1.0014, 0.9985, 1.0012))
        sweeps = self._detect(candles, min_rejection_ratio=0.5, displacement_atr_mult=0.8)
        assert len(sweeps) == 1
        det = sweeps[0]
        assert det.direction == "bullish"
        assert det.details["displacement_confirmed"] is True
        assert det.details["quality"] == "strong"
        assert det.details["rejection_ratio"] >= 0.5

    def test_simple_wick_is_not_a_sweep(self):
        """Pénétration profonde + fermeture juste au-dessus → mèche, pas de sweep."""
        candles = baseline_candles()
        # Pénétration 10 pips, rejet 3 pips (ratio 0.3 < 0.5) → simple mèche.
        candles.append(mk(15, 1.0015, 1.0017, 0.9980, 0.9993))
        sweeps = self._detect(candles, min_rejection_ratio=0.5, displacement_atr_mult=0.8)
        assert sweeps == []

    def test_sweep_without_displacement_filtered(self):
        """Rapport de rejet fort mais aucun displacement → sweep rejeté."""
        candles = baseline_candles()
        # Pénétration 5 pips, close 13 pips au-dessus (ratio 2.6) mais micro-corps.
        candles.append(mk(15, 1.0000, 1.0005, 0.9985, 1.0003))
        # Bougies suivantes calmes : jamais de displacement confirmé.
        candles.append(mk(16, 1.0003, 1.0010, 0.9998, 1.0006))
        candles.append(mk(17, 1.0006, 1.0012, 1.0002, 1.0009))
        sweeps = self._detect(candles, min_rejection_ratio=0.5, displacement_atr_mult=1.0)
        assert sweeps == []

    def test_default_params_keep_historical_behavior(self):
        """Valeurs par défaut (0.0) → aucun filtrage nouveau : le sweep est détecté."""
        candles = baseline_candles()
        candles.append(mk(15, 1.0015, 1.0017, 0.9980, 0.9993))  # simple mèche
        sweeps = self._detect(candles)
        assert len(sweeps) == 1  # comportement historique conservé


class TestFvgQuality:
    """3B — FVG vs micro-gap."""

    def _detect(self, candles, **kwargs):
        detector = FairValueGapDetector(**kwargs)
        return [d for d in detector.detect(candles) if d.concept.value == "fair_value_gap"]

    def _gap_candles(self, gap_size):
        """Historique plat (ATR ~14 pips) puis gap de `gap_size` entre high[i] et low[i+2]."""
        g = gap_size
        candles = flat_candles(16)
        candles += [
            mk(16, 1.0006, 1.0010, 1.0000, 1.0008),
            mk(17, 1.0010 + g, 1.0022 + g, 1.0010 + g, 1.0020 + g),
            mk(18, 1.0020 + g, 1.0030 + g, 1.0010 + g, 1.0025 + g),
            mk(19, 1.0025 + g, 1.0035 + g, 1.0020 + g, 1.0030 + g),
        ]
        return candles

    def test_micro_gap_filtered_by_min_gap_atr(self):
        candles = self._gap_candles(gap_size=0.0003)  # 3 pips < 0.5 × ATR
        fvgs = self._detect(candles, min_gap_pips=0.0, min_gap_atr=0.5)
        assert fvgs == []

    def test_valid_gap_passes_and_reports_gap_size_atr(self):
        candles = self._gap_candles(gap_size=0.0015)  # 15 pips ≈ 1 × ATR
        fvgs = self._detect(candles, min_gap_pips=0.0, min_gap_atr=0.5)
        assert len(fvgs) >= 1
        assert all(d.details["gap_size_atr"] is not None and d.details["gap_size_atr"] >= 0.5 for d in fvgs)

    def test_default_params_keep_historical_behavior(self):
        candles = self._gap_candles(gap_size=0.0003)  # 3 pips
        # Seuil fixe désactivé et pas de filtre ATR → détecté comme avant Phase 3.
        fvgs = self._detect(candles, min_gap_pips=0.0)
        assert len(fvgs) >= 1


class TestOrderBlockQuality:
    """3B — OB géant filtré + confirmation par displacement."""

    def _detect(self, candles, **kwargs):
        detector = OrderBlockDetector(**kwargs)
        return [d for d in detector.detect(candles) if d.concept.value == "order_block"]

    def _ob_candles(self, ob_range):
        """Historique plat (ATR ~10 pips) puis OB baissier + impulse haussier."""
        candles = flat_candles(16)
        candles += [
            mk(16, 1.0020, 1.0022, 1.0010, 1.0012),
            mk(17, 1.0012, 1.0014, 1.0010 - ob_range, 1.0011 - ob_range * 0.5),
            mk(18, 1.0012, 1.0032, 1.0011, 1.0030),  # corps ~18 pips (displacement)
            mk(19, 1.0025, 1.0035, 1.0022, 1.0032),
        ]
        return candles

    def _obs(self, candles):
        return [d for d in candles if d.concept.value == "order_block"]

    def test_oversized_ob_filtered(self):
        candles = self._ob_candles(ob_range=0.0060)  # OB de ~64 pips vs ATR ~14 pips
        obs = self._detect(candles, max_ob_atr_mult=3.0)
        assert self._obs(obs) == []

    def test_normal_ob_passes(self):
        candles = self._ob_candles(ob_range=0.0015)  # OB ~19 pips < 3 × ATR
        obs = self._detect(candles, max_ob_atr_mult=3.0)
        assert len(self._obs(obs)) == 1

    def test_displacement_confirmation_required(self):
        """displacement_confirmation_bars=2 : deux corps directionnels cumulés exigés."""
        candles = self._ob_candles(ob_range=0.0015)
        # Remplace la bougie 19 par une bougie indécise (petit corps baissier).
        candles[19] = mk(19, 1.0025, 1.0027, 1.0020, 1.0022)
        obs = self._detect(candles, displacement_confirmation_bars=2)
        assert self._obs(obs) == []
        # Comportement historique (1 barre) : détecté.
        obs_hist = self._detect(candles)
        assert len(self._obs(obs_hist)) == 1


class TestDisplacementHelper:
    """3B — helpers.is_displacement (ATR-relatif)."""

    def test_small_candle_is_not_displacement(self):
        candles = baseline_candles() + [mk(15, 1.0015, 1.0021, 1.0013, 1.0019)]
        atr = calculate_atr(candles, period=14)
        assert is_displacement(candles, 15, atr, body_atr_mult=1.0, range_atr_mult=1.0) is False

    def test_impulse_candle_is_displacement(self):
        candles = baseline_candles() + [mk(15, 0.9988, 1.0014, 0.9985, 1.0012)]
        atr = calculate_atr(candles, period=14)
        assert is_displacement(candles, 15, atr, body_atr_mult=0.8, range_atr_mult=0.8) is True

    def test_directional_close_location_required(self):
        candles = baseline_candles() + [mk(15, 1.0012, 1.0042, 1.0010, 1.0014)]  # mèche haute
        atr = calculate_atr(candles, period=14)
        assert is_displacement(candles, 15, atr, body_atr_mult=0.5, range_atr_mult=0.5, direction="bullish") is False


class TestSetupClassifier:
    """3D — classification sans look-ahead."""

    def test_liquidity_sweep_reversal(self):
        smc = [
            {"concept": "liquidity_sweep", "direction": "bullish", "index": 5},
            {"concept": "order_block", "direction": "bullish", "index": 6},
        ]
        assert classify_setup_type("order_block", "bullish", smc, zone_index=7) == "LIQUIDITY_SWEEP_REVERSAL"

    def test_reversal_via_choch(self):
        smc = [{"concept": "choch", "direction": "bullish", "index": 5}]
        assert classify_setup_type("order_block", "bullish", smc, zone_index=7) == "REVERSAL"

    def test_fvg_retrace(self):
        smc = [{"concept": "fair_value_gap", "direction": "bullish", "index": 6}]
        assert classify_setup_type("fair_value_gap", "bullish", smc, zone_index=7) == "FVG_RETRACE"

    def test_order_block_retrace(self):
        assert classify_setup_type("order_block", "bullish", [], zone_index=7) == "ORDER_BLOCK_RETRACE"

    def test_no_look_ahead(self):
        """Un sweep postérieur à la zone ne doit pas changer la classification."""
        smc = [
            {"concept": "order_block", "direction": "bullish", "index": 6},
            {"concept": "liquidity_sweep", "direction": "bullish", "index": 20},
        ]
        assert classify_setup_type("order_block", "bullish", smc, zone_index=7) == "ORDER_BLOCK_RETRACE"

    def test_opposite_direction_events_ignored(self):
        smc = [{"concept": "liquidity_sweep", "direction": "bearish", "index": 5}]
        assert classify_setup_type("order_block", "bullish", smc, zone_index=7) == "ORDER_BLOCK_RETRACE"

    def test_all_types_in_catalog(self):
        assert set(SETUP_TYPES) == {
            "CONTINUATION", "REVERSAL", "LIQUIDITY_SWEEP_REVERSAL",
            "FVG_RETRACE", "ORDER_BLOCK_RETRACE",
        }


class TestValidatedSetupFamilies:
    def test_bos_retest_continuation_requires_h4_h1_alignment(self):
        result = qualify_setup_type(
            direction="bullish",
            smc_data=[{"concept": "bos", "direction": "bullish", "index": 4}],
            zone_index=7,
            h4_trend="bullish",
            h1_trend="bullish",
            m5_confirmed=True,
            m5_retested=True,
        )
        assert result.setup_type is SetupType.BOS_RETEST_CONTINUATION
        assert "h4_h1_aligned" in result.evidence

    def test_sweep_reversal_requires_reentry_and_follow_through(self):
        result = qualify_setup_type(
            direction="bullish",
            smc_data=[
                {
                    "concept": "liquidity_sweep",
                    "direction": "bullish",
                    "index": 4,
                    "details": {"swept_level": 2000.0, "rejection_ratio": 0.8},
                },
                {"concept": "choch", "direction": "bullish", "index": 5},
            ],
            zone_index=7,
            h4_trend="bearish",
            h1_trend="bullish",
            m5_confirmed=True,
            m5_retested=True,
        )
        assert result.setup_type is SetupType.SWEEP_REVERSAL

    def test_choch_reversal_requires_prior_opposite_structure(self):
        result = qualify_setup_type(
            direction="bullish",
            smc_data=[
                {"concept": "bos", "direction": "bearish", "index": 2},
                {"concept": "choch", "direction": "bullish", "index": 5},
            ],
            zone_index=7,
            h4_trend="bearish",
            h1_trend="bullish",
            m5_confirmed=True,
            m5_retested=True,
        )
        assert result.setup_type is SetupType.CHOCH_REVERSAL

    @pytest.mark.parametrize(
        ("m5_confirmed", "m5_retested", "reason"),
        [
            (False, True, "m5_confirmation_missing"),
            (True, False, "zone_not_retested_after_creation"),
        ],
    )
    def test_missing_m5_evidence_is_no_trade(self, m5_confirmed, m5_retested, reason):
        result = qualify_setup_type(
            direction="bullish",
            smc_data=[{"concept": "bos", "direction": "bullish", "index": 4}],
            zone_index=7,
            h4_trend="bullish",
            h1_trend="bullish",
            m5_confirmed=m5_confirmed,
            m5_retested=m5_retested,
        )
        assert result.setup_type is SetupType.NO_TRADE
        assert result.reasons == (reason,)

    def test_post_zone_structure_cannot_validate_setup(self):
        result = qualify_setup_type(
            direction="bullish",
            smc_data=[{"concept": "bos", "direction": "bullish", "index": 8}],
            zone_index=7,
            h4_trend="bullish",
            h1_trend="bullish",
            m5_confirmed=True,
            m5_retested=True,
        )
        assert result.setup_type is SetupType.NO_TRADE
        assert result.reasons == ("no_coherent_structure_family",)


class TestInstrumentProfileWiring:
    """3F — profil XAUUSD et exposition des paramètres Phase 3."""

    def test_xauusd_profile_values(self):
        s = Settings()
        profile = s.get_instrument_profile("XAUUSD")
        assert profile is not None
        assert profile.min_fvg_atr == 0.25
        assert profile.sweep_min_rejection_ratio == 0.5
        assert profile.sweep_displacement_atr_mult == 1.0
        assert profile.max_ob_atr_mult == 3.0
        assert profile.displacement_confirmation_bars == 2

    def test_eurusd_profile_keeps_defaults(self):
        s = Settings()
        profile = s.get_instrument_profile("EURUSD")
        assert profile is not None
        assert profile.min_fvg_atr == 0.0
        assert profile.sweep_min_rejection_ratio == 0.0
        assert profile.displacement_confirmation_bars == 1  # comportement historique

    def test_timeframe_settings(self):
        s = Settings()
        assert s.htf_timeframe == TimeFrame.H1
        assert s.context_timeframe == TimeFrame.H4
        assert s.setup_timeframe == TimeFrame.M5
        assert s.entry_timeframe == TimeFrame.M5

    def test_detector_params_exist(self):
        """Les sous-détecteurs exposent bien les nouveaux paramètres Phase 3."""
        detector = SMCDetector()
        liq = detector.detectors["liquidity"]
        fvg = detector.detectors["fair_value_gap"]
        ob = detector.detectors["order_blocks"]
        assert hasattr(liq, "_min_rejection_ratio")
        assert hasattr(liq, "_displacement_atr_mult")
        assert hasattr(fvg, "_min_gap_atr")
        assert hasattr(ob, "_max_ob_atr_mult")
        assert hasattr(ob, "_displacement_confirmation_bars")
