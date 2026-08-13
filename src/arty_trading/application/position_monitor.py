"""
Surveillance des positions ouvertes (Position Monitor).

Extrait de ``application/trading_engine._monitor_open_positions`` sans changer
le comportement. Applique les règles actives de suivi de position (break-even,
TP partiel, trailing) produites par le ``PositionManager`` :

- **modify** : déplace le stop-loss (``executor.modify_order``).
- **close** : ferme la position et enregistre le résultat
  (statistiques, journal, risque, notifications).
- **partial_close** : clôture partielle si ``close_partial_order`` est supporté.

Ne prend aucune décision de trading : applique les actions du ``PositionManager``
au marché via l'exécuteur et met à jour les états partagés avec le
``TradingEngine`` (``managed_trades`` — référence partagée —, ``RiskManager``,
``PositionManager``, statistiques, journal).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Awaitable, Callable

from arty_trading.core.entities import Trade
from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.risk import RiskManager

logger = get_logger(LogCategory.POSITION)

# Type des callbacks de notification (bound sur le TradingEngine).
Notifier = Callable[[str, str], Awaitable[None]]


class PositionMonitor:
    """Applique les règles actives de suivi aux positions ouvertes."""

    def __init__(
        self,
        position_manager: Any,
        market_data: Any,
        executor: Any,
        risk_manager: Any,
        statistics: Any,
        journal: Any | None,
        notify_critical: Notifier,
        notify_trade_closed: Callable[[Trade, str], Awaitable[None]],
    ) -> None:
        """Injecte les collaborateurs nécessaires au suivi des positions.

        Args:
            position_manager: ``PositionManager`` (ou ``None`` si désactivé).
            market_data: Provider de données (fournit ``get_tick``).
            executor: Exécuteur d'ordres (modify/close/close_partial).
            risk_manager: Gestionnaire de risque.
            statistics: Tracker de statistiques.
            journal: Journal de trading (ou ``None``).
            notify_critical: Envoie une alerte critique (bound au moteur).
            notify_trade_closed: Notifie la fermeture d'un trade (bound au moteur).
        """
        self._position_manager = position_manager
        self._market_data = market_data
        self._executor = executor
        self._risk_manager = risk_manager
        self._statistics = statistics
        self._journal = journal
        self._notify_critical = notify_critical
        self._notify_trade_closed = notify_trade_closed

    async def monitor(self, managed_trades: dict[str, Trade]) -> None:
        """Applique les règles de position aux ticks disponibles du provider.

        Args:
            managed_trades: Dictionnaire partagé (référence) des trades suivis.
        """
        if self._position_manager is None:
            return
        get_tick = getattr(self._market_data, "get_tick", None)
        if get_tick is None:
            logger.warning("Gestion de position inactive : provider sans get_tick")
            return
        for key, trade in list(managed_trades.items()):
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
                        except Exception as exc:  # noqa: BLE001 - géré localement comme avant
                            error_msg = str(exc)
                            if "introuvable" in error_msg:
                                logger.warning(
                                    "Position fantôme supprimée du suivi | ticket=%s | %s",
                                    trade.ticket, exc,
                                )
                                self._position_manager.forget(trade)
                                managed_trades.pop(key, None)
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
                        managed_trades.pop(key, None)
                        await self._notify_trade_closed(closed, action.reason)
                    elif action.kind == "partial_close":
                        partial_close = getattr(self._executor, "close_partial_order", None)
                        if partial_close is None or action.close_fraction is None:
                            logger.warning("TP partiel non supporté | ticket=%s", trade.ticket)
                            continue
                        try:
                            closed = await partial_close(trade, float(action.close_fraction))
                        except Exception as exc:  # noqa: BLE001 - géré localement comme avant
                            logger.error("Échec TP partiel | %s | %s", trade.ticket, exc)
                            continue
                        if closed is not None:
                            self._statistics.record_trade_closed(closed)
                            if self._journal is not None:
                                self._journal.record(closed, action.reason)
            except Exception as exc:
                logger.error("Erreur suivi position | ticket=%s | %s", trade.ticket, exc)