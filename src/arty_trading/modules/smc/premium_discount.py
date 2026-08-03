"""
Détecteur Premium/Discount et Optimal Trade Entry (OTE).

- **Premium / Discount** : division du range en zones
  - Premium (upper half) : zone de vente
  - Discount (lower half) : zone d'achat
  - Equilibrium (50%) : fair value
- **OTE (Optimal Trade Entry)** : zone de retracement Fibonacci
  - 0.62 - 0.79 retracement du range
  - Sweet spot à 0.705 (70.5%)
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
)


class PremiumDiscountDetector(BaseDetector):
    """
    Détecteur Premium/Discount et OTE.

    Divise le range actuel (swing high → swing low) en zones de premium
    et discount, et identifie la zone OTE (Optimal Trade Entry).
    """

    def __init__(
        self,
        enabled: bool = True,
        swing_window: int = 2,
        ote_min: float = 0.62,
        ote_max: float = 0.79,
        ote_sweet_spot: float = 0.705,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            swing_window: Fenêtre pour les swing points
            ote_min: Retracement minimum pour l'OTE (0.62 = 62%)
            ote_max: Retracement maximum pour l'OTE (0.79 = 79%)
            ote_sweet_spot: Point optimal d'entrée (0.705 = 70.5%)
        """
        super().__init__(enabled=enabled)
        self._swing_window = swing_window
        self._ote_min = Decimal(str(ote_min))
        self._ote_max = Decimal(str(ote_max))
        self._ote_sweet_spot = Decimal(str(ote_sweet_spot))

    @property
    def name(self) -> str:
        return "premium_discount"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les zones Premium/Discount et OTE.

        Algorithme :
        1. Identifier le swing high et swing low les plus récents
        2. Calculer le range et l'equilibrium (50%)
        3. Premium = upper half, Discount = lower half
        4. OTE = zone de retracement 62%-79% du range
        """
        if not self._enabled or len(candles) < 5:
            return []

        detections: list[SMCDetection] = []

        swing_highs = find_swing_highs(candles, self._swing_window)
        swing_lows = find_swing_lows(candles, self._swing_window)

        if not swing_highs or not swing_lows:
            return detections

        # Prendre le swing high et swing low les plus récents
        latest_high = swing_highs[-1]
        latest_low = swing_lows[-1]

        # S'assurer qu'ils forment un range valide
        if latest_high.price <= latest_low.price:
            return detections

        range_size = latest_high.price - latest_low.price
        equilibrium = latest_low.price + (range_size / 2)

        # Premium (upper half) et Discount (lower half)
        detections.append(
            SMCDetection(
                concept=SMCConcept.PREMIUM_DISCOUNT,
                direction="neutral",
                price=equilibrium,
                index=max(latest_high.index, latest_low.index),
                details={
                    "swing_high": float(latest_high.price),
                    "swing_low": float(latest_low.price),
                    "range_size": float(range_size),
                    "equilibrium": float(equilibrium),
                    "premium_start": float(equilibrium),
                    "premium_end": float(latest_high.price),
                    "discount_start": float(latest_low.price),
                    "discount_end": float(equilibrium),
                    "current_price": float(candles[-1].close),
                    "current_zone": "premium" if candles[-1].close > equilibrium else "discount",
                },
            )
        )

        # OTE pour un retracement haussier (depuis le swing low)
        # OTE zone = entre 62% et 79% du retracement
        ote_low_bullish = latest_low.price + (range_size * self._ote_min)
        ote_high_bullish = latest_low.price + (range_size * self._ote_max)
        ote_sweet_bullish = latest_low.price + (range_size * self._ote_sweet_spot)

        detections.append(
            SMCDetection(
                concept=SMCConcept.OTE,
                direction="bullish",
                price=ote_sweet_bullish,
                index=latest_low.index,
                details={
                    "swing_high": float(latest_high.price),
                    "swing_low": float(latest_low.price),
                    "ote_zone_low": float(ote_low_bullish),
                    "ote_zone_high": float(ote_high_bullish),
                    "ote_sweet_spot": float(ote_sweet_bullish),
                    "retracement_min": float(self._ote_min),
                    "retracement_max": float(self._ote_max),
                    "direction": "bullish (buy zone)",
                },
            )
        )

        # OTE pour un retracement baissier (depuis le swing high)
        ote_low_bearish = latest_high.price - (range_size * self._ote_max)
        ote_high_bearish = latest_high.price - (range_size * self._ote_min)
        ote_sweet_bearish = latest_high.price - (range_size * self._ote_sweet_spot)

        detections.append(
            SMCDetection(
                concept=SMCConcept.OTE,
                direction="bearish",
                price=ote_sweet_bearish,
                index=latest_high.index,
                details={
                    "swing_high": float(latest_high.price),
                    "swing_low": float(latest_low.price),
                    "ote_zone_low": float(ote_low_bearish),
                    "ote_zone_high": float(ote_high_bearish),
                    "ote_sweet_spot": float(ote_sweet_bearish),
                    "retracement_min": float(self._ote_min),
                    "retracement_max": float(self._ote_max),
                    "direction": "bearish (sell zone)",
                },
            )
        )

        return detections