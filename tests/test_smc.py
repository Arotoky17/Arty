"""Tests du moteur SMC (Smart Money Concepts)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept, TimeFrame
from arty_trading.modules.smc import (
    FairValueGapDetector,
    LiquidityDetector,
    OrderBlockDetector,
    PremiumDiscountDetector,
    SMCDetector,
    StructureDetector,
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
) -> Candle:
    """Crée une bougie de test avec un index basé sur le temps."""
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, idx, 0, tzinfo=timezone.utc),
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
        # Phase de hausse (3 bougies)
        for _ in range(3):
            if i >= n:
                break
            o = base + i * 0.0010
            c = base + (i + 1) * 0.0010
            h = c + 0.0008
            l = o - 0.0002
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        # Pullback baissier (2 bougies) — crée un swing high
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
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0810, 1.0790, 1.0792),
        make_candle(2, 1.0792, 1.0830, 1.0792, 1.0828),
        make_candle(3, 1.0828, 1.0835, 1.0820, 1.0832),
        make_candle(4, 1.0832, 1.0840, 1.0825, 1.0838),
    ]


def make_sweep_candles() -> list[Candle]:
    """Crée des bougies avec un liquidity sweep."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0820, 1.0800, 1.0815),
        make_candle(2, 1.0815, 1.0825, 1.0810, 1.0820),
        make_candle(3, 1.0820, 1.0822, 1.0790, 1.0795),  # Swing low (low=1.0790)
        make_candle(4, 1.0795, 1.0810, 1.0795, 1.0805),  # low=1.0795 > 1.0790
        make_candle(5, 1.0805, 1.0815, 1.0800, 1.0810),  # low=1.0800 > 1.0790
        make_candle(6, 1.0810, 1.0820, 1.0805, 1.0815),  # low=1.0805 > 1.0790
        # Sweep: low < 1.0790, close > 1.0790
        make_candle(7, 1.0815, 1.0820, 1.0785, 1.0810),
        make_candle(8, 1.0810, 1.0825, 1.0805, 1.0820),
        make_candle(9, 1.0820, 1.0830, 1.0815, 1.0825),
    ]


def make_equal_highs_candles() -> list[Candle]:
    """Crée des bougies avec des Equal Highs."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0815, 1.0800, 1.0810),
        make_candle(2, 1.0810, 1.0830, 1.0805, 1.0825),  # Swing high 1 (high=1.0830)
        make_candle(3, 1.0825, 1.0825, 1.0810, 1.0815),
        make_candle(4, 1.0815, 1.0820, 1.0795, 1.0800),
        make_candle(5, 1.0800, 1.0810, 1.0785, 1.0790),
        make_candle(6, 1.0790, 1.0810, 1.0785, 1.0800),
        make_candle(7, 1.0800, 1.0830, 1.0795, 1.0825),  # Swing high 2 (high=1.0830, equal!)
        make_candle(8, 1.0825, 1.0825, 1.0820, 1.0822),
        make_candle(9, 1.0822, 1.0828, 1.0815, 1.0820),
    ]


def make_range_candles() -> list[Candle]:
    """Crée des bougies formant un range (pour Premium/Discount)."""
    return [
        make_candle(0, 1.0800, 1.0810, 1.0795, 1.0805),
        make_candle(1, 1.0805, 1.0815, 1.0800, 1.0810),
        make_candle(2, 1.0810, 1.0835, 1.0805, 1.0830),  # Swing high (high=1.0835)
        make_candle(3, 1.0830, 1.0832, 1.0815, 1.0820),
        make_candle(4, 1.0820, 1.0825, 1.0775, 1.0780),  # Swing low (low=1.0775)
        make_candle(5, 1.0780, 1.0810, 1.0790, 1.0800),
        make_candle(6, 1.0800, 1.0815, 1.0795, 1.0810),
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


# =============================================================================
# Tests du détecteur de structure
# =============================================================================


class TestStructureDetector:
    """Tests du détecteur de structure (BOS, CHoCH, MSS)."""

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
            make_candle(15, float(candles[-1].close), float(last_high) + 0.0020, float(candles[-1].close), float(last_high) + 0.0015)
        )
        detector = StructureDetector()
        detections = detector.detect(candles)
        bos = [d for d in detections if d.concept == SMCConcept.BOS]
        assert len(bos) > 0

    def test_disabled_detector(self):
        detector = StructureDetector(enabled=False)
        candles = make_uptrend_candles(20)
        assert detector.detect(candles) == []


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

    def test_disabled_detector(self):
        detector = LiquidityDetector(enabled=False)
        candles = make_sweep_candles()
        assert detector.detect(candles) == []


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
# Tests du détecteur SMC principal
# =============================================================================


class TestSMCDetector:
    """Tests du détecteur SMC principal."""

    def test_initialization(self):
        detector = SMCDetector()
        assert len(detector.detectors) == 5
        assert "structure" in detector.detectors
        assert "fair_value_gap" in detector.detectors
        assert "order_blocks" in detector.detectors
        assert "liquidity" in detector.detectors
        assert "premium_discount" in detector.detectors

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
        assert len(detector.get_enabled_concepts()) == 5

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

    def test_detect_sync(self):
        candles = make_uptrend_candles(20)
        detector = SMCDetector()
        detections = detector.detect_sync(candles)
        assert isinstance(detections, list)
        for i in range(1, len(detections)):
            assert detections[i - 1].index <= detections[i].index