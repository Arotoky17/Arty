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
from decimal import Decimal
from typing import Any

from arty_trading.application.candle_synchronizer import CandleSynchronizer
from arty_trading.application.statistics import TradingStatistics
from arty_trading.application.trade_journal import TradeJournal
from arty_trading.config.settings import Settings
from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import LogCategory, TimeFrame, TradingMode
from arty_trading.core.interfaces import (
    IMarketDataProvider,
    IMT5Connector,
    IOrderExecutor,
    IRiskManager,
    ISMCDetector,
)
from arty_trading.logging import get_logger
from arty_trading.modules.decision import MultiTimeframeAnalyzer
from arty_trading.modules.execution import OrderExecutor, PaperOrderExecutor, PositionManager
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.signals.news import EconomicCalendar

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
        position_settings = getattr(settings, "position", None)
        self._position_manager = (
            PositionManager(position_settings)
            if getattr(position_settings, "enabled", False) is True
            else None
        )
        self._managed_trades: dict[str, Trade] = {}
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
        3. Analyse SMC (``_analyze_smc``)
        4. Validation / génération de signal (``_generate_signal``)
        5. Calcul du risque (``_calculate_risk``)
        6. Trade / exécution (``_execute_trade``)
        7. Monitoring (``_monitor_trade``)

        Args:
            symbol: Symbole à analyser (ex: EURUSD)
        """
        # ------------------------------------------------------------------
        # Étape 1 : Téléchargement des données
        # ------------------------------------------------------------------
        candles = await self._download_data(symbol)
        if candles is None:
            return

        # ------------------------------------------------------------------
        # Étape 2 : Vérification nouvelle bougie
        # ------------------------------------------------------------------
        latest_candle = max(candles, key=lambda c: c.time)

        if not self._synchronizer.is_new_candle(symbol, latest_candle.time):
            logger.debug(
                "Pas de nouvelle bougie | %s | %s | dernière traitée=%s",
                symbol,
                self._timeframe.value,
                self._synchronizer.get_last_processed(symbol),
            )
            return

        # Marquer la bougie comme traitée **immédiatement** pour garantir
        # qu'elle ne sera jamais ré-analysée, même si l'analyse échoue.
        self._synchronizer.mark_processed(symbol, latest_candle.time)

        # Enregistrer l'analyse dans les statistiques (tous les modes)
        self._statistics.record_analysis(symbol)

        logger.info("╔════════════════════════════════════════════════════════════")
        logger.info(
            "║ Nouvelle bougie détectée | %s | %s | time=%s",
            symbol,
            self._timeframe.value,
            latest_candle.time.isoformat(),
        )
        logger.info("╚════════════════════════════════════════════════════════════")

        # ------------------------------------------------------------------
        # Étape 3 : Analyse SMC
        # ------------------------------------------------------------------
        smc_data = await self._analyze_smc(symbol, candles)
        if smc_data is None:
            return

        # ------------------------------------------------------------------
        # Étape 4 : Validation (génération de signal)
        # ------------------------------------------------------------------
        htf_trends: dict[str, str] | None = None
        htf_smc_data: list[dict] | None = None
        decision_settings = getattr(self._settings, "decision", None)
        if (
            getattr(decision_settings, "enabled", False) is True
            and getattr(decision_settings, "enable_mtf", False) is True
        ):
            try:
                mtf = await self._mtf_analyzer.analyze(symbol, self._candle_count)
                htf_trends = mtf.trends
                htf_smc_data = mtf.detections.get(TimeFrame.H1, [])
            except Exception as exc:
                logger.error("Analyse multi-timeframe échouée | %s | %s", symbol, exc)
                return

        signal = await self._generate_signal(
            symbol,
            candles,
            smc_data,
            htf_smc_data=htf_smc_data,
            htf_trends=htf_trends,
            has_high_impact_news=(
                self._economic_calendar.is_blocked(symbol)
                if self._economic_calendar is not None
                else False
            ),
        )
        if signal is None:
            return

        # Mode ANALYSIS : arrêter après la génération du signal
        # (aucune position ouverte, uniquement les analyses)
        if self._trading_mode == TradingMode.ANALYSIS:
            logger.info(
                "Mode ANALYSIS | signal généré, aucune position ouverte | %s | %s",
                symbol,
                signal.direction.value,
            )
            return

        # ------------------------------------------------------------------
        # Étape 5 : Calcul du risque
        # ------------------------------------------------------------------
        volume = await self._calculate_risk(symbol, signal)
        if volume is None:
            return

        # ------------------------------------------------------------------
        # Étape 6 : Trade (exécution)
        # ------------------------------------------------------------------
        trade = await self._execute_trade(symbol, signal, volume)
        if trade is None:
            return

        # Enregistrer le trade dans les statistiques (modes PAPER et LIVE)
        self._statistics.record_trade_opened(trade)
        if self._journal is not None:
            self._journal.record(trade, "opened")

        # ------------------------------------------------------------------
        # Étape 7 : Monitoring
        # ------------------------------------------------------------------
        await self._monitor_trade(symbol, trade)

    # -------------------------------------------------------------------------
    # Étapes du pipeline
    # -------------------------------------------------------------------------

    async def _download_data(self, symbol: str) -> list[Candle] | None:
        """
        Étape 1 — Téléchargement des données de marché.

        Récupère les dernières bougies OHLCV via ``IMarketDataProvider``.

        Args:
            symbol: Symbole à analyser (ex: EURUSD)

        Returns:
            Liste de bougies, ou ``None`` si la récupération échoue.
        """
        market_logger = get_logger(LogCategory.MARKET_DATA)
        try:
            candles = await self._market_data.get_latest_candles(
                symbol, self._timeframe, self._candle_count
            )
        except Exception as exc:
            market_logger.error("Erreur récupération bougies | %s | %s", symbol, exc)
            return None

        if not candles:
            market_logger.warning("Aucune bougie reçue | %s", symbol)
            return None

        market_logger.info(
            "Bougies récupérées | %s | %s | %d bougies",
            symbol,
            self._timeframe.value,
            len(candles),
        )
        return candles

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
        htf_trends: dict[str, str] | None = None,
        has_high_impact_news: bool = False,
    ) -> Signal | None:
        """
        Étape 4 — Validation : génération du signal de trading.

        Appelle ``SignalGenerator.generate()`` avec les bougies et les
        détections SMC. Si aucun signal n'est généré, le cycle s'arrête.

        Args:
            symbol: Symbole à analyser.
            candles: Liste des bougies OHLCV.
            smc_data: Détections SMC.

        Returns:
            Le signal généré, ou ``None`` si aucun signal n'est produit.
        """
        signal_logger = get_logger(LogCategory.SIGNAL)
        try:
            signal = await self._signal_generator.generate(
                candles,
                smc_data,
                htf_smc_data=htf_smc_data,
                htf_trend=htf_trends.get("H1") if htf_trends else None,
                htf_trends=htf_trends,
                has_high_impact_news=has_high_impact_news,
            )
        except Exception as exc:
            signal_logger.error("Erreur génération signal | %s | %s", symbol, exc)
            return None

        if signal is None:
            signal_logger.info("Aucun signal généré | %s", symbol)
            return None

        # Enregistrer le dernier signal pour le statut / endpoint
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

        # Enregistrer le signal dans les statistiques (tous les modes)
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

        Sous-étapes :
        a. Récupération des infos du compte (``get_account_info``)
        b. ``can_open_trade`` — vérifie si un nouveau trade peut être ouvert
        c. ``validate_signal`` — valide si le signal respecte les règles
        d. ``calculate_position_size`` — calcule la taille optimale

        Args:
            symbol: Symbole à analyser.
            signal: Signal validé par le générateur.

        Returns:
            La taille de position (volume), ou ``None`` si le risque est
            refusé.
        """
        risk_logger = get_logger(LogCategory.RISK)

        # a. Récupérer les infos du compte
        try:
            account = await self._mt5_connector.get_account_info()
        except Exception as exc:
            risk_logger.error("Erreur récupération compte | %s | %s", symbol, exc)
            return None

        # b. can_open_trade
        try:
            can_open = await self._risk_manager.can_open_trade(symbol)
        except Exception as exc:
            risk_logger.error("Erreur can_open_trade | %s | %s", symbol, exc)
            return None

        if not can_open:
            risk_logger.info(
                "Trade refusé (can_open_trade=False) | %s | positions ouvertes=%d",
                symbol,
                self._get_open_positions_count(),
            )
            return None

        # c. validate_signal
        try:
            is_valid = await self._risk_manager.validate_signal(signal, account)
        except Exception as exc:
            risk_logger.error("Erreur validate_signal | %s | %s", symbol, exc)
            return None

        if not is_valid:
            risk_logger.info(
                "Signal rejeté par le risk manager | %s | %s | confiance=%.2f | R/R=%.2f",
                symbol,
                signal.direction.value,
                signal.confidence,
                signal.risk_reward_ratio,
            )
            return None

        risk_logger.info(
            "Signal validé par le risk manager | %s | %s",
            symbol,
            signal.direction.value,
        )

        # d. calculate_position_size
        try:
            volume = await self._risk_manager.calculate_position_size(signal, account)
        except Exception as exc:
            risk_logger.error("Erreur calculate_position_size | %s | %s", symbol, exc)
            return None

        risk_logger.info("Taille de position calculée | %s | volume=%.2f", symbol, volume)
        return volume

    async def _execute_trade(self, symbol: str, signal: Signal, volume: float) -> Trade | None:
        """
        Étape 6 — Trade : exécution de l'ordre.

        Appelle ``OrderExecutor.open_order()`` avec le signal et le volume.

        Args:
            symbol: Symbole à trader.
            signal: Signal validé.
            volume: Taille de position calculée.

        Returns:
            Le trade ouvert, ou ``None`` si l'exécution échoue.
        """
        exec_logger = get_logger(LogCategory.EXECUTION)
        try:
            trade = await self._executor.open_order(signal, volume)
        except Exception as exc:
            exec_logger.error("Erreur ouverture ordre | %s | %s", symbol, exc)
            return None

        exec_logger.info(
            "Ordre ouvert | %s | %s | volume=%s | ticket=%s",
            trade.symbol,
            trade.direction.value,
            trade.volume,
            trade.ticket,
        )
        return trade

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

        logger.info(
            "Trade enregistré pour monitoring | %s | ticket=%s | volume=%s | direction=%s",
            symbol,
            trade.ticket,
            trade.volume,
            trade.direction.value,
        )

    async def _monitor_open_positions(self) -> None:
        """Applique les règles de position aux ticks disponibles du provider."""
        if self._position_manager is None:
            return
        get_tick = getattr(self._market_data, "get_tick", None)
        if get_tick is None:
            logger.warning("Gestion de position inactive : provider sans get_tick")
            return
        for key, trade in list(self._managed_trades.items()):
            try:
                tick = await get_tick(trade.symbol)
                if not tick:
                    continue
                raw_price = tick["bid"] if trade.direction.value == "buy" else tick["ask"]
                actions = self._position_manager.evaluate(trade, Decimal(str(raw_price)))
                for action in actions:
                    if action.kind == "modify" and action.stop_loss is not None:
                        await self._executor.modify_order(trade, stop_loss=float(action.stop_loss))
                    elif action.kind == "close":
                        closed = await self._executor.close_order(trade)
                        self._statistics.record_trade_closed(closed)
                        if self._journal is not None:
                            self._journal.record(closed, action.reason)
                        if isinstance(self._risk_manager, RiskManager):
                            self._risk_manager.close_trade(closed, closed.profit or Decimal("0"))
                        self._position_manager.forget(trade)
                        self._managed_trades.pop(key, None)
                    elif action.kind == "partial_close":
                        partial_close = getattr(self._executor, "close_partial_order", None)
                        if partial_close is None or action.close_fraction is None:
                            logger.warning("TP partiel non supporté | ticket=%s", trade.ticket)
                            continue
                        closed = await partial_close(trade, float(action.close_fraction))
                        if closed is not None:
                            self._statistics.record_trade_closed(closed)
                            if self._journal is not None:
                                self._journal.record(closed, action.reason)
            except Exception as exc:
                logger.error("Erreur suivi position | ticket=%s | %s", trade.ticket, exc)

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
                await self._monitor_open_positions()
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
