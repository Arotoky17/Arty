"""Tests du MarketStructureEngine — régime, structure courante, score de tendance.

Couvre les scénarios cibles de l'architecture SMC/ICT :

- TEST 1 : H1 bullish → BUY possible
- TEST 2 : H1 bearish → SELL possible
- TEST 3 : H1 bearish + candidat BUY → REJECT (contre-tendance)
- TEST 4 : H1 bullish + candidat SELL → REJECT (contre-tendance)
- TEST 5 : RANGE → NO TRADE
- TEST 6 : TRANSITION → NO TRADE
- TEST 7 : ancien BOS bullish + structure bearish → BUY REJECT
- Structure trop ancienne → invalidée (RANGE / NO TRADE)
"""

from datetime import UTC, datetime
from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import (
    MarketRegime,
    NoTradeReason,
    SMCConcept,
    TimeFrame,
)
from arty_trading.modules.decision.market_structure_engine import (
    MarketStructureEngine,
    MarketStructureSettings,
)
from arty_trading.modules.smc.base import SMCDetection


def _candle(index: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, 0, index, tzinfo=UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=100,
        spread=3,
    )


def _uptrend() -> list[Candle]:
    """HH + HL (hauts/bas croissants) — permet de détecter des swing points."""
    return [
        _candle(0, 1.0800, 1.0805, 1.0795, 1.0803),
        _candle(1, 1.0803, 1.0812, 1.0802, 1.0810),
        _candle(2, 1.0810, 1.0820, 1.0809, 1.0818),  # HH
        _candle(3, 1.0818, 1.0815, 1.0806, 1.0808),
        _candle(4, 1.0808, 1.0812, 1.0804, 1.0810),  # HL
        _candle(5, 1.0810, 1.0822, 1.0809, 1.0820),
        _candle(6, 1.0820, 1.0830, 1.0819, 1.0828),  # HH
        _candle(7, 1.0828, 1.0825, 1.0816, 1.0818),
        _candle(8, 1.0818, 1.0822, 1.0814, 1.0820),  # HL
        _candle(9, 1.0820, 1.0832, 1.0819, 1.0830),
        _candle(10, 1.0830, 1.0840, 1.0829, 1.0838),  # HH
        _candle(11, 1.0838, 1.0845, 1.0832, 1.0843),
        _candle(12, 1.0843, 1.0848, 1.0835, 1.0846),  # HL
        _candle(13, 1.0846, 1.0855, 1.0844, 1.0853),
        _candle(14, 1.0853, 1.0862, 1.0851, 1.0860),  # HH
    ]


def _downtrend() -> list[Candle]:
    """LH + LL : reflet strict de la tendance haussière."""
    base = Decimal("1.0820")
    series = []
    for c in _uptrend():
        series.append(
            _candle(
                int(c.time.minute),
                float(2 * base - c.open),
                float(2 * base - c.low),
                float(2 * base - c.high),
                float(2 * base - c.close),
            )
        )
    return series


def _flat() -> list[Candle]:
    """Oscillation plate : pas de HH/HL/LH/LL progressifs (range)."""
    levels = [1.0800, 1.0810, 1.0802, 1.0808, 1.0801, 1.0809, 1.0803, 1.0807]
    return [
        _candle(i, levels[i], levels[i] + 0.0008, levels[i] - 0.0008, levels[i])
        for i in range(len(levels))
    ]


def _bos(direction: str, index: int, price: float = 1.0800) -> SMCDetection:
    return SMCDetection(
        concept=SMCConcept.BOS,
        direction=direction,
        price=Decimal(str(price)),
        index=index,
        details={},
    )


def _choch(direction: str, index: int, price: float = 1.0800) -> SMCDetection:
    return SMCDetection(
        concept=SMCConcept.CHOCH,
        direction=direction,
        price=Decimal(str(price)),
        index=index,
        details={},
    )


def _fvg(direction: str, index: int, price: float = 1.0800) -> SMCDetection:
    return SMCDetection(
        concept=SMCConcept.FVG,
        direction=direction,
        price=Decimal(str(price)),
        index=index,
        details={},
    )
class TestMarketStructureEngine:
    def test_uptrend_is_bullish_and_allows_buy(self):
        """TEST 1 : HH+HL → BULLISH, BUY autorisé, SELL interdit."""
        result = MarketStructureEngine().analyze(_uptrend(), [_bos("bullish", 8)])
        assert result.regime in {MarketRegime.BULLISH, MarketRegime.STRONG_BULLISH}
        assert result.trend == "bullish"
        assert result.trend_score > 0
        assert result.allows_buy() is True
        assert result.allows_sell() is False

    def test_downtrend_is_bearish_and_allows_sell(self):
        """TEST 2 : LH+LL → BEARISH, SELL autorisé, BUY interdit."""
        result = MarketStructureEngine().analyze(_downtrend(), [_bos("bearish", 8)])
        assert result.regime in {MarketRegime.BEARISH, MarketRegime.STRONG_BEARISH}
        assert result.trend == "bearish"
        assert result.trend_score < 0
        assert result.allows_sell() is True
        assert result.allows_buy() is False

    def test_bearish_rejects_buy_candidate(self):
        """TEST 3 : H1 bearish → un candidat BUY est rejeté (contre-tendance)."""
        result = MarketStructureEngine().analyze(_downtrend(), [_bos("bearish", 8)])
        assert result.allows_buy() is False
        assert NoTradeReason.COUNTER_TREND.value in result.no_trade_reasons

    def test_bullish_rejects_sell_candidate(self):
        """TEST 4 : H1 bullish → un candidat SELL est rejeté (contre-tendance)."""
        result = MarketStructureEngine().analyze(_uptrend(), [_bos("bullish", 8)])
        assert result.allows_sell() is False
        assert NoTradeReason.COUNTER_TREND.value in result.no_trade_reasons

    def test_flat_is_range_and_no_trade(self):
        """TEST 5 : structure plate → RANGE, aucune direction autorisée."""
        result = MarketStructureEngine().analyze(_flat())
        assert result.regime == MarketRegime.RANGE
        assert result.allows_buy() is False
        assert result.allows_sell() is False
        assert NoTradeReason.H1_RANGE.value in result.no_trade_reasons

    def test_choch_gives_transition_and_no_trade(self):
        """TEST 6 : CHoCH récent non confirmé → TRANSITION, NO TRADE."""
        result = MarketStructureEngine().analyze(_uptrend(), [_choch("bearish", 10)])
        assert result.regime == MarketRegime.TRANSITION
        assert result.allows_buy() is False
        assert result.allows_sell() is False
        assert NoTradeReason.H1_TRANSITION.value in result.no_trade_reasons

    def test_bullish_choch_confirms_bullish_structure(self):
        """HH+HL + bullish CHoCH → BULLISH, allows_buy=True (confirmation)."""
        total = len(_uptrend())
        result = MarketStructureEngine().analyze(
            _uptrend(), [_choch("bullish", total - 1)]
        )
        assert result.structure_bias == "bullish"
        assert result.regime in {MarketRegime.BULLISH, MarketRegime.STRONG_BULLISH}
        assert result.allows_buy() is True
        assert result.allows_sell() is False

    def test_bearish_choch_confirms_bearish_structure(self):
        """LH+LL + bearish CHoCH → BEARISH, allows_sell=True (confirmation)."""
        total = len(_downtrend())
        result = MarketStructureEngine().analyze(
            _downtrend(), [_choch("bearish", total - 1)]
        )
        assert result.structure_bias == "bearish"
        assert result.regime in {MarketRegime.BEARISH, MarketRegime.STRONG_BEARISH}
        assert result.allows_sell() is True
        assert result.allows_buy() is False

    def test_bearish_choch_against_bullish_structure_is_transition(self):
        """HH+HL + bearish CHoCH → TRANSITION, allows_buy=False."""
        total = len(_uptrend())
        result = MarketStructureEngine().analyze(
            _uptrend(), [_choch("bearish", total - 1)]
        )
        assert result.structure_bias == "bullish"
        assert result.regime == MarketRegime.TRANSITION
        assert result.allows_buy() is False
        assert result.allows_sell() is False

    def test_bullish_choch_against_bearish_structure_is_transition(self):
        """LH+LL + bullish CHoCH → TRANSITION, allows_sell=False."""
        total = len(_downtrend())
        result = MarketStructureEngine().analyze(
            _downtrend(), [_choch("bullish", total - 1)]
        )
        assert result.structure_bias == "bearish"
        assert result.regime == MarketRegime.TRANSITION
        assert result.allows_buy() is False
        assert result.allows_sell() is False

    def test_structure_bias_neutral_without_clear_hh_hl_or_lh_ll(self):
        """Pas de HH+HL ni LH+LL → structure_bias=neutral."""
        result = MarketStructureEngine().analyze(_flat())
        assert result.structure_bias == "neutral"
        assert result.regime == MarketRegime.RANGE
    def test_old_bullish_bos_does_not_override_bearish_structure(self):
        """TEST 7 : ancien BOS bullish + structure bearish → BUY rejeté.

        Le BOS bullish est trop ancien (index 0) ; la structure courante
        (LH+LL + BOS bearish récent) reste bearish.
        """
        total = len(_downtrend())
        detections = [
            _bos("bullish", 0),
            _bos("bearish", total - 1, price=1.0810),
        ]
        result = MarketStructureEngine().analyze(_downtrend(), detections)
        assert result.latest_bos is not None
        assert result.latest_bos["direction"] == "bearish"
        assert result.allows_buy() is False
        assert result.allows_sell() is True

    def test_stale_structure_is_invalid(self):
        """Structure trop ancienne → invalidée → RANGE / NO TRADE."""
        settings = MarketStructureSettings(max_structure_age=2)
        detections = [_bos("bullish", 0)]
        result = MarketStructureEngine(settings).analyze(_uptrend(), detections)
        assert result.structure_valid is False
        assert result.allows_buy() is False
        assert result.allows_sell() is False

    def test_latest_bos_recency_priority(self):
        """Le dernier BOS détecté est celui conservé (priorité récence)."""
        result = MarketStructureEngine().analyze(
            _uptrend(),
            [_bos("bullish", 3), _bos("bearish", 9, price=1.0815)],
        )
        assert result.latest_bos is not None
        assert result.latest_bos["index"] == 9
        assert result.latest_bos["direction"] == "bearish"

    def test_strong_regime_with_high_evidence(self):
        """Beaucoup de confirmations bullish → STRONG_BULLISH / BULLISH haussier."""
        detections = [
            _bos("bullish", 8),
            _bos("bullish", 9),
            _bos("bullish", 10),
        ]
        result = MarketStructureEngine().analyze(_uptrend(), detections)
        assert result.trend == "bullish"
        assert result.trend_score > 0

    def test_allow_counter_trend_removes_counter_reason(self):
        """allow_counter_trend=True ne bloque plus (info seulement)."""
        settings = MarketStructureSettings(allow_counter_trend=True)
        result = MarketStructureEngine(settings).analyze(
            _downtrend(), [_bos("bearish", 8)]
        )
        assert NoTradeReason.COUNTER_TREND.value not in result.no_trade_reasons

    def test_empty_data_is_no_trade(self):
        """Pas assez de données → RANGE / NO TRADE, jamais de signal."""
        result = MarketStructureEngine().analyze([])
        assert result.regime == MarketRegime.RANGE
        assert NoTradeReason.NO_STRUCTURE.value in result.no_trade_reasons
        assert result.allows_buy() is False
        assert result.allows_sell() is False

    def test_to_dict_serializable(self):
        """to_dict() est sérialisable et contient les champs clés."""
        result = MarketStructureEngine().analyze(_uptrend(), [_bos("bullish", 8)])
        data = result.to_dict()
        assert data["regime"] == result.regime.value
        assert "trend_score" in data
        assert "allows_buy" in data
        assert "no_trade_reasons" in data