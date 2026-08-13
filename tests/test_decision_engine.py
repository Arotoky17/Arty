"""Tests du moteur de décision ICT déterministe."""

from datetime import UTC, datetime
from decimal import Decimal

from arty_trading.config.settings import DecisionSettings
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.decision import DecisionEngine


def _candles() -> list[Candle]:
    return [
        Candle(
            symbol="EURUSD",
            timeframe=TimeFrame.M5,
            time=datetime(2024, 1, 2, 8, i, tzinfo=UTC),
            open=Decimal("1.1000"),
            high=Decimal("1.1010"),
            low=Decimal("1.0990"),
            close=Decimal("1.1005"),
            spread=3,
        )
        for i in range(20)
    ]


def _signal() -> Signal:
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("1.1005"),
        stop_loss=Decimal("1.0990"),
        take_profit=Decimal("1.1035"),
        confidence=0.5,
        strategy_name="test",
        timeframe=TimeFrame.M5,
    )


def _data() -> list[dict]:
    concepts = [
        "break_of_structure",
        "change_of_character",
        "order_block",
        "fair_value_gap",
        "optimal_trade_entry",
        "liquidity_sweep",
        "equal_high",
        "premium_discount",
    ]
    result = []
    for concept in concepts:
        item = {
            "concept": concept,
            "direction": "bullish",
            "price": 1.0990 if concept in {"order_block", "liquidity_sweep"} else 1.1050,
            "details": {},
        }
        if concept == "premium_discount":
            item["direction"] = "neutral"
            item["details"] = {"current_zone": "discount"}
        result.append(item)
    return result


def test_approves_high_confluence_buy_and_enriches_confidence() -> None:
    engine = DecisionEngine(DecisionSettings())
    decision = engine.decide(
        _signal(), _candles(), _data(), htf_trends={"D1": "bullish", "H4": "bullish"}
    )
    signal = engine.enrich(_signal(), decision)
    assert decision.approved is True
    assert decision.score >= 90
    assert signal is not None and signal.confidence == decision.confidence
    assert signal.metadata["decision"]["tier"] == "premium"


def test_rejects_buy_outside_discount_even_with_confluences() -> None:
    data = _data()
    data[-1]["details"] = {"current_zone": "premium"}
    decision = DecisionEngine(DecisionSettings()).decide(
        _signal(), _candles(), data, htf_trends={"H4": "bullish"}
    )
    assert decision.approved is False
    assert "premium_discount" in decision.rejected_by


def test_high_impact_news_is_a_hard_block() -> None:
    decision = DecisionEngine(DecisionSettings()).decide(
        _signal(), _candles(), _data(), htf_trends={"H4": "bullish"}, has_high_impact_news=True
    )
    assert decision.approved is False
    assert "high_impact_news" in decision.rejected_by


# ---------------------------------------------------------------------------
# Test de caractérisation du calcul ATR du DecisionEngine.
# Fige le comportement ACTUEL (moyenne simple glissante sur `period` bougies),
# qui est volontairement DIFFÉRENT du ATR Wilder de utils.helpers/strategies.
# Tout refactor qui fusionnerait ces deux calculs changerait silencieusement
# le score du DecisionEngine → ce test le bloquerait.
# ---------------------------------------------------------------------------


def _volatile_candles(n: int) -> list[Candle]:
    """Bougies déterministes reproduisant exactement le jeu du probe de figer."""
    o = Decimal("1.1000")
    c = Decimal("1.1000")
    out: list[Candle] = []
    for i in range(n):
        out.append(
            Candle(
                symbol="EURUSD",
                timeframe=TimeFrame.M5,
                time=datetime(2024, 1, 2, 8, i, tzinfo=UTC),
                open=o,
                high=o + Decimal("0.0020") + Decimal(i) * Decimal("0.0005"),
                low=o - Decimal("0.0010"),
                close=c + Decimal(i) * Decimal("0.0004"),
                volume=100,
                spread=3,
            )
        )
        o = c + Decimal(i) * Decimal("0.0004")
        c = o
    return out


def test_atr_characterization_simple_moving_average_window() -> None:
    """La valeur ATR du DecisionEngine est une moyenne simple glissante, pas Wilder."""
    # Moins d'une bougie → 0.
    assert DecisionEngine._atr(_volatile_candles(1), 14) == Decimal("0")
    # period=1, n=2 : un seul true range (moyenne simple sur la fenêtre).
    assert DecisionEngine._atr(_volatile_candles(2), 1) == Decimal("0.0035")
    # period=2, n=6 : moyenne simple sur les 2 derniers true ranges.
    assert DecisionEngine._atr(_volatile_candles(6), 2) == Decimal("0.00525")
    # period=14, n=8 : le zip limite naturellement à la fenêtre disponible.
    assert DecisionEngine._atr(_volatile_candles(8), 14) == Decimal("0.0045")


def test_atr_characterization_differs_from_wilder() -> None:
    """Confirme que _atr ne produit PAS la valeur Wilder (helpers.calculate_atr)."""
    candles_6 = _volatile_candles(6)
    from arty_trading.utils.helpers import calculate_atr

    # Wilder renvoie une valeur différente de la fenêtre glissante simple.
    assert DecisionEngine._atr(candles_6, 2) != calculate_atr(candles_6, 2)
