"""Tests de l'analyse hiérarchique multi-timeframe."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.decision.mtf import MTF_HIERARCHY, MultiTimeframeAnalyzer


def _candle(timeframe: TimeFrame) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe=timeframe,
        time=datetime(2024, 1, 1, tzinfo=UTC),
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
    )


@pytest.mark.asyncio
async def test_mtf_loads_ict_hierarchy_and_derives_htf_trends() -> None:
    class MarketData:
        async def get_latest_candles(self, symbol, timeframe, count):
            return [_candle(timeframe)]

    class Detector:
        async def detect(self, candles, symbol):
            return [{"concept": "break_of_structure", "direction": "bullish", "index": 1}]

    result = await MultiTimeframeAnalyzer(MarketData(), Detector()).analyze("EURUSD")
    assert tuple(result.candles) == MTF_HIERARCHY
    assert result.trends == {"D1": "bullish", "H4": "bullish", "H1": "bullish"}
