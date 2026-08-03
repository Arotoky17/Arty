"""
Moteur de trading (Trading Engine) - Orchestration live des modules.

Le ``TradingEngine`` relie les briques existantes (données de marché, détecteur
SMC, générateur de signaux, gestionnaire de risque, exécuteur d'ordres) en une
boucle d'analyse continue. Pour chaque symbole configuré et à chaque nouvelle
bougie fermée, il déclenche la chaîne :

    get_latest_candles → SMCDetector.detect → SignalGenerator.generate
    → RiskManager.can_open_trade → RiskManager.validate_signal
    → RiskManager.calculate_position_size → OrderExecutor.open_order

Chaque étape est journalisée avec la catégorie ``LogCategory`` appropriée afin
de pouvoir retracer l'ordre analyse → signal → décision risque → ordre dans
les logs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from arty_trading.config.settings import Settings
from arty_trading.core.enums import LogCategory, TimeFrame
from arty_trading.core.interfaces import (
    IMarketDataProvider,
    IMT5Connector,
    IOrderExecutor,
    IRiskManager,
    ISMCDetector,
)
from arty_trading.logging import get_logger
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalGenerator

logger = get_logger(LogCategory.SYSTEM)


class TradingEngine:
    """
    Moteur d'orchestration du trading en live.

    Pour chaque symbole configuré (``settings.symbols_list``) et à chaque
    nouvelle bougie fermée sur le timeframe configuré (``settings.default_timeframe``),
    déclenche la chaîne d'analyse complète :

    1. Récupère les dernières bougies via ``IMarketDataProvider``
    2. Appelle ``SMCDetector.detect()`` pour les détections SMC
    3. Appelle ``SignalGenerator.generate()`` avec les bougies + détections
    4. Si un signal est généré :
       a. ``RiskManager.can_open_trade()``
       b. ``RiskManager.validate_signal()``
       c. ``RiskManager.calculate_position_size()``
    5. Si tout est validé, ``OrderExecutor.open_order()``
    6. Journalise chaque étape avec la catégorie appropriée

    Attributes:
        _settings: Configuration globale
        _market_data: Provider de données de marché
        _smc_detector: Détecteur SMC
        _signal_generator: Générateur de signaux
        _risk_manager: Gestionnaire de risque
        _executor: Exécuteur d'ordres
        _mt5_connector: Connecteur MT5 (pour les infos du compte)
        _poll_interval: Intervalle de polling en secondes
        _candle_count: Nombre de bougies à récupérer
        _symbols: Liste des symboles à analyser
        _timeframe: Timeframe d'analyse
        _last_candle_time: Dernière bougie traitée par symbole (évite la ré-analyse)
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

        # Suivi des bougies traitées : symbol -> time de la dernière bougie analysée
        self._last_candle_time: dict[str, datetime] = {}
        # Dernier signal analysé par symbole (pour le statut / endpoint)
        self._last_signal: dict[str, dict[str, Any]] = {}

        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

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

    # -------------------------------------------------------------------------
    # Analyse d'un symbole
    # -------------------------------------------------------------------------

    async def analyze_symbol(self, symbol: str) -> None:
        """
        Analyse un symbole : récupère les bougies, détecte le SMC, génère un
        signal, valide le risque et ouvre un ordre si tout est validé.

        Ne déclenche l'analyse que sur une nouvelle bougie (pas de ré-analyse
        sur une bougie déjà traitée pour ce symbole).

        Args:
            symbol: Symbole à analyser (ex: EURUSD)
        """
        # ------------------------------------------------------------------
        # Étape a : Récupérer les dernières bougies
        # ------------------------------------------------------------------
        market_logger = get_logger(LogCategory.MARKET_DATA)
        try:
            candles = await self._market_data.get_latest_candles(
                symbol, self._timeframe, self._candle_count
            )
        except Exception as exc:
            market_logger.error("Erreur récupération bougies | %s | %s", symbol, exc)
            return

        if not candles:
            market_logger.warning("Aucune bougie reçue | %s", symbol)
            return

        market_logger.info(
            "Bougies récupérées pour analyse | %s | %s | %d bougies",
            symbol,
            self._timeframe.value,
            len(candles),
        )

        # ------------------------------------------------------------------
        # Détection de nouvelle bougie fermée
        # ------------------------------------------------------------------
        # Les bougies sont en ordre chronologique (plus ancienne → plus récente).
        # On utilise la bougie la plus récente pour détecter un changement.
        latest_candle = max(candles, key=lambda c: c.time)
        last_processed = self._last_candle_time.get(symbol)

        if last_processed is not None and latest_candle.time <= last_processed:
            # Pas de nouvelle bougie → on ne ré-analyse pas
            return

        self._last_candle_time[symbol] = latest_candle.time
        logger.info(
            "Nouvelle bougie détectée | %s | %s | time=%s",
            symbol,
            self._timeframe.value,
            latest_candle.time.isoformat(),
        )

        # ------------------------------------------------------------------
        # Étape b : Détection SMC
        # ------------------------------------------------------------------
        smc_logger = get_logger(LogCategory.SMC)
        try:
            smc_data = await self._smc_detector.detect(candles, symbol)
        except Exception as exc:
            smc_logger.error("Erreur détection SMC | %s | %s", symbol, exc)
            return

        smc_logger.info(
            "SMC analysé | %s | %d détections",
            symbol,
            len(smc_data),
        )

        # ------------------------------------------------------------------
        # Étape c : Génération de signal
        # ------------------------------------------------------------------
        signal_logger = get_logger(LogCategory.SIGNAL)
        try:
            signal = await self._signal_generator.generate(candles, smc_data)
        except Exception as exc:
            signal_logger.error("Erreur génération signal | %s | %s", symbol, exc)
            return

        if signal is None:
            signal_logger.info("Aucun signal généré | %s", symbol)
            return

        # Enregistrer le dernier signal pour le statut
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

        signal_logger.info(
            "Signal généré | %s | %s | %s | confiance=%.2f | R/R=%.2f | stratégie=%s",
            symbol,
            signal.direction.value,
            signal.signal_type.value,
            signal.confidence,
            signal.risk_reward_ratio,
            signal.strategy_name,
        )

        # ------------------------------------------------------------------
        # Étape d : Validation du risque
        # ------------------------------------------------------------------
        risk_logger = get_logger(LogCategory.RISK)

        # Récupérer les infos du compte (nécessaires pour validate_signal et
        # calculate_position_size)
        try:
            account = await self._mt5_connector.get_account_info()
        except Exception as exc:
            risk_logger.error("Erreur récupération compte | %s | %s", symbol, exc)
            return

        # can_open_trade : vérifie si un nouveau trade peut être ouvert
        try:
            can_open = await self._risk_manager.can_open_trade(symbol)
        except Exception as exc:
            risk_logger.error("Erreur can_open_trade | %s | %s", symbol, exc)
            return

        if not can_open:
            risk_logger.info(
                "Trade refusé (can_open_trade=False) | %s | positions ouvertes=%d",
                symbol,
                self._get_open_positions_count(),
            )
            return

        # validate_signal : valide si le signal respecte les règles de risque
        try:
            is_valid = await self._risk_manager.validate_signal(signal, account)
        except Exception as exc:
            risk_logger.error("Erreur validate_signal | %s | %s", symbol, exc)
            return

        if not is_valid:
            risk_logger.info(
                "Signal rejeté par le risk manager | %s | %s | confiance=%.2f | R/R=%.2f",
                symbol,
                signal.direction.value,
                signal.confidence,
                signal.risk_reward_ratio,
            )
            return

        risk_logger.info(
            "Signal validé par le risk manager | %s | %s",
            symbol,
            signal.direction.value,
        )

        # calculate_position_size : calcule la taille de position optimale
        try:
            volume = await self._risk_manager.calculate_position_size(signal, account)
        except Exception as exc:
            risk_logger.error("Erreur calculate_position_size | %s | %s", symbol, exc)
            return

        risk_logger.info(
            "Taille de position calculée | %s | volume=%.2f",
            symbol,
            volume,
        )

        # ------------------------------------------------------------------
        # Étape e : Exécution de l'ordre
        # ------------------------------------------------------------------
        exec_logger = get_logger(LogCategory.EXECUTION)
        try:
            trade = await self._executor.open_order(signal, volume)
        except Exception as exc:
            exec_logger.error("Erreur ouverture ordre | %s | %s", symbol, exc)
            return

        exec_logger.info(
            "Ordre ouvert | %s | %s | volume=%s | ticket=%s",
            trade.symbol,
            trade.direction.value,
            trade.volume,
            trade.ticket,
        )

        # ------------------------------------------------------------------
        # Suivi : enregistrer le trade auprès du risk manager
        # ------------------------------------------------------------------
        # register_trade n'est pas dans l'interface IRiskManager mais est
        # disponible sur la classe concrète RiskManager. On l'appelle si
        # possible pour que can_open_trade et validate_signal restent cohérents.
        if isinstance(self._risk_manager, RiskManager):
            self._risk_manager.register_trade(trade)

    # -------------------------------------------------------------------------
    # Boucle principale
    # -------------------------------------------------------------------------

    async def run_forever(self) -> None:
        """
        Boucle principale du moteur.

        Poll toutes les ``poll_interval`` secondes et déclenche l'analyse sur
        chaque symbole configuré. L'analyse n'est effective que sur une nouvelle
        bougie (voir ``analyze_symbol``).

        La boucle s'arrête proprement si :
        - ``stop()`` est appelée (``_running`` mis à False)
        - La tâche est annulée (``asyncio.CancelledError``)
        """
        self._running = True
        logger.info(
            "TradingEngine démarré | symboles=%s | timeframe=%s | poll=%.1fs",
            self._symbols,
            self._timeframe.value,
            self._poll_interval,
        )

        try:
            while self._running:
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
        return {
            "running": self._running,
            "symbols": list(self._symbols),
            "timeframe": self._timeframe.value,
            "poll_interval": self._poll_interval,
            "candle_count": self._candle_count,
            "last_candle_time": {
                symbol: time.isoformat() if time else None
                for symbol, time in self._last_candle_time.items()
            },
            "last_signal": dict(self._last_signal),
        }

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
