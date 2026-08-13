"""
Détecteur de Fair Value Gap (FVG) et Inverse FVG (IFVG).

- **FVG (Fair Value Gap)** : gap de 3 bougies où le prix a bougé trop vite
  - Bullish FVG : high de la bougie 0 < low de la bougie 2
  - Bearish FVG : low de la bougie 0 > high de la bougie 2
- **IFVG (Inverse FVG)** : un FVG rempli puis inversé
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import BaseDetector, SMCDetection


class FairValueGapDetector(BaseDetector):
    """
    Détecteur de Fair Value Gap (FVG) et Inverse FVG (IFVG).

    Un FVG est un déséquilibre (imbalance) créé par 3 bougies consécutives
    où le prix s'est déplacé trop rapidement pour que le marché l'absorbe.
    """

    MAX_ZONE_AGE = 30

    def __init__(
        self,
        enabled: bool = True,
        min_gap_pips: float = 5.0,
        pip_size: float = 0.0001,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            min_gap_pips: Taille minimale du gap en pips (5.0 par défaut - filtre les faux positifs)
            pip_size: Taille d'un pip (0.0001 pour EURUSD, 0.01 pour JPY)
        """
        super().__init__(enabled=enabled)
        self._min_gap = Decimal(str(min_gap_pips * pip_size))
        self._pip_size = pip_size

    @property
    def name(self) -> str:
        return "fair_value_gap"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les FVG et IFVG sur les bougies.

        Algorithme :
        1. Parcourir les bougies par groupes de 3
        2. Bullish FVG : high[0] < low[2] → gap entre high[0] et low[2]
        3. Bearish FVG : low[0] > high[2] → gap entre low[0] et high[2]
        4. IFVG : vérifier si un FVG précédent a été rempli puis inversé
        5. Filtrer les zones trop anciennes (MAX_ZONE_AGE bougies)
        """
        if not self._enabled or len(candles) < 3:
            return []

        detections: list[SMCDetection] = []
        active_fvgs: list[dict] = []  # FVG non encore remplis

        for i in range(len(candles) - 2):
            c0 = candles[i]
            c2 = candles[i + 2]

            # Bullish FVG : high[0] < low[2]
            if c0.high < c2.low:
                gap_size = c2.low - c0.high
                if gap_size >= self._min_gap:
                    fvg = SMCDetection(
                        concept=SMCConcept.FVG,
                        direction="bullish",
                        price=c0.high,
                        index=i + 1,
                        details={
                            "gap_top": float(c2.low),
                            "gap_bottom": float(c0.high),
                            "gap_size": float(gap_size),
                            "candle_index": i + 1,
                        },
                    )
                    detections.append(fvg)
                    active_fvgs.append({
                        "detection": fvg,
                        "gap_top": c2.low,
                        "gap_bottom": c0.high,
                        "direction": "bullish",
                        "filled": False,
                        "start_index": i + 1,
                    })

            # Bearish FVG : low[0] > high[2]
            if c0.low > c2.high:
                gap_size = c0.low - c2.high
                if gap_size >= self._min_gap:
                    fvg = SMCDetection(
                        concept=SMCConcept.FVG,
                        direction="bearish",
                        price=c0.low,
                        index=i + 1,
                        details={
                            "gap_top": float(c0.low),
                            "gap_bottom": float(c2.high),
                            "gap_size": float(gap_size),
                            "candle_index": i + 1,
                        },
                    )
                    detections.append(fvg)
                    active_fvgs.append({
                        "detection": fvg,
                        "gap_top": c0.low,
                        "gap_bottom": c2.high,
                        "direction": "bearish",
                        "filled": False,
                        "start_index": i + 1,
                    })

            # Vérifier si un FVG actif est rempli ou inversé
            for fvg in active_fvgs:
                if fvg["filled"]:
                    continue

                # Bullish FVG rempli si le prix redescend sous gap_bottom
                if fvg["direction"] == "bullish":
                    if candles[i + 2].close < fvg["gap_bottom"]:
                        fvg["filled"] = True
                        # IFVG bearish : le FVG bullish a été rempli puis inversé
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.IFVG,
                                direction="bearish",
                                price=fvg["gap_bottom"],
                                index=i + 2,
                                details={
                                    "original_fvg_direction": "bullish",
                                    "gap_top": float(fvg["gap_top"]),
                                    "gap_bottom": float(fvg["gap_bottom"]),
                                    "fill_index": i + 2,
                                },
                            )
                        )

                # Bearish FVG rempli si le prix remonte au-dessus gap_top
                elif fvg["direction"] == "bearish":
                    if candles[i + 2].close > fvg["gap_top"]:
                        fvg["filled"] = True
                        # IFVG bullish : le FVG bearish a été rempli puis inversé
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.IFVG,
                                direction="bullish",
                                price=fvg["gap_top"],
                                index=i + 2,
                                details={
                                    "original_fvg_direction": "bearish",
                                    "gap_top": float(fvg["gap_top"]),
                                    "gap_bottom": float(fvg["gap_bottom"]),
                                    "fill_index": i + 2,
                                },
                            )
                        )

        detections = [
            d for d in detections
            if (len(candles) - 1 - d.index) <= self.MAX_ZONE_AGE
        ]

        return detections
