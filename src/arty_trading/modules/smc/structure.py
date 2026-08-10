"""
Détecteur de structure de marché — BOS, Internal/External BOS, CHoCH, MSS.

Concepts ICT
------------
- **BOS (Break of Structure)** : cassure d'un swing high/low dans le sens de la
  tendance. Désigne la continuation.
- **Internal BOS** : cassure d'un swing point *interne* (mineur / faible).
  Représente la continuation de la structure mineure.
- **External BOS** : cassure d'un swing point *externe* (majeur / fort).
  Représente la continuation de la structure majeure.
- **CHoCH (Change of Character)** : cassure contre la tendance (signal de
  retournement).
- **MSS (Market Structure Shift)** : retournement significatif de structure,
  confirmé par une cassure contre-tendance.

Règles de classification
------------------------
1. On identifie les swing points (internes et externes).
2. On détermine la tendance courante (HH/HL = haussière, LH/LL = baissière).
3. Une cassure dans le sens de la tendance = BOS.
   - Si le swing cassé est *internal* → Internal BOS.
   - Si le swing cassé est *external* → External BOS.
4. Une cassure contre la tendance = CHoCH.
5. Un CHoCH confirmé (changement de tendance) = MSS.

Aucune fonction ici n'ouvre de trade. Le détecteur retourne uniquement des
informations de marché.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import (
    BaseDetector,
    SMCDetection,
    SwingPoint,
    find_swing_points,
)


class StructureDetector(BaseDetector):
    """
    Détecteur de structure de marché.

    Détecte les Break of Structure (BOS), Internal/External BOS, Change of
    Character (CHoCH) et Market Structure Shift (MSS) en analysant les swing
    points et les cassures de niveaux.
    """

    def __init__(
        self,
        enabled: bool = True,
        swing_window: int = 2,
        external_window: int = 5,
        confirmation_bars: int = 1,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            swing_window: Fenêtre pour les swing points internes (2 = fractal)
            external_window: Fenêtre élargie pour qualifier un pivot d'external
            confirmation_bars: Nombre de bougies de confirmation après la cassure
        """
        super().__init__(enabled=enabled)
        self._swing_window = swing_window
        self._external_window = external_window
        self._confirmation_bars = confirmation_bars

    @property
    def name(self) -> str:
        return "structure"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte BOS, Internal/External BOS, CHoCH et MSS sur les bougies.

        Algorithme :
        1. Identifier les swing points (avec classification internal/external)
        2. Déterminer la tendance (HH/HL = haussière, LH/LL = baissière)
        3. Détecter les cassures de swing highs/lows
        4. Classifier en BOS (sens de la tendance) ou CHoCH (contre tendance)
        5. BOS est subdivisé en Internal BOS ou External BOS selon la force du swing
        6. MSS = CHoCH confirmé par un changement de tendance
        """
        if not self._enabled or len(candles) < 5:
            return []

        swing_points = find_swing_points(candles, self._swing_window, self._external_window)
        if len(swing_points) < 2:
            return []

        detections: list[SMCDetection] = []
        trend = "unknown"  # "bullish", "bearish", "unknown"

        # Suivre les derniers swing highs/lows non encore cassés
        pending_high: SwingPoint | None = None
        pending_low: SwingPoint | None = None

        # Index du dernier swing point traité
        sp_idx = 0

        for i in range(len(candles)):
            # Mettre à jour les swing points disponibles jusqu'à i
            while sp_idx < len(swing_points) and swing_points[sp_idx].index <= i:
                sp = swing_points[sp_idx]
                if sp.type == "high":
                    if pending_high is None or sp.price > pending_high.price:
                        pending_high = sp
                else:
                    if pending_low is None or sp.price < pending_low.price:
                        pending_low = sp
                sp_idx += 1

            if pending_high is None or pending_low is None:
                continue

            # Vérifier la cassure du swing high (bullish)
            if candles[i].close > pending_high.price and i > pending_high.index:
                strength = pending_high.strength
                if trend in ("bullish", "unknown"):
                    # BOS bullish — Internal ou External selon la force du swing
                    bos_concept = (
                        SMCConcept.EXTERNAL_BOS if strength == "external" else SMCConcept.INTERNAL_BOS
                    )
                    detections.append(
                        SMCDetection(
                            concept=bos_concept,
                            direction="bullish",
                            price=pending_high.price,
                            index=i,
                            details={
                                "broken_level": float(pending_high.price),
                                "broken_index": pending_high.index,
                                "trend": trend,
                                "swing_strength": strength,
                                "swing_window": pending_high.window,
                                "swing_amplitude": float(pending_high.amplitude),
                            },
                        )
                    )
                    # Aussi émettre un BOS générique pour compatibilité
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.BOS,
                            direction="bullish",
                            price=pending_high.price,
                            index=i,
                            details={
                                "broken_level": float(pending_high.price),
                                "broken_index": pending_high.index,
                                "trend": trend,
                                "swing_strength": strength,
                                "subtype": "external" if strength == "external" else "internal",
                            },
                        )
                    )
                    trend = "bullish"
                else:
                    # CHoCH bullish (cassure contre tendance baissière)
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.CHOCH,
                            direction="bullish",
                            price=pending_high.price,
                            index=i,
                            details={
                                "broken_level": float(pending_high.price),
                                "broken_index": pending_high.index,
                                "previous_trend": trend,
                                "swing_strength": strength,
                            },
                        )
                    )
                    # MSS si on avait une tendance baissière
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.MSS,
                            direction="bullish",
                            price=pending_high.price,
                            index=i,
                            details={
                                "shift_from": "bearish",
                                "shift_to": "bullish",
                                "broken_level": float(pending_high.price),
                                "swing_strength": strength,
                            },
                        )
                    )
                    trend = "bullish"

                # Réinitialiser le swing high cassé
                pending_high = None

            # Vérifier la cassure du swing low (bearish)
            if pending_low is not None and candles[i].close < pending_low.price and i > pending_low.index:
                strength = pending_low.strength
                if trend in ("bearish", "unknown"):
                    # BOS bearish — Internal ou External selon la force du swing
                    bos_concept = (
                        SMCConcept.EXTERNAL_BOS if strength == "external" else SMCConcept.INTERNAL_BOS
                    )
                    detections.append(
                        SMCDetection(
                            concept=bos_concept,
                            direction="bearish",
                            price=pending_low.price,
                            index=i,
                            details={
                                "broken_level": float(pending_low.price),
                                "broken_index": pending_low.index,
                                "trend": trend,
                                "swing_strength": strength,
                                "swing_window": pending_low.window,
                                "swing_amplitude": float(pending_low.amplitude),
                            },
                        )
                    )
                    # Aussi émettre un BOS générique pour compatibilité
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.BOS,
                            direction="bearish",
                            price=pending_low.price,
                            index=i,
                            details={
                                "broken_level": float(pending_low.price),
                                "broken_index": pending_low.index,
                                "trend": trend,
                                "swing_strength": strength,
                                "subtype": "external" if strength == "external" else "internal",
                            },
                        )
                    )
                    trend = "bearish"
                else:
                    # CHoCH bearish (cassure contre tendance haussière)
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.CHOCH,
                            direction="bearish",
                            price=pending_low.price,
                            index=i,
                            details={
                                "broken_level": float(pending_low.price),
                                "broken_index": pending_low.index,
                                "previous_trend": trend,
                                "swing_strength": strength,
                            },
                        )
                    )
                    # MSS si on avait une tendance haussière
                    detections.append(
                        SMCDetection(
                            concept=SMCConcept.MSS,
                            direction="bearish",
                            price=pending_low.price,
                            index=i,
                            details={
                                "shift_from": "bullish",
                                "shift_to": "bearish",
                                "broken_level": float(pending_low.price),
                                "swing_strength": strength,
                            },
                        )
                    )
                    trend = "bearish"

                # Réinitialiser le swing low cassé
                pending_low = None

        return detections