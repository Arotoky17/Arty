"""
Détecteur Premium/Discount et Optimal Trade Entry (OTE).

Concepts ICT
------------
- **Premium / Discount** : division du range en zones
  - Premium (upper half) : zone de vente (sell zone)
  - Discount (lower half) : zone d'achat (buy zone)
  - Equilibrium (50%) : fair value
- **OTE (Optimal Trade Entry)** : zone de retracement Fibonacci
  - 0.62 - 0.79 retracement du range
  - Sweet spot à 0.705 (70.5%)

Aucune fonction ici n'ouvre de trade. Le détecteur retourne uniquement des
informations de marché.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import (
    BaseDetector,
    SMCDetection,
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
        ref_index = max(latest_high.index, latest_low.index)

        # Premium/Discount combiné (rétro-compatibilité)
        detections.append(
            SMCDetection(
                concept=SMCConcept.PREMIUM_DISCOUNT,
                direction="neutral",
                price=equilibrium,
                index=ref_index,
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

        # Détection PREMIUM (zone de vente, upper half)
        detections.append(
            SMCDetection(
                concept=SMCConcept.PREMIUM,
                direction="bearish",
                price=equilibrium,
                index=ref_index,
                details={
                    "zone_start": float(equilibrium),
                    "zone_end": float(latest_high.price),
                    "equilibrium": float(equilibrium),
                    "swing_high": float(latest_high.price),
                    "swing_low": float(latest_low.price),
                    "range_size": float(range_size),
                    "current_price": float(candles[-1].close),
                    "in_premium": bool(candles[-1].close > equilibrium),
                },
            )
        )

        # Détection DISCOUNT (zone d'achat, lower half)
        detections.append(
            SMCDetection(
                concept=SMCConcept.DISCOUNT,
                direction="bullish",
                price=equilibrium,
                index=ref_index,
                details={
                    "zone_start": float(latest_low.price),
                    "zone_end": float(equilibrium),
                    "equilibrium": float(equilibrium),
                    "swing_high": float(latest_high.price),
                    "swing_low": float(latest_low.price),
                    "range_size": float(range_size),
                    "current_price": float(candles[-1].close),
                    "in_discount": bool(candles[-1].close < equilibrium),
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


# =============================================================================
# Diagnostic Premium/Discount (Phase 3A — audit instrumentation)
# =============================================================================


@dataclass
class PremiumDiscountDiagnostic:
    """Résultat détaillé du calcul Premium/Discount pour diagnostic.

    Expose toutes les valeurs utilisées par le calcul existant, sans
    modifier la logique de validation.
    """

    valid: bool
    reason: str
    symbol: str = ""
    direction: str = ""
    timeframe: str = ""
    swing_high: float = 0.0
    swing_low: float = 0.0
    range_high: float = 0.0
    range_low: float = 0.0
    range_size: float = 0.0
    midpoint_50: float = 0.0
    current_price: float = 0.0
    location: str = "unknown"  # "premium", "discount", "equilibrium"
    expected_location: str = ""
    in_correct_zone: bool = False
    distance_to_midpoint: float = 0.0
    pct_from_midpoint: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "symbol": self.symbol,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_size": self.range_size,
            "midpoint_50": self.midpoint_50,
            "current_price": self.current_price,
            "location": self.location,
            "expected_location": self.expected_location,
            "in_correct_zone": self.in_correct_zone,
            "distance_to_midpoint": self.distance_to_midpoint,
            "pct_from_midpoint": self.pct_from_midpoint,
        }


def premium_discount_diagnostic(
    candles: list[Candle],
    smc_data: list[dict[str, Any]],
    direction: str,
    symbol: str = "",
    timeframe: str = "M5",
    swing_window: int = 2,
) -> PremiumDiscountDiagnostic:
    """Calcule le diagnostic Premium/Discount en utilisant la même logique
    que ``PremiumDiscountDetector.detect`` sans modifier le comportement.

    Args:
        candles: Bougies du timeframe analysé (M5 par défaut).
        smc_data: Détections SMC (contient éventuellement une detection
            ``premium_discount`` déjà calculée).
        direction: "bullish" ou "bearish" (sens du signal).
        symbol: Symbole pour le diagnostic.
        timeframe: Timeframe des bougies (pour le rapport).
        swing_window: Fenêtre pour les swing points (identique au détecteur).

    Returns:
        PremiumDiscountDiagnostic avec toutes les valeurs du calcul.
    """
    if not candles or len(candles) < 5:
        return PremiumDiscountDiagnostic(
            valid=False,
            reason="INSUFFICIENT_DATA",
            symbol=symbol,
            direction=direction,
            timeframe=timeframe,
        )

    # Prefer existing detection in smc_data if available (matches production path)
    pd_detections = [
        d for d in smc_data
        if d.get("concept") == SMCConcept.PREMIUM_DISCOUNT.value
    ]

    if pd_detections:
        latest = max(pd_detections, key=lambda d: d.get("index", 0))
        d = latest.get("details", {})
        equilibrium = float(d.get("equilibrium", 0.0))
        current_price = float(d.get("current_price", candles[-1].close))
        range_size = float(d.get("range_size", 0.0))
        location = d.get("current_zone", "unknown")
        expected = "discount" if direction == "bullish" else "premium"
        in_correct = location == expected
        distance = abs(current_price - equilibrium)
        pct = (distance / range_size * 100.0) if range_size > 0 else 0.0
        return PremiumDiscountDiagnostic(
            valid=in_correct,
            reason="OK" if in_correct else (
                f"Prix en zone {location} (achat nécessite discount / "
                f"vente nécessite premium)"
            ),
            symbol=symbol,
            direction=direction,
            timeframe=timeframe,
            swing_high=float(d.get("swing_high", 0.0)),
            swing_low=float(d.get("swing_low", 0.0)),
            range_high=float(d.get("swing_high", 0.0)),
            range_low=float(d.get("swing_low", 0.0)),
            range_size=range_size,
            midpoint_50=equilibrium,
            current_price=current_price,
            location=location,
            expected_location=expected,
            in_correct_zone=in_correct,
            distance_to_midpoint=distance,
            pct_from_midpoint=round(pct, 2),
        )

    # Fallback: recompute from raw candles (same algorithm as detect())
    detector = PremiumDiscountDetector(swing_window=swing_window)
    detections = detector.detect(candles)

    pd_dets: list[SMCDetection] = [
        d for d in detections
        if d.concept == SMCConcept.PREMIUM_DISCOUNT
    ]

    if not pd_dets:
        swing_highs = find_swing_highs(candles, swing_window)
        swing_lows = find_swing_lows(candles, swing_window)

        if not swing_highs or not swing_lows:
            return PremiumDiscountDiagnostic(
                valid=False,
                reason="NO_SWING_POINTS",
                symbol=symbol,
                direction=direction,
                timeframe=timeframe,
            )

        latest_high = swing_highs[-1]
        latest_low = swing_lows[-1]

        if latest_high.price <= latest_low.price:
            return PremiumDiscountDiagnostic(
                valid=False,
                reason="INVALID_RANGE",
                symbol=symbol,
                direction=direction,
                timeframe=timeframe,
            )

        range_size = float(latest_high.price - latest_low.price)
        equilibrium = float(latest_low.price) + (range_size / 2.0)
        current_price = float(candles[-1].close)
        location = "premium" if current_price > float(equilibrium) else "discount"
        expected = "discount" if direction == "bullish" else "premium"
        in_correct = location == expected
        distance = abs(current_price - float(equilibrium))
        pct = (distance / float(range_size) * 100.0) if float(range_size) > 0 else 0.0

        return PremiumDiscountDiagnostic(
            valid=in_correct,
            reason="OK" if in_correct else (
                f"Prix en zone {location} (achat nécessite discount / "
                f"vente nécessite premium)"
            ),
            symbol=symbol,
            direction=direction,
            timeframe=timeframe,
            swing_high=float(latest_high.price),
            swing_low=float(latest_low.price),
            range_high=float(latest_high.price),
            range_low=float(latest_low.price),
            range_size=float(range_size),
            midpoint_50=float(equilibrium),
            current_price=current_price,
            location=location,
            expected_location=expected,
            in_correct_zone=in_correct,
            distance_to_midpoint=distance,
            pct_from_midpoint=round(pct, 2),
        )

    latest_det = pd_dets[0]
    for det in pd_dets[1:]:
        if det.index > latest_det.index:
            latest_det = det
    d = latest_det.details
    equilibrium = float(d.get("equilibrium", 0.0))
    current_price = float(d.get("current_price", candles[-1].close))
    range_size = float(d.get("range_size", 0.0))
    location = d.get("current_zone", "unknown")
    expected = "discount" if direction == "bullish" else "premium"
    in_correct = location == expected
    distance = abs(current_price - equilibrium)
    pct = (distance / range_size * 100.0) if range_size > 0 else 0.0

    return PremiumDiscountDiagnostic(
        valid=in_correct,
        reason="OK" if in_correct else (
            f"Prix en zone {location} (achat nécessite discount / "
            f"vente nécessite premium)"
        ),
        symbol=symbol,
        direction=direction,
        timeframe=timeframe,
        swing_high=float(d.get("swing_high", 0.0)),
        swing_low=float(d.get("swing_low", 0.0)),
        range_high=float(d.get("premium_end", float(d.get("swing_high", 0.0)))),
        range_low=float(d.get("discount_start", float(d.get("swing_low", 0.0)))),
        range_size=range_size,
        midpoint_50=equilibrium,
        current_price=current_price,
        location=location,
        expected_location=expected,
        in_correct_zone=in_correct,
        distance_to_midpoint=distance,
        pct_from_midpoint=round(pct, 2),
    )
