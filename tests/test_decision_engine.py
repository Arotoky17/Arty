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
