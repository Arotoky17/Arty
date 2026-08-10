"""
Détecteur SMC principal — orchestre tous les détecteurs de concepts Smart Money.

Implémente le port ``ISMCDetector`` et délègue la détection à des
sous-détecteurs indépendants, chacun activable ou désactivable.
"""

from __future__ import annotations

from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import LogCategory, SMCConcept
from arty_trading.core.interfaces import ISMCDetector
from arty_trading.logging.logger import get_logger
from arty_trading.modules.smc.base import SMCDetection
from arty_trading.modules.smc.fair_value_gap import FairValueGapDetector
from arty_trading.modules.smc.liquidity import LiquidityDetector
from arty_trading.modules.smc.order_blocks import OrderBlockDetector
from arty_trading.modules.smc.premium_discount import PremiumDiscountDetector
from arty_trading.modules.smc.sessions import SessionDetector
from arty_trading.modules.smc.structure import StructureDetector

logger = get_logger(LogCategory.SMC)


class SMCDetector(ISMCDetector):
    """
    Détecteur SMC principal.

    Orchestre tous les détecteurs de concepts Smart Money :
    - Structure (BOS, CHoCH, MSS)
    - Fair Value Gap (FVG, IFVG)
    - Order Blocks (OB, Breaker, Mitigation)
    - Liquidité (Sweep, Equal High, Equal Low)
    - Premium/Discount et OTE

    Chaque détecteur est indépendant et peut être activé/désactivé
    individuellement via ``enable_concept`` et ``disable_concept``.
    """

    def __init__(self) -> None:
        """Initialise le détecteur SMC avec tous les sous-détecteurs."""
        self._detectors: dict[str, Any] = {
            "structure": StructureDetector(),
            "fair_value_gap": FairValueGapDetector(),
            "order_blocks": OrderBlockDetector(),
            "liquidity": LiquidityDetector(),
            "premium_discount": PremiumDiscountDetector(),
            "sessions": SessionDetector(),
        }

    @property
    def detectors(self) -> dict[str, Any]:
        """Retourne le dictionnaire des détecteurs."""
        return self._detectors

    def enable_concept(self, concept: str) -> None:
        """
        Active un détecteur spécifique.

        Args:
            concept: Nom du détecteur ("structure", "fair_value_gap",
                     "order_blocks", "liquidity", "premium_discount")
        """
        if concept in self._detectors:
            self._detectors[concept].enabled = True
            logger.info("Détecteur SMC activé | %s", concept)
        else:
            logger.warning("Détecteur SMC inconnu | %s", concept)

    def disable_concept(self, concept: str) -> None:
        """
        Désactive un détecteur spécifique.

        Args:
            concept: Nom du détecteur
        """
        if concept in self._detectors:
            self._detectors[concept].enabled = False
            logger.info("Détecteur SMC désactivé | %s", concept)
        else:
            logger.warning("Détecteur SMC inconnu | %s", concept)

    def enable_all(self) -> None:
        """Active tous les détecteurs."""
        for detector in self._detectors.values():
            detector.enabled = True

    def disable_all(self) -> None:
        """Désactive tous les détecteurs."""
        for detector in self._detectors.values():
            detector.enabled = False

    def get_enabled_concepts(self) -> list[str]:
        """Retourne la liste des détecteurs activés."""
        return [name for name, d in self._detectors.items() if d.enabled]

    async def detect(self, candles: list[Candle], symbol: str) -> list[dict]:
        """
        Détecte tous les concepts SMC sur les bougies fournies.

        Args:
            candles: Liste des bougies (du plus ancien au plus récent)
            symbol: Symbole analysé

        Returns:
            Liste de dictionnaires, chacun représentant une détection SMC.
            Chaque dict contient : concept, direction, price, index, details.
        """
        if not candles:
            logger.warning("Aucune bougie à analyser | %s", symbol)
            return []

        all_detections: list[SMCDetection] = []

        for name, detector in self._detectors.items():
            if not detector.enabled:
                continue
            try:
                detections = detector.detect(candles)
                all_detections.extend(detections)
            except Exception as exc:
                logger.error(
                    "Erreur détecteur SMC | %s | %s | %s",
                    name,
                    symbol,
                    exc,
                )

        # Trier par index de bougie
        all_detections.sort(key=lambda d: d.index)

        # Convertir en liste de dicts
        result = [d.to_dict() for d in all_detections]

        logger.info(
            "SMC analyse | %s | %d bougies | %d détections | détecteurs: %s",
            symbol,
            len(candles),
            len(result),
            self.get_enabled_concepts(),
        )

        return result

    def detect_sync(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Version synchrone de detect (retourne des SMCDetection).

        Utile pour les tests et l'utilisation interne.

        Args:
            candles: Liste des bougies

        Returns:
            Liste des détections SMC (objets SMCDetection)
        """
        all_detections: list[SMCDetection] = []

        for detector in self._detectors.values():
            if not detector.enabled:
                continue
            try:
                detections = detector.detect(candles)
                all_detections.extend(detections)
            except Exception as exc:
                logger.error("Erreur détecteur SMC sync | %s", exc)

        all_detections.sort(key=lambda d: d.index)
        return all_detections