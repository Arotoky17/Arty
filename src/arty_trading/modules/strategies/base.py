"""
Classe de base pour toutes les stratégies de trading.

Fournit des utilitaires communs : calcul de SL/TP, score de confiance,
et helpers pour analyser les données SMC.
"""

from __future__ import annotations

from abc import abstractmethod
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, LogCategory, SignalType, TimeFrame
from arty_trading.core.interfaces import IStrategy
from arty_trading.logging.logger import get_logger
from arty_trading.utils.helpers import calculate_atr

logger = get_logger(LogCategory.STRATEGY)


class BaseStrategy(IStrategy):
    """
    Classe de base pour toutes les stratégies.

    Fournit :
    - Gestion de l'activation (enabled/disabled)
    - Calcul automatique du Stop Loss et Take Profit
    - Score de confiance basé sur les confluences SMC
    - Helpers pour filtrer les détections SMC par concept
    """

    def __init__(
        self,
        enabled: bool = True,
        risk_reward_min: float = 1.0,
        confidence_min: float = 0.3,
    ) -> None:
        """
        Args:
            enabled: Activer/désactiver la stratégie
            risk_reward_min: Ratio risque/rendement minimum (défaut 1.0)
            confidence_min: Score de confiance minimum (défaut 0.3)
        """
        self._enabled = enabled
        self._rr_min = risk_reward_min
        self._confidence_min = confidence_min

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def risk_reward_min(self) -> float:
        return self._rr_min

    @property
    def confidence_min(self) -> float:
        return self._confidence_min

    @abstractmethod
    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        """Analyse le marché et retourne un signal ou None.

        Args:
            candles: Liste des bougies OHLCV
            smc_data: Détections SMC sur le timeframe courant
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel)
            htf_trend: Tendance HTF explicite (\"bullish\", \"bearish\",
                \"neutral\") (optionnel)
        """

    # =========================================================================
    # Helpers partagés
    # =========================================================================

    def _filter_smc(
        self,
        smc_data: list[dict],
        concept: str,
        direction: str | None = None,
    ) -> list[dict]:
        """
        Filtre les détections SMC par concept et direction.

        Args:
            smc_data: Liste des détections SMC
            concept: Nom du concept (ex: "break_of_structure")
            direction: "bullish", "bearish" ou None pour tous

        Returns:
            Liste filtrée des détections
        """
        result = [d for d in smc_data if d.get("concept") == concept]
        if direction:
            result = [d for d in result if d.get("direction") == direction]
        return result

    def _has_concept(
        self,
        smc_data: list[dict],
        concept: str,
        direction: str | None = None,
    ) -> bool:
        """Vérifie si un concept SMC est présent dans les données."""
        return len(self._filter_smc(smc_data, concept, direction)) > 0

    def _is_htf_aligned(
        self,
        direction: Direction,
        htf_trend: str | None = None,
        htf_smc_data: list[dict] | None = None,
    ) -> bool:
        """
        Vérifie si une direction de signal est alignée avec la tendance HTF /
        la structure HTF.

        La tendance HTF est dérivée, dans l'ordre :
        1. ``htf_trend`` explicite (\"bullish\" / \"bearish\" / \"neutral\")
        2. Le BOS le plus récent détecté sur ``htf_smc_data`` (si ``htf_trend``
           n'est pas fourni)

        Si aucune information HTF n'est disponible, la direction n'est **pas**
        bloquée (l'alignement reste ensuite imposé par le ``SignalGenerator``
        et le ``SignalValidator``).

        Args:
            direction: Direction du signal candidat (BUY ou SELL)
            htf_trend: Tendance HTF explicite (optionnel)
            htf_smc_data: Détections SMC HTF (optionnel)

        Returns:
            True si la direction est alignée (ou si l'info HTF est inconnue),
            False si elle est en conflit avec la tendance HTF.
        """
        direction_str = "bullish" if direction == Direction.BUY else "bearish"

        trend = htf_trend
        if not trend and htf_smc_data:
            # Dériver la tendance HTF depuis le BOS le plus récent.
            htf_bulls = self._filter_smc(htf_smc_data, "break_of_structure", "bullish")
            htf_bears = self._filter_smc(htf_smc_data, "break_of_structure", "bearish")
            latest_bull = max(htf_bulls, key=lambda d: d.get("index", -1), default=None)
            latest_bear = max(htf_bears, key=lambda d: d.get("index", -1), default=None)
            if latest_bull and (
                not latest_bear or latest_bull["index"] > latest_bear["index"]
            ):
                trend = "bullish"
            elif latest_bear:
                trend = "bearish"

        if not trend or trend == "neutral":
            return True
        return trend == direction_str

    def _count_confluences(
        self,
        smc_data: list[dict],
        directions: list[str],
    ) -> int:
        """
        Compte le nombre de confluences SMC dans les directions données.

        Args:
            smc_data: Liste des détections SMC
            directions: Liste des directions à compter ("bullish" ou "bearish")

        Returns:
            Nombre de confluences
        """
        count = 0
        for d in smc_data:
            if d.get("direction") in directions:
                count += 1
        return count

    def _calculate_confidence(
        self,
        confluences: int,
        max_confluences: int = 10,
    ) -> float:
        """
        Calcule un score de confiance basé sur le nombre de confluences.

        Args:
            confluences: Nombre de confluences détectées
            max_confluences: Nombre maximum de confluences possibles

        Returns:
            Score de confiance entre 0.0 et 1.0
        """
        confidence = min(confluences / max_confluences, 1.0)
        return round(max(confidence, 0.0), 2)

    def _build_signal(
        self,
        symbol: str,
        signal_type: SignalType,
        direction: Direction,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        confidence: float,
        timeframe: TimeFrame,
        smc_concepts: list[str],
        justification: str,
        metadata: dict[str, Any] | None = None,
    ) -> Signal:
        """
        Construit un objet Signal avec tous les champs requis.

        Args:
            symbol: Symbole tradé
            signal_type: Type de signal (BUY, SELL)
            direction: Direction (BUY, SELL)
            entry_price: Prix d'entrée
            stop_loss: Niveau de Stop Loss
            take_profit: Niveau de Take Profit
            confidence: Score de confiance (0-1)
            timeframe: Timeframe analysé
            smc_concepts: Liste des concepts SMC détectés
            justification: Justification textuelle du signal
            metadata: Métadonnées supplémentaires

        Returns:
            Objet Signal
        """
        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            strategy_name=self.name,
            timeframe=timeframe,
            justification=justification,
            smc_concepts=smc_concepts,
            metadata=metadata or {},
        )

    def _calculate_sl_tp(
        self,
        entry: Decimal,
        direction: Direction,
        risk_pips: float = 20,
        reward_pips: float = 40,
        pip_size: float = 0.0001,
    ) -> tuple[Decimal, Decimal]:
        """
        Calcule le Stop Loss et Take Profit basés sur l'entrée et la direction.

        Args:
            entry: Prix d'entrée
            direction: BUY ou SELL
            risk_pips: Risque en pips (défaut 20)
            reward_pips: Rendement en pips (défaut 40)
            pip_size: Taille d'un pip

        Returns:
            Tuple (stop_loss, take_profit)
        """
        risk = Decimal(str(risk_pips * pip_size))
        reward = Decimal(str(reward_pips * pip_size))

        if direction == Direction.BUY:
            sl = entry - risk
            tp = entry + reward
        else:
            sl = entry + risk
            tp = entry - reward

        return sl, tp

    def _calculate_atr(self, candles: list[Candle], period: int = 14) -> Decimal:
        """Calcule l'Average True Range (ATR) d'une série de bougies.

        Délègue à ``utils.helpers.calculate_atr`` : c'est exactement la même
        implémentation (moyenne simple initiale puis lissage Wilder), centralisée
        ici afin d'avoir une source de vérité unique pour le calcul ATR des
        stratégies. Le comportement est strictement identique.

        Args:
            candles: Liste des bougies (du plus ancien au plus récent)
            period: Période de calcul (défaut 14)

        Returns:
            Valeur ATR en prix (pas en pips)
        """
        return calculate_atr(candles, period)

    def _calculate_atr_based_sl_tp(
        self,
        entry: Decimal,
        direction: Direction,
        candles: list[Candle],
        atr_multiplier_sl: float = 1.5,
        atr_multiplier_tp: float = 3.0,
        pip_size: float = 0.0001,
    ) -> tuple[Decimal, Decimal]:
        """
        Calcule SL/TP basés sur l'ATR pour adapter à la volatilité.

        Args:
            entry: Prix d'entrée
            direction: BUY ou SELL
            candles: Liste des bougies pour calculer l'ATR
            atr_multiplier_sl: Multiplicateur ATR pour le SL (défaut 1.5)
            atr_multiplier_tp: Multiplicateur ATR pour le TP (défaut 3.0)
            pip_size: Taille d'un pip

        Returns:
            Tuple (stop_loss, take_profit)
        """
        atr = self._calculate_atr(candles)
        if atr == 0:
            # Fallback sur les valeurs fixes si ATR impossible
            return self._calculate_sl_tp(entry, direction, pip_size=pip_size)

        sl_distance = atr * Decimal(str(atr_multiplier_sl))
        tp_distance = atr * Decimal(str(atr_multiplier_tp))

        if direction == Direction.BUY:
            sl = entry - sl_distance
            tp = entry + tp_distance
        else:
            sl = entry + sl_distance
            tp = entry - tp_distance

        return sl, tp

    def _has_confirmed_rejection(
        self,
        candles: list[Candle],
        top: float,
        bottom: float,
        direction: str,
    ) -> bool:
        """
        Vérifie que la dernière bougie clôture dans la zone (ou au-delà) et dans
        le bon sens.

        Un vrai retest SMC doit être confirmé par une clôture de bougie dans la
        zone, pas seulement une mèche qui touche la zone.

        Args:
            candles: Liste des bougies OHLCV
            top: Limite haute de la zone
            bottom: Limite basse de la zone
            direction: "bullish" ou "bearish"

        Returns:
            True si la dernière bougie confirme un rejet valide de la zone
        """
        if not candles:
            return False

        last = candles[-1]
        if direction == "bullish":
            return last.is_bullish and last.close >= bottom
        return not last.is_bullish and last.close <= top
