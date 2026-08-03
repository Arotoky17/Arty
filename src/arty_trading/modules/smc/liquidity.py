"""
Détecteur de liquidité — Liquidity Sweep, Equal High, Equal Low.

- **Liquidity Sweep** : le prix dépasse brièvement un swing high/low puis inverse
  - Bullish sweep : prix descend sous un swing low puis remonte (stop hunt)
  - Bearish sweep : prix monte au-dessus d'un swing high puis descend
- **Equal High (EQH)** : deux ou plusieurs swing highs au même niveau
- **Equal Low (EQL)** : deux ou plusieurs swing lows au même niveau
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import (
    BaseDetector,
    SMCDetection,
    SwingPoint,
    find_swing_highs,
    find_swing_lows,
    find_swing_points,
)


class LiquidityDetector(BaseDetector):
    """
    Détecteur de liquidité.

    Détecte les Liquidity Sweeps (stop hunts), Equal Highs et Equal Lows
    qui représentent des zones de liquidité importantes pour le trading SMC.
    """

    def __init__(
        self,
        enabled: bool = True,
        swing_window: int = 2,
        tolerance_pips: float = 2.0,
        pip_size: float = 0.0001,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            swing_window: Fenêtre pour les swing points
            tolerance_pips: Tolérance en pips pour Equal High/Low
            pip_size: Taille d'un pip
        """
        super().__init__(enabled=enabled)
        self._swing_window = swing_window
        self._tolerance = Decimal(str(tolerance_pips * pip_size))

    @property
    def name(self) -> str:
        return "liquidity"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les Liquidity Sweeps, Equal Highs et Equal Lows.

        Algorithme :
        1. Identifier les swing highs et swing lows
        2. Liquidity Sweep : prix dépasse un swing puis inverse
        3. Equal High : swing highs au même niveau (± tolérance)
        4. Equal Low : swing lows au même niveau (± tolérance)
        """
        if not self._enabled or len(candles) < 5:
            return []

        detections: list[SMCDetection] = []

        # Détecter les liquidity sweeps
        detections.extend(self._detect_sweeps(candles))

        # Détecter les equal highs/lows
        detections.extend(self._detect_equal_levels(candles))

        return detections

    def _detect_sweeps(self, candles: list[Candle]) -> list[SMCDetection]:
        """Détecte les liquidity sweeps (stop hunts)."""
        detections: list[SMCDetection] = []
        swing_points = find_swing_points(candles, self._swing_window)

        if len(swing_points) < 2:
            return detections

        for sp in swing_points:
            # Chercher une bougie après le swing point qui dépasse le niveau
            # puis inverse
            for i in range(sp.index + 1, min(sp.index + 20, len(candles))):
                candle = candles[i]

                if sp.type == "low":
                    # Bullish sweep : prix descend sous le swing low puis remonte
                    if candle.low < sp.price and candle.close > sp.price:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.LIQUIDITY_SWEEP,
                                direction="bullish",
                                price=sp.price,
                                index=i,
                                details={
                                    "swept_level": float(sp.price),
                                    "swept_index": sp.index,
                                    "sweep_low": float(candle.low),
                                    "type": "buy_side_liquidity_grab",
                                },
                            )
                        )
                        break  # Un seul sweep par swing point

                elif sp.type == "high":
                    # Bearish sweep : prix monte au-dessus du swing high puis descend
                    if candle.high > sp.price and candle.close < sp.price:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.LIQUIDITY_SWEEP,
                                direction="bearish",
                                price=sp.price,
                                index=i,
                                details={
                                    "swept_level": float(sp.price),
                                    "swept_index": sp.index,
                                    "sweep_high": float(candle.high),
                                    "type": "sell_side_liquidity_grab",
                                },
                            )
                        )
                        break  # Un seul sweep par swing point

        return detections

    def _detect_equal_levels(self, candles: list[Candle]) -> list[SMCDetection]:
        """Détecte les Equal Highs et Equal Lows."""
        detections: list[SMCDetection] = []

        swing_highs = find_swing_highs(candles, self._swing_window)
        swing_lows = find_swing_lows(candles, self._swing_window)

        # Equal Highs
        for i in range(len(swing_highs)):
            for j in range(i + 1, len(swing_highs)):
                price_diff = abs(swing_highs[i].price - swing_highs[j].price)
                if price_diff <= self._tolerance:
                    avg_price = (swing_highs[i].price + swing_highs[j].price) / 2
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.EQUAL_HIGH,
                            direction="neutral",
                            price=avg_price,
                            index=swing_highs[j].index,
                            details={
                                "level1_index": swing_highs[i].index,
                                "level1_price": float(swing_highs[i].price),
                                "level2_index": swing_highs[j].index,
                                "level2_price": float(swing_highs[j].price),
                                "avg_price": float(avg_price),
                                "tolerance": float(self._tolerance),
                            },
                        )
                    )

        # Equal Lows
        for i in range(len(swing_lows)):
            for j in range(i + 1, len(swing_lows)):
                price_diff = abs(swing_lows[i].price - swing_lows[j].price)
                if price_diff <= self._tolerance:
                    avg_price = (swing_lows[i].price + swing_lows[j].price) / 2
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.EQUAL_LOW,
                            direction="neutral",
                            price=avg_price,
                            index=swing_lows[j].index,
                            details={
                                "level1_index": swing_lows[i].index,
                                "level1_price": float(swing_lows[i].price),
                                "level2_index": swing_lows[j].index,
                                "level2_price": float(swing_lows[j].price),
                                "avg_price": float(avg_price),
                                "tolerance": float(self._tolerance),
                            },
                        )
                    )

        return detections