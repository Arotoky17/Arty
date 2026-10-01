"""Tests du filtre OB de la stratégie SMC Trend Following."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from arty_trading.config.settings import OBQualitySettings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.modules.smc.confirmation import (
    ConfirmationResult,
    M5ConfirmationChecker,
    M5ConfirmationType,
)
from arty_trading.modules.smc.order_block_quality import (
    OBGrade,
    OrderBlockQuality,
    OrderBlockQualityScorer,
)
from arty_trading.modules.smc.order_block_tracker import OrderBlockTracker
from arty_trading.modules.strategies.strategies import SMCTrendStrategy


class FixedScorer(OrderBlockQualityScorer):
    def __init__(self, grade: OBGrade, score: float) -> None:
        super().__init__()
        self.quality = OrderBlockQuality(
            grade=grade,
            score=score,
            is_fresh=True,
            displacement_atr=1.5,
            htf_confluence=True,
            has_liquidity_sweep=True,
            has_fvg_adjacent=True,
            rejection_confirmed=True,
        )

    def score(
        self,
        ob_candle: pd.Series,
        next_candles: pd.DataFrame,
        atr_value: float,
        htf_obs: list[Any],
        htf_fvgs: list[Any],
        liquidity_sweeps: list[Any],
        fvgs_m5: list[Any],
    ) -> OrderBlockQuality:
        return self.quality


class FixedConfirmation(M5ConfirmationChecker):
    def __init__(self, confirmed: bool) -> None:
        super().__init__()
        self.result = ConfirmationResult(
            confirmed=confirmed,
            type=M5ConfirmationType.MICRO_BOS if confirmed else None,
            details={"reason": "test_confirmation"},
        )

    def check(
        self,
        ob: Any,
        m5_candles_after_ob: pd.DataFrame,
        m5_structure: dict[str, Any],
    ) -> ConfirmationResult:
        return self.result


def make_candles() -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            symbol="XAUUSD",
            timeframe=TimeFrame.M5,
            time=start + timedelta(minutes=5 * index),
            open="1.1010",
            high="1.1020",
            low="1.1005",
            close="1.1015",
        )
        for index in range(20)
    ]
    candles[17] = Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=start + timedelta(minutes=85),
        open="1.0995",
        high="1.1002",
        low="1.0990",
        close="1.1000",
    )
    candles[18] = Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=start + timedelta(minutes=90),
        open="1.1005",
        high="1.1012",
        low="1.1002",
        close="1.1008",
    )
    candles[19] = Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=start + timedelta(minutes=95),
        open="1.1005",
        high="1.1010",
        low="1.0995",
        close="1.1008",
    )
    return candles


def make_smc_data() -> list[dict[str, Any]]:
    return [
        {
            "concept": "break_of_structure",
            "direction": "bullish",
            "price": 1.1008,
            "index": 18,
            "details": {},
        },
        {
            "concept": "order_block",
            "direction": "bullish",
            "price": 1.0995,
            "index": 17,
            "details": {"ob_top": 1.1000, "ob_bottom": 1.0990},
        },
    ]


def make_strategy(
    grade: OBGrade,
    *,
    score: float = 0.8,
    confirmed: bool = True,
    use_ob_quality_filter: bool = True,
) -> SMCTrendStrategy:
    return SMCTrendStrategy(
        confidence_min=0.1,
        ob_scorer=FixedScorer(grade, score),
        ob_tracker=OrderBlockTracker(),
        m5_confirmation=FixedConfirmation(confirmed),
        ob_config=OBQualitySettings(
            use_ob_quality_filter=True,
            min_score=0.55,
            rr_min_grade_a=2.5,
            rr_min_grade_b=2.0,
        ),
        use_ob_quality_filter=use_ob_quality_filter,
    )


async def evaluate(strategy: SMCTrendStrategy):
    return await strategy.analyze(make_candles(), make_smc_data())


async def test_strategy_rejects_grade_c_ob() -> None:
    strategy = make_strategy(OBGrade.C, score=0.4)

    signal = await evaluate(strategy)

    assert signal is None
    assert strategy.last_ob_rejection is not None
    assert strategy.last_ob_rejection["reason"] == "no_qualified_ob"
    assert strategy.last_ob_rejection["rejected_obs"][0]["grade"] == "C"


async def test_strategy_accepts_grade_a_with_confirmation() -> None:
    signal = await evaluate(make_strategy(OBGrade.A))

    assert signal is not None
    assert signal.direction == Direction.BUY
    assert signal.metadata["ob_grade"] == "A"
    assert signal.risk_reward_ratio >= 2.5


async def test_strategy_rejects_without_m5_confirmation() -> None:
    strategy = make_strategy(OBGrade.A, confirmed=False)

    signal = await evaluate(strategy)

    assert signal is None
    assert strategy.last_ob_rejection is not None
    assert strategy.last_ob_rejection["reason"] == "no_m5_confirmation"
    assert strategy.last_ob_rejection["grade"] == "A"
    assert strategy.last_ob_rejection["score"] == 0.8


async def test_strategy_fallback_when_flag_disabled() -> None:
    strategy = make_strategy(OBGrade.C, score=0.4, use_ob_quality_filter=False)

    signal = await evaluate(strategy)

    assert signal is not None
    assert strategy.last_ob_rejection is None


async def test_rr_min_adapts_to_grade() -> None:
    grade_a_signal = await evaluate(make_strategy(OBGrade.A))
    grade_b_signal = await evaluate(make_strategy(OBGrade.B, score=0.6))

    assert grade_a_signal is not None
    assert grade_b_signal is not None
    assert grade_a_signal.risk_reward_ratio >= 2.5
    assert grade_b_signal.risk_reward_ratio >= 2.0
    assert grade_a_signal.risk_reward_ratio > grade_b_signal.risk_reward_ratio
