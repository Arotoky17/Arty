"""
Orchestration risque + exécution (Trade Orchestrator).

Extrait de ``application/trading_engine`` (``_calculate_risk`` / ``_execute_trade``)
sans changer le comportement. Cette classe est un simple coordinateur sans état
propre : elle orchestre le ``RiskManager`` et l'exécuteur d'ordres sur ordre du
``TradingEngine``.

1. ``calculate_risk`` : récupère le compte, applique le ``RiskManager``
   (can_open_trade / validate_signal / calculate_position_size) et retourne le
   volume, ou ``None`` si le risque est refusé.
2. ``execute_trade`` : ouvre l'ordre via l'exécuteur, consomme le setup SMC lié
   si présent, et notifie l'ouverture.

Les callbacks de notification et le compteur de positions proviennent du
``TradingEngine`` (liés, pour préserver exactement le comportement).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import LogCategory, TradingMode
from arty_trading.logging.logger import get_logger
from arty_trading.modules.execution import MT5OrderError

logger = get_logger(LogCategory.SYSTEM)

OverlayNotifier = Callable[[str, str], Awaitable[None]]
TradeNotifier = Callable[[Trade], Awaitable[None]]


class TradeOrchestrator:
    """Coordonne le calcul du risque et l'exécution d'un ordre."""

    def __init__(
        self,
        mt5_connector: Any,
        risk_manager: Any,
        executor: Any,
        setup_tracker: Any,
        trading_mode: TradingMode,
        notify_critical: OverlayNotifier,
        notify_trade_opened: TradeNotifier,
        get_open_positions_count: Callable[[], int],
    ) -> None:
        """Injecte les collaborateurs et callbacks (vus via le TradingEngine).

        Args:
            mt5_connector: Connecteur MT5 (infos du compte).
            risk_manager: Gestionnaire de risque.
            executor: Exécuteur d'ordres.
            setup_tracker: State machine des setups SMC.
            trading_mode: Mode de trading courant (DEMO garde-fou).
            notify_critical: Alerte critique (bound au moteur).
            notify_trade_opened: Notifie l'ouverture d'un trade (bound au moteur).
            get_open_positions_count: Compteur de positions ouvertes (bound au moteur).
        """
        self._mt5_connector = mt5_connector
        self._risk_manager = risk_manager
        self._executor = executor
        self._setup_tracker = setup_tracker
        self._trading_mode = trading_mode
        self._notify_critical = notify_critical
        self._notify_trade_opened = notify_trade_opened
        self._get_open_positions_count = get_open_positions_count

    async def calculate_risk(self, symbol: str, signal: Signal) -> float | None:
        """Étape 5 — Calcul du risque et de la taille de position.

        Sous-étapes :
        a. Récupération des infos du compte (``get_account_info``)
        b. ``can_open_trade`` — vérifie si un nouveau trade peut être ouvert
        c. ``validate_signal`` — valide si le signal respecte les règles
        d. ``calculate_position_size`` — calcule la taille optimale

        Args:
            symbol: Symbole à analyser.
            signal: Signal validé par le générateur.

        Returns:
            La taille de position (volume), ou ``None`` si le risque est refusé.
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

    async def execute_trade(self, symbol: str, signal: Signal, volume: float) -> Trade | None:
        """Étape 6 — Trade : exécution de l'ordre.

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
        if signal.setup_id:
            setup = self._setup_tracker.get_setup_by_id(signal.setup_id)
            if setup is not None:
                self._setup_tracker.mark_consumed(setup, reason="trade_executed")
                logger.info("Setup consommé | %s | %s", signal.setup_id, trade.ticket)
        # Alerte Telegram : nouveau trade ouvert.
        await self._notify_trade_opened(trade)
        return trade