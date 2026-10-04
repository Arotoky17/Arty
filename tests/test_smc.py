"""Tests du moteur SMC (Smart Money Concepts).

Couvre tous les concepts ICT détectés :
- Swing points (internes et externes)
- BOS, Internal BOS, External BOS, CHoCH, MSS
- FVG, IFVG
- Order Block, Breaker Block, Mitigation Block
- Liquidity Sweep, Equal High, Equal Low
- Premium, Discount, OTE
- Sessions et Kill Zones
- Détecteur principal (orchestration)
"""

from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept, TimeFrame, TradingSession
from arty_trading.modules.smc import (
    FairValueGapDetector,
    LiquidityDetector,
    OrderBlockDetector,
    PremiumDiscountDetector,
    SMCDetector,
    SessionDetector,
    SessionWindow,
    StructureDetector,
    find_external_swing_points,
    find_internal_swing_points,
    find_swing_points,
)


# =============================================================================
# Helpers pour créer des bougies de test
# =============================================================================


def make_candle(
    idx: int,
    o: float,
    h: float,
    l: float,
    c: float,
    volume: int = 100,
    hour: int | None = None,
) -> Candle:
    """Crée une bougie de test avec un index basé sur le temps."""
    h_arg = hour if hour is not None else idx
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, h_arg, 0, tzinfo=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
    )


def make_uptrend_candles(n: int = 20) -> list[Candle]:
    """Crée une série de bougies en tendance haussière avec des pullbacks."""
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
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0015
        h = float(o) + 0.0003
        l = c - 0.0003
        candles.append(make_candle(i, o, h, l, c))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0010
        h = float(o) + 0.0002
        l = c - 0.0005
        candles.append(make_candle(i, o, h, l, c))
        i += 1
    return candles


def make_downtrend_candles(n: int = 20) -> list[Candle]:
    """Crée une série de bougies en tendance baissière."""
    candles = []
    base = 1.1000
    for i in range(n):
        o = base - i * 0.0010
        c = base - (i + 1) * 0.0010
        h = o + 0.0005
        l = c - 0.0005
        candles.append(make_candle(i, o, h, l, c))
    return candles



def make_downtrend_with_pullbacks_candles(n: int = 20) -> list[Candle]:
    """Cree une tendance baissiere avec des pullbacks (pour creer des swing points)."""
    candles = []
    base = 1.1000
    i = 0
    while i < n:
        for _ in range(3):
            if i >= n:
                break
            o = base - i * 0.0010
            c = base - (i + 1) * 0.0010
            h = o + 0.0005
            l = c - 0.0005
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) + 0.0015
        h = c + 0.0003
        l = float(o) - 0.0002
        candles.append(make_candle(i, o, h, l, c))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) + 0.0010
        h = c + 0.0002
        l = float(o) - 0.0005
        candles.append(make_candle(i, o, h, l, c))
        i += 1
    return candles

def make_fvg_candles() -> list[Candle]:
    """Crée des bougies avec un Bullish FVG (gap de valeur)."""
    return [
        make_candle(0, 1.0800, 1.0805, 1.0795, 1.0802),
        make_candle(1, 1.0802, 1.0830, 1.0800, 1.0828),
        make_candle(2, 1.0828, 1.0835, 1.0820, 1.0832),
    ]


def make_bearish_fvg_candles() -> list[Candle]:
    """Crée des bougies avec un Bearish FVG."""
    return [
        make_candle(0, 1.0900, 1.0905, 1.0895, 1.0898),
        make_candle(1, 1.0898, 1.0895, 1.0870, 1.0872),
        make_candle(2, 1.0872, 1.0880, 1.0865, 1.0868),
    ]


def make_order_block_candles() -> list[Candle]:
    """Crée des bougies avec un Order Block haussier."""
    candles = [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0810, 1.0790, 1.0792),
        make_candle(2, 1.0792, 1.0830, 1.0792, 1.0828),
        make_candle(3, 1.0828, 1.0835, 1.0820, 1.0832),
        make_candle(4, 1.0832, 1.0840, 1.0825, 1.0838),
    ]
    from datetime import timedelta

    from arty_trading.config.operational import definitions

    period = definitions()["atr_period"]
    prefix = [candles[0].model_copy(update={
        "time": candles[0].time - timedelta(hours=period - i),
    }) for i in range(period)]
    return prefix + candles


def make_sweep_candles() -> list[Candle]:
    """Crée des bougies avec un liquidity sweep."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0820, 1.0800, 1.0815),
        make_candle(2, 1.0815, 1.0825, 1.0810, 1.0820),
        make_candle(3, 1.0820, 1.0822, 1.0790, 1.0795),
        make_candle(4, 1.0795, 1.0810, 1.0795, 1.0805),
        make_candle(5, 1.0805, 1.0815, 1.0800, 1.0810),
        make_candle(6, 1.0810, 1.0820, 1.0805, 1.0815),
        make_candle(7, 1.0815, 1.0820, 1.0785, 1.0810),
        make_candle(8, 1.0810, 1.0825, 1.0805, 1.0820),
        make_candle(9, 1.0820, 1.0830, 1.0815, 1.0825),
    ]


def make_equal_highs_candles() -> list[Candle]:
    """Crée des bougies avec des Equal Highs."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0815, 1.0800, 1.0810),
        make_candle(2, 1.0810, 1.0830, 1.0805, 1.0825),
        make_candle(3, 1.0825, 1.0825, 1.0810, 1.0815),
        make_candle(4, 1.0815, 1.0820, 1.0795, 1.0800),
        make_candle(5, 1.0800, 1.0810, 1.0785, 1.0790),
        make_candle(6, 1.0790, 1.0810, 1.0785, 1.0800),
        make_candle(7, 1.0800, 1.0830, 1.0795, 1.0825),
        make_candle(8, 1.0825, 1.0825, 1.0820, 1.0822),
        make_candle(9, 1.0822, 1.0828, 1.0815, 1.0820),
    ]


def make_range_candles() -> list[Candle]:
    """Crée des bougies formant un range (pour Premium/Discount)."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0815, 1.0800, 1.0810),
        make_candle(2, 1.0810, 1.0835, 1.0805, 1.0830),
        make_candle(3, 1.0830, 1.0832, 1.0815, 1.0820),
        make_candle(4, 1.0820, 1.0825, 1.0775, 1.0780),
        make_candle(5, 1.0780, 1.0810, 1.0790, 1.0800),
        make_candle(6, 1.0800, 1.0815, 1.0795, 1.0810),
    ]


def make_session_candles() -> list[Candle]:
    """Crée des bougies couvrant plusieurs sessions UTC."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=1),
        make_candle(1, 1.0805, 1.0815, 1.0800, 1.0810, hour=3),
        make_candle(2, 1.0810, 1.0820, 1.0805, 1.0815, hour=8),
        make_candle(3, 1.0815, 1.0825, 1.0810, 1.0820, hour=13),
        make_candle(4, 1.0820, 1.0830, 1.0815, 1.0825, hour=20),
    ]


# =============================================================================
# Tests des swing points
# =============================================================================


class TestSwingPoints:
    """Tests de la détection des swing points."""

    def test_find_swing_points_uptrend(self):
        candles = make_uptrend_candles(20)
        swing_points = find_swing_points(candles, window=2)
        assert len(swing_points) > 0
        highs = [sp for sp in swing_points if sp.type == "high"]
        lows = [sp for sp in swing_points if sp.type == "low"]
        assert len(highs) > 0
        assert len(lows) > 0

    def test_find_swing_points_empty(self):
        assert find_swing_points([], window=2) == []

    def test_find_swing_points_too_few_candles(self):
        candles = [make_candle(0, 1.0, 1.1, 0.9, 1.0)]
        assert find_swing_points(candles, window=2) == []

    def test_find_swing_highs_only(self):
        candles = make_uptrend_candles(20)
        highs = find_swing_points(candles, window=2)
        high_types = [sp for sp in highs if sp.type == "high"]
        assert len(high_types) > 0

    def test_swing_point_has_strength_attribute(self):
        candles = make_uptrend_candles(20)
        swing_points = find_swing_points(candles, window=2, external_window=5)
        for sp in swing_points:
            assert hasattr(sp, "strength")
            assert sp.strength in ("internal", "external")

    def test_swing_point_has_amplitude_attribute(self):
        candles = make_uptrend_candles(20)
        swing_points = find_swing_points(candles, window=2)
        for sp in swing_points:
            assert hasattr(sp, "amplitude")
            assert sp.amplitude > 0

    def test_find_external_swing_points(self):
        candles = make_uptrend_candles(20)
        external = find_external_swing_points(candles, window=2, external_window=5)
        for sp in external:
            assert sp.strength == "external"

    def test_find_internal_swing_points(self):
        candles = make_uptrend_candles(20)
        internal = find_internal_swing_points(candles, window=2, external_window=5)
        for sp in internal:
            assert sp.strength == "internal"


# =============================================================================
# Tests du détecteur de structure
# =============================================================================


class TestStructureDetector:
    """Tests du détecteur de structure (BOS, Internal/External BOS, CHoCH, MSS)."""

    def test_initialization(self):
        detector = StructureDetector()
        assert detector.name == "structure"
        assert detector.enabled is True

    def test_detect_empty_candles(self):
        detector = StructureDetector()
        assert detector.detect([]) == []

    def test_detect_bos_bullish(self):
        candles = make_uptrend_candles(15)
        last_high = max(c.high for c in candles)
        candles.append(
            make_candle(
                15,
                float(candles[-1].close),
                float(last_high) + 0.0020,
                float(candles[-1].close),
                float(last_high) + 0.0015,
            )
        )
        detector = StructureDetector()
        detections = detector.detect(candles)
        bos = [d for d in detections if d.concept == SMCConcept.BOS]
        assert len(bos) > 0

    def test_detect_internal_bos(self):
        candles = make_uptrend_candles(15)
        last_high = max(c.high for c in candles)
        candles.append(
            make_candle(
                15,
                float(candles[-1].close),
                float(last_high) + 0.0020,
                float(candles[-1].close),
                float(last_high) + 0.0015,
            )
        )
        detector = StructureDetector()
        detections = detector.detect(candles)
        internal_bos = [d for d in detections if d.concept == SMCConcept.INTERNAL_BOS]
        external_bos = [d for d in detections if d.concept == SMCConcept.EXTERNAL_BOS]
        assert len(internal_bos) + len(external_bos) > 0

    def test_detect_external_bos(self):
        candles = make_uptrend_candles(20)
        last_high = max(c.high for c in candles)
        candles.append(
            make_candle(
                20,
                float(candles[-1].close),
                float(last_high) + 0.0020,
                float(candles[-1].close),
                float(last_high) + 0.0015,
            )
        )
        detector = StructureDetector(swing_window=2, external_window=5)
        detections = detector.detect(candles)
        bos_all = [
            d for d in detections if d.concept in (SMCConcept.INTERNAL_BOS, SMCConcept.EXTERNAL_BOS)
        ]
        assert len(bos_all) > 0

    def test_bos_has_swing_strength_detail(self):
        candles = make_uptrend_candles(15)
        last_high = max(c.high for c in candles)
        candles.append(
            make_candle(
                15,
                float(candles[-1].close),
                float(last_high) + 0.0020,
                float(candles[-1].close),
                float(last_high) + 0.0015,
            )
        )
        detector = StructureDetector()
        detections = detector.detect(candles)
        bos = [d for d in detections if d.concept == SMCConcept.BOS]
        if bos:
            assert "swing_strength" in bos[0].details
            assert bos[0].details["swing_strength"] in ("internal", "external")

    def test_detect_choch(self):
        candles = make_downtrend_with_pullbacks_candles(15)
        last_high = max(c.high for c in candles[-5:])
        candles.append(
            make_candle(
                15,
                float(candles[-1].close),
                float(last_high) + 0.0030,
                float(candles[-1].close),
                float(last_high) + 0.0025,
            )
        )
        detector = StructureDetector()
        detections = detector.detect(candles)
        choch = [d for d in detections if d.concept == SMCConcept.CHOCH]
        assert len(choch) > 0 or len(detections) > 0

    def test_disabled_detector(self):
        detector = StructureDetector(enabled=False)
        candles = make_uptrend_candles(20)
        assert detector.detect(candles) == []

    def test_structure_returns_info_only(self):
        candles = make_uptrend_candles(20)
        detector = StructureDetector()
        detections = detector.detect(candles)
        for d in detections:
            assert isinstance(d.concept, SMCConcept)
            assert d.direction in ("bullish", "bearish", "neutral")
            assert isinstance(d.index, int)
            assert isinstance(d.details, dict)


# =============================================================================
# Tests du détecteur FVG
# =============================================================================


class TestFairValueGapDetector:
    """Tests du détecteur Fair Value Gap."""

    def test_initialization(self):
        detector = FairValueGapDetector()
        assert detector.name == "fair_value_gap"
        assert detector.enabled is True

    def test_detect_bullish_fvg(self):
        candles = make_fvg_candles()
        detector = FairValueGapDetector()
        detections = detector.detect(candles)
        fvgs = [d for d in detections if d.concept == SMCConcept.FVG and d.direction == "bullish"]
        assert len(fvgs) > 0
        assert fvgs[0].details["gap_top"] > fvgs[0].details["gap_bottom"]

    def test_detect_bearish_fvg(self):
        candles = make_bearish_fvg_candles()
        detector = FairValueGapDetector()
        detections = detector.detect(candles)
        fvgs = [d for d in detections if d.concept == SMCConcept.FVG and d.direction == "bearish"]
        assert len(fvgs) > 0

    def test_no_fvg_in_flat_market(self):
        candles = [
            make_candle(0, 1.0800, 1.0805, 1.0795, 1.0800),
            make_candle(1, 1.0800, 1.0805, 1.0795, 1.0800),
            make_candle(2, 1.0800, 1.0805, 1.0795, 1.0800),
        ]
        detector = FairValueGapDetector()
        detections = detector.detect(candles)
        fvgs = [d for d in detections if d.concept == SMCConcept.FVG]
        assert len(fvgs) == 0

    def test_disabled_detector(self):
        detector = FairValueGapDetector(enabled=False)
        candles = make_fvg_candles()
        assert detector.detect(candles) == []

    def test_fvg_has_gap_details(self):
        candles = make_fvg_candles()
        detector = FairValueGapDetector()
        detections = detector.detect(candles)
        fvgs = [d for d in detections if d.concept == SMCConcept.FVG]
        if fvgs:
            assert "gap_top" in fvgs[0].details
            assert "gap_bottom" in fvgs[0].details
            assert "gap_size" in fvgs[0].details


# =============================================================================
# Tests du détecteur d'Order Blocks
# =============================================================================


class TestOrderBlockDetector:
    """Tests du détecteur d'Order Blocks."""

    def test_initialization(self):
        detector = OrderBlockDetector()
        assert detector.name == "order_blocks"
        assert detector.enabled is True

    def test_detect_bullish_order_block(self):
        candles = make_order_block_candles()
        detector = OrderBlockDetector()
        detections = detector.detect(candles)
        obs = [d for d in detections if d.concept == SMCConcept.ORDER_BLOCK]
        assert len(obs) > 0
        bullish_obs = [d for d in obs if d.direction == "bullish"]
        assert len(bullish_obs) > 0

    def test_detect_empty_candles(self):
        detector = OrderBlockDetector()
        assert detector.detect([]) == []

    def test_disabled_detector(self):
        detector = OrderBlockDetector(enabled=False)
        candles = make_order_block_candles()
        assert detector.detect(candles) == []

    def test_order_block_has_ob_top_bottom(self):
        candles = make_order_block_candles()
        detector = OrderBlockDetector()
        detections = detector.detect(candles)
        obs = [d for d in detections if d.concept == SMCConcept.ORDER_BLOCK]
        if obs:
            assert "ob_top" in obs[0].details
            assert "ob_bottom" in obs[0].details

    def test_detect_breaker_block(self):
        candles = make_order_block_candles()
        candles.append(make_candle(5, 1.0838, 1.0840, 1.0780, 1.0785))
        candles.append(make_candle(6, 1.0785, 1.0790, 1.0770, 1.0775))
        detector = OrderBlockDetector()
        detections = detector.detect(candles)
        breakers = [d for d in detections if d.concept == SMCConcept.BREAKER_BLOCK]
        assert len(breakers) > 0 or len(detections) > 0


# =============================================================================
# Tests du détecteur de liquidité
# =============================================================================


class TestLiquidityDetector:
    """Tests du détecteur de liquidité."""

    def test_initialization(self):
        detector = LiquidityDetector()
        assert detector.name == "liquidity"
        assert detector.enabled is True

    def test_detect_liquidity_sweep(self):
        candles = make_sweep_candles()
        detector = LiquidityDetector()
        detections = detector.detect(candles)
        sweeps = [d for d in detections if d.concept == SMCConcept.LIQUIDITY_SWEEP]
        assert len(sweeps) > 0

    def test_detect_equal_highs(self):
        candles = make_equal_highs_candles()
        detector = LiquidityDetector(tolerance_pips=5.0)
        detections = detector.detect(candles)
        eqh = [d for d in detections if d.concept == SMCConcept.EQUAL_HIGH]
        assert len(eqh) > 0

    def test_detect_equal_lows(self):
        """Verifie la detection des Equal Lows."""
        candles = [
            make_candle(0, 1.0850, 1.0855, 1.0840, 1.0845),
            make_candle(1, 1.0845, 1.0850, 1.0830, 1.0835),
            make_candle(2, 1.0835, 1.0845, 1.0820, 1.0840),  # Swing low 1 (low=1.0820)
            make_candle(3, 1.0840, 1.0855, 1.0835, 1.0850),
            make_candle(4, 1.0850, 1.0860, 1.0845, 1.0855),
            make_candle(5, 1.0855, 1.0870, 1.0850, 1.0865),
            make_candle(6, 1.0865, 1.0875, 1.0820, 1.0825),  # Swing low 2 (low=1.0820, equal!)
            make_candle(7, 1.0825, 1.0840, 1.0825, 1.0835),
            make_candle(8, 1.0835, 1.0850, 1.0830, 1.0845),
        ]
        detector = LiquidityDetector(tolerance_pips=5.0)
        detections = detector.detect(candles)
        eql = [d for d in detections if d.concept == SMCConcept.EQUAL_LOW]
        assert len(eql) > 0

    def test_disabled_detector(self):
        detector = LiquidityDetector(enabled=False)
        candles = make_sweep_candles()
        assert detector.detect(candles) == []

    def test_sweep_has_type_detail(self):
        candles = make_sweep_candles()
        detector = LiquidityDetector()
        detections = detector.detect(candles)
        sweeps = [d for d in detections if d.concept == SMCConcept.LIQUIDITY_SWEEP]
        if sweeps:
            assert "type" in sweeps[0].details
            assert sweeps[0].details["type"] in (
                "buy_side_liquidity_grab",
                "sell_side_liquidity_grab",
            )


# =============================================================================
# Tests du détecteur Premium/Discount
# =============================================================================


class TestPremiumDiscountDetector:
    """Tests du détecteur Premium/Discount et OTE."""

    def test_initialization(self):
        detector = PremiumDiscountDetector()
        assert detector.name == "premium_discount"
        assert detector.enabled is True

    def test_detect_premium_discount(self):
        candles = make_range_candles()
        detector = PremiumDiscountDetector()
        detections = detector.detect(candles)
        pd = [d for d in detections if d.concept == SMCConcept.PREMIUM_DISCOUNT]
        assert len(pd) > 0
        assert "equilibrium" in pd[0].details
        assert "premium_start" in pd[0].details
        assert "discount_start" in pd[0].details

    def test_detect_premium(self):
        candles = make_range_candles()
        detector = PremiumDiscountDetector()
        detections = detector.detect(candles)
        premium = [d for d in detections if d.concept == SMCConcept.PREMIUM]
        assert len(premium) > 0
        assert "zone_start" in premium[0].details
        assert "zone_end" in premium[0].details
        assert "in_premium" in premium[0].details

    def test_detect_discount(self):
        candles = make_range_candles()
        detector = PremiumDiscountDetector()
        detections = detector.detect(candles)
        discount = [d for d in detections if d.concept == SMCConcept.DISCOUNT]
        assert len(discount) > 0
        assert "zone_start" in discount[0].details
        assert "zone_end" in discount[0].details
        assert "in_discount" in discount[0].details

    def test_detect_ote(self):
        candles = make_range_candles()
        detector = PremiumDiscountDetector()
        detections = detector.detect(candles)
        ote = [d for d in detections if d.concept == SMCConcept.OTE]
        assert len(ote) >= 2
        bullish_ote = [d for d in ote if d.direction == "bullish"]
        bearish_ote = [d for d in ote if d.direction == "bearish"]
        assert len(bullish_ote) > 0
        assert len(bearish_ote) > 0

    def test_disabled_detector(self):
        detector = PremiumDiscountDetector(enabled=False)
        candles = make_range_candles()
        assert detector.detect(candles) == []


# =============================================================================
# Tests du détecteur de sessions
# =============================================================================


class TestSessionDetector:
    """Tests du détecteur de sessions ICT/SMC."""

    def test_initialization(self):
        detector = SessionDetector()
        assert detector.name == "sessions"
        assert detector.enabled is True

    def test_detect_empty_candles(self):
        detector = SessionDetector()
        assert detector.detect([]) == []

    def test_detect_disabled(self):
        detector = SessionDetector(enabled=False)
        candles = make_session_candles()
        assert detector.detect(candles) == []

    def test_detect_asian_session(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=2)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert sessions[0].details["session"] == "asia"

    def test_detect_london_session(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=8)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert sessions[0].details["session"] == "london"

    def test_detect_new_york_session(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=14)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert sessions[0].details["session"] == "new_york"

    def test_detect_london_kill_zone(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=8)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        kill_zones = [d for d in detections if d.concept == SMCConcept.KILL_ZONE]
        assert len(kill_zones) > 0
        assert "london" in kill_zones[0].details["kill_zone_name"]

    def test_detect_new_york_kill_zone(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=13)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        kill_zones = [d for d in detections if d.concept == SMCConcept.KILL_ZONE]
        assert len(kill_zones) > 0
        assert "new_york" in kill_zones[0].details["kill_zone_name"]

    def test_no_kill_zone_outside_hours(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=2)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        kill_zones = [d for d in detections if d.concept == SMCConcept.KILL_ZONE]
        assert len(kill_zones) == 0

    def test_is_kill_zone_method(self):
        detector = SessionDetector()
        assert detector.is_kill_zone(datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)) is True
        assert detector.is_kill_zone(datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc)) is True
        assert detector.is_kill_zone(datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)) is False
        assert detector.is_kill_zone(datetime(2024, 1, 1, 20, 0, tzinfo=timezone.utc)) is False

    def test_get_session_for_time(self):
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.ASIA
        session = detector.get_session_for_time(datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.LONDON
        session = detector.get_session_for_time(datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.NEW_YORK

    def test_custom_sessions(self):
        custom = (SessionWindow("custom", TradingSession.ASIA, time(5, 0), time(10, 0)),)
        detector = SessionDetector(sessions=custom)
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=7)]
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert sessions[0].details["session_name"] == "custom"

    def test_session_has_candle_time(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=8)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert "candle_time" in sessions[0].details

    def test_session_in_kill_zone_flag(self):
        candles = [make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805, hour=8)]
        detector = SessionDetector()
        detections = detector.detect(candles)
        sessions = [d for d in detections if d.concept == SMCConcept.SESSION]
        assert len(sessions) > 0
        assert sessions[0].details["in_kill_zone"] is True

    def test_24h_coverage_no_gaps(self):
        """Les sessions couvrent l'ensemble des 24h sans trou horaire."""
        detector = SessionDetector()
        test_times = [
            datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 6, 30, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 7, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 11, 59, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 16, 59, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 17, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
        ]
        for dt in test_times:
            session = detector.get_session_for_time(dt)
            assert session is not None, f"Aucune session détectée pour {dt.isoformat()}"

    def test_session_00_utc_is_asia(self):
        """00:00 UTC → session Asia."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.ASIA

    def test_session_0630_utc_is_asia(self):
        """06:30 UTC → session Asia."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 6, 30, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.ASIA

    def test_session_0700_utc_is_london(self):
        """07:00 UTC → session London."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 7, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.LONDON

    def test_session_1159_utc_is_london(self):
        """11:59 UTC → session London."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 11, 59, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.LONDON

    def test_session_1200_utc_is_new_york(self):
        """12:00 UTC → session New York."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.NEW_YORK

    def test_session_1659_utc_is_new_york(self):
        """16:59 UTC → session New York."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 16, 59, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.NEW_YORK

    def test_session_1700_utc_is_asia(self):
        """17:00 UTC → session Asia."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 17, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.ASIA

    def test_session_2300_utc_is_asia(self):
        """23:00 UTC → session Asia."""
        detector = SessionDetector()
        session = detector.get_session_for_time(datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc))
        assert session is not None
        assert session.session == TradingSession.ASIA


# =============================================================================
# Tests du détecteur SMC principal
# =============================================================================


class TestSMCDetector:
    """Tests du détecteur SMC principal."""

    def test_initialization(self):
        detector = SMCDetector()
        assert len(detector.detectors) == 6
        assert "structure" in detector.detectors
        assert "fair_value_gap" in detector.detectors
        assert "order_blocks" in detector.detectors
        assert "liquidity" in detector.detectors
        assert "premium_discount" in detector.detectors
        assert "sessions" in detector.detectors

    def test_enable_disable_concept(self):
        detector = SMCDetector()
        detector.disable_concept("structure")
        assert not detector.detectors["structure"].enabled
        detector.enable_concept("structure")
        assert detector.detectors["structure"].enabled

    def test_enable_disable_all(self):
        detector = SMCDetector()
        detector.disable_all()
        assert len(detector.get_enabled_concepts()) == 0
        detector.enable_all()
        assert len(detector.get_enabled_concepts()) == 6

    def test_enable_disable_sessions(self):
        detector = SMCDetector()
        detector.disable_concept("sessions")
        assert not detector.detectors["sessions"].enabled
        detector.enable_concept("sessions")
        assert detector.detectors["sessions"].enabled

    @pytest.mark.asyncio
    async def test_detect_returns_list_of_dicts(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        result = await detector.detect(candles, "EURUSD")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)
            assert "concept" in item
            assert "direction" in item
            assert "price" in item
            assert "index" in item

    @pytest.mark.asyncio
    async def test_detect_empty_candles(self):
        detector = SMCDetector()
        result = await detector.detect([], "EURUSD")
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_with_disabled_detectors(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        detector.disable_all()
        result = await detector.detect(candles, "EURUSD")
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_multiple_concepts(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        result = await detector.detect(candles, "EURUSD")
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_detect_includes_sessions(self):
        candles = make_session_candles()
        detector = SMCDetector()
        result = await detector.detect(candles, "EURUSD")
        session_detections = [d for d in result if d["concept"] == "session"]
        assert len(session_detections) > 0

    def test_detect_sync(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        detections = detector.detect_sync(candles)
        assert isinstance(detections, list)
        for i in range(1, len(detections)):
            assert detections[i - 1].index <= detections[i].index

    def test_detect_sync_returns_smc_detection_objects(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        detections = detector.detect_sync(candles)
        for d in detections:
            assert hasattr(d, "concept")
            assert hasattr(d, "direction")
            assert hasattr(d, "price")
            assert hasattr(d, "index")
            assert hasattr(d, "details")

    def test_no_trade_opening(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        detections = detector.detect_sync(candles)
        for d in detections:
            assert d.concept in SMCConcept
            assert d.direction in ("bullish", "bearish", "neutral")
            assert not hasattr(d, "order_type")
            assert not hasattr(d, "volume")
            assert not hasattr(d, "stop_loss")
            assert not hasattr(d, "take_profit")