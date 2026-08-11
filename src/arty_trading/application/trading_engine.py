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
from arty_trading.application.statistics import TradingStatistics
from arty_trading.application.trade_journal import TradeJournal
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
    MT5OrderError,
    OrderExecutor,
    PaperOrderExecutor,
    PositionManager,
)
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
        notifier: Any | None = None,
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
        # ------------------------------------------------------------------
        # Étape 1 : Téléchargement des données H1 + M5
        # ------------------------------------------------------------------
        htf_candles = await self._download_data(symbol, self._settings.default_timeframe)
        if htf_candles is None:
            return

        ltf_candles = await self._download_data(symbol, TimeFrame.M5)
        if ltf_candles is None:
            return

        # ------------------------------------------------------------------
        # Étape 2 : Vérification nouvelle bougie (sur le LTF)
        # ------------------------------------------------------------------
        latest_candle = max(ltf_candles, key=lambda c: c.time)

        if not self._synchronizer.is_new_candle(symbol, latest_candle.time):
            logger.debug(
                "Pas de nouvelle bougie | %s | %s | dernière traitée=%s",
                symbol,
                self._timeframe.value,
                self._synchronizer.get_last_processed(symbol),
            )
            return

        self._synchronizer.mark_processed(symbol, latest_candle.time)
        self._statistics.record_analysis(symbol)

        logger.info("╔════════════════════════════════════════════════════════════")
        logger.info(
            "║ Nouvelle bougie détectée | %s | M5 | time=%s",
            symbol,
            latest_candle.time.isoformat(),
        )
        logger.info("╚════════════════════════════════════════════════════════════")

        # ------------------------------------------------------------------
        # Étape 3 : Analyse MTF (H1 direction + M5 confirmation)
        # ------------------------------------------------------------------
        market_context = await self._analyze_multitimeframe(symbol, htf_candles, ltf_candles)
        if market_context is None:
            logger.info("Pipeline arrete (Etape 3) | %s | analyse MTF echouee", symbol)
            return

        self._log_market_context(market_context)

        if market_context.is_neutral():
            logger.info(
                "NO_TRADE | %s | H1=NEUTRAL | aucune direction claire",
                symbol,
            )
            return

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
            logger.info("Pipeline arrete (Etape 4) | %s | aucun signal genere", symbol)
            return

        # Revalidation juste avant exécution (étape 5)
        revalidation_passed = await self._revalidate_before_execution(
            symbol, signal, market_context
        )
        if not revalidation_passed:
            logger.info(
                "Pipeline arrete (Revalidation) | %s | signal devenu invalide",
                symbol,
            )
            return

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
            logger.info("Pipeline arrete (Etape 5) | %s | calcul du risque refuse", symbol)
            return

        # ------------------------------------------------------------------
        # Étape 6 : Trade (exécution)
        # ------------------------------------------------------------------
        trade = await self._execute_trade(symbol, signal, volume)
        if trade is None:
            logger.info("Pipeline arrete (Etape 6) | %s | execution ordre echouee", symbol)
            return

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

    async def _download_data(self, symbol: str, timeframe: TimeFrame | None = None) -> list[Candle] | None:
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
        self, symbol: str, htf_candles: list[Candle], ltf_candles: list[Candle]
    ) -> Any | None:
        """
        Étape 3 — Analyse multi-timeframe (MTF).

        Détermine la tendance maître sur le timeframe supérieur (1H) et
        analyse les concepts SMC sur le timeframe d'entrée (5M).

        Args:
            symbol: Symbole à analyser
            htf_candles: Bougies du timeframe supérieur (1H)
            ltf_candles: Bougies du timeframe d'entrée (5M)

        Returns:
            MarketContext ou None si l'analyse échoue
        """
        from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer

        trend_analyzer = MasterTrendAnalyzer(htf=TimeFrame.H1)
        master_trend = trend_analyzer.get_master_trend(htf_candles)

        htf_smc_data: list[dict] = []
        ltf_smc_data: list[dict] = []

        try:
            htf_smc_data = await self._smc_detector.detect(htf_candles, symbol)
        except Exception as exc:
            logger.error("Erreur analyse SMC H1 | %s | %s", symbol, exc)

        try:
            ltf_smc_data = await self._smc_detector.detect(ltf_candles, symbol)
        except Exception as exc:
            logger.error("Erreur analyse SMC M5 | %s | %s", symbol, exc)

        market_context = trend_analyzer.build_market_context(
            symbol=symbol,
            htf_candles=htf_candles,
            ltf_candles=ltf_candles,
            htf_smc_data=htf_smc_data,
            ltf_smc_data=ltf_smc_data,
        )

        logger.info(
            "Analyse MTF | %s | H1=%s | M5 detections=%d | H1 detections=%d",
            symbol,
            market_context.master_trend,
            len(ltf_smc_data),
            len(htf_smc_data),
        )

        return market_context

    def _log_market_context(self, ctx: Any) -> None:
        """Log structuré du contexte marché."""
        data = ctx.to_dict()
        logger.info("[MARKET] symbol=%s HTF=%s LTF=%s", data["symbol"], data["htf"], data["ltf"])
        logger.info("[TREND] H1=%s structure=%s", data["master_trend"], data.get("trend_details", {}).get("pattern", "N/A"))
        logger.info("[LEVEL] premium=%s discount=%s", data["premium"], data["discount"])
        logger.info("[5M] LTF detections=%d", len(ctx.ltf_smc_data))
        logger.info("[DIRECTION GATE] master=%s allows_buy=%s allows_sell=%s",
                     data["master_trend"], data["allows_buy"], data["allows_sell"])

    async def _revalidate_before_execution(
        self, symbol: str, signal: Signal, market_context: Any
    ) -> bool:
        """
        Revalidation finale d'un signal juste avant l'exécution.

        Vérifie que le contexte n'a pas changé entre la génération du signal
        et l'envoi de l'ordre. Un signal devenu invalide est rejeté.

        Args:
            symbol: Symbole du trade.
            signal: Signal à revalider.
            market_context: Contexte marché actuel.

        Returns:
            True si le signal est toujours valide, False sinon.
        """
        reval_logger = get_logger(LogCategory.SIGNAL)

        direction_str = "bullish" if signal.direction == Direction.BUY else "bearish"
        current_trend = market_context.master_trend

        reval_logger.info(
            "[REVALIDATION] %s | direction=%s | master_trend=%s",
            symbol, direction_str, current_trend,
        )

        if current_trend == "neutral":
            reval_logger.warning(
                "Signal REJETÉ (revalidation) | %s | H1=NEUTRAL | BUY et SELL interdits",
                symbol,
            )
            return False

        if current_trend == "bullish" and direction_str == "bearish":
            reval_logger.warning(
                "Signal REJETÉ (revalidation) | %s | H1=BULLISH + SELL → MASTER_TREND_CONFLICT",
                symbol,
            )
            return False

        if current_trend == "bearish" and direction_str == "bullish":
            reval_logger.warning(
                "Signal REJETÉ (revalidation) | %s | H1=BEARISH + BUY → MASTER_TREND_CONFLICT",
                symbol,
            )
            return False

        reval_logger.info(
            "[REVALIDATION] %s | PASS | signal toujours valide",
            symbol,
        )
        return True

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

        # Garde-fou : en mode DEMO, refuser si le compte s'avère réel.
        if self._trading_mode == TradingMode.DEMO and account.mode == TradingMode.LIVE:
            risk_logger.error(
                "SECURITE : mode DEMO mais compte réel détecté (login=%s) - trade refusé",
                account.login,
            )
            await self._notify_critical(
                "Sécurité DEMO",
                f"Compte réel détecté (login={account.login}) en mode DEMO - trade bloqué.",
            )
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
        except MT5OrderError as exc:
            exec_logger.error(
                "Échec exécution ordre | %s | %s | %s", symbol, signal.direction.value, exc,
            )
            await self._notify_critical(
                "Échec exécution ordre",
                f"{symbol} | {signal.direction.value} | {exc}",
            )
            return None
        except Exception as exc:
            exec_logger.error("Erreur ouverture ordre | %s | %s", symbol, exc)
            return None

        exec_logger.info(
            "Ordre ouvert | %s | %s | volume=%s | ticket=%s | entrée=%s",
            trade.symbol,
            trade.direction.value,
            trade.volume,
            trade.ticket,
            trade.entry_price,
        )
        # Alerte Telegram : nouveau trade ouvert.
        await self._notify_trade_opened(trade)
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
                        try:
                            closed = await self._executor.close_order(trade)
                        except MT5OrderError as exc:
                            error_msg = str(exc)
                            if "introuvable" in error_msg:
                                logger.warning(
                                    "Position fantôme supprimée du suivi | ticket=%s | %s",
                                    trade.ticket, exc,
                                )
                                self._position_manager.forget(trade)
                                self._managed_trades.pop(key, None)
                                if isinstance(self._risk_manager, RiskManager):
                                    self._risk_manager.close_trade(
                                        trade, Decimal("0")
                                    )
                                continue
                            logger.error("Échec fermeture position | %s | %s", trade.ticket, exc)
                            await self._notify_critical(
                                "Échec fermeture position",
                                f"ticket={trade.ticket} | {exc}",
                            )
                            continue
                        self._statistics.record_trade_closed(closed)
                        if self._journal is not None:
                            self._journal.record(closed, action.reason)
                        if isinstance(self._risk_manager, RiskManager):
                            self._risk_manager.close_trade(closed, closed.profit or Decimal("0"))
                        self._position_manager.forget(trade)
                        self._managed_trades.pop(key, None)
                        await self._notify_trade_closed(closed, action.reason)
                    elif action.kind == "partial_close":
                        partial_close = getattr(self._executor, "close_partial_order", None)
                        if partial_close is None or action.close_fraction is None:
                            logger.warning("TP partiel non supporté | ticket=%s", trade.ticket)
                            continue
                        try:
                            closed = await partial_close(trade, float(action.close_fraction))
                        except MT5OrderError as exc:
                            logger.error("Échec TP partiel | %s | %s", trade.ticket, exc)
                            continue
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
