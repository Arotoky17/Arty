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
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.execution.executor import MT5PositionNotFoundError
from arty_trading.modules.risk import RiskManager
from arty_trading.utils.helpers import round_price

logger = get_logger(LogCategory.POSITION)

# Marqueurs d'erreur indiquant que la position n'existe plus côté broker
# (fermée par TP, SL ou manuellement). Ce n'est PAS une erreur transitoire :
# le ticket doit être réconcilié puis retiré du suivi, jamais retenté.
_POSITION_NOT_FOUND_MARKERS = ("introuvable", "not found", "position closed", "unknown position")

# Type des callbacks de notification (bound sur le TradingEngine).
Notifier = Callable[[str, str], Awaitable[None]]
# Provider du snapshot de structure confirmé (mis à jour par bougie).
StructureProvider = Callable[[str], Awaitable[Any]]


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
        structure_provider: StructureProvider | None = None,
        state_store: Any | None = None,
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
            structure_provider: Fournit le ``StructureContext`` confirmé du
                symbole (calculé par bougie, jamais sur tick — anti look-ahead).
            state_store: Persistance durable de l'état de gestion (reprise).
        """
        self._position_manager = position_manager
        self._market_data = market_data
        self._executor = executor
        self._risk_manager = risk_manager
        self._statistics = statistics
        self._journal = journal
        self._notify_critical = notify_critical
        self._notify_trade_closed = notify_trade_closed
        self._structure_provider = structure_provider
        self._state_store = state_store
        self._symbol_info_cache: dict[str, dict[str, Any] | None] = {}
        # Tickets déjà réconciliés (fermeture externe détectée) : garantit un
        # seul log [POSITION_RECONCILED] par ticket et aucun nouvel ajustement.
        self._reconciled_tickets: set[Any] = set()

    # ------------------------------------------------------------------
    # Validation MT5 d'un SL candidat
    # ------------------------------------------------------------------

    async def _validated_sl(self, trade: Trade, candidate: Decimal, price: Decimal) -> Decimal | None:
        """Normalise et valide le SL candidat (digits, stop/freeze level MT5).

        Retourne le SL validé, ou ``None`` si le courtier le refuserait
        (trop proche du prix courant) — l'action est alors rejetée.
        """
        symbol = trade.symbol
        normalized = round_price(candidate, symbol)
        info = await self._symbol_info(symbol)
        if info is None:
            return normalized  # infos indisponibles : pas de validation possible
        point = Decimal(str(info.get("point", 0)))
        digits = int(info.get("digits", 0) or 0)
        if digits > 0:
            normalized = Decimal(str(candidate)).quantize(Decimal(1).scaleb(-digits))
        stops_level = int(info.get("trade_stops_level", 0) or 0)
        freeze_level = int(info.get("trade_freeze_level", 0) or 0) or stops_level
        min_distance = Decimal(max(stops_level, freeze_level)) * point
        if min_distance > 0:
            gap = (
                price - normalized if trade.direction == Direction.BUY else normalized - price
            )
            if gap < min_distance:
                logger.warning(
                    "[SL UPDATE REJECTED] %s | ticket=%s | candidate_sl=%s | price=%s | min_distance=%s | reason=TOO_CLOSE_TO_MARKET",
                    symbol, trade.ticket, normalized, price, min_distance,
                )
                return None
        return normalized

    async def _symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """Retourne les infos du symbole (cache mémoire, tolérant aux erreurs)."""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]
        get_info = getattr(self._market_data, "get_symbol_info", None)
        info: dict[str, Any] | None = None
        if get_info is not None:
            try:
                info = await get_info(symbol)
            except Exception as exc:  # noqa: BLE001 - validation optionnelle
                logger.debug("symbol_info indisponible | %s | %s", symbol, exc)
        self._symbol_info_cache[symbol] = info
        return info

    def _persist(self, trade: Trade) -> None:
        """Sauvegarde l'état de gestion persisté (reprise après redémarrage)."""
        if self._state_store is None:
            return
        snapshot = self._position_manager.snapshot(trade)
        if snapshot is not None:
            self._state_store.save(trade.ticket if trade.ticket else str(trade.id), snapshot)

    def _forget_state(self, trade: Trade) -> None:
        """Supprime l'état persisté d'une position clôturée."""
        if self._state_store is not None:
            self._state_store.delete(trade.ticket if trade.ticket else str(trade.id))

    def _confirm_action(self, trade: Trade, action: Any) -> None:
        """Confirme la consommation d'un niveau après exécution réussie."""
        confirm = getattr(self._position_manager, "confirm", None)
        if confirm is not None:
            confirm(trade, action)

    @staticmethod
    def _log_sl_update(trade: Trade, sl: Decimal, price: Decimal, reason: str) -> None:
        """Log taggé selon l'origine du déplacement de SL."""
        tag = "[STRUCTURE TRAILING]" if reason.startswith("structure_trailing") else "[PROFIT LOCK]"
        logger.info(
            "%s %s | ticket=%s | direction=%s | new_sl=%s | price=%s | reason=%s",
            tag, trade.symbol, trade.ticket, trade.direction.value, sl, price, reason,
        )

    # ------------------------------------------------------------------
    # Réconciliation des positions fermées côté broker (TP / SL / manuel)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_position_not_found(exc: Exception) -> bool:
        """Distingue une fermeture externe d'une erreur transitoire broker."""
        if isinstance(exc, MT5PositionNotFoundError):
            return True
        msg = str(exc).lower()
        return any(marker in msg for marker in _POSITION_NOT_FOUND_MARKERS)

    def _broker_position_exists(self, trade: Trade) -> bool:
        """Vérifie explicitement l'état réel du ticket côté broker.

        Retourne ``True`` uniquement si le broker confirme que la position
        n'existe **plus**. Tout état indéterminable (executor sans la méthode,
        MT5 indisponible, mock) retourne ``False`` : on ne retire jamais une
        position suivie sans confirmation explicite du broker.
        """
        exists_fn = getattr(self._executor, "position_exists", None)
        if exists_fn is None:
            return False
        try:
            result = exists_fn(trade.ticket if trade.ticket else trade.id)
        except Exception:  # noqa: BLE001 - état indéterminable
            return False
        # Seule une réponse explicitement False confirme la disparition
        # (True / None / mock truthy => état inconnu => pas de retrait).
        return result is False

    @staticmethod
    def _closed_via(trade: Trade, price: Decimal | None) -> str:
        """Détermine la cause probable de la fermeture (TP / SL / MANUAL)."""
        if price is None:
            return "UNKNOWN"
        try:
            if trade.take_profit is not None:
                tp_hit = (
                    price >= trade.take_profit
                    if trade.direction == Direction.BUY
                    else price <= trade.take_profit
                )
                if tp_hit:
                    return "TP"
            if trade.stop_loss is not None:
                sl_hit = (
                    price <= trade.stop_loss
                    if trade.direction == Direction.BUY
                    else price >= trade.stop_loss
                )
                if sl_hit:
                    return "SL"
        except Exception:  # noqa: BLE001 - heuristique best-effort
            return "UNKNOWN"
        return "MANUAL"

    async def _reconcile_missing_position(
        self,
        trade: Trade,
        key: str,
        managed_trades: dict[str, Trade],
        price: Decimal | None = None,
    ) -> bool:
        """Réconcilie une position suspectée disparue côté broker.

        Vérifie l'état réel du ticket (``executor.position_exists``) ; si la
        position n'existe plus : retrait immédiat de tout le suivi interne
        (profit lock, partial profit, structure trailing), libération du
        risque, et un **unique** log ``[POSITION_RECONCILED]``.

        Returns:
            ``True`` si la position a été réconciliée et retirée du suivi.
        """
        if not self._broker_position_exists(trade):
            return False  # état indéterminable ou position toujours ouverte
        ticket = trade.ticket if trade.ticket else str(trade.id)
        already = ticket in self._reconciled_tickets
        self._reconciled_tickets.add(ticket)
        if not already:
            logger.warning(
                "[POSITION_RECONCILED] symbol=%s | ticket=%s | "
                "reason=NOT_FOUND_ON_BROKER | closed_via=%s | action=REMOVED_FROM_TRACKING",
                trade.symbol, ticket, self._closed_via(trade, price),
            )
        self._position_manager.forget(trade)
        self._forget_state(trade)
        managed_trades.pop(key, None)
        if isinstance(self._risk_manager, RiskManager):
            self._risk_manager.close_trade(trade, Decimal("0"))
        return True

    async def _reconcile_tracked_positions(self, managed_trades: dict[str, Trade]) -> None:
        """Filet de sécurité : réconciliation périodique avec le broker.

        Comparé à chaque cycle : tout ticket suivi en interne mais absent
        côté broker est retiré du tracking (fermeture externe silencieuse,
        ex. TP touché pendant un cycle sans tentative d'ajustement).
        Un seul log par ticket (dédubloublage via ``_reconciled_tickets``).
        """
        if not managed_trades:
            return
        if getattr(self._executor, "position_exists", None) is None:
            return  # executor sans vérification broker (paper/mock) : rien à faire
        for key, trade in list(managed_trades.items()):
            await self._reconcile_missing_position(trade, key, managed_trades)

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
        # Réconciliation périodique (filet de sécurité) : retire du suivi tout
        # ticket fermé côté broker sans passer par une tentative d'ajustement.
        await self._reconcile_tracked_positions(managed_trades)
        for key, trade in list(managed_trades.items()):
            try:
                tick = await get_tick(trade.symbol)
                if not tick:
                    continue
                raw_price = tick["bid"] if trade.direction.value == "buy" else tick["ask"]
                price = Decimal(str(raw_price))
                structure = None
                if self._structure_provider is not None:
                    try:
                        structure = await self._structure_provider(trade.symbol)
                    except Exception as exc:  # noqa: BLE001 - structure optionnelle
                        logger.debug("Structure indisponible | %s | %s", trade.symbol, exc)
                actions = self._position_manager.evaluate(trade, price, structure)
                for action in actions:
                    if action.kind == "modify" and action.stop_loss is not None:
                        validated = await self._validated_sl(trade, action.stop_loss, price)
                        if validated is None:
                            continue
                        try:
                            await self._executor.modify_order(trade, stop_loss=float(validated))
                        except Exception as exc:  # noqa: BLE001 - échec broker
                            if self._is_position_not_found(exc):
                                # Position probablement fermée côté broker
                                # (TP/SL/manuel) : réconciliation immédiate,
                                # pas de retry au cycle suivant.
                                if await self._reconcile_missing_position(
                                    trade, key, managed_trades, price
                                ):
                                    break
                            logger.error(
                                "[PROFIT LOCK FAILED] %s | ticket=%s | level=%s | "
                                "candidate_sl=%s | reason=%s | error=%s",
                                trade.symbol, trade.ticket, action.reason,
                                validated, action.reason, exc,
                            )
                            continue  # niveau NON consommé : re-proposé au tick suivant
                        # Succes confirmé : consommation du niveau + persistance.
                        self._confirm_action(trade, action)
                        self._log_sl_update(trade, validated, price, action.reason)
                        if action.lock_level is not None:
                            logger.info(
                                "[PROFIT LOCK CONFIRMED] %s | ticket=%s | level=%s | sl=%s",
                                trade.symbol, trade.ticket, action.reason, validated,
                            )
                        self._persist(trade)
                    elif action.kind == "close":
                        try:
                            closed = await self._executor.close_order(trade)
                        except Exception as exc:  # noqa: BLE001 - géré localement comme avant
                            if self._is_position_not_found(exc):
                                # Position fermée côté broker (TP/SL/manuel) :
                                # réconciliation immédiate (log unique + retrait).
                                if await self._reconcile_missing_position(
                                    trade, key, managed_trades, price
                                ):
                                    continue
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
                        self._forget_state(trade)
                        managed_trades.pop(key, None)
                        await self._notify_trade_closed(closed, action.reason)
                    elif action.kind == "partial_close":
                        partial_close = getattr(self._executor, "close_partial_order", None)
                        if partial_close is None or action.close_fraction is None:
                            logger.warning("TP partiel non supporté | ticket=%s", trade.ticket)
                            continue
                        try:
                            closed = await partial_close(trade, float(action.close_fraction))
                        except Exception as exc:  # noqa: BLE001 - échec broker
                            if self._is_position_not_found(exc):
                                # Position probablement fermée côté broker
                                # (TP/SL/manuel) : réconciliation immédiate,
                                # pas de retry au cycle suivant.
                                if await self._reconcile_missing_position(
                                    trade, key, managed_trades, price
                                ):
                                    break
                            logger.error(
                                "[PARTIAL PROFIT FAILED] %s | ticket=%s | level=%s | "
                                "requested_volume=%s | reason=%s | error=%s",
                                trade.symbol, trade.ticket, action.reason,
                                action.close_fraction, action.reason, exc,
                            )
                            continue  # niveau NON consommé : re-proposé au tick suivant
                        if closed is None:
                            # Volume invalide (min/step broker) : partial ignoré,
                            # niveau NON consommé (voir executor : SKIPPED loggé).
                            continue
                        # Succès confirmé : consommation du niveau + persistance.
                        self._confirm_action(trade, action)
                        logger.info(
                            "[PARTIAL PROFIT CONFIRMED] %s | ticket=%s | level=%s | "
                            "closed_volume=%s | remaining_volume=%s",
                            trade.symbol, trade.ticket, action.reason,
                            closed.volume, trade.volume,
                        )
                        self._statistics.record_trade_closed(closed)
                        if self._journal is not None:
                            self._journal.record(closed, action.reason)
                        self._persist(trade)
                # Persistance immédiate des transitions d'état sans ordre associé
                # (ex: engagement du runner).
                if self._position_manager.take_persist_flag(trade):
                    self._persist(trade)
            except Exception as exc:
                logger.error("Erreur suivi position | ticket=%s | %s", trade.ticket, exc)