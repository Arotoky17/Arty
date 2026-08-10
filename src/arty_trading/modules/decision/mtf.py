"""Analyse multi-timeframe utilisée par le moteur de décision."""

from __future__ import annotations

from dataclasses import dataclass

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept, TimeFrame
from arty_trading.core.interfaces import IMarketDataProvider, ISMCDetector

MTF_HIERARCHY = (
    TimeFrame.D1,
    TimeFrame.H4,
    TimeFrame.H1,
    TimeFrame.M15,
    TimeFrame.M5,
    TimeFrame.M1,
)


@dataclass(frozen=True)
class MultiTimeframeAnalysis:
    """Données SMC et tendance dérivée pour chaque timeframe ICT."""

    candles: dict[TimeFrame, list[Candle]]
    detections: dict[TimeFrame, list[dict]]
    trends: dict[str, str]


class MultiTimeframeAnalyzer:
    """Télécharge et analyse la hiérarchie ICT sans effet de bord."""

    def __init__(self, market_data: IMarketDataProvider, detector: ISMCDetector) -> None:
        self._market_data = market_data
        self._detector = detector

    async def analyze(self, symbol: str, count: int = 100) -> MultiTimeframeAnalysis:
        """Analyse D1 → M1 et dérive les tendances D1/H4/H1."""
        candles_by_tf: dict[TimeFrame, list[Candle]] = {}
        detections_by_tf: dict[TimeFrame, list[dict]] = {}
        trends: dict[str, str] = {}
        for timeframe in MTF_HIERARCHY:
            candles = await self._market_data.get_latest_candles(symbol, timeframe, count)
            candles_by_tf[timeframe] = candles
            detections = await self._detector.detect(candles, symbol) if candles else []
            detections_by_tf[timeframe] = detections
            if timeframe in (TimeFrame.D1, TimeFrame.H4, TimeFrame.H1):
                trend = self._derive_trend(detections)
                if trend is not None:
                    trends[timeframe.value] = trend
        return MultiTimeframeAnalysis(candles_by_tf, detections_by_tf, trends)

    @staticmethod
    def _derive_trend(detections: list[dict]) -> str | None:
        structural = [
            item
            for item in detections
            if item.get("concept")
            in {
                SMCConcept.BOS.value,
                SMCConcept.INTERNAL_BOS.value,
                SMCConcept.EXTERNAL_BOS.value,
                SMCConcept.MSS.value,
            }
            and item.get("direction") in {"bullish", "bearish"}
        ]
        if not structural:
            return None
        return max(structural, key=lambda item: item.get("index", -1))["direction"]
