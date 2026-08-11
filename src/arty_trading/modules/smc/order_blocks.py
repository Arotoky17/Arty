"""
Détecteur d'Order Blocks, Breaker Blocks et Mitigation Blocks.

- **Order Block (OB)** : dernière bougie opposée avant un mouvement fort
  - Bullish OB : dernière bougie baissière avant une forte hausse
  - Bearish OB : dernière bougie haussière avant une forte baisse
- **Breaker Block** : un OB échoué qui devient un niveau de résistance/support
- **Mitigation Block** : similaire à l'OB mais après un liquidity sweep
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import BaseDetector, SMCDetection


class OrderBlockDetector(BaseDetector):
    """
    Détecteur d'Order Blocks, Breaker Blocks et Mitigation Blocks.

    Les Order Blocks représentent les zones où les institutions ont placé
    leurs ordres. Le prix a tendance à revenir mitiguer ces zones.
    """

    def __init__(
        self,
        enabled: bool = True,
        displacement_threshold: float = 2.0,
        mitigation_lookback: int = 20,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            displacement_threshold: Facteur minimum du corps de la bougie de
                déplacement par rapport à la moyenne (ex: 2.0 = 2x la moyenne)
            mitigation_lookback: Nombre de bougies en arrière pour vérifier
                la mitigation
        """
        super().__init__(enabled=enabled)
        self._displacement_threshold = displacement_threshold
        self._mitigation_lookback = mitigation_lookback

    @property
    def name(self) -> str:
        return "order_blocks"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les Order Blocks, Breaker Blocks et Mitigation Blocks.

        Algorithme :
        1. Calculer la taille moyenne des corps de bougies
        2. Identifier les bougies de déplacement (corps > seuil × moyenne)
        3. Bullish OB : dernière bougie baissière avant un déplacement haussier
        4. Bearish OB : dernière bougie haussière avant un déplacement baissier
        5. Breaker : un OB dont le prix a cassé le niveau opposé
        6. Mitigation : vérifier si le prix est revenu mitigier l'OB
        """
        if not self._enabled or len(candles) < 5:
            return []

        detections: list[SMCDetection] = []

        # Calculer la taille moyenne des corps
        body_sizes = [float(abs(c.close - c.open)) for c in candles]
        avg_body = sum(body_sizes) / len(body_sizes) if body_sizes else 0

        if avg_body == 0:
            return []

        displacement_min = avg_body * self._displacement_threshold

        # Détecter les Order Blocks
        for i in range(1, len(candles) - 1):
            # Bullish OB : chercher une bougie baissière suivie d'un déplacement haussier
            if candles[i].close < candles[i].open:  # Bougie baissière
                # Vérifier le déplacement haussier suivant
                next_candle = candles[i + 1]
                next_body = float(abs(next_candle.close - next_candle.open))
                if next_candle.close > next_candle.open and next_body >= displacement_min:
                    ob_top = candles[i].high
                    ob_bottom = candles[i].low
                    # Vérifier si l'OB est mitigé plus tard
                    mitigated = self._is_mitigated(candles, i + 2, ob_top, ob_bottom, "bullish")
                    violated = self._is_violated(candles, i + 2, ob_bottom, "bullish")

                    if violated:
                        # Breaker Block bearish (OB bullish violé)
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.BREAKER_BLOCK,
                                direction="bearish",
                                price=ob_bottom,
                                index=i,
                                details={
                                    "ob_top": float(ob_top),
                                    "ob_bottom": float(ob_bottom),
                                    "displacement_index": i + 1,
                                    "displacement_size": next_body,
                                    "mitigated": mitigated,
                                },
                            )
                        )
                    else:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.ORDER_BLOCK,
                                direction="bullish",
                                price=ob_bottom,
                                index=i,
                                details={
                                    "ob_top": float(ob_top),
                                    "ob_bottom": float(ob_bottom),
                                    "displacement_index": i + 1,
                                    "displacement_size": next_body,
                                    "mitigated": mitigated,
                                },
                            )
                        )

            # Bearish OB : chercher une bougie haussière suivie d'un déplacement baissier
            if candles[i].close > candles[i].open:  # Bougie haussière
                # Vérifier le déplacement baissier suivant
                next_candle = candles[i + 1]
                next_body = float(abs(next_candle.close - next_candle.open))
                if next_candle.close < next_candle.open and next_body >= displacement_min:
                    ob_top = candles[i].high
                    ob_bottom = candles[i].low
                    # Vérifier si l'OB est mitigé plus tard
                    mitigated = self._is_mitigated(candles, i + 2, ob_top, ob_bottom, "bearish")
                    violated = self._is_violated(candles, i + 2, ob_top, "bearish")

                    if violated:
                        # Breaker Block bullish (OB bearish violé)
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.BREAKER_BLOCK,
                                direction="bullish",
                                price=ob_top,
                                index=i,
                                details={
                                    "ob_top": float(ob_top),
                                    "ob_bottom": float(ob_bottom),
                                    "displacement_index": i + 1,
                                    "displacement_size": next_body,
                                    "mitigated": mitigated,
                                },
                            )
                        )
                    else:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.ORDER_BLOCK,
                                direction="bearish",
                                price=ob_top,
                                index=i,
                                details={
                                    "ob_top": float(ob_top),
                                    "ob_bottom": float(ob_bottom),
                                    "displacement_index": i + 1,
                                    "displacement_size": next_body,
                                    "mitigated": mitigated,
                                },
                            )
                        )

        # Détecter les Mitigation Blocks (après un liquidity sweep)
        detections.extend(self._detect_mitigation_blocks(candles))

        return detections

    def _is_mitigated(
        self,
        candles: list[Candle],
        start_idx: int,
        ob_top: Decimal,
        ob_bottom: Decimal,
        direction: str,
    ) -> bool:
        """
        Vérifie si un Order Block a été mitigé (le prix est revenu le tester).

        Args:
            candles: Liste des bougies
            start_idx: Index à partir duquel vérifier
            ob_top: Limite haute de l'OB
            ob_bottom: Limite basse de l'OB
            direction: "bullish" ou "bearish"

        Returns:
            True si l'OB a été mitigé
        """
        end_idx = min(start_idx + self._mitigation_lookback, len(candles))

        for i in range(start_idx, end_idx):
            if direction == "bullish":
                # OB bullish mitigé si le prix redescend dans la zone
                if candles[i].low <= ob_top:
                    return True
            else:
                # OB bearish mitigé si le prix remonte dans la zone
                if candles[i].high >= ob_bottom:
                    return True

        return False

    def _is_violated(
        self,
        candles: list[Candle],
        start_idx: int,
        level: Decimal,
        direction: str,
    ) -> bool:
        """
        Vérifie si un Order Block a été violé (cassé dans le sens opposé).

        Args:
            candles: Liste des bougies
            start_idx: Index à partir duquel vérifier
            level: Niveau à vérifier
            direction: "bullish" ou "bearish"

        Returns:
            True si l'OB a été violé
        """
        end_idx = min(start_idx + self._mitigation_lookback, len(candles))

        for i in range(start_idx, end_idx):
            if direction == "bullish":
                # OB bullish violé si le prix casse en dessous
                if candles[i].close < level:
                    return True
            else:
                # OB bearish violé si le prix casse au-dessus
                if candles[i].close > level:
                    return True

        return False

    def _detect_mitigation_blocks(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les Mitigation Blocks.

        Un Mitigation Block se forme après un liquidity sweep :
        la dernière bougie opposée avant le mouvement de retournement
        qui suit un sweep de liquidité.
        """
        detections: list[SMCDetection] = []

        if len(candles) < 6:
            return detections

        # Identifier les sweeps simples (bougie avec mèche longue)
        for i in range(2, len(candles) - 3):
            # Vérifier si la bougie i a une mèche longue (sweep potentiel)
            candle = candles[i]
            body = abs(candle.close - candle.open)
            upper_wick = candle.high - max(candle.close, candle.open)
            lower_wick = min(candle.close, candle.open) - candle.low

            # Sweep baissier (mèche basse longue) suivi d'un retournement haussier
            if lower_wick > body * Decimal("1.5"):
                # Chercher la bougie baissière avant le retournement
                if i > 0 and candles[i - 1].close < candles[i - 1].open:
                    # Vérifier le déplacement haussier suivant
                    next_candle = candles[i + 1]
                    if next_candle.close > next_candle.open:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.MITIGATION_BLOCK,
                                direction="bullish",
                                price=candles[i - 1].low,
                                index=i - 1,
                                details={
                                    "sweep_index": i,
                                    "sweep_low": float(candle.low),
                                    "mitigation_top": float(candles[i - 1].high),
                                    "mitigation_bottom": float(candles[i - 1].low),
                                },
                            )
                        )

            # Sweep haussier (mèche haute longue) suivi d'un retournement baissier
            if upper_wick > body * Decimal("1.5"):
                # Chercher la bougie haussière avant le retournement
                if i > 0 and candles[i - 1].close > candles[i - 1].open:
                    # Vérifier le déplacement baissier suivant
                    next_candle = candles[i + 1]
                    if next_candle.close < next_candle.open:
                        detections.append(
                            SMCDetection(
                                concept=SMCConcept.MITIGATION_BLOCK,
                                direction="bearish",
                                price=candles[i - 1].high,
                                index=i - 1,
                                details={
                                    "sweep_index": i,
                                    "sweep_high": float(candle.high),
                                    "mitigation_top": float(candles[i - 1].high),
                                    "mitigation_bottom": float(candles[i - 1].low),
                                },
                            )
                        )

        return detections
