"""Tests du checker de confirmation M5 avant entrée sur un OB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pandas as pd
import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.smc.base import SwingPoint
from arty_trading.modules.smc.confirmation import (
    ConfirmationResult,
    M5ConfirmationChecker,
    M5ConfirmationType,
)
from arty_trading.modules.smc.order_block_tracker import TrackedOB
from arty_trading.modules.smc.structure import StructureDetector


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
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2024-01-01T00:05:00Z", periods=len(rows), freq="5min"),
    )


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
        {
            "detections": [
                {
                    "concept": "choch",
                    "direction": "bullish",
                    "index": 500,
                    "timestamp": candles.index[1],
                }
            ]
        },
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


def rejection_check(
    candidate: tuple[float, float, float, float],
    ob: TrackedOB | None = None,
) -> ConfirmationResult:
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 4 + [candidate])
    checker = M5ConfirmationChecker(False, False, True)
    selected_ob = ob if ob is not None else make_ob()
    return checker.check(
        selected_ob,
        candles,
        {},
        reference_timestamp=pd.Timestamp(selected_ob.created_at),
    )


@pytest.mark.parametrize(
    ("direction", "candidate"),
    [
        ("bullish", (110.4, 110.9, 109.8, 110.7)),
        ("bearish", (110.7, 111.4, 110.3, 110.4)),
        ("bullish", (90.4, 90.9, 89.8, 90.7)),
        ("bearish", (90.7, 91.4, 90.3, 90.4)),
    ],
)
def test_rejection_rejects_candle_far_from_ob(
    direction: str,
    candidate: tuple[float, float, float, float],
) -> None:
    result = rejection_check(candidate, make_ob(direction))
    assert not result.confirmed
    assert result.details["rejection_candle"] == {"reason": "no_contact_with_ob"}


@pytest.mark.parametrize("low", [98.8, 100.0])
def test_rejection_accepts_candle_touching_ob_bullish(low: float) -> None:
    result = rejection_check((100.6, 101.0, low, 100.9))
    assert result.confirmed
    assert result.type is M5ConfirmationType.REJECTION_CANDLE
    assert result.details["rejection_candle"]["wick_body_ratio"] == pytest.approx(
        (100.6 - low) / 0.3
    )


@pytest.mark.parametrize("high", [100.2, 99.0])
def test_rejection_accepts_candle_touching_ob_bearish(high: float) -> None:
    result = rejection_check((98.4, high, 98.0, 98.1), make_ob("bearish"))
    assert result.confirmed
    assert result.type is M5ConfirmationType.REJECTION_CANDLE
    assert result.details["rejection_candle"]["wick_body_ratio"] == pytest.approx(
        (high - 98.4) / 0.3
    )


def test_rejection_logs_debug_on_no_contact() -> None:
    with patch("arty_trading.modules.smc.confirmation.rejection_logger") as logger:
        result = rejection_check((110.4, 110.9, 109.8, 110.7))
    assert not result.confirmed
    logger.debug.assert_called_once_with(
        "m5_rejection_ignored", reason="no_contact_with_ob", ob_id="ob-test"
    )


@pytest.mark.parametrize("missing_bounds", [{}, {"low": None, "high": None}, {"low": 99.0}])
@pytest.mark.parametrize(("open_price", "close"), [(100.0, 99.0), (99.0, 100.0)])
def test_rejection_falls_back_to_ob_body(
    missing_bounds: dict[str, float | None],
    open_price: float,
    close: float,
) -> None:
    ob = cast(
        TrackedOB,
        SimpleNamespace(
            ob_id="ob-body",
            symbol="XAUUSD",
            direction="bullish",
            created_at=make_ob().created_at,
            open=open_price,
            close=close,
            **missing_bounds,
        ),
    )
    assert rejection_check((99.4, 99.9, 98.8, 99.7), ob).confirmed
    assert not rejection_check((110.4, 110.9, 109.8, 110.7), ob).confirmed


def test_rejection_rejects_ob_without_bounds_or_body() -> None:
    ob = cast(
        TrackedOB,
        SimpleNamespace(
            ob_id="ob-invalid",
            symbol="XAUUSD",
            direction="bullish",
            created_at=make_ob().created_at,
        ),
    )
    result = rejection_check((99.4, 99.9, 98.8, 99.7), ob)
    assert not result.confirmed
    assert result.details["reason"] == "invalid_ob_zone"


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


def choch_check(timestamp: object, *, include_timestamp: bool = True) -> ConfirmationResult:
    checker = M5ConfirmationChecker(
        require_micro_bos=False, require_choch=True, require_rejection_candle=False
    )
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 10)
    event = {"concept": "choch", "direction": "bullish", "index": 500}
    if include_timestamp:
        event["timestamp"] = timestamp
    return checker.check(
        make_ob(),
        candles,
        {"events": [event]},
        reference_timestamp=pd.Timestamp(make_ob().created_at),
    )


def test_choch_event_before_ob_is_ignored() -> None:
    assert not choch_check("2023-12-31T23:55:00Z").confirmed


def test_choch_event_after_ob_is_considered() -> None:
    result = choch_check("2024-01-01T00:30:00Z")
    assert result.confirmed
    assert result.details["choch"]["event_timestamp"] == "2024-01-01T00:30:00+00:00"


@pytest.mark.parametrize("timestamp", ["2024-01-01T00:55:00Z", "2024-01-01T00:31:00Z"])
def test_choch_event_out_of_window_is_ignored(timestamp: str) -> None:
    assert not choch_check(timestamp).confirmed


@pytest.mark.parametrize("timestamp", [None, "invalid", 5, "2024-01-01T00:00:00Z"])
def test_choch_invalid_or_ob_timestamp_is_ignored(timestamp: object) -> None:
    assert not choch_check(timestamp).confirmed


def test_choch_index_only_event_is_ignored() -> None:
    assert not choch_check(None, include_timestamp=False).confirmed


def test_choch_event_at_contact_is_ignored() -> None:
    assert not choch_check("2024-01-01T00:05:00Z").confirmed


def test_choch_equivalent_timezone_is_considered() -> None:
    assert choch_check("2024-01-01T03:30:00+03:00").confirmed


def test_choch_key_supplies_concept_for_timestamp_only_event() -> None:
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 6)
    checker = M5ConfirmationChecker(False, True, False)
    result = checker.check(
        make_ob(),
        candles,
        {"choch": [{"direction": "bullish", "timestamp": candles.index[2]}]},
        reference_timestamp=pd.Timestamp(make_ob().created_at),
    )
    assert result.confirmed


def micro_bos_frame() -> pd.DataFrame:
    return frame(
        [
            (99.5, 100.2, 98.9, 99.2),
            (99.2, 100.0, 99.0, 99.5),
            (99.5, 101.0, 99.3, 100.0),
            (100.0, 100.5, 99.5, 100.1),
            (100.1, 100.2, 99.6, 100.0),
            (100.0, 101.4, 99.8, 101.2),
        ]
    )


def test_micro_bos_event_before_ob_is_ignored() -> None:
    candles = micro_bos_frame()
    candles.index = pd.date_range("2023-12-31T23:25:00Z", periods=6, freq="5min")
    candles = pd.concat([candles, frame([(99.5, 100.2, 98.9, 99.2)] * 6)])
    checker = M5ConfirmationChecker(require_rejection_candle=False)
    assert not checker.check(
        make_ob(), candles, {}, reference_timestamp=pd.Timestamp(make_ob().created_at)
    ).confirmed


def test_micro_bos_event_after_ob_is_considered() -> None:
    checker = M5ConfirmationChecker(require_rejection_candle=False)
    result = checker.check(
        make_ob(),
        micro_bos_frame(),
        {},
        reference_timestamp=pd.Timestamp(make_ob().created_at),
    )
    assert result.confirmed
    assert result.details["micro_bos"]["break_timestamp"] == "2024-01-01T00:30:00+00:00"


def test_check_signature_accepts_reference_timestamp() -> None:
    result = M5ConfirmationChecker().check(
        make_ob(), micro_bos_frame(), {}, reference_timestamp=pd.Timestamp(make_ob().created_at)
    )
    assert result.confirmed


def test_check_without_reference_timestamp_logs_warning() -> None:
    with patch("arty_trading.modules.smc.confirmation.logger") as logger:
        result = M5ConfirmationChecker().check(make_ob(), micro_bos_frame(), {})
    assert result.confirmed
    logger.warning.assert_any_call(
        "m5_confirmation_missing_reference_timestamp ob_id=%s", "ob-test"
    )


def test_choch_accepts_timestamp_column_with_original_integer_index() -> None:
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 6).reset_index(names="timestamp")
    candles.index = pd.RangeIndex(500, 506)
    checker = M5ConfirmationChecker(False, True, False)
    result = checker.check(
        make_ob(),
        candles,
        {
            "events": [
                {
                    "concept": "choch",
                    "direction": "bullish",
                    "index": 502,
                    "timestamp": candles["timestamp"].iloc[2],
                }
            ]
        },
        reference_timestamp=pd.Timestamp(make_ob().created_at),
    )
    assert result.confirmed


def test_check_rejects_missing_candle_timestamps() -> None:
    result = M5ConfirmationChecker().check(make_ob(), micro_bos_frame().reset_index(drop=True), {})
    assert result.details["reason"] == "missing_candle_timestamps"


def test_structure_detector_dates_all_events_at_break_candle() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            symbol="XAUUSD",
            timeframe=TimeFrame.M5,
            time=start + timedelta(minutes=5 * i),
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("97"),
            close=Decimal(close),
        )
        for i, close in enumerate([100, 100, 100, 102, 98, 100])
    ]
    swings = [
        SwingPoint(index=0, price=Decimal("101"), type="high", timestamp=candles[0].time),
        SwingPoint(index=1, price=Decimal("99"), type="low", timestamp=candles[1].time),
        SwingPoint(index=4, price=Decimal("103"), type="high", timestamp=candles[4].time),
    ]
    with patch("arty_trading.modules.smc.structure.find_swing_points", return_value=swings):
        events = StructureDetector().detect(candles)
    assert {event.concept.value for event in events} >= {
        "break_of_structure",
        "internal_bos",
        "change_of_character",
        "market_structure_shift",
    }
    for event in events:
        assert event.timestamp == candles[event.index].time
        assert event.to_dict()["timestamp"] == candles[event.index].time.isoformat()


def test_choch_continues_after_out_of_window_event() -> None:
    candles = frame([(99.5, 100.2, 98.9, 99.2)] * 6)
    checker = M5ConfirmationChecker(False, True, False)
    result = checker.check(
        make_ob(),
        candles,
        {
            "events": [
                {"concept": "choch", "direction": "bullish", "timestamp": "2024-01-01T01:00:00Z"},
                {"concept": "choch", "direction": "bullish", "timestamp": candles.index[2]},
            ]
        },
        reference_timestamp=pd.Timestamp(make_ob().created_at),
    )
    assert result.confirmed


def test_micro_bos_out_of_window_break_is_ignored() -> None:
    candles = micro_bos_frame().iloc[:-1]
    result = M5ConfirmationChecker(require_rejection_candle=False).check(
        make_ob(), candles, {}, reference_timestamp=pd.Timestamp(make_ob().created_at)
    )
    assert not result.confirmed
