"""Tests de caractérisation du constructeur de contexte marché (MarketContextBuilder).

Extrait de ``application/trading_engine._analyze_multitimeframe`` sans changement
de comportement. Ces tests figent : la construction du ``MarketContext``, le
cache H1 (pas de redétection sur même bougie H1) et l'appel au H4 informatif.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame


def _candle(idx: int, tf: TimeFrame, base: str = "1.1000") -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe=tf,
        time=datetime(2024, 1, 2, 8, idx % 60, tzinfo=timezone.utc),
        open=Decimal(base),
        high=Decimal(base) + Decimal("0.002"),
        low=Decimal(base) - Decimal("0.002"),
        close=Decimal(base) + Decimal("0.001"),
        volume=100,
        spread=3,
    )


class _Detector:
    """Détecteur SMC simulé : retourne une détection par appel, avec compteur."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[Candle], str]] = []
        self.results: dict[str, list[dict]] = {}

    async def detect(self, candles: list[Candle], symbol: str) -> list[dict]:
        self.calls.append((candles, symbol))
        key = candles[0].timeframe.value if candles else "?"
        return self.results.get(key, [{"concept": "break_of_structure", "direction": "bullish", "index": 1}])


@pytest.mark.asyncio
async def test_build_returns_market_context() -> None:
    htf = [_candle(i, TimeFrame.H1) for i in range(6)]
    ltf = [_candle(i, TimeFrame.M5) for i in range(10)]
    detector = _Detector()
    download = AsyncMock(return_value=[_candle(i, TimeFrame.H4) for i in range(6)])

    builder = MarketContextBuilder(smc_detector=detector, download_data=download)
    ctx = await builder.build("EURUSD", htf, ltf)

    assert ctx is not None
    assert ctx.symbol == "EURUSD"
    assert ctx.master_trend in ("bullish", "bearish", "neutral")
    # Le H4 informatif a bien été téléchargé.
    assert download.await_count >= 1
    # Le détecteur SMC a été appelé au moins sur le timeframe d'entrée (M5).
    detected_timeframes = {c[0][0].timeframe.value for c in detector.calls if c[0]}
    assert TimeFrame.M5.value in detected_timeframes


@pytest.mark.asyncio
async def test_htf_cache_reuses_h1_on_second_call() -> None:
    htf = [_candle(i, TimeFrame.H1) for i in range(6)]
    ltf = [_candle(i, TimeFrame.M5) for i in range(10)]
    detector = _Detector()
    download = AsyncMock(return_value=[_candle(i, TimeFrame.H4) for i in range(6)])

    builder = MarketContextBuilder(smc_detector=detector, download_data=download)
    ctx1 = await builder.build("EURUSD", htf, ltf)
    smoke = [c for c in detector.calls if c[0] and c[0][0].timeframe == TimeFrame.H1]
    h1_call_count_first = len(smoke)

    # Même bougie H1 (même heure) → le cache H1 évite une redétection.
    ctx2 = await builder.build("EURUSD", htf, ltf)
    smoke2 = [c for c in detector.calls if c[0] and c[0][0].timeframe == TimeFrame.H1]
    assert ctx1 is not None and ctx2 is not None
    assert ctx1.master_trend == ctx2.master_trend
    assert len(smoke2) == h1_call_count_first