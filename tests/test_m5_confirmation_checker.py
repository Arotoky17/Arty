"""Tests du checker de confirmation M5 avant entrée sur un OB."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd
import pytest

from arty_trading.modules.smc.confirmation import (
    ConfirmationResult,
    M5ConfirmationChecker,
    M5ConfirmationType,
)
from arty_trading.modules.smc.order_block_tracker import TrackedOB


def make_ob(direction: str = "bullish") -> TrackedOB:
    return TrackedOB.create(
        symbol="XAUUSD",
        timeframe="H1",
        direction=direction,
        high=100.0,
        low=99.0,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ob_id="ob-test",
    )


def frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_micro_bos_confirmation() -> None:
    candles = frame(
        [
            (99.5, 100.2, 98.9, 99.2),
            (99.2, 100.0, 99.0, 99.5),
            (99.5, 101.0, 99.3, 100.0),
            (100.0, 100.5, 99.5, 100.1),
            (100.1, 100.2, 99.6, 100.0),
            (100.0, 101.4, 99.8, 101.2),
        ]
    )
    checker = M5ConfirmationChecker(
        require_micro_bos=True,
        require_choch=False,
        require_rejection_candle=False,
    )

    result = checker.check(make_ob(), candles, {})

    assert result.confirmed is True
    assert result.type is M5ConfirmationType.MICRO_BOS


def test_choch_confirmation() -> None:
    candles = frame(
        [
            (99.5, 100.2, 98.9, 99.2),
            (99.2, 99.8, 99.1, 99.6),
            (100.3, 100.7, 100.2, 100.5),
            (100.5, 100.8, 100.3, 100.6),
            (100.6, 100.9, 100.4, 100.7),
        ]
    )
    checker = M5ConfirmationChecker(
        require_micro_bos=False,
        require_choch=True,
        require_rejection_candle=False,
    )

    result = checker.check(
        make_ob(),
        candles,
        {"detections": [{"concept": "choch", "direction": "bullish", "index": 1}]},
    )

    assert result.confirmed is True
    assert result.type is M5ConfirmationType.CHOCH


def test_rejection_candle_confirmation() -> None:
    candles = frame(
        [
            (99.5, 100.2, 98.9, 99.2),
            (100.3, 100.7, 100.2, 100.5),
            (100.5, 100.8, 100.3, 100.6),
            (100.6, 100.9, 100.4, 100.7),
            (99.4, 99.9, 98.8, 99.7),
        ]
    )
    checker = M5ConfirmationChecker(
        require_micro_bos=False,
        require_choch=False,
        require_rejection_candle=True,
    )

    result = checker.check(make_ob(), candles, {})

    assert result.confirmed is True
    assert result.type is M5ConfirmationType.REJECTION_CANDLE


def test_contact_candle_can_confirm_rejection() -> None:
    candles = frame(
        [
            (100.3, 100.7, 100.2, 100.5),
            (100.5, 100.8, 100.3, 100.6),
            (100.6, 100.9, 100.4, 100.7),
            (100.7, 101.0, 100.5, 100.8),
            (99.4, 99.9, 98.8, 99.7),
        ]
    )
    checker = M5ConfirmationChecker(
        require_micro_bos=False,
        require_choch=False,
        require_rejection_candle=True,
    )

    result = checker.check(make_ob(), candles, {})

    assert result.confirmed is True
    assert result.type is M5ConfirmationType.REJECTION_CANDLE


def test_no_confirmation_returns_false() -> None:
    candles = frame([(99.5, 100.2, 98.9, 99.2)])

    result = M5ConfirmationChecker().check(make_ob(), candles, {})

    assert result.confirmed is False
    assert result.type is None


def test_rejects_when_minimum_candle_count_is_not_met() -> None:
    candles = frame([(100.3, 100.7, 100.2, 100.5)] * 2)

    result = M5ConfirmationChecker().check(make_ob(), candles, {})

    assert result.details["reason"] == "insufficient_candles"
    assert result.details["have"] == 2
    assert result.details["need"] == 3


def test_rejects_when_swing_warmup_is_not_met() -> None:
    candles = frame([(100.3, 100.7, 100.2, 100.5)] * 3)

    result = M5ConfirmationChecker().check(make_ob(), candles, {})

    assert result.details["reason"] == "insufficient_candles_for_swing"
    assert result.details["have"] == 3
    assert result.details["need"] == 5


def choch_check(indices: list[int]) -> ConfirmationResult:
    checker = M5ConfirmationChecker(
        require_micro_bos=False, require_choch=True, require_rejection_candle=False
    )
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 10)
    return checker.check(
        make_ob(), candles,
        {"events": [{"concept": "choch", "direction": "bullish", "index": index}
                    for index in indices]},
    )


@pytest.mark.parametrize("index", [10, 999])
def test_choch_ignores_index_out_of_bounds(index: int) -> None:
    result = choch_check([index])
    assert not result.confirmed
    assert result.type is None


def test_choch_ignores_negative_index() -> None:
    assert not choch_check([-1]).confirmed


@pytest.mark.parametrize("index", [5, 9])
def test_choch_accepts_valid_index(index: int) -> None:
    with patch("arty_trading.modules.smc.confirmation.choch_logger") as logger:
        result = choch_check([index])
    assert result.confirmed
    assert result.type is M5ConfirmationType.CHOCH
    assert result.details["choch"]["event_index"] == index
    logger.warning.assert_not_called()


@pytest.mark.parametrize("index", [-1, 10, 999])
def test_choch_logs_warning_on_out_of_bounds(index: int) -> None:
    with patch("arty_trading.modules.smc.confirmation.choch_logger") as logger:
        choch_check([index])
    logger.warning.assert_called_once_with(
        "choch_index_out_of_bounds", index=index, available=10, ob_id="ob-test"
    )


@pytest.mark.parametrize("index", [-1, 10, 999])
def test_choch_continues_after_one_invalid_event(index: int) -> None:
    result = choch_check([index, 5])
    assert result.confirmed
    assert result.details["choch"]["event_index"] == 5


def test_choch_warning_uses_event_ob_id() -> None:
    checker = M5ConfirmationChecker(
        require_micro_bos=False, require_choch=True, require_rejection_candle=False
    )
    with patch("arty_trading.modules.smc.confirmation.choch_logger") as logger:
        checker.check(make_ob(), frame([(99.5, 100.2, 98.9, 99.2)] * 10),
                      {"events": [{"concept": "choch", "direction": "bullish",
                                   "index": 999, "ob_id": "event-ob"}]})
    assert logger.warning.call_args.kwargs["ob_id"] == "event-ob"
