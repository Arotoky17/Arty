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

from typing import Any

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.signals.confidence_policy import (
    ConfidencePolicyDecision,
    evaluate_confidence_policy,
)
from arty_trading.modules.signals.validator import SignalValidator, ValidationResult
from arty_trading.modules.smc.setup_tracker import SetupTracker
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
        setup_tracker: SetupTracker | None = None,
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
            decision_engine: Moteur de décision ICT (optionnel). Si fourni,
                enrichit le signal avec le score de décision.
            setup_tracker: Tracker de setups SMC (optionnel). Si fourni,
                le générateur vérifie d'abord les setups prêts avant de
                demander des signaux aux stratégies.
        """
        self._min_confidence = min_confidence
        self._active_strategy = active_strategy
        self._strategies: dict[str, BaseStrategy] = {}
        self._validator = validator
        self._decision_engine = decision_engine
        self._setup_tracker = setup_tracker
        self._last_validation: ValidationResult | None = None
        self._last_rejection_stage: str | None = None
        self._last_rejection_reason: str | None = None
        self._last_confidence_policy: ConfidencePolicyDecision | None = None

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
            "stratégies=%d | activées=%s | validateur=%s | setup_tracker=%s",
            self._active_strategy,
            self._min_confidence,
            len(self._strategies),
            self.get_enabled_strategies(),
            "oui" if self._validator is not None else "non",
            "oui" if self._setup_tracker is not None else "non",
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
    def last_confidence_policy(self) -> ConfidencePolicyDecision | None:
        """Dernière décision de la politique confiance/RR (diagnostic)."""
        return self._last_confidence_policy

    @property
    def validator(self) -> SignalValidator | None:
        """Le validateur de signaux (None si non configuré)."""
        return self._validator

    @property
    def last_validation(self) -> ValidationResult | None:
        """Résultat de la dernière validation (None si aucune)."""
        return self._last_validation

    @property
    def last_rejection_stage(self) -> str | None:
        """Dernière étape ayant rejeté un signal."""
        return self._last_rejection_stage

    @property
    def last_rejection_reason(self) -> str | None:
        """Dernière raison de rejet."""
        return self._last_rejection_reason

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
        master_trend: str | None = None,
        market_context: Any | None = None,
    ) -> Signal | None:
        """
        Génère le meilleur signal parmi les stratégies activées.

        Règles :
        1. Seuls les signaux de la stratégie active sont acceptés (garde-fou).
        2. La confiance doit être >= ``min_confidence``.
        3. **Master Direction Gate** : le signal DOIT respecter la tendance 1H.
           - 1H BULLISH → seulement BUY autorisé
           - 1H BEARISH → seulement SELL autorisé
           - 1H NEUTRAL → aucun trade
           Ce filtre est ABSOLU et ne peut PAS être contourné.
        4. Si un ``SignalValidator`` est configuré, le signal est validé avant
           d'être retourné. Le générateur ne décide plus seul.
        5. Si aucun signal n'est accepté, retourne ``None`` (NO_SIGNAL).

        Args:
            candles: Liste des bougies OHLCV
            smc_data: Détections SMC
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel)
            htf_trend: Tendance HTF explicite (optionnel)
            spread: Spread actuel en points (optionnel)
            has_high_impact_news: True s'il y a des news à impact élevé
            master_trend: Tendance maître 1H ("bullish", "bearish", "neutral")
                (optionnel). Si fournie, elle prime sur htf_trend pour le
                Master Direction Gate.
            market_context: Contexte marché centralisé (optionnel)

        Returns:
            Le meilleur signal validé ou ``None`` (NO_SIGNAL)
        """
        if not candles:
            logger.debug("Aucune bougie fournie → NO_SIGNAL")
            self._last_rejection_stage = "no_data"
            self._last_rejection_reason = "no_candles"
            return None

        self._last_rejection_stage = None
        self._last_rejection_reason = None

        enabled = self.get_enabled_strategies()
        logger.debug(
            "Génération de signal | stratégies activées=%s | active=%s | "
            "min_confidence=%.2f | master_trend=%s",
            enabled,
            self._active_strategy,
            self._min_confidence,
            master_trend,
        )

        signals: list[Signal] = []

        # -----------------------------------------------------------------
        # Étape 0 : Vérifier les setups prêts dans le tracker
        # -----------------------------------------------------------------
        if self._setup_tracker is not None and candles:
            symbol = candles[0].symbol
            ready_setups = self._setup_tracker.get_ready_setups(symbol)
            if ready_setups:
                setup = max(ready_setups, key=lambda s: s.confidence_estimate)
                logger.info(
                    "[SETUP READY] %s | %s | setup_id=%s | state=%s | confidence_estimate=%.2f",
                    symbol,
                    "BUY" if setup.direction == Direction.BUY else "SELL",
                    setup.setup_id,
                    setup.state.value,
                    setup.confidence_estimate,
                )
                signal = self._build_signal_from_setup(setup, candles, smc_data)
                if signal is not None:
                    signals.append(signal)

        if not signals:
            signals = await self._run_strategies(
                candles, smc_data, htf_smc_data, htf_trend, master_trend, market_context
            )

        if not signals:
            logger.info(
                "NO_SIGNAL | aucun signal accepté | stratégies_activées=%s | active=%s | "
                "seuil=%.2f | master_trend=%s",
                enabled,
                self._active_strategy,
                self._min_confidence,
                master_trend,
            )
            return None

        best = max(signals, key=lambda s: s.confidence)
        logger.info(
            "Meilleur signal sélectionné | stratégie=%s | confiance=%.2f | direction=%s",
            best.strategy_name,
            best.confidence,
            best.direction.value,
        )

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
                self._last_rejection_stage = "validator"
                self._last_rejection_reason = "validator_rejected"
                return None
            logger.info(
                "Signal VALIDÉ par le validateur | stratégie=%s | score=%.2f",
                best.strategy_name,
                result.score,
            )

        if self._decision_engine is not None:
            decision_trends = htf_trends or ({"H1": htf_trend} if htf_trend else None)
            decision = self._decision_engine.decide(
                best,
                candles,
                smc_data,
                htf_trends=decision_trends,
                spread=spread,
                has_high_impact_news=has_high_impact_news,
                master_trend=master_trend,
                market_context=market_context,
            )
            enriched = self._decision_engine.enrich(best, decision)
            if enriched is None:
                logger.info(
                    "Signal REJETÉ par le moteur de décision | score=%d | raisons=%s",
                    decision.score,
                    decision.rejected_by,
                )
                self._last_rejection_stage = "decision_engine"
                self._last_rejection_reason = "low_score"
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
        master_trend: str | None = None,
        market_context: Any | None = None,
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
            master_trend: Tendance maître 1H (optionnel)
            market_context: Contexte marché centralisé (optionnel)

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
                signal = await strategy.analyze(
                    candles,
                    smc_data,
                    htf_smc_data=htf_smc_data,
                    htf_trend=htf_trend,
                )
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc, exc_info=True)
                continue

            if signal is None:
                continue

            policy = evaluate_confidence_policy(
                signal.confidence,
                signal.risk_reward_ratio,
                base_threshold=self._min_confidence,
            )
            self._last_confidence_policy = policy
            if not policy.allowed:
                logger.info(
                    "Signal REJETÉ (politique confiance/RR, generate_all) | stratégie=%s | "
                    "confiance=%.2f | bucket=%s | R/R=%.2f | RR_requis=%s | raison=%s",
                    name,
                    signal.confidence,
                    policy.confidence_bucket,
                    signal.risk_reward_ratio,
                    policy.required_rr,
                    policy.reason,
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

    def _check_master_direction_gate(
        self,
        master_trend: str | None,
        direction: str,
        signal: Signal,
        regime: str | None = None,
    ) -> str | None:
        """
        Vérifie le Master Direction Gate : filtre ABSOLU basé sur la tendance 1H
        et le régime de marché.

        Hiérarchie des timeframes (rôle explicite de chacun) :
        - H4 : Contexte macro — **informatif seulement**, n'autorise ni ne bloque
          un trade. Utilisé pour le rapport et la compréhension du contexte large.
        - H1 : Master trend — **Gate absolu**. Détermine la direction autorisée.
        - M15 : Contexte intermédiaire — Bonus de confluence uniquement, jamais
          un hard reject.
        - M5 : Confirmation + entrée — CHoCH/BOS/displacement/retest/rejection
          se jouent ici.

        Règles du gate :
        - 1H BULLISH → SELL interdit
        - 1H BEARISH → BUY interdit
        - 1H NEUTRAL → BUY et SELL interdits
        - Régime H1 = RANGE → BUY et SELL interdits (pas de tendance claire)
        - Régime H1 = TRANSITION → BUY et SELL interdits (bascule non confirmée)

        Ce filtre ne peut PAS être contourné par le score ou toute autre confluence.

        Args:
            master_trend: Tendance maître ("bullish", "bearish", "neutral")
            direction: Direction du signal ("bullish" ou "bearish")
            signal: Signal candidat
            regime: Régime de marché H1 ("strong_bullish", "bullish",
                "weak_bullish", "range", "weak_bearish", "bearish",
                "strong_bearish", "transition")

        Returns:
            None si le filtre passe, sinon le nom du rejet
        """
        if master_trend is None:
            return None

        if master_trend == "neutral":
            return "MASTER_TREND_CONFLICT"

        if regime in ("range", "transition"):
            return f"H1_{regime.upper()}"

        if master_trend == "bullish" and direction == "bearish":
            return "MASTER_TREND_CONFLICT"

        if master_trend == "bearish" and direction == "bullish":
            return "MASTER_TREND_CONFLICT"

        return None

    def _htf_conflicts(self, htf_trend: str | None, direction: str) -> bool:
        """
        Vérifie si la tendance HTF entre en conflit avec la direction du signal.

        Args:
            htf_trend: Tendance HTF ("bullish", "bearish", "neutral")
            direction: Direction du signal ("bullish" ou "bearish")

        Returns:
            True si conflit, False sinon
        """
        if htf_trend is None or htf_trend == "neutral":
            return False
        return htf_trend != direction

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

    async def _run_strategies(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        master_trend: str | None = None,
        market_context: Any | None = None,
    ) -> list[Signal]:
        """Exécute les stratégies activées et retourne les signaux acceptés."""
        signals: list[Signal] = []

        for name, strategy in self._strategies.items():
            if not strategy.enabled:
                continue

            if not self._is_strategy_allowed(name):
                logger.debug(
                    "Stratégie ignorée (non-active) | %s | active=%s",
                    name,
                    self._active_strategy,
                )
                continue

            try:
                signal = await strategy.analyze(
                    candles,
                    smc_data,
                    htf_smc_data=htf_smc_data,
                    htf_trend=htf_trend,
                )
            except Exception as exc:
                logger.error("Erreur stratégie %s | %s", name, exc, exc_info=True)
                continue

            if signal is None:
                logger.debug("Aucun signal produit | stratégie=%s", name)
                continue

            direction_str = "bullish" if signal.direction == Direction.BUY else "bearish"

            market_regime = None
            if market_context is not None:
                market_regime = getattr(market_context, "regime", None)

            gate_rejection = self._check_master_direction_gate(
                master_trend, direction_str, signal, regime=market_regime
            )
            if gate_rejection is not None:
                logger.info(
                    "Signal REJETÉ (Master Direction Gate) | %s | %s | "
                    "master=%s | reason=%s | justification=%s",
                    signal.symbol,
                    direction_str,
                    master_trend,
                    gate_rejection,
                    signal.justification,
                )
                self._last_rejection_stage = "master_gate"
                self._last_rejection_reason = gate_rejection
                continue

            htf_trend_for_gate = master_trend or htf_trend or (
                htf_smc_data[0].get("direction") if htf_smc_data else None
            )
            if htf_trend_for_gate and self._htf_conflicts(htf_trend_for_gate, direction_str):
                logger.info(
                    "Signal REJETÉ (HTF conflict) | %s | %s | htf=%s | justification=%s",
                    signal.symbol,
                    direction_str,
                    htf_trend_for_gate,
                    signal.justification,
                )
                self._last_rejection_stage = "htf_conflict"
                self._last_rejection_reason = "htf_conflict"
                continue

            policy = evaluate_confidence_policy(
                signal.confidence,
                signal.risk_reward_ratio,
                base_threshold=self._min_confidence,
            )
            self._last_confidence_policy = policy
            if not policy.allowed:
                logger.info(
                    "Signal REJETÉ (politique confiance/RR) | stratégie=%s | "
                    "confiance=%.2f | bucket=%s | R/R=%.2f | RR_requis=%s | "
                    "raison=%s | direction=%s | justification=%s",
                    name,
                    signal.confidence,
                    policy.confidence_bucket,
                    signal.risk_reward_ratio,
                    policy.required_rr,
                    policy.reason,
                    signal.direction.value,
                    signal.justification,
                )
                self._last_rejection_stage = "confidence"
                self._last_rejection_reason = policy.reason
                continue

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

        return signals

    def _build_signal_from_setup(
        self,
        setup: Any,
        candles: list[Candle],
        smc_data: list[dict],
    ) -> Signal | None:
        """Construit un signal à partir d'un setup prêt (READY/ENTRY_READY)."""
        from decimal import Decimal
        from arty_trading.core.enums import Direction, SignalType, TimeFrame

        direction = setup.direction
        symbol = setup.symbol
        timeframe = candles[0].timeframe if candles else TimeFrame.M5

        zone_high = setup.zone_high
        zone_low = setup.zone_low
        if zone_high is None or zone_low is None:
            return None

        entry_price = Decimal(str(setup.zone_price or (zone_high + zone_low) / 2.0))
        current_price = Decimal(str(float(candles[-1].close))) if candles else entry_price

        atr = Decimal(str(setup.atr_at_detection))
        if atr == 0 and candles:
            from arty_trading.utils.helpers import calculate_atr
            atr = calculate_atr(candles)

        sl_buffer = atr * Decimal("0.5")
        if direction == Direction.BUY:
            stop_loss = min(Decimal(str(zone_low)), current_price) - sl_buffer
            risk = entry_price - stop_loss
            take_profit = entry_price + risk * Decimal("2.0")
        else:
            stop_loss = max(Decimal(str(zone_high)), current_price) + sl_buffer
            risk = stop_loss - entry_price
            take_profit = entry_price - risk * Decimal("2.0")

        if risk <= 0:
            return None

        confidence = max(setup.confidence_estimate, 0.5)
        concepts = [setup.zone_concept] if setup.zone_concept else []
        justification = (
            f"Setup SMC {direction.value} prêt | zone={zone_low:.5f}-{zone_high:.5f} | "
            f"state={setup.state.value} | setup_id={setup.setup_id}"
        )

        return Signal(
            symbol=symbol,
            signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            strategy_name="SMC Trend Following",
            timeframe=timeframe,
            smc_concepts=concepts,
            justification=justification,
            metadata={
                "setup_id": setup.setup_id,
                "setup_state": setup.state.value,
                "zone_concept": setup.zone_concept,
                "zone_index": setup.zone_index,
                "atr_at_detection": setup.atr_at_detection,
                "htf_trend_at_detection": setup.htf_trend_at_detection,
            },
        )
