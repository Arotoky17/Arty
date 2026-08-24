"""
Moteur de trading (Trading Engine) - Orchestration live des modules.

Le ``TradingEngine`` relie les briques existantes (données de marché, détecteur
SMC, générateur de signaux, gestionnaire de risque, exécuteur d'ordres) en une
boucle d'analyse continue.

Le bot fonctionne selon le flux suivant :

    Nouvelle bougie
        ↓
    Téléchargement des données
        ↓
    Analyse (SMC)
        ↓
    Validation (génération de signal)
        ↓
    Calcul du risque
        ↓
    Trade (exécution)
        ↓
    Monitoring
        ↓
    Attente prochaine bougie

Le ``CandleSynchronizer`` garantit qu'une bougie n'est **jamais** analysée deux
fois : au démarrage, la bougie actuelle est enregistrée comme « déjà traitée »
et le bot attend véritablement la prochaine bougie fermée avant de lancer
l'analyse.

Chaque étape est journalisée avec la catégorie ``LogCategory`` appropriée afin
de pouvoir retracer l'ordre analyse → signal → décision risque → ordre dans
les logs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from arty_trading.application.candle_synchronizer import CandleSynchronizer
from arty_trading.application.execution_guards import (
    final_gate_before_execution,
    revalidate_before_execution,
)
from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.application.position_monitor import PositionMonitor
from arty_trading.application.position_state_store import PositionStateStore
from arty_trading.application.statistics import TradingStatistics
from arty_trading.application.trade_decision_debugger import TradeDecisionDebugger
from arty_trading.application.trade_decision_diagnostic import (
    PipelineStep,
    RejectionReason,
    TradeDecisionDiagnostic,
)
from arty_trading.application.trade_journal import TradeJournal
from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.application.trade_orchestrator import TradeOrchestrator
from arty_trading.config.settings import Settings
from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import Direction, LogCategory, TimeFrame, TradingMode
from arty_trading.core.interfaces import (
    IMarketDataProvider,
    IMT5Connector,
    IOrderExecutor,
    IRiskManager,
    ISMCDetector,
)
from arty_trading.logging import get_logger
from arty_trading.modules.decision import MultiTimeframeAnalyzer
from arty_trading.modules.execution import (
    OrderExecutor,
    PaperOrderExecutor,
    PositionManager,
)
from arty_trading.modules.execution.sl_guard import StructureContext
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.signals.news import EconomicCalendar
from arty_trading.modules.smc import SetupState, SetupTracker
from arty_trading.modules.smc.base import find_swing_points
from arty_trading.utils.helpers import calculate_atr
from arty_trading.modules.smc.premium_discount import premium_discount_diagnostic
from arty_trading.utils.helpers import retest_still_valid_detailed

logger = get_logger(LogCategory.SYSTEM)


class TradingEngine:
    """
    Moteur d'orchestration du trading en live.

    Pour chaque symbole configuré (``settings.symbols_list``) et à chaque
    nouvelle bougie fermée sur le timeframe configuré
    (``settings.default_timeframe``), déclenche le pipeline complet :

    1. **Téléchargement** — Récupère les dernières bougies via
       ``IMarketDataProvider``.
    2. **Analyse** — Appelle ``SMCDetector.detect()`` pour les détections SMC.
    3. **Validation** — Appelle ``SignalGenerator.generate()`` avec les bougies
       + détections. Si aucun signal n'est généré, le cycle s'arrête.
    4. **Calcul du risque** — ``RiskManager.can_open_trade()``,
       ``RiskManager.validate_signal()``,
       ``RiskManager.calculate_position_size()``.
    5. **Trade** — ``OrderExecutor.open_order()`` si tout est validé.
    6. **Monitoring** — Enregistrement du trade auprès du risk manager.
    7. **Attente** — Le moteur attend la prochaine bougie fermée.

    Le ``CandleSynchronizer`` empêche toute ré-analyse d'une bougie déjà
    traitée.

    Attributes:
        _settings: Configuration globale
        _market_data: Provider de données de marché
        _smc_detector: Détecteur SMC
        _signal_generator: Générateur de signaux
        _risk_manager: Gestionnaire de risque
        _executor: Exécuteur d'ordres
        _mt5_connector: Connecteur MT5 (pour les infos du compte)
        _synchronizer: Synchroniseur de bougies (anti double-analyse)
        _poll_interval: Intervalle de polling en secondes
        _candle_count: Nombre de bougies à récupérer
        _symbols: Liste des symboles à analyser
        _timeframe: Timeframe d'analyse
        _last_signal: Dernier signal analysé par symbole (pour le statut)
        _running: Indique si le moteur tourne
        _task: Tâche asyncio de la boucle principale
    """

    def __init__(
        self,
        settings: Settings,
        market_data: IMarketDataProvider,
        smc_detector: ISMCDetector,
        signal_generator: SignalGenerator,
        risk_manager: IRiskManager,
        executor: IOrderExecutor,
        mt5_connector: IMT5Connector,
        poll_interval: float = 5.0,
        candle_count: int = 100,
        notifier: Any | None = None,
        decision_debugger: TradeDecisionDebugger | None = None,
        setup_tracker: Any | None = None,
    ) -> None:
        """
        Initialise le moteur de trading.

        Args:
            settings: Configuration globale (symboles, timeframe, mode)
            market_data: Provider de données de marché
            smc_detector: Détecteur SMC
            signal_generator: Générateur de signaux
            risk_manager: Gestionnaire de risque
            executor: Exécuteur d'ordres
            mt5_connector: Connecteur MT5 (pour les infos du compte)
            poll_interval: Intervalle de polling en secondes (défaut 5s)
            candle_count: Nombre de bougies à récupérer (défaut 100)
            notifier: Gestionnaire de notifications (NotificationManager) pour
                les alertes critiques (trade ouvert/fermé, erreurs, rapport
                journalier). Optionnel — désactivé si None.
            decision_debugger: Debugger de décision de trading pour l'observabilité.
                Optionnel — désactivé si None.
            setup_tracker: Tracker de setups SMC. Si None, un nouveau est créé.
        """
        self._settings = settings
        self._market_data = market_data
        self._smc_detector = smc_detector
        self._signal_generator = signal_generator
        self._risk_manager = risk_manager
        self._executor = executor
        self._mt5_connector = mt5_connector
        self._poll_interval = poll_interval
        self._candle_count = candle_count
        self._notifier = notifier
        self._decision_debugger = decision_debugger or TradeDecisionDebugger(enabled=False)

        self._symbols: list[str] = list(settings.symbols_list)
        self._timeframe: TimeFrame = settings.default_timeframe
        self._trading_mode: TradingMode = settings.trading_mode

        # Synchroniseur de bougies — garantit qu'une bougie n'est jamais
        # analysée deux fois.
        self._synchronizer = CandleSynchronizer()

        # Tracker de statistiques — enregistre toutes les analyses, signaux
        # et trades pour tous les modes (ANALYSIS, PAPER, LIVE).
        self._statistics = TradingStatistics(mode=self._trading_mode)

        # Dernier signal analysé par symbole (pour le statut / endpoint)
        self._last_signal: dict[str, dict[str, Any]] = {}

        self._running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._mtf_analyzer = MultiTimeframeAnalyzer(market_data, smc_detector)
        self._market_context_builder = MarketContextBuilder(
            smc_detector=smc_detector,
            download_data=self._download_data,
        )
        position_settings = getattr(settings, "position", None)
        self._position_manager = (
            PositionManager(position_settings)
            if getattr(position_settings, "enabled", False) is True
            else None
        )
        self._managed_trades: dict[str, Trade] = {}
        # Persistance durable de l'état de gestion (reprise après redémarrage) :
        # le SL MT5 courant n'est jamais considéré comme le SL initial.
        self._position_state_store = (
            PositionStateStore(position_settings.state_directory)
            if self._position_manager is not None
            else None
        )
        # Cache des snapshots de structure (mis à jour par bougie uniquement).
        self._structure_cache: dict[str, tuple[Any, StructureContext]] = {}
        news_settings = getattr(settings, "news", None)
        self._economic_calendar = (
            EconomicCalendar(news_settings)
            if getattr(news_settings, "enabled", False) is True
            else None
        )
        journal_settings = getattr(settings, "journal", None)
        self._journal = (
            TradeJournal(journal_settings.directory)
            if getattr(journal_settings, "enabled", False) is True
            else None
        )
        self._position_monitor = PositionMonitor(
            position_manager=self._position_manager,
            market_data=self._market_data,
            executor=self._executor,
            risk_manager=self._risk_manager,
            statistics=self._statistics,
            journal=self._journal,
            notify_critical=self._notify_critical,
            notify_trade_closed=self._notify_trade_closed,
            structure_provider=self._build_structure_context,
            state_store=self._position_state_store,
        )
        from arty_trading.modules.smc import SetupTracker

        self._setup_tracker = setup_tracker if setup_tracker is not None else SetupTracker()
        # Observabilité : le debugger est notifié à chaque création de setup
        # (exposition de l'événement, sans modifier les règles du tracker).
        self._setup_tracker.set_on_setup_created(self._on_setup_created)
        self._trade_orchestrator = TradeOrchestrator(
            mt5_connector=self._mt5_connector,
            risk_manager=self._risk_manager,
            executor=self._executor,
            setup_tracker=self._setup_tracker,
            trading_mode=self._trading_mode,
            notify_critical=self._notify_critical,
            notify_trade_opened=self._notify_trade_opened,
            get_open_positions_count=self._get_open_positions_count,
        )

        # --- Robustesse 24/7 ---
        # Date UTC du dernier reset journalier (au premier cycle, il est nul).
        self._last_daily_reset: str | None = None
        # Séquence de reconnexion pour éviter une boucle de retry agressive.
        self._reconnect_attempts: int = 0
        # Dernière alerte de déconnexion envoyée (anti-spam).
        self._disconnected_notified: bool = False

    # -------------------------------------------------------------------------
    # Propriétés
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Indique si le moteur tourne."""
        return self._running

    @property
    def symbols(self) -> list[str]:
        """Liste des symboles analysés."""
        return list(self._symbols)

    @property
    def timeframe(self) -> TimeFrame:
        """Timeframe d'analyse."""
        return self._timeframe

    @property
    def synchronizer(self) -> CandleSynchronizer:
        """Synchroniseur de bougies (exposé pour les tests / monitoring)."""
        return self._synchronizer

    @property
    def trading_mode(self) -> TradingMode:
        """Mode de trading actuel (ANALYSIS, PAPER, LIVE)."""
        return self._trading_mode

    @property
    def statistics(self) -> TradingStatistics:
        """Tracker de statistiques (exposé pour les tests / monitoring)."""
        return self._statistics

    @property
    def decision_debugger(self) -> TradeDecisionDebugger:
        """Debugger de décision de trading."""
        return self._decision_debugger

    # -------------------------------------------------------------------------
    # Diagnostic helpers
    # -------------------------------------------------------------------------

    def _create_diagnostic(
        self, symbol: str, direction: str = "neutral"
    ) -> TradeDecisionDiagnostic:
        """Crée (et compte une seule fois) le diagnostic d'une opportunité."""
        return self._decision_debugger.start_opportunity(
            symbol=symbol,
            timeframe=self._timeframe,
            direction=direction,
        )

    def _record_step(self, diagnostic: TradeDecisionDiagnostic, step: PipelineStep) -> None:
        """Enregistre une étape complétée (délégué au debugger)."""
        self._decision_debugger.record_step(diagnostic, step)

    def _fail_step(
        self,
        diagnostic: TradeDecisionDiagnostic,
        step: PipelineStep,
        reason: RejectionReason | str,
        message: str = "",
    ) -> None:
        """Enregistre une étape échouée avec une raison de rejet (délégué au debugger)."""
        self._decision_debugger.fail_step(diagnostic, step, reason, message)

    def _add_rejection_reason(
        self, diagnostic: TradeDecisionDiagnostic, reason: RejectionReason
    ) -> None:
        """Ajoute une raison de rejet secondaire (détail) sans marquer d'étape."""
        self._decision_debugger.add_rejection_reason(diagnostic, reason)

    # Mappage conditions du SignalValidator -> RejectionReason détaillées.
    # Ne fabrique aucune raison : seules les conditions réellement fournies
    # par le validateur (failed_conditions / checked_conditions) sont mappées.
    _VALIDATOR_CONDITION_REASONS: dict[str, RejectionReason] = {
        "htf_trend": RejectionReason.MASTER_TREND_CONFLICT,
        "bos_valid": RejectionReason.BOS_MISSING,
        "choch_valid": RejectionReason.CHOCH_MISSING,
        "order_block_valid": RejectionReason.ORDER_BLOCK_MISSING,
        "fvg_valid": RejectionReason.FVG_MISSING,
        "liquidity_sweep_confirmed": RejectionReason.LIQUIDITY_MISSING,
        "premium_discount_correct": RejectionReason.PREMIUM_DISCOUNT_INVALID,
        "min_rr": RejectionReason.RR_TOO_LOW,
        "spread_acceptable": RejectionReason.SPREAD_TOO_HIGH,
        "news_filter": RejectionReason.NEWS_BLOCKED,
    }

    def _populate_validator_diagnostic(self, diagnostic: TradeDecisionDiagnostic) -> None:
        """Peuple le diagnostic avec les détails réellement fournis par le validateur."""
        validation = self._signal_generator.last_validation
        if validation is None:
            return

        checked = getattr(validation, "checked_conditions", None)
        failed = getattr(validation, "failed_conditions", None)
        if not isinstance(checked, dict):
            checked = {}
        if not isinstance(failed, list):
            failed = []

        smc_diag = diagnostic.smc_validator
        smc_diag.htf_trend = bool(checked.get("htf_trend", False))
        smc_diag.bos = bool(checked.get("bos_valid", False))
        smc_diag.choch = bool(checked.get("choch_valid", False))
        smc_diag.order_block = bool(checked.get("order_block_valid", False))
        smc_diag.fvg = bool(checked.get("fvg_valid", False))
        smc_diag.liquidity_sweep = bool(checked.get("liquidity_sweep_confirmed", False))
        smc_diag.premium_discount = bool(checked.get("premium_discount_correct", False))

        # Raisons détaillées (BOS/CHoCH/OB/FVG/etc.) — uniquement si le
        # validateur a réellement rejeté le signal.
        if not bool(getattr(validation, "is_valid", True)):
            detail_reasons: set[RejectionReason] = set()
            for cond in failed:
                reason = self._VALIDATOR_CONDITION_REASONS.get(cond)
                if reason is not None:
                    detail_reasons.add(reason)
            for cond, passed in checked.items():
                if not passed:
                    reason = self._VALIDATOR_CONDITION_REASONS.get(cond)
                    if reason is not None:
                        detail_reasons.add(reason)
            for reason in sorted(detail_reasons, key=lambda r: r.value):
                self._add_rejection_reason(diagnostic, reason)

    def _populate_confidence_policy_diagnostic(
        self,
        diagnostic: TradeDecisionDiagnostic,
        decision: str,
    ) -> None:
        """Ajoute la politique confiance/RR au diagnostic (observabilité)."""
        policy = getattr(self._signal_generator, "last_confidence_policy", None)
        if policy is None:
            return
        required_rr: float | None = None
        if isinstance(policy.required_rr, (int, float)):
            required_rr = float(policy.required_rr)
        actual_rr: float | None = None
        if isinstance(policy.actual_rr, (int, float)):
            actual_rr = float(policy.actual_rr)
        diagnostic.metadata["confidence_policy"] = policy.to_dict()
        diagnostic.metadata["confidence_policy"]["decision"] = decision
        logger.info(
            "CONFIDENCE POLICY | %s | confidence=%s | confidence_threshold=%s | "
            "confidence_bucket=%s | required_rr=%s | actual_rr=%s | "
            "rr_security_level=%s | decision=%s%s",
            diagnostic.symbol,
            policy.confidence,
            policy.confidence_threshold,
            policy.confidence_bucket,
            f"{required_rr:.1f}" if required_rr is not None else "none",
            f"{actual_rr:.2f}" if actual_rr is not None else "none",
            policy.security_level,
            decision,
            f" | primary_reason={policy.reason}" if policy.reason else "",
        )

    def _populate_signal_diagnostics(
        self,
        diagnostic: TradeDecisionDiagnostic,
        signal: Signal,
        market_context: Any | None = None,
    ) -> None:
        """Peuple le diagnostic avec les informations du signal accepté."""
        diagnostic.score = int(signal.confidence * 100)
        diagnostic.confidence = signal.confidence
        diagnostic.setup_type = signal.signal_type.value

        # Direction réelle du candidat (BUY / SELL), sans deviner.
        self._decision_debugger.set_direction(diagnostic, signal.direction.value)
        if signal.setup_id:
            diagnostic.metadata["setup_id"] = signal.setup_id

        # RR diagnostic
        rr_diag = diagnostic.rr_diagnostic
        rr_diag.entry = signal.entry_price
        rr_diag.stop_loss = signal.stop_loss
        rr_diag.take_profit = signal.take_profit
        rr_diag.risk_distance = abs(signal.entry_price - signal.stop_loss)
        rr_diag.reward_distance = abs(signal.take_profit - signal.entry_price)
        rr_diag.risk_reward = signal.risk_reward_ratio
        profile = self._settings.get_instrument_profile(diagnostic.symbol)
        rr_diag.minimum_required_rr = float(
            profile.min_risk_reward if profile is not None else 2.0
        )

        # Score diagnostic from metadata
        decision_meta = signal.metadata.get("decision", {})
        if decision_meta:
            diagnostic.score_diagnostic.total = decision_meta.get("score", 0)
            diagnostic.score_diagnostic.tier = decision_meta.get("tier", "unknown")
            diagnostic.score = decision_meta.get("score", 0)

        # Spread diagnostic
        spread_diag = diagnostic.spread_diagnostic
        spread_diag.current_spread = getattr(signal, "spread", 0)
        spread_diag.maximum_allowed_spread = int(
            profile.max_spread_points if profile is not None else 30
        )

        # Master trend diagnostic
        mt_diag = diagnostic.master_trend_diagnostic
        mt_diag.h1_trend = diagnostic.h1_trend
        mt_diag.candidate_direction = diagnostic.direction_candidate
        mt_diag.passed = True
        mt_diag.reason = "aligned"

        # Premium/Discount diagnostic (Phase 3A — instrumentation)
        if market_context is not None:
            dir_str = "bullish" if signal.direction == Direction.BUY else "bearish"
            pd_diag = premium_discount_diagnostic(
                candles=market_context.ltf_candles,
                smc_data=market_context.ltf_smc_data,
                direction=dir_str,
                symbol=diagnostic.symbol,
                timeframe=TimeFrame.M5.value,
            )
            diagnostic.premium_discount_diagnostic = pd_diag.to_dict()
            if not pd_diag.valid:
                self._add_rejection_reason(
                    diagnostic,
                    RejectionReason.PREMIUM_DISCOUNT_INVALID,
                )
        self._populate_validator_diagnostic(diagnostic)
        self._populate_confidence_policy_diagnostic(diagnostic, "accepted")

    # -------------------------------------------------------------------------
    # Pipeline d'analyse (point d'entrée principal)
    # -------------------------------------------------------------------------

    async def analyze_symbol(self, symbol: str) -> None:
        """
        Point d'entrée pour traiter un symbole.

        Exécute le pipeline complet si une **nouvelle** bougie est détectée
        par le ``CandleSynchronizer``. Si la bougie a déjà été traitée, la
        méthode retourne immédiatement après le téléchargement (étape 1).

        Pipeline :

        1. Téléchargement des données (``_download_data``)
        2. Vérification nouvelle bougie (``CandleSynchronizer``)
        3. Analyse MTF (H1 direction + M5 confirmation)
        4. Validation / génération de signal (``_generate_signal``)
        5. Calcul du risque (``_calculate_risk``)
        6. Trade / exécution (``_execute_trade``)
        7. Monitoring (``_monitor_trade``)

        Args:
            symbol: Symbole à analyser (ex: EURUSD)
        """
        symbol_upper = symbol.upper()
        if symbol_upper not in self._settings.supported_symbols:
            logger.info(
                "UNSUPPORTED_SYMBOL | %s | symbole non supporte par la strategie ICT/SMC | "
                "symboles supportes=%s",
                symbol,
                self._settings.supported_symbols,
            )
            return

        # Phase 3 : appliquer la qualité de détection du profil instrument
        # (filtres sweep/FVG/OB en multiples d'ATR) au détecteur SMC.
        self._configure_detector_for_symbol(symbol_upper)

        diagnostic = self._create_diagnostic(symbol)

        # ------------------------------------------------------------------
        # Étape 1 : Téléchargement des données H1 + M5
        # ------------------------------------------------------------------
        htf_candles = await self._download_data(symbol, self._settings.htf_timeframe)
        if htf_candles is None:
            self._fail_step(diagnostic, PipelineStep.DATA_AVAILABLE, RejectionReason.NO_MARKET_DATA)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        ltf_candles = await self._download_data(symbol, self._settings.entry_timeframe)
        if ltf_candles is None:
            self._fail_step(diagnostic, PipelineStep.DATA_AVAILABLE, RejectionReason.NO_MARKET_DATA)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        # Phase 3 : timeframe de setup (M15). Optionnel — si indisponible,
        # le pipeline continue en H1/M5 (rétro-compatibilité).
        setup_tf_candles = (
            ltf_candles
            if self._settings.setup_timeframe == self._settings.entry_timeframe
            else await self._download_data(symbol, self._settings.setup_timeframe)
        )
        if setup_tf_candles is None:
            logger.info(
                "%s indisponible | %s | pipeline %s/%s sans timeframe de setup",
                self._settings.setup_timeframe.value,
                symbol,
                self._settings.htf_timeframe.value,
                self._settings.entry_timeframe.value,
            )

        self._record_step(diagnostic, PipelineStep.DATA_AVAILABLE)

        # ------------------------------------------------------------------
        # Étape 2 : Vérification nouvelle bougie (sur le LTF)
        # ------------------------------------------------------------------
        latest_candle = max(ltf_candles, key=lambda c: c.time)
        diagnostic.candle_time = latest_candle.time

        if not self._synchronizer.is_new_candle(symbol, latest_candle.time):
            logger.debug(
                "Pas de nouvelle bougie | %s | %s | dernière traitée=%s",
                symbol,
                self._timeframe.value,
                self._synchronizer.get_last_processed(symbol),
            )
            diagnostic.metadata["skipped_reason"] = "candle_already_processed"
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._synchronizer.mark_processed(symbol, latest_candle.time)
        self._statistics.record_analysis(symbol)
        self._decision_debugger.record_candle_analyzed(
            symbol_upper, self._settings.entry_timeframe, latest_candle.time
        )

        logger.info("=" * 56)
        logger.info(
            "Nouvelle bougie détectée | %s | %s | time=%s",
            symbol,
            self._settings.entry_timeframe.value,
            latest_candle.time.isoformat(),
        )
        logger.info("=" * 56)

        # ------------------------------------------------------------------
        # Étape 3 : Analyse MTF (H1 direction + M5 confirmation)
        # ------------------------------------------------------------------
        market_context = await self._analyze_multitimeframe(
            symbol, htf_candles, ltf_candles, setup_tf_candles=setup_tf_candles
        )
        if market_context is None:
            self._fail_step(
                diagnostic,
                PipelineStep.MARKET_CONTEXT,
                RejectionReason.INSUFFICIENT_DATA,
            )
            logger.info("Pipeline arrete (Etape 3) | %s | analyse MTF echouee", symbol)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._record_step(diagnostic, PipelineStep.MARKET_CONTEXT)

        diagnostic.h1_trend = market_context.master_trend
        diagnostic.market_regime = market_context.regime
        diagnostic.m15_context = getattr(market_context, "entry_confirmation", "none")

        self._log_market_context(market_context)

        if market_context.is_neutral():
            self._fail_step(
                diagnostic,
                PipelineStep.MARKET_REGIME,
                RejectionReason.MASTER_TREND_UNKNOWN,
            )
            logger.info(
                "NO_TRADE | %s | H1=NEUTRAL | aucune direction claire",
                symbol,
            )
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        if market_context._regime_blocks_trade():
            regime_reason = RejectionReason.REGIME_BLOCKED
            if market_context.regime in ("range", "transition"):
                regime_reason = RejectionReason.REGIME_BLOCKED
            self._fail_step(diagnostic, PipelineStep.MARKET_REGIME, regime_reason)
            logger.info(
                "NO_TRADE | %s | régime=%s | %s",
                symbol,
                market_context.regime,
                market_context.no_trade_reasons,
            )
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._record_step(diagnostic, PipelineStep.MARKET_REGIME)

        # ------------------------------------------------------------------
        # Suivi des setups (Phase 6 — state machine)
        # ------------------------------------------------------------------
        self._update_setups(symbol, market_context)

        # ------------------------------------------------------------------
        # Étape 4 : Validation / génération de signal
        # ------------------------------------------------------------------
        signal = await self._generate_signal(
            symbol,
            ltf_candles,
            market_context.ltf_smc_data,
            htf_smc_data=market_context.htf_smc_data,
            htf_trend=market_context.master_trend,
            htf_trends={"H1": market_context.master_trend},
            has_high_impact_news=(
                self._economic_calendar.is_blocked(symbol)
                if self._economic_calendar is not None
                else False
            ),
            master_trend=market_context.master_trend,
            market_context=market_context,
        )
        if signal is None:
            rejection_stage = self._signal_generator.last_rejection_stage
            if rejection_stage == "master_gate":
                self._fail_step(
                    diagnostic,
                    PipelineStep.MASTER_DIRECTION_GATE,
                    RejectionReason.MASTER_TREND_CONFLICT,
                )
            elif rejection_stage == "htf_conflict":
                self._fail_step(
                    diagnostic,
                    PipelineStep.MASTER_DIRECTION_GATE,
                    RejectionReason.MASTER_TREND_CONFLICT,
                )
            elif rejection_stage == "confidence":
                policy = getattr(self._signal_generator, "last_confidence_policy", None)
                self._populate_confidence_policy_diagnostic(diagnostic, "rejected")
                if policy is not None and policy.reason == "rr_too_low":
                    self._fail_step(
                        diagnostic,
                        PipelineStep.RISK_REWARD,
                        RejectionReason.RR_TOO_LOW,
                    )
                else:
                    self._fail_step(
                        diagnostic,
                        PipelineStep.STRATEGY_EVALUATION,
                        RejectionReason.LOW_CONFIDENCE,
                    )
            elif rejection_stage == "validator":
                self._fail_step(
                    diagnostic,
                    PipelineStep.SIGNAL_VALIDATOR,
                    RejectionReason.VALIDATOR_REJECTED,
                )
                self._populate_validator_diagnostic(diagnostic)
            elif rejection_stage == "decision_engine":
                self._fail_step(
                    diagnostic,
                    PipelineStep.DECISION_SCORE,
                    RejectionReason.LOW_SCORE,
                )
            else:
                self._fail_step(
                    diagnostic,
                    PipelineStep.SIGNAL_GENERATED,
                    RejectionReason.NO_SIGNAL,
                )
            logger.info("Pipeline arrete (Etape 4) | %s | aucun signal genere", symbol)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        diagnostic.strategy_name = signal.strategy_name
        diagnostic.confidence = signal.confidence
        self._record_step(diagnostic, PipelineStep.STRATEGY_EVALUATION)
        self._record_step(diagnostic, PipelineStep.SIGNAL_GENERATED)
        self._record_step(diagnostic, PipelineStep.MASTER_DIRECTION_GATE)
        self._record_step(diagnostic, PipelineStep.SIGNAL_VALIDATOR)
        self._record_step(diagnostic, PipelineStep.DECISION_SCORE)
        self._record_step(diagnostic, PipelineStep.RISK_REWARD)

        # Peupler le diagnostic avec les détails du signal
        self._populate_signal_diagnostics(diagnostic, signal, market_context)

        # Revalidation juste avant exécution (étape 5)
        revalidation_passed = await self._revalidate_before_execution(
            symbol, signal, market_context
        )
        diagnostic.execution_guard_diagnostic.revalidate_passed = revalidation_passed
        if not revalidation_passed:
            self._fail_step(
                diagnostic,
                PipelineStep.EXECUTION_REVALIDATION,
                RejectionReason.REVALIDATION_FAILED,
            )
            logger.info(
                "Pipeline arrete (Revalidation) | %s | signal devenu invalide",
                symbol,
            )
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._record_step(diagnostic, PipelineStep.EXECUTION_REVALIDATION)

        if self._trading_mode == TradingMode.ANALYSIS:
            logger.info(
                "Mode ANALYSIS | signal généré, aucune position ouverte | %s | %s",
                symbol,
                signal.direction.value,
            )
            # Le signal a passé toutes les validations ; seule l'exécution est
            # volontairement omise en mode ANALYSIS (aucun ordre soumis/exécuté).
            diagnostic.decision = "accepted"
            diagnostic.metadata["execution_not_reached"] = True
            diagnostic.metadata["trading_mode"] = self._trading_mode.value
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        # ------------------------------------------------------------------
        # Étape 5bis : Gate final avant exécution (Phase 7)
        # ------------------------------------------------------------------
        # Capturer le diagnostic de retest AVANT le gate (Phase 3A — instrumentation)
        direction_str = "bullish" if signal.direction == Direction.BUY else "bearish"
        profile = self._settings.get_instrument_profile(symbol)
        retest_diag = retest_still_valid_detailed(
            candles=market_context.ltf_candles,
            smc_data=market_context.ltf_smc_data,
            direction=direction_str,
            max_age_bars=profile.max_zone_age_bars if profile else 20,
            max_distance_atr_mult=profile.retest_atr_mult if profile else 1.0,
            symbol=symbol,
        )
        diagnostic.retest_diagnostic = {
            "valid": retest_diag.valid,
            "reason": retest_diag.reason,
            "symbol": retest_diag.symbol,
            "direction": retest_diag.direction,
            "last_candle_time": retest_diag.last_candle_time.isoformat()
            if retest_diag.last_candle_time
            else None,
            "atr": retest_diag.atr,
            "max_distance": retest_diag.max_distance,
            "max_zone_age_bars": retest_diag.max_zone_age_bars,
            "retest_atr_mult": retest_diag.retest_atr_mult,
            "zone_type": retest_diag.zone_type,
            "zone_id": retest_diag.zone_id,
            "zone_created_index": retest_diag.zone_created_index,
            "zone_age_bars": retest_diag.zone_age_bars,
            "distance_to_zone": retest_diag.distance_to_zone,
            "zone_consumed": retest_diag.zone_consumed,
            "zone_direction": retest_diag.zone_direction,
            "retest_detected": retest_diag.retest_detected,
            "retest_confirmed": retest_diag.retest_confirmed,
            "zones_in_direction": retest_diag.zones_in_direction,
        }

        final_gate_passed = await self._final_gate_before_execution(
            symbol, signal, market_context
        )
        diagnostic.execution_guard_diagnostic.final_gate_passed = final_gate_passed
        if not final_gate_passed:
            diagnostic.execution_guard_diagnostic.final_gate_reason = (
                f"retest_reason={retest_diag.reason}"
                if not retest_diag.valid
                else "other_gate_condition"
            )
            self._fail_step(
                diagnostic,
                PipelineStep.EXECUTION_FINAL_GATE,
                RejectionReason.FINAL_GATE_REJECTED,
            )
            # Log détaillé du rejet Final Gate (Phase 2.1 — instrumentation)
            logger.warning(
                "FINAL GATE REJECT DIAG | symbol=%s direction=%s score=%d confidence=%.2f RR=%.2f "
                "zone_type=%s zone_age_bars=%s max_zone_age=%d ATR=%.6f "
                "zone_distance=%s max_distance=%.6f retest_atr_mult=%.1f "
                "age_condition=%s distance_condition=%s confirmation_condition=%s "
                "retest_still_valid=%s reason=%s zones_in_direction=%d",
                symbol,
                direction_str,
                diagnostic.score,
                signal.confidence,
                signal.risk_reward_ratio,
                retest_diag.zone_type,
                retest_diag.zone_age_bars,
                retest_diag.max_zone_age_bars,
                retest_diag.atr,
                retest_diag.distance_to_zone,
                retest_diag.max_distance,
                retest_diag.retest_atr_mult,
                (
                    retest_diag.zone_age_bars is not None
                    and retest_diag.zone_age_bars <= retest_diag.max_zone_age_bars
                ),
                (
                    retest_diag.distance_to_zone is not None
                    and retest_diag.distance_to_zone <= retest_diag.max_distance
                ),
                retest_diag.retest_confirmed,
                retest_diag.valid,
                retest_diag.reason,
                retest_diag.zones_in_direction,
            )
            logger.info(
                "Pipeline arrete (Gate final) | %s | conditions critiques non remplies",
                symbol,
            )
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._record_step(diagnostic, PipelineStep.EXECUTION_FINAL_GATE)

        # ------------------------------------------------------------------
        # Étape 5 : Calcul du risque
        # ------------------------------------------------------------------
        volume = await self._calculate_risk(symbol, signal)
        if volume is None:
            self._fail_step(diagnostic, PipelineStep.RISK_MANAGER, RejectionReason.RISK_REJECTED)
            logger.info("Pipeline arrete (Etape 5) | %s | calcul du risque refuse", symbol)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        self._record_step(diagnostic, PipelineStep.RISK_MANAGER)

        # ------------------------------------------------------------------
        # Étape 6 : Trade (exécution)
        # ------------------------------------------------------------------
        trade = await self._execute_trade(symbol, signal, volume)
        if trade is None:
            self._fail_step(diagnostic, PipelineStep.ORDER_SUBMITTED, RejectionReason.MT5_ERROR)
            logger.info("Pipeline arrete (Etape 6) | %s | execution ordre echouee", symbol)
            self._decision_debugger.finalize_opportunity(diagnostic)
            return

        # L'exécuteur confirme l'exécution : l'ordre a été soumis puis exécuté.
        self._record_step(diagnostic, PipelineStep.ORDER_SUBMITTED)
        self._record_step(diagnostic, PipelineStep.ORDER_EXECUTED)

        self._statistics.record_trade_opened(trade)
        if self._journal is not None:
            self._journal.record(trade, "opened")

        # ------------------------------------------------------------------
        # Étape 7 : Monitoring
        # ------------------------------------------------------------------
        await self._monitor_trade(symbol, trade)

        diagnostic.decision = "accepted"
        self._decision_debugger.finalize_opportunity(diagnostic)

        logger.info(
            "CYCLE COMPLETE | %s | TRADE | direction=%s | volume=%s | R/R=%.2f",
            symbol,
            trade.direction.value,
            trade.volume,
            signal.risk_reward_ratio,
        )

    # -------------------------------------------------------------------------
    # Étapes du pipeline
    # -------------------------------------------------------------------------

    def _configure_detector_for_symbol(self, symbol: str) -> None:
        """
        Phase 3 — applique les filtres de qualité du profil instrument aux
        sous-détecteurs SMC (liquidity, FVG, order blocks).

        Les paramètres inconnus d'un détecteur sont ignorés silencieusement
        (rétro-compatibilité avec des détecteurs mockés dans les tests).
        """
        try:
            profile = self._settings.get_instrument_profile(symbol)
        except Exception:
            profile = None
        if profile is None:
            return

        detectors = getattr(self._smc_detector, "detectors", None)
        if not isinstance(detectors, dict):
            return

        param_map = {
            "liquidity": {
                "_min_rejection_ratio": profile.sweep_min_rejection_ratio,
                "_displacement_atr_mult": profile.sweep_displacement_atr_mult,
            },
            "fair_value_gap": {
                "_min_gap_atr": profile.min_fvg_atr,
            },
            "order_blocks": {
                "_max_ob_atr_mult": profile.max_ob_atr_mult,
                "_displacement_confirmation_bars": profile.displacement_confirmation_bars,
            },
        }
        for name, params in param_map.items():
            detector = detectors.get(name)
            if detector is None:
                continue
            for attr, value in params.items():
                if hasattr(detector, attr):
                    setattr(detector, attr, value)

    async def _download_data(
        self, symbol: str, timeframe: TimeFrame | None = None
    ) -> list[Candle] | None:
        """
        Étape 1 — Téléchargement des données de marché.

        Récupère les dernières bougies OHLCV via ``IMarketDataProvider``.

        Args:
            symbol: Symbole à analyser (ex: EURUSD)
            timeframe: Timeframe à télécharger (défaut: timeframe du moteur)

        Returns:
            Liste de bougies, ou ``None`` si la récupération échoue.
        """
        market_logger = get_logger(LogCategory.MARKET_DATA)
        tf = timeframe or self._timeframe
        try:
            candles = await self._market_data.get_latest_candles(
                symbol, tf, self._candle_count
            )
        except Exception as exc:
            market_logger.error("Erreur récupération bougies | %s | %s | %s", symbol, tf.value, exc)
            return None

        if not candles:
            market_logger.warning("Aucune bougie reçue | %s | %s", symbol, tf.value)
            return None

        market_logger.info(
            "Bougies récupérées | %s | %s | %d bougies",
            symbol,
            tf.value,
            len(candles),
        )
        return candles

    async def _analyze_multitimeframe(
        self,
        symbol: str,
        htf_candles: list[Candle],
        ltf_candles: list[Candle],
        setup_tf_candles: list[Candle] | None = None,
    ) -> Any | None:
        """
        Étape 3 — Analyse multi-timeframe (MTF).

        Hiérarchie des timeframes :
        - H4 : Contexte macro — **informatif seulement**, loggé pour le rapport.
          N'autorise ni ne bloque un trade.
        - H1 : Master trend — **Gate absolu**. Détermine la direction autorisée.
        - M15 : Setup — zones FVG/OB + tendance locale (Phase 3). Bonus de
          confluence et source des setups ; ne modifie jamais le biais H1.
        - M5 : Confirmation + entrée — CHoCH/BOS/displacement/retest/rejection.

        La structure H1 est calculée une seule fois par bougie H1 fermée
        (cache). Le M5 est recalculé à chaque nouvelle bougie M5.

        Délègue à ``application.market_context_builder.MarketContextBuilder``.

        Args:
            symbol: Symbole à analyser
            htf_candles: Bougies du timeframe supérieur (1H)
            ltf_candles: Bougies du timeframe d'entrée (5M)
            setup_tf_candles: Bougies du timeframe de setup (15M), optionnel

        Returns:
            MarketContext ou None si l'analyse échoue
        """
        return await self._market_context_builder.build(
            symbol, htf_candles, ltf_candles, setup_tf_candles=setup_tf_candles
        )

    def _log_market_context(self, ctx: Any) -> None:
        """Log structuré du contexte marché."""
        data = ctx.to_dict()
        logger.info("[MARKET] symbol=%s HTF=%s LTF=%s", data["symbol"], data["htf"], data["ltf"])
        logger.info(
            "[TREND] H1=%s structure=%s",
            data["master_trend"],
            data.get("trend_details", {}).get("pattern", "N/A"),
        )
        logger.info("[LEVEL] premium=%s discount=%s", data["premium"], data["discount"])
        logger.info("[15M] setup_tf=%s trend=%s detections=%d",
                     data.get("setup_tf", "M15"), data.get("setup_trend", "neutral"),
                     data.get("setup_smc_data_count", 0))
        logger.info("[5M] LTF detections=%d", len(ctx.ltf_smc_data))
        logger.info("[DIRECTION GATE] master=%s allows_buy=%s allows_sell=%s",
                     data["master_trend"], data["allows_buy"], data["allows_sell"])

    def _on_setup_created(self, setup: Any) -> None:
        """Callback d'observabilité : notifie le debugger d'un nouveau setup.

        Ne modifie aucune règle de trading — comptabilise uniquement l'événement
        de création (dédupliqué par ``setup_id`` dans le debugger).
        """
        if setup is None:
            return
        self._decision_debugger.record_setup_detected(
            symbol=getattr(setup, "symbol", ""),
            setup_id=getattr(setup, "setup_id", ""),
            direction=getattr(getattr(setup, "direction", None), "value", None),
        )

    def _update_setups(self, symbol: str, market_context: Any) -> None:
        """
        Met a jour le state machine des setups pour le symbole.

        Delegue a ``application.setup_service.update_setups_from_market_context``
        (meme logique partagee avec le backtest multi-timeframe).
        """
        update_setups_from_market_context(
            self._setup_tracker, symbol, market_context, self._settings
        )

    async def _revalidate_before_execution(
        self, symbol: str, signal: Signal, market_context: Any
    ) -> bool:
        """
        Revalidation finale d'un signal juste avant l'exécution.

        Vérifie que le contexte n'a pas changé entre la génération du signal
        et l'envoi de l'ordre. Un signal devenu invalide est rejeté.

        Délègue à ``application.execution_guards.revalidate_before_execution``.

        Args:
            symbol: Symbole du trade.
            signal: Signal à revalider.
            market_context: Contexte marché actuel.

        Returns:
            True si le signal est toujours valide, False sinon.
        """
        return await revalidate_before_execution(symbol, signal, market_context)

    async def _final_gate_before_execution(
        self, symbol: str, signal: Signal, market_context: Any
    ) -> bool:
        """
        Gate final avant OrderSend.

        Revalide TOUTES les conditions critiques au moment de l'envoi.
        Si une condition n'est plus valide → REJECT, même si elle était
        valide lors de la génération du signal.

        Délègue à ``application.execution_guards.final_gate_before_execution``.

        Args:
            symbol: Symbole du trade.
            signal: Signal à revalider.
            market_context: Contexte marché actuel.

        Returns:
            True si toutes les conditions sont remplies, False sinon.
        """
        return await final_gate_before_execution(
            symbol, signal, market_context, self._settings
        )

    async def _analyze_smc(self, symbol: str, candles: list[Candle]) -> list[dict] | None:
        """
        Étape 3 — Analyse SMC (Smart Money Concepts).

        Appelle ``SMCDetector.detect()`` sur les bougies fournies.

        Args:
            symbol: Symbole à analyser.
            candles: Liste des bougies OHLCV.

        Returns:
            Liste des détections SMC, ou ``None`` si l'analyse échoue.
        """
        smc_logger = get_logger(LogCategory.SMC)
        try:
            smc_data = await self._smc_detector.detect(candles, symbol)
        except Exception as exc:
            smc_logger.error("Erreur détection SMC | %s | %s", symbol, exc)
            return None

        smc_logger.info("SMC analysé | %s | %d détections", symbol, len(smc_data))
        return smc_data

    async def _generate_signal(
        self,
        symbol: str,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        htf_trends: dict[str, str] | None = None,
        has_high_impact_news: bool = False,
        master_trend: str | None = None,
        market_context: Any | None = None,
    ) -> Signal | None:
        """
        Étape 4 — Validation : génération du signal de trading.

        Appelle ``SignalGenerator.generate()`` avec les bougies et les
        détections SMC. Si aucun signal n'est généré, le cycle s'arrête.

        Args:
            symbol: Symbole à analyser.
            candles: Liste des bougies OHLCV (LTF - 5M).
            smc_data: Détections SMC (LTF).
            htf_smc_data: Détections SMC (HTF - 1H).
            htf_trend: Tendance HTF explicite (optionnel).
            htf_trends: Tendance HTF par timeframe (optionnel).
            has_high_impact_news: Présence de news à impact élevé.
            master_trend: Tendance maître 1H.
            market_context: Contexte marché centralisé.

        Returns:
            Le signal généré, ou ``None`` si aucun signal n'est produit.
        """
        signal_logger = get_logger(LogCategory.SIGNAL)
        try:
            signal = await self._signal_generator.generate(
                candles,
                smc_data,
                htf_smc_data=htf_smc_data,
                htf_trend=htf_trend or (htf_trends.get("H1") if htf_trends else None),
                htf_trends=htf_trends,
                has_high_impact_news=has_high_impact_news,
                master_trend=master_trend,
                market_context=market_context,
            )
        except Exception as exc:
            signal_logger.error("Erreur génération signal | %s | %s", symbol, exc)
            return None

        if signal is None:
            signal_logger.info("Aucun signal généré | %s", symbol)
            return None

        self._last_signal[symbol] = {
            "direction": signal.direction.value,
            "signal_type": signal.signal_type.value,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "confidence": signal.confidence,
            "strategy_name": signal.strategy_name,
            "risk_reward_ratio": signal.risk_reward_ratio,
            "time": signal.created_at.isoformat(),
        }

        self._statistics.record_signal(signal)

        signal_logger.info(
            "Signal généré | %s | %s | %s | confiance=%.2f | R/R=%.2f | stratégie=%s",
            symbol,
            signal.direction.value,
            signal.signal_type.value,
            signal.confidence,
            signal.risk_reward_ratio,
            signal.strategy_name,
        )
        return signal

    async def _calculate_risk(self, symbol: str, signal: Signal) -> float | None:
        """
        Étape 5 — Calcul du risque et de la taille de position.

        Délègue à ``application.trade_orchestrator.TradeOrchestrator``.

        Args:
            symbol: Symbole à analyser.
            signal: Signal validé par le générateur.

        Returns:
            La taille de position (volume), ou ``None`` si le risque est refusé.
        """
        return await self._trade_orchestrator.calculate_risk(symbol, signal)

    async def _execute_trade(self, symbol: str, signal: Signal, volume: float) -> Trade | None:
        """
        Étape 6 — Trade : exécution de l'ordre.

        Délègue à ``application.trade_orchestrator.TradeOrchestrator``.

        Args:
            symbol: Symbole à trader.
            signal: Signal validé.
            volume: Taille de position calculée.

        Returns:
            Le trade ouvert, ou ``None`` si l'exécution échoue.
        """
        return await self._trade_orchestrator.execute_trade(symbol, signal, volume)

    async def _monitor_trade(self, symbol: str, trade: Trade) -> None:
        """
        Étape 7 — Monitoring : suivi du trade ouvert.

        Enregistre le trade auprès du risk manager pour que
        ``can_open_trade`` et ``validate_signal`` restent cohérents
        (ex: ``one_trade_per_symbol``).

        Args:
            symbol: Symbole du trade.
            trade: Trade ouvert à monitorer.
        """
        # Enregistrer le trade auprès du risk manager
        if isinstance(self._risk_manager, RiskManager):
            self._risk_manager.register_trade(trade)
        if self._position_manager is not None:
            self._position_manager.register(trade)
            self._managed_trades[str(trade.id)] = trade
            if self._position_state_store is not None:
                snapshot = self._position_manager.snapshot(trade)
                if snapshot is not None:
                    self._position_state_store.save(
                        trade.ticket if trade.ticket else str(trade.id), snapshot
                    )
        logger.info(
            "Trade enregistré pour monitoring | %s | ticket=%s | volume=%s | direction=%s",
            symbol,
            trade.ticket,
            trade.volume,
            trade.direction.value,
        )

    async def _build_structure_context(self, symbol: str) -> StructureContext | None:
        """Snapshot de structure confirmée pour le suivi (cache par bougie).

        La structure (HL/LH, breaks) est calculée sur les bougies **fermées**
        uniquement et mise à jour **par bougie**, jamais sur tick — aucun
        look-ahead bias possible. Les swings fractals sont confirmés par
        construction (fenêtre de chaque côté du pivot).
        """
        cached = self._structure_cache.get(symbol)
        candles = None
        try:
            candles = await self._market_data.get_latest_candles(symbol, self._timeframe, 60)
        except Exception as exc:  # noqa: BLE001 - provider indisponible
            logger.debug("Structure trailing indisponible | %s | %s", symbol, exc)
            return cached[1] if cached is not None else None
        if not candles:
            return cached[1] if cached is not None else None

        latest_time = max(c.time for c in candles)
        if cached is not None and cached[0] == latest_time:
            return cached[1]  # pas de nouvelle bougie : snapshot inchangé

        last_close = candles[-1].close
        swings = find_swing_points(candles, window=2)
        swing_lows = [s for s in swings if s.type == "low"]
        swing_highs = [s for s in swings if s.type == "high"]
        last_low = swing_lows[-1].price if swing_lows else None
        last_high = swing_highs[-1].price if swing_highs else None
        try:
            atr = calculate_atr(candles, period=14)
        except Exception:  # noqa: BLE001
            atr = None

        context = StructureContext(
            swing_low=last_low,
            swing_high=last_high,
            hl=last_low,
            lh=last_high,
            atr=atr,
            bearish_break=bool(last_low is not None and last_close < last_low),
            bullish_break=bool(last_high is not None and last_close > last_high),
        )
        self._structure_cache[symbol] = (latest_time, context)
        return context

    async def _monitor_open_positions(self) -> None:
        """Applique les règles de position aux ticks disponibles du provider.

        Délègue à ``application.position_monitor.PositionMonitor``.
        """
        await self._position_monitor.monitor(self._managed_trades)

    # -------------------------------------------------------------------------
    # Boucle principale
    # -------------------------------------------------------------------------

    async def _initialize_symbols(self) -> None:
        """
        Phase d'initialisation au démarrage.

        Pour chaque symbole configuré, récupère la bougie actuelle et
        l'enregistre dans le ``CandleSynchronizer`` comme « déjà traitée ».
        Le bot attendra alors véritablement la **prochaine** bougie fermée
        avant de lancer l'analyse, conformément au flux attendu.

        Si la récupération échoue pour un symbole, le symbole est ignoré
        (il sera traité normalement lors du premier cycle de polling).
        """
        logger.info(
            "Phase d'initialisation | enregistrement des bougies actuelles pour %d symbole(s)",
            len(self._symbols),
        )
        market_logger = get_logger(LogCategory.MARKET_DATA)
        for symbol in self._symbols:
            try:
                candles = await self._market_data.get_latest_candles(
                    symbol, self._timeframe, self._candle_count
                )
                if not candles:
                    market_logger.warning("Initialisation ignorée (aucune bougie) | %s", symbol)
                    continue

                latest = max(candles, key=lambda c: c.time)
                self._synchronizer.initialize(symbol, latest.time)
            except Exception as exc:
                logger.error(
                    "Erreur initialisation | %s | %s",
                    symbol,
                    exc,
                    exc_info=True,
                )

    async def _reconcile_open_positions(self) -> None:
        """
        Réconcilie les positions déjà ouvertes sur le compte au démarrage.

        Dans les modes PAPER et LIVE, un compte peut déjà comporter des
        positions (ex: positions restantes d'une session précédente). Cette
        étape récupère ces positions via l'exécuteur (``get_open_positions``)
        et les enregistre auprès ::

        - du ``RiskManager`` (``register_trade``) pour que ``can_open_trade``
          et ``validate_signal`` respectent ``max_open_positions`` et
          ``one_trade_per_symbol`` ;
        - du ``PositionManager`` (``register``) et de ``_managed_trades`` pour
          que les règles de suivi actif (break-even, TP partiel, trailing)
          s'appliquent aussi aux positions existantes.

        En mode ANALYSIS, aucune position n'est gérée : la réconciliation est
        simplement ignorée.
        """
        # En mode ANALYSIS, aucune position n'est ouverte ni gérée.
        if self._trading_mode == TradingMode.ANALYSIS:
            logger.info("Mode ANALYSIS | réconciliation des positions ignorée")
            return

        # Seuls les exécuteurs concrets supportent la récupération des
        # positions (un mock en test ne l'implémente pas).
        if not isinstance(self._executor, (OrderExecutor, PaperOrderExecutor)):
            logger.warning(
                "Réconciliation des positions ignorée | exécuteur=%s",
                type(self._executor).__name__,
            )
            return

        try:
            positions = await self._executor.get_open_positions()
        except Exception as exc:
            logger.error("Erreur réconciliation des positions | %s", exc)
            return

        for trade in positions:
            if not trade.is_open:
                continue
            if isinstance(self._risk_manager, RiskManager):
                self._risk_manager.register_trade(trade)
            if self._position_manager is not None:
                restored = None
                if self._position_state_store is not None and trade.ticket is not None:
                    restored = self._position_state_store.load(trade.ticket)
                if restored is not None:
                    # État persisté : R initial et paliers restaurés sans
                    # recalcul — aucun faux R après redémarrage.
                    self._position_manager.register(trade, restored=restored)
                    logger.info(
                        "[POSITION STATE] %s | ticket=%s | initial_risk=%s | "
                        "profit_lock_level=%s | partials=%s | runner=%s | source=PERSISTED",
                        trade.symbol, trade.ticket, restored.get("initial_risk"),
                        restored.get("profit_lock_level"),
                        restored.get("partial_levels_done"),
                        restored.get("runner_active"),
                    )
                else:
                    # Fallback dégradé : le SL courant sert de SL initial
                    # (risque potentiellement sous-estimé si le SL a déjà bougé).
                    logger.warning(
                        "[POSITION STATE] %s | ticket=%s | état persisté ABSENT | "
                        "fallback DÉGRADÉ : SL courant (%s) utilisé comme SL initial | "
                        "le R initial peut être erroné si le SL avait déjà été déplacé",
                        trade.symbol, trade.ticket, trade.stop_loss,
                    )
                    self._position_manager.register(trade)
                self._managed_trades[str(trade.id)] = trade
            logger.info(
                "Position réconciliée | %s | ticket=%s | volume=%s | entrée=%s",
                trade.symbol,
                trade.ticket,
                trade.volume,
                trade.entry_price,
            )

        if positions:
            logger.info(
                "Réconciliation terminée | %d position(s) ouverte(s) | mode=%s",
                len(positions),
                self._trading_mode.value,
            )

    # -------------------------------------------------------------------------
    # Robustesse 24/7 : heartbeat, reset UTC, équité, notifications
    # -------------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        """
        Vérifie la connexion MT5 et tente une reconnexion automatique.

        En modes DEMO/LIVE, si la connexion est perdue, le moteur tente de
        se reconnecter avec un backoff simple (1, 2, 4, ... 60 s d'écart
        entre les tentatives) pour éviter une boucle de retry agressive.
        Une alerte est envoyée à la perte de connexion et au retour.
        """
        # Heartbeat uniquement pertinent quand vrais ordres MT5 (démo/live).
        if not self._settings.is_trading_active:
            return

        try:
            connected = await self._mt5_connector.is_connected()
        except Exception as exc:
            logger.warning("Heartbeat : erreur lecture connexion | %s", exc)
            return

        if connected:
            # Reconnexion réussie : réinitialiser l'état et notifier.
            if self._disconnected_notified:
                logger.info("MT5 reconnexion réussie")
                await self._notify_critical(
                    "MT5 reconnecté",
                    "La connexion MetaTrader 5 a été rétablie automatiquement.",
                )
            self._disconnected_notified = False
            self._reconnect_attempts = 0
            return

        # Déconnecté : tenter une reconnexion avec backoff.
        delay = min(60, 2 ** self._reconnect_attempts)
        self._reconnect_attempts += 1
        logger.warning(
            "MT5 déconnecté | tentative=%d | prochaine tentative dans %ds",
            self._reconnect_attempts, delay,
        )
        if not self._disconnected_notified:
            await self._notify_critical(
                "MT5 déconnecté",
                "Perte de connexion MetaTrader 5 - reconnexion automatique en cours.",
            )
            self._disconnected_notified = True

        try:
            ok = await self._mt5_connector.reconnect()
            if ok:
                logger.info("MT5 reconnecté avec succès")
                self._disconnected_notified = False
                self._reconnect_attempts = 0
        except Exception as exc:
            logger.error("Erreur reconnexion MT5 | %s", exc)

    async def _daily_utc_reset(self) -> None:
        """
        Réinitialise les circuit breakers journaliers à minuit UTC.

        Compare la date UTC courante à celle du dernier reset. Si elle a
        changé, appelle ``RiskManager.reset_daily()`` et envoie un rapport
        journalier simple résumant la journée écoulée.
        """
        if not isinstance(self._risk_manager, RiskManager):
            return

        today = datetime.now(UTC).date().isoformat()
        if today == self._last_daily_reset:
            return

        # Premier cycle : initialiser sans reset (le jour n'a pas changé).
        if self._last_daily_reset is not None:
            logger.info("Reset journalier UTC | %s", today)
            self._risk_manager.reset_daily()
            await self._notify_daily_report()
        self._last_daily_reset = today

    async def _update_equity_tracking(self) -> None:
        """
        Met à jour l'équité courante pour le calcul du drawdown (RiskManager).

        Appelée à chaque cycle en modes DEMO/LIVE : récupère l'équité
        réelle du compte et alimente ``update_equity`` pour surveiller le
        drawdown maximal et déclencher le circuit breaker si nécessaire.
        """
        if not isinstance(self._risk_manager, RiskManager):
            return
        if not self._settings.is_trading_active:
            return
        try:
            account = await self._mt5_connector.get_account_info()
            self._risk_manager.update_equity(account.equity)
            # Alerte si le drawdown maximal est atteint.
            if self._risk_manager.current_drawdown >= self._risk_manager.settings.max_drawdown:
                await self._notify_critical(
                    "Drawdown atteint",
                    f"Drawdown courant {self._risk_manager.current_drawdown * 100:.1f}% "
                    f"≥ max {self._risk_manager.settings.max_drawdown * 100:.0f}%.",
                )
        except Exception as exc:
            logger.warning("Erreur suivi équité | %s", exc)

    # -------------------------------------------------------------------------
    # Notifications (Telegram / Discord / Email)
    # -------------------------------------------------------------------------

    async def _notify_critical(self, title: str, message: str) -> None:
        """Envoie une alerte critique si un notificateur est configuré."""
        if self._notifier is None:
            return
        try:
            await self._notifier.send_critical(title, message)
        except Exception as exc:
            logger.warning("Notification critique échouée | %s | %s", title, exc)

    async def _notify_trade_opened(self, trade: Trade) -> None:
        """Envoie une alerte de nouveau trade ouvert."""
        if self._notifier is None:
            return
        try:
            await self._notifier.send_trade_notification(
                {
                    "action": "ouvert",
                    "symbol": trade.symbol,
                    "direction": trade.direction.value,
                    "volume": str(trade.volume),
                    "entry_price": str(trade.entry_price),
                    "stop_loss": str(trade.stop_loss),
                    "take_profit": str(trade.take_profit),
                    "ticket": trade.ticket,
                }
            )
        except Exception as exc:
            logger.warning("Notification trade ouvert échouée | %s", exc)

    async def _notify_trade_closed(self, trade: Trade, reason: str) -> None:
        """Envoie une alerte de trade fermé (avec résultat)."""
        if self._notifier is None:
            return
        profit = trade.profit if trade.profit is not None else Decimal("0")
        result = "win" if profit > 0 else "loss" if profit < 0 else "breakeven"
        try:
            await self._notifier.send_trade_notification(
                {
                    "action": "fermé",
                    "result": result,
                    "symbol": trade.symbol,
                    "direction": trade.direction.value,
                    "volume": str(trade.volume),
                    "entry_price": str(trade.entry_price),
                    "profit": str(profit),
                    "reason": reason,
                }
            )
        except Exception as exc:
            logger.warning("Notification trade fermé échouée | %s", exc)

    async def _notify_daily_report(self) -> None:
        """Envoie un rapport journalier simple."""
        if self._notifier is None:
            return
        try:
            summary = self._statistics.get_summary()
            await self._notifier.send_daily_report(summary)
        except Exception as exc:
            logger.warning("Rapport journalier échoué | %s", exc)

    async def run_forever(self) -> None:
        """
        Boucle principale du moteur.

        1. **Initialisation** — Enregistre la bougie actuelle de chaque
           symbole dans le ``CandleSynchronizer`` (sans l'analyser).
        2. **Boucle de polling** — Toutes les ``poll_interval`` secondes,
           appelle ``analyze_symbol`` pour chaque symbole. L'analyse
           n'est effective que sur une nouvelle bougie.
        3. **Attente** — Le moteur attend la prochaine bougie fermée.

        La boucle s'arrête proprement si :
        - ``stop()`` est appelée (``_running`` mis à False)
        - La tâche est annulée (``asyncio.CancelledError``)
        """
        self._running = True
        logger.info(
            "TradingEngine démarré | mode=%s | symboles=%s | timeframe=%s | poll=%.1fs",
            self._trading_mode.value,
            self._symbols,
            self._timeframe.value,
            self._poll_interval,
        )

        try:
            # Phase 1 : Initialisation
            await self._initialize_symbols()

            # Phase 1bis : Réconciliation des positions ouvertes (modes PAPER/LIVE)
            await self._reconcile_open_positions()

            # Phase 2 : Boucle de polling
            while self._running:
                # Heartbeat : vérifie/relance la connexion MT5 (modes actifs).
                await self._heartbeat()

                # Monitoring actif des positions ouvertes (BE, TP partiel, trailing).
                await self._monitor_open_positions()

                # Reset journalier fiable des circuit breakers (UTC).
                await self._daily_utc_reset()

                # Suivi du drawdown à partir de l'équité réelle du compte.
                await self._update_equity_tracking()

                for symbol in self._symbols:
                    try:
                        await self.analyze_symbol(symbol)
                    except Exception as exc:
                        logger.error(
                            "Erreur inattendue analyse | %s | %s",
                            symbol,
                            exc,
                            exc_info=True,
                        )

                # Phase 3 : Attente prochaine bougie
                logger.debug("Attente prochaine bougie | %.1fs", self._poll_interval)
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("TradingEngine arrêté (tâche annulée)")
            self._running = False
            raise

    def start(self) -> asyncio.Task[None]:
        """
        Lance le moteur en tâche de fond.

        Returns:
            La tâche asyncio créée.
        """
        if self._task is not None and not self._task.done():
            return self._task
        # Marquer comme actif avant de créer la tâche pour que is_running
        # soit True immédiatement après l'appel à start().
        self._running = True
        self._task = asyncio.create_task(self.run_forever())
        return self._task

    async def stop(self) -> None:
        """Arrête le moteur proprement."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("TradingEngine arrêté")

    # -------------------------------------------------------------------------
    # Statut
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """
        Retourne le statut du moteur pour le monitoring.

        Returns:
            Dictionnaire avec :
            - running: bool
            - symbols: list[str]
            - timeframe: str
            - poll_interval: float
            - candle_count: int
            - last_candle_time: dict[symbol, ISO time | None]
            - last_signal: dict[symbol, signal info]
        """
        last_processed = self._synchronizer.get_all_last_processed()
        return {
            "running": self._running,
            "trading_mode": self._trading_mode.value,
            "symbols": list(self._symbols),
            "timeframe": self._timeframe.value,
            "poll_interval": self._poll_interval,
            "candle_count": self._candle_count,
            "last_candle_time": {
                symbol: time.isoformat() if time else None
                for symbol, time in last_processed.items()
            },
            "last_signal": dict(self._last_signal),
            "statistics": self._statistics.get_summary(),
        }

    # -------------------------------------------------------------------------
    # Statistiques
    # -------------------------------------------------------------------------

    def get_statistics(self) -> dict[str, Any]:
        """
        Retourne les statistiques de trading complètes.

        Inclut : nombre d'analyses, signaux, trades, win rate, profit
        factor, expectancy, drawdown, Sharpe ratio, équity curve, etc.

        Returns:
            Dictionnaire complet des statistiques.
        """
        return self._statistics.get_stats()

    def get_statistics_summary(self) -> dict[str, Any]:
        """
        Retourne un résumé léger des statistiques (sans les listes).

        Returns:
            Dictionnaire résumé des statistiques.
        """
        return self._statistics.get_summary()

    # -------------------------------------------------------------------------
    # Helpers internes
    # -------------------------------------------------------------------------

    def _get_open_positions_count(self) -> int:
        """
        Retourne le nombre de positions ouvertes suivies par le risk manager.

        Utilise ``open_positions_count`` si disponible (classe concrète
        ``RiskManager``), sinon retourne 0.
        """
        if isinstance(self._risk_manager, RiskManager):
            return self._risk_manager.open_positions_count
        return 0
