"""
Détecteur d'Order Blocks, Breaker Blocks et Mitigation Blocks.

- **Order Block (OB)** : dernière bougie opposée avant un mouvement fort
  - Bullish OB : dernière bougie baissière avant une forte hausse
  - Bearish OB : dernière bougie haussière avant une forte baisse
- **Breaker Block** : un OB échoué qui devient un niveau de résistance/support
- **Mitigation Block** : similaire à l'OB mais après un liquidity sweep

Le déplacement est validé relativement à l'ATR (seuil configurable),
jamais en distance fixe.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept
from arty_trading.modules.smc.base import BaseDetector, SMCDetection
from arty_trading.utils.helpers import calculate_atr


class OrderBlockDetector(BaseDetector):
    """
    Détecteur d'Order Blocks, Breaker Blocks et Mitigation Blocks.

    Les Order Blocks représentent les zones où les institutions ont placé
    leurs ordres. Le prix a tendance à revenir mitiguer ces zones.
    """

    MAX_ZONE_AGE = 30

    def __init__(
        self,
        enabled: bool = True,
        displacement_threshold: float = 1.0,
        mitigation_lookback: int = 20,
        max_mitigations: int = 2,
        max_ob_atr_mult: float = 0.0,
        displacement_confirmation_bars: int = 1,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver le détecteur
            displacement_threshold: Facteur minimum du corps de la bougie de
                déplacement par rapport à l'ATR (ex: 1.0 = 1x ATR).
                L'ATR est calculée sur les bougies fournies.
            mitigation_lookback: Nombre de bougies en arrière pour vérifier
                la mitigation
            max_mitigations: Nombre maximum de retours sur la zone avant de
                considérer celle-ci comme épuisée
            max_ob_atr_mult: Hauteur maximale de l'OB en multiple d'ATR.
                0 = désactivé. > 0 : filtre les OB gigantesques (clumps de
                volatilité) qui ne sont pas de vraies zones institutionnelles.
            displacement_confirmation_bars: Nombre de bougies directionnelles
                consécutives exigées après la bougie OB pour confirmer le
                déplacement (1 = comportement historique). Le corps cumulé
                doit dépasser le seuil de displacement et chaque bougie doit
                être dans le sens du déplacement.
        """
        super().__init__(enabled=enabled)
        self._displacement_threshold = displacement_threshold
        self._mitigation_lookback = mitigation_lookback
        self._max_mitigations = max_mitigations
        self._max_ob_atr_mult = max_ob_atr_mult
        self._displacement_confirmation_bars = max(1, displacement_confirmation_bars)

    @property
    def name(self) -> str:
        return "order_blocks"

    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les Order Blocks, Breaker Blocks et Mitigation Blocks.

        Algorithme :
        1. Calculer l'ATR sur les bougies fournies
        2. Identifier les bougies de déplacement (corps >= ATR × seuil)
        3. Bullish OB : dernière bougie baissière avant un déplacement haussier
        4. Bearish OB : dernière bougie haussière avant un déplacement baissier
        5. Breaker : un OB dont le prix a cassé le niveau opposé
        6. Mitigation : vérifier si le prix est revenu mitigier l'OB
        """
        if not self._enabled or len(candles) < 5:
            return []

        detections: list[SMCDetection] = []

        atr = calculate_atr(candles, period=14)
        if atr == 0:
            average_range = sum(float(abs(c.high - c.low)) for c in candles) / len(candles)
            atr = Decimal(str(average_range))

        displacement_min = atr * Decimal(str(self._displacement_threshold))

        # Détecter les Order Blocks
        for i in range(1, len(candles) - 1):
            # Bullish OB : chercher une bougie baissière suivie d'un déplacement haussier
            if candles[i].close < candles[i].open:  # Bougie baissière
                # Vérifier le déplacement haussier suivant
                confirmed, next_body = self._confirmed_displacement(
                    candles, i + 1, "bullish", displacement_min
                )
                if confirmed:
                    ob_top = candles[i].high
                    ob_bottom = candles[i].low

                    # Filtre de taille : OB gigantesque = clump de volatilité
                    if self._max_ob_atr_mult > 0 and atr > 0 and (
                        ob_top - ob_bottom > atr * Decimal(str(self._max_ob_atr_mult))
                    ):
                        continue
                    # Vérifier si l'OB est mitigé plus tard
                    mitigated_count = self._is_mitigated(candles, i + 2, ob_top, ob_bottom, "bullish")
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
                                    "mitigation_count": mitigated_count,
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
                                    "mitigation_count": mitigated_count,
                                },
                            )
                        )

            # Bearish OB : chercher une bougie haussière suivie d'un déplacement baissier
            if candles[i].close > candles[i].open:  # Bougie haussière
                # Vérifier le déplacement baissier suivant
                confirmed, next_body = self._confirmed_displacement(
                    candles, i + 1, "bearish", displacement_min
                )
                if confirmed:
                    ob_top = candles[i].high
                    ob_bottom = candles[i].low

                    # Filtre de taille : OB gigantesque = clump de volatilité
                    if self._max_ob_atr_mult > 0 and atr > 0 and (
                        ob_top - ob_bottom > atr * Decimal(str(self._max_ob_atr_mult))
                    ):
                        continue
                    # Vérifier si l'OB est mitigé plus tard
                    mitigated_count = self._is_mitigated(candles, i + 2, ob_top, ob_bottom, "bearish")
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
                                    "mitigation_count": mitigated_count,
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
                                    "mitigation_count": mitigated_count,
                                },
                            )
                        )

        # Détecter les Mitigation Blocks (après un liquidity sweep)
        detections.extend(self._detect_mitigation_blocks(candles))

        detections = [
            d for d in detections
            if (len(candles) - 1 - d.index) <= self.MAX_ZONE_AGE
        ]

        return detections

    def _confirmed_displacement(
        self,
        candles: list[Candle],
        start_idx: int,
        direction: str,
        displacement_min: Decimal,
    ) -> tuple[bool, Decimal]:
        """
        Vérifie le déplacement confirmé après une bougie OB.

        Exige ``displacement_confirmation_bars`` bougies consécutives dans le
        sens du déplacement, avec un corps cumulé >= ``displacement_min``.
        Retourne (confirmé, corps cumulé).
        """
        n = self._displacement_confirmation_bars
        cumulative = Decimal("0")
        for j in range(start_idx, min(start_idx + n, len(candles))):
            c = candles[j]
            body = abs(c.close - c.open)
            if direction == "bullish" and c.close <= c.open:
                return False, Decimal("0")
            if direction == "bearish" and c.close >= c.open:
                return False, Decimal("0")
            cumulative += body
        if cumulative >= displacement_min:
            return True, cumulative
        return False, cumulative

    def _is_mitigated(
        self,
        candles: list[Candle],
        start_idx: int,
        ob_top: Decimal,
        ob_bottom: Decimal,
        direction: str,
    ) -> int:
        """
        Vérifie le nombre de fois où un Order Block a été mitigé (le prix est revenu le tester).

        Args:
            candles: Liste des bougies
            start_idx: Index à partir duquel vérifier
            ob_top: Limite haute de l'OB
            ob_bottom: Limite basse de l'OB
            direction: "bullish" ou "bearish"

        Returns:
            Nombre de fois où l'OB a été mitigé
        """
        end_idx = min(start_idx + self._mitigation_lookback, len(candles))
        count = 0

        for i in range(start_idx, end_idx):
            if direction == "bullish":
                if candles[i].low <= ob_top:
                    count += 1
            else:
                if candles[i].high >= ob_bottom:
                    count += 1

        return count

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
