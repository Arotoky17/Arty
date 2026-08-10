"""
Générateur de signaux — fusionne les données SMC et les stratégies.

Pendant la phase de développement, une seule stratégie est active par défaut :
``SMCTrendStrategy`` ("SMC Trend Following"). Les autres stratégies sont
désactivées proprement (non supprimées) et le générateur ne retourne **jamais**
un signal provenant d'une stratégie autre que la stratégie active.

La confiance minimale est configurable (voir ``SignalSettings``) et sa valeur
par défaut est ``0.85``. Tout signal dont la confiance est inférieure au seuil
est rejeté (``NO_SIGNAL``).

Le générateur ne décide plus seul : si un ``SignalValidator`` est configuré,
chaque signal est soumis à une validation finale avant d'être retourné. Le
validateur vérifie 11 conditions SMC/ICT (tendance HTF, BOS, CHoCH, Order
Block, FVG, Liquidity Sweep, Premium/Discount, session, spread, news filter,
RR minimum). Le trade n'est autorisé que si toutes les conditions sont
validées.

Chaque décision (acceptation / rejet) est journalisée avec des logs détaillés
afin de pouvoir retracer pourquoi un signal a été émis ou ignoré.
"""

from __future__ import annotations

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.signals.validator import SignalValidator, ValidationResult
from arty_trading.modules.strategies.base import BaseStrategy
from arty_trading.modules.strategies.strategies import (
    BreakoutStrategy,
    MomentumStrategy,
    ReversalStrategy,
    ScalpingStrategy,
    SMCTrendStrategy,
    SwingStrategy,
)

logger = get_logger(LogCategory.SIGNAL)

# Nom par défaut de la stratégie active pendant le développement.
DEFAULT_ACTIVE_STRATEGY = "SMC Trend Following"

# Confiance minimale par défaut (peut être surchargée via SignalSettings).
DEFAULT_MIN_CONFIDENCE = 0.85


class SignalGenerator:
    """Générateur de signaux de trading.

    Le générateur orchestre les stratégies activées, fusionne les données SMC
    et retourne le meilleur signal respectant :

    1. La stratégie active (``active_strategy``) — un signal provenant d'une
       autre stratégie n'est **jamais** retourné, même si cette stratégie est
       activée manuellement.
    2. La confiance minimale (``min_confidence``) — un signal dont la
       confiance est inférieure au seuil est rejeté (``NO_SIGNAL``).

    Attributes:
        _min_confidence: Seuil de confiance minimal (défaut 0.85)
        _active_strategy: Nom de la stratégie active (défaut "SMC Trend Following")
        _strategies: Dictionnaire nom → stratégie
    """

    def __init__(
        self,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        active_strategy: str = DEFAULT_ACTIVE_STRATEGY,
        strategies: list[BaseStrategy] | None = None,
        validator: SignalValidator | None = None,
        decision_engine: DecisionEngine | None = None,
    ) -> None:
        """
        Initialise le générateur de signaux.

        Args:
            min_confidence: Confiance minimale pour accepter un signal (défaut 0.85)
            active_strategy: Nom de la stratégie active (défaut "SMC Trend Following").
                Seule cette stratégie est autorisée à produire des signaux.
            strategies: Liste de stratégies. Si None, les 6 stratégies sont
                créées et seule la stratégie active est activée.
            validator: Validateur de signaux (optionnel). Si fourni, chaque
                signal est validé avant d'être retourné. Le générateur ne
                décide plus seul.
        """
        self._min_confidence = min_confidence
        self._active_strategy = active_strategy
        self._strategies: dict[str, BaseStrategy] = {}
        self._validator = validator
        self._decision_engine = decision_engine
        self._last_validation: ValidationResult | None = None

        if strategies is None:
            strategies = [
                SMCTrendStrategy(),
                BreakoutStrategy(),
                MomentumStrategy(),
                ReversalStrategy(),
                ScalpingStrategy(),
                SwingStrategy(),
            ]

        for s in strategies:
            self._strategies[s.name] = s

        # Désactiver proprement toutes les stratégies sauf la stratégie active.
        # Les stratégies ne sont pas supprimées : elles restent disponibles
        # pour une réactivation future.
        self._apply_active_strategy()

        logger.info(
            "SignalGenerator initialisé | stratégie active=%s | min_confidence=%.2f | "
            "stratégies=%d | activées=%s | validateur=%s",
            self._active_strategy,
            self._min_confidence,
            len(self._strategies),
            self.get_enabled_strategies(),
            "oui" if self._validator is not None else "non",
        )

    # -------------------------------------------------------------------------
    # Propriétés
    # -------------------------------------------------------------------------

    @property
    def strategies(self) -> dict[str, BaseStrategy]:
        """Retourne toutes les stratégies (activées et désactivées)."""
        return self._strategies

    @property
    def min_confidence(self) -> float:
        """Seuil de confiance minimal."""
        return self._min_confidence

    @min_confidence.setter
    def min_confidence(self, value: float) -> None:
        self._min_confidence = value

    @property
    def active_strategy(self) -> str:
        """Nom de la stratégie active."""
        return self._active_strategy

    @property
    def validator(self) -> SignalValidator | None:
        """Le validateur de signaux (None si non configuré)."""
        return self._validator

    @property
    def last_validation(self) -> ValidationResult | None:
        """Résultat de la dernière validation (None si aucune)."""
        return self._last_validation

    # -------------------------------------------------------------------------
    # Gestion des stratégies
    # -------------------------------------------------------------------------

    def enable_strategy(self, name: str) -> None:
        """Active une stratégie spécifique.

        Note : activer une stratégie non-active ne suffit pas pour que ses
        signaux soient retournés. Le garde-fou ``_is_strategy_allowed`` filtre
        tout signal provenant d'une stratégie autre que ``active_strategy``.
        """
        if name in self._strategies:
            self._strategies[name].enabled = True
            logger.info("Stratégie activée | %s", name)
        else:
            logger.warning("Stratégie inconnue (activation ignorée) | %s", name)

    def disable_strategy(self, name: str) -> None:
        """Désactive une stratégie spécifique."""
        if name in self._strategies:
            self._strategies[name].enabled = False
            logger.info("Stratégie désactivée | %s", name)
        else:
            logger.warning("Stratégie inconnue (désactivation ignorée) | %s", name)

    def enable_all(self) -> None:
        """Active toutes les stratégies.

        Note : le garde-fou sur ``active_strategy`` reste actif — seuls les
        signaux de la stratégie active seront retournés par ``generate``.
        """
        for s in self._strategies.values():
            s.enabled = True
        logger.info("Toutes les stratégies activées | active=%s", self._active_strategy)

    def disable_all(self) -> None:
        """Désactive toutes les stratégies."""
        for s in self._strategies.values():
            s.enabled = False
        logger.info("Toutes les stratégies désactivées")

    def get_enabled_strategies(self) -> list[str]:
        """Retourne les noms des stratégies activées."""
        return [name for name, s in self._strategies.items() if s.enabled]

    def set_active_strategy(self, name: str) -> None:
        """Change la stratégie active et désactive les autres.

        Args:
            name: Nom de la stratégie à activer.
        """
        if name not in self._strategies:
            logger.warning("Stratégie inconnue (set_active_strategy ignoré) | %s", name)
            return
        self._active_strategy = name
        self._apply_active_strategy()
        logger.info(
            "Stratégie active changée | %s | activées=%s",
            name,
            self.get_enabled_strategies(),
        )

    # -------------------------------------------------------------------------
    # Génération de signaux
    # -------------------------------------------------------------------------

    async def generate(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        htf_trends: dict[str, str] | None = None,
        spread: int | None = None,
        has_high_impact_news: bool = False,
    ) -> Signal | None:
        """
        Génère le meilleur signal parmi les stratégies activées.

        Règles :
        1. Seuls les signaux de la stratégie active sont acceptés (garde-fou).
        2. La confiance doit être >= ``min_confidence``.
        3. Si un ``SignalValidator`` est configuré, le signal est validé avant
           d'être retourné. Le générateur ne décide plus seul.
        4. Si aucun signal n'est accepté, retourne ``None`` (NO_SIGNAL).

        Args:
            candles: Liste des bougies OHLCV
            smc_data: Détections SMC
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel)
            htf_trend: Tendance HTF explicite (optionnel)
            spread: Spread actuel en points (optionnel)
            has_high_impact_news: True s'il y a des news à impact élevé

        Returns:
            Le meilleur signal validé ou ``None`` (NO_SIGNAL)
        """
        if not candles:
            logger.debug("Aucune bougie fournie → NO_SIGNAL")
            return None

        enabled = self.get_enabled_strategies()
        logger.debug(
            "Génération de signal | stratégies activées=%s | active=%s | min_confidence=%.2f",
            enabled,
            self._active_strategy,
            self._min_confidence,
        )

        signals: list[Signal] = []

        for name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue

            # Garde-fou : ignorer toute stratégie non-active, même si elle est
            # activée manuellement. Le SignalGenerator ne doit jamais retourner
            # un signal provenant d'une autre stratégie.
            if not self._is_strategy_allowed(name):
                logger.debug(
                    "Stratégie ignorée (non-active) | %s | active=%s",
                    name,
                    self._active_strategy,
                )
                continue

            try:
                signal = await strategy.analyze(candles, smc_data)
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc, exc_info=True)
                continue

            if signal is None:
                logger.debug("Aucun signal produit | stratégie=%s", name)
                continue

            # Vérification du seuil de confiance
            if signal.confidence < self._min_confidence:
                logger.info(
                    "Signal REJETÉ (confiance insuffisante) | stratégie=%s | "
                    "confiance=%.2f | seuil=%.2f | direction=%s | justification=%s",
                    name,
                    signal.confidence,
                    self._min_confidence,
                    signal.direction.value,
                    signal.justification,
                )
                continue

            # Signal accepté
            logger.info(
                "Signal ACCEPTÉ | stratégie=%s | confiance=%.2f | seuil=%.2f | "
                "direction=%s | type=%s | R/R=%.2f | concepts=%s | justification=%s",
                name,
                signal.confidence,
                self._min_confidence,
                signal.direction.value,
                signal.signal_type.value,
                signal.risk_reward_ratio,
                signal.smc_concepts,
                signal.justification,
            )
            signals.append(signal)

        if not signals:
            logger.info(
                "NO_SIGNAL | aucun signal accepté | stratégies_activées=%s | active=%s | "
                "seuil=%.2f",
                enabled,
                self._active_strategy,
                self._min_confidence,
            )
            return None

        # Sélectionner le signal avec la plus grande confiance.
        best = max(signals, key=lambda s: s.confidence)
        logger.info(
            "Meilleur signal sélectionné | stratégie=%s | confiance=%.2f | direction=%s",
            best.strategy_name,
            best.confidence,
            best.direction.value,
        )

        # Validation par le SignalValidator (le générateur ne décide plus seul)
        if self._validator is not None:
            result = self._validator.validate(
                signal=best,
                candles=candles,
                smc_data=smc_data,
                htf_smc_data=htf_smc_data,
                htf_trend=htf_trend,
                spread=spread,
                has_high_impact_news=has_high_impact_news,
            )
            self._last_validation = result
            if not result.is_valid:
                logger.info(
                    "Signal REJETÉ par le validateur | stratégie=%s | score=%.2f | "
                    "échecs=%s | explication=%s",
                    best.strategy_name,
                    result.score,
                    result.failed_conditions,
                    result.explanation,
                )
                return None
            logger.info(
                "Signal VALIDÉ par le validateur | stratégie=%s | score=%.2f",
                best.strategy_name,
                result.score,
            )

        if self._decision_engine is not None:
            decision_trends = htf_trends or ({"H4": htf_trend} if htf_trend else None)
            decision = self._decision_engine.decide(
                best,
                candles,
                smc_data,
                htf_trends=decision_trends,
                spread=spread,
                has_high_impact_news=has_high_impact_news,
            )
            enriched = self._decision_engine.enrich(best, decision)
            if enriched is None:
                logger.info(
                    "Signal REJETÉ par le moteur de décision | score=%d | raisons=%s",
                    decision.score,
                    decision.rejected_by,
                )
                return None
            best = enriched
            logger.info("Signal ICT validé | score=%d | tier=%s", decision.score, decision.tier)

        return best

    def validate(
        self,
        signal: Signal,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        spread: int | None = None,
        has_high_impact_news: bool = False,
    ) -> ValidationResult | None:
        """
        Valide un signal avec le SignalValidator.

        Si aucun validateur n'est configuré, retourne None.

        Args:
            signal: Le signal à valider
            candles: Liste des bougies OHLCV
            smc_data: Détections SMC
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel)
            htf_trend: Tendance HTF explicite (optionnel)
            spread: Spread actuel en points (optionnel)
            has_high_impact_news: True s'il y a des news à impact élevé

        Returns:
            ValidationResult ou None si aucun validateur configuré
        """
        if self._validator is None:
            return None
        result = self._validator.validate(
            signal=signal,
            candles=candles,
            smc_data=smc_data,
            htf_smc_data=htf_smc_data,
            htf_trend=htf_trend,
            spread=spread,
            has_high_impact_news=has_high_impact_news,
        )
        self._last_validation = result
        return result

    async def generate_all(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        spread: int | None = None,
        has_high_impact_news: bool = False,
    ) -> list[Signal]:
        """
        Génère tous les signaux valides (stratégie active + confiance >= seuil).

        Si un ``SignalValidator`` est configuré, seuls les signaux validés sont
        retournés.

        Args:
            candles: Liste des bougies OHLCV
            smc_data: Détections SMC
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel)
            htf_trend: Tendance HTF explicite (optionnel)
            spread: Spread actuel en points (optionnel)
            has_high_impact_news: True s'il y a des news à impact élevé

        Returns:
            Liste triée par confiance décroissante.
        """
        if not candles:
            return []

        signals: list[Signal] = []

        for name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue
            if not self._is_strategy_allowed(name):
                continue

            try:
                signal = await strategy.analyze(candles, smc_data)
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc, exc_info=True)
                continue

            if signal is None:
                continue

            if signal.confidence < self._min_confidence:
                logger.info(
                    "Signal REJETÉ (confiance insuffisante) | stratégie=%s | "
                    "confiance=%.2f | seuil=%.2f",
                    name,
                    signal.confidence,
                    self._min_confidence,
                )
                continue

            logger.info(
                "Signal ACCEPTÉ | stratégie=%s | confiance=%.2f | direction=%s",
                name,
                signal.confidence,
                signal.direction.value,
            )
            signals.append(signal)

        # Filtrer par le validateur si configuré
        if self._validator is not None:
            validated: list[Signal] = []
            for sig in signals:
                result = self._validator.validate(
                    signal=sig,
                    candles=candles,
                    smc_data=smc_data,
                    htf_smc_data=htf_smc_data,
                    htf_trend=htf_trend,
                    spread=spread,
                    has_high_impact_news=has_high_impact_news,
                )
                if result.is_valid:
                    validated.append(sig)
                else:
                    logger.info(
                        "Signal REJETÉ par le validateur (generate_all) | stratégie=%s | "
                        "score=%.2f | échecs=%s",
                        sig.strategy_name,
                        result.score,
                        result.failed_conditions,
                    )
            signals = validated

        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals

    # -------------------------------------------------------------------------
    # Helpers internes
    # -------------------------------------------------------------------------

    def _is_strategy_allowed(self, name: str) -> bool:
        """Vérifie si une stratégie est autorisée à produire des signaux.

        Une stratégie est autorisée si et seulement si son nom correspond à
        ``active_strategy``. Ce garde-fou garantit que le SignalGenerator ne
        retourne jamais un signal provenant d'une autre stratégie.

        Args:
            name: Nom de la stratégie

        Returns:
            True si la stratégie est autorisée, False sinon.
        """
        return name == self._active_strategy

    def _apply_active_strategy(self) -> None:
        """Active uniquement la stratégie active et désactive les autres.

        Les stratégies ne sont pas supprimées : elles restent disponibles
        pour une réactivation future via ``set_active_strategy``.
        """
        for name, strategy in self._strategies.items():
            strategy.enabled = name == self._active_strategy
