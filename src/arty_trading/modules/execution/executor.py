"""
Exécuteur d'ordres — implémente IOrderExecutor.

Gère l'ouverture, fermeture et modification d'ordres via MT5.
Inclut le trailing stop et le break-even automatique.
Le mode LIVE est désactivé par défaut pour la sécurité.
"""

from __future__ import annotations

import asyncio
from decimal import ROUND_DOWN, Decimal
from math import isfinite
from typing import Any

from arty_trading.config.settings import get_settings
from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import Direction, LogCategory, TradingMode
from arty_trading.core.interfaces import IOrderExecutor
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.EXECUTION)

try:
    import MetaTrader5 as mt5  # noqa: N813 - broker module convention

    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

# Erreurs MT5 transitoires -> retry automatique.
_TRANSIENT_RETCODES: set[int] = set()
if MT5_AVAILABLE and mt5 is not None:
    for _name in (
        "TRADE_RETCODE_REQUOTE",
        "TRADE_RETCODE_PRICE_CHANGED",
        "TRADE_RETCODE_TIMEOUT",
        "TRADE_RETCODE_REJECT",
    ):
        _code = getattr(mt5, _name, None)
        if _code is not None:
            _TRANSIENT_RETCODES.add(_code)


class MT5OrderError(Exception):
    """
    Erreur d'exécution d'ordre MT5.

    Levée lorsqu'un ordre réel (démo ou live) ne peut pas être exécuté
    (requote, marché fermé, fonds insuffisants, etc.). Contrairement au
    mode mock, aucune simulation silencieuse ne doit masquer l'échec d'un
    ordre réel : l'erreur doit remonter jusqu'au moteur pour être
    journalisée et notifiée.
    """


class MT5PositionNotFoundError(MT5OrderError):
    """
    La position n'existe plus côté broker (fermée par TP, SL ou manuellement).

    Erreur **non transitoire** : réessayer au cycle suivant n'a aucun sens.
    Le Position Monitor doit la traiter comme un signal de fermeture externe
    et réconcilier son état interne (voir PositionMonitor).
    """


class OrderExecutor(IOrderExecutor):
    """
    Exécuteur d'ordres MT5.

    Fonctionnalités :
    - Ouverture d'ordres market (BUY/SELL)
    - Fermeture d'ordres
    - Modification SL/TP
    - Trailing stop automatique
    - Break-even automatique
    - Mode mock quand MT5 indisponible
    - Mode Réel désactivé par défaut
    """

    def __init__(
        self,
        trailing_stop_pips: float = 0,
        break_even_pips: float = 0,
        mock_mode: bool | None = None,
    ) -> None:
        """
        Args:
            trailing_stop_pips: Distance du trailing stop en pips (0 = désactivé)
            break_even_pips: Profit en pips pour activer le break-even (0 = désactivé)
            mock_mode: Force le mode mock (défaut: auto si MT5 indisponible)
        """
        self._settings = get_settings()
        self._trailing_stop_pips = trailing_stop_pips
        self._break_even_pips = break_even_pips
        self._mock_mode = mock_mode if mock_mode is not None else not MT5_AVAILABLE
        self._mock_ticket = 10000
        self._mock_trades: dict[int, Trade] = {}
        # Paramètres d'exécution MT5 (démo/live).
        self._max_deviation: int = 20
        self._magic_number: int = 123456
        self._order_send_timeout: float | None = None
        # Métriques d'exécution pour le monitoring.
        self._execution_metrics: dict[str, Any] = {
            "total_attempts": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "last_error": None,
        }

    @property
    def is_mock_mode(self) -> bool:
        return self._mock_mode

    @property
    def is_live_trading_enabled(self) -> bool:
        """Vérifie si le trading réel est autorisé."""
        return self._settings.is_live_trading_enabled

    @property
    def execution_metrics(self) -> dict[str, Any]:
        """Métriques d'exécution pour le monitoring."""
        return dict(self._execution_metrics)

    @property
    def order_send_timeout(self) -> float | None:
        """Maximum wait in seconds for an order response; None disables it.

        A timeout stops waiting, but cannot cancel the broker operation in
        the worker thread. It is propagated without an automatic order retry.
        """
        return self._order_send_timeout

    @order_send_timeout.setter
    def order_send_timeout(self, timeout: float | None) -> None:
        if timeout is not None and (not isfinite(timeout) or timeout <= 0):
            raise ValueError("order_send_timeout must be finite and positive, or None")
        self._order_send_timeout = timeout

    # =========================================================================
    # IOrderExecutor
    # =========================================================================

    async def open_order(self, signal: Signal, volume: float) -> Trade:
        """
        Ouvre un ordre market basé sur un signal.

        Args:
            signal: Signal d'entrée (entry, SL, TP, direction)
            volume: Taille de position en lots

        Returns:
            Trade: Le trade créé avec son ticket MT5
        """
        # Vérification du mode live
        if self._settings.trading_mode == TradingMode.LIVE and not self.is_live_trading_enabled:
            logger.warning("Trading LIVE bloqué - passage en mode mock")
            self._mock_mode = True

        if self._mock_mode or not MT5_AVAILABLE:
            return self._mock_open_order(signal, volume)

        # Exécution MT5 réelle avec retry
        return await self._mt5_open_order_with_retry(signal, volume)

    async def close_order(self, trade: Trade) -> Trade:
        """
        Ferme un ordre ouvert.

        Args:
            trade: Le trade à fermer

        Returns:
            Trade: Le trade mis à jour (fermé)
        """
        if self._mock_mode or not MT5_AVAILABLE:
            return self._mock_close_order(trade)

        return await self._mt5_close_order_with_retry(trade)

    async def modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """
        Modifie le SL/TP d'un ordre existant.

        Args:
            trade: Le trade à modifier
            stop_loss: Nouveau SL (None = inchangé)
            take_profit: Nouveau TP (None = inchangé)

        Returns:
            Trade: Le trade modifié
        """
        if self._mock_mode or not MT5_AVAILABLE:
            return self._mock_modify_order(trade, stop_loss, take_profit)

        return await self._mt5_modify_order_with_retry(trade, stop_loss, take_profit)

    async def close_partial_order(self, trade: Trade, fraction: float) -> Trade | None:
        """Clôture une fraction d'une position MT5 et conserve le reliquat ouvert.

        Le volume est normalisé selon ``volume_min`` / ``volume_step`` du
        broker : si le volume partiel est inférieur au minimum, l'ordre
        n'est PAS envoyé (aucun ``INVALID_VOLUME`` inutile) et ``None`` est
        retourné — le niveau de partial reste alors non consommé côté
        ``PositionManager``.
        """
        if not 0 < fraction < 1:
            raise ValueError("La fraction de clôture doit être entre 0 et 1")
        requested_volume = trade.volume * Decimal(str(fraction))
        closed_volume, skip_reason = await asyncio.to_thread(
            self._normalize_partial_volume, trade, requested_volume
        )
        if skip_reason is not None:
            volume_min, volume_step = await asyncio.to_thread(
                self._partial_volume_constraints, trade.symbol
            )
            logger.warning(
                "[PARTIAL PROFIT SKIPPED] %s | ticket=%s | position_volume=%s | "
                "requested_volume=%s | volume_min=%s | volume_step=%s | reason=%s",
                trade.symbol, trade.ticket, trade.volume, requested_volume,
                volume_min, volume_step, skip_reason,
            )
            return None
        if closed_volume <= 0 or closed_volume >= trade.volume:
            return await self.close_order(trade)
        partial = trade.model_copy(update={"volume": closed_volume})
        closed = await self.close_order(partial)
        trade.volume -= closed_volume
        logger.info("TP partiel | ticket=%s | volume=%s", trade.ticket, closed_volume)
        return closed

    def _partial_volume_constraints(self, symbol: str) -> tuple[Decimal | None, Decimal | None]:
        """Retourne ``(volume_min, volume_step)`` du symbole, ou ``(None, None)``."""
        if MT5_AVAILABLE and not self._mock_mode:
            try:
                info = mt5.symbol_info(symbol)
                if info is not None:
                    return Decimal(str(info.volume_min)), Decimal(str(info.volume_step))
            except Exception:  # noqa: BLE001 - contraintes optionnelles
                pass
        return None, None

    def _normalize_partial_volume(
        self, trade: Trade, requested: Decimal
    ) -> tuple[Decimal, str | None]:
        """Normalise le volume partiel selon les contraintes du broker.

        Retourne ``(volume, None)`` si exécutable, ou ``(requested, raison)``
        si le volume est invalide. Ne ferme jamais plus que le volume de la
        position ; si le reliquat devient inférieur au volume minimum, toute
        la position est fermée (seule alternative acceptée par le broker).
        """
        volume_min, volume_step = self._partial_volume_constraints(trade.symbol)
        if volume_min is None:
            return requested, None  # contraintes inconnues (mock/paper) : inchangé
        step = volume_step if volume_step and volume_step > 0 else Decimal("0.01")
        # Arrondi vers le bas au step : jamais fermer plus que demandé.
        normalized = (requested / step).to_integral_value(rounding=ROUND_DOWN) * step
        if normalized < volume_min:
            return requested, "INVALID_PARTIAL_VOLUME"
        remaining = trade.volume - normalized
        if remaining > 0 and remaining < volume_min:
            # Reliququat non conforme : fermeture totale de la position.
            logger.info(
                "Partial → fermeture totale | ticket=%s | remaining=%s < volume_min=%s",
                trade.ticket, remaining, volume_min,
            )
            return trade.volume, None
        return normalized, None

    async def get_open_positions(self) -> list[Trade]:
        """Retourne la liste des positions actuellement ouvertes sur le compte.

        En mode mock ou si MT5 est indisponible, retourne les trades simulés.
        Sinon, interroge ``mt5.positions_get()`` et convertit chaque position
        en entité ``Trade``. Utilisée au démarrage pour réconcilier les
        positions existantes avant de passer en mode PAPER/LIVE.

        Returns:
            Liste des positions ouvertes (vide si aucune / erreur).
        """
        if self._mock_mode or not MT5_AVAILABLE:
            return list(self._mock_trades.values())

        if not await asyncio.to_thread(mt5.initialize):
            logger.warning("MT5 initialize() a échoué - réconciliation vide")
            return []

        raw_positions = await asyncio.to_thread(mt5.positions_get)
        if raw_positions is None:
            return []

        trades: list[Trade] = []
        for pos in raw_positions:
            try:
                trades.append(self._position_to_trade(pos))
            except Exception as exc:  # noqa: BLE001 - une position invalide ne doit pas bloquer
                logger.warning("Position ignorée | ticket=%s | %s", pos.ticket, exc)
        return trades

    def position_exists(self, ticket: int) -> bool | None:
        """Vérifie l'existence réelle d'une position côté broker.

        Utilisé par le Position Monitor pour réconcilier un ticket suivi en
        interne après une erreur "position introuvable" (fermeture externe).

        Returns:
            ``True`` si la position existe, ``False`` si elle n'existe plus,
            ou ``None`` si l'état ne peut pas être déterminé (MT5 indisponible,
            mode mock) — dans ce cas aucune réconciliation ne doit avoir lieu.
        """
        if self._mock_mode or not MT5_AVAILABLE:
            return None
        try:
            if not mt5.initialize():
                return None
            positions = mt5.positions_get(ticket=ticket)
            return bool(positions)
        except Exception as exc:  # noqa: BLE001 - état indéterminable
            logger.debug("position_exists indisponible | ticket=%s | %s", ticket, exc)
            return None

    def _position_to_trade(self, pos: Any) -> Trade:
        """Convertit une position MT5 brute en entité ``Trade``."""
        direction = Direction.BUY if pos.type == mt5.POSITION_TYPE_BUY else Direction.SELL
        return Trade(
            symbol=pos.symbol,
            direction=direction,
            entry_price=Decimal(str(pos.price_open)),
            stop_loss=Decimal(str(pos.sl)),
            take_profit=Decimal(str(pos.tp)),
            volume=Decimal(str(pos.volume)),
            ticket=int(pos.ticket),
            profit=Decimal(str(pos.profit)),
            is_open=True,
        )

    # =========================================================================
    # Trailing Stop & Break-Even
    # =========================================================================

    async def apply_trailing_stop(self, trade: Trade, current_price: float) -> Trade | None:
        """
        Applique le trailing stop si activé.

        Args:
            trade: Le trade à modifier
            current_price: Prix actuel du marché

        Returns:
            Trade modifié ou None si pas de modification
        """
        if self._trailing_stop_pips <= 0:
            return None

        pip_size = 0.01 if "JPY" in trade.symbol else 0.0001
        trail_distance = self._trailing_stop_pips * pip_size

        if trade.direction == Direction.BUY:
            new_sl = current_price - trail_distance
            # Ne déplacer le SL que vers le haut
            if new_sl > float(trade.stop_loss):
                return await self.modify_order(trade, stop_loss=new_sl)
        else:
            new_sl = current_price + trail_distance
            # Ne déplacer le SL que vers le bas
            if new_sl < float(trade.stop_loss):
                return await self.modify_order(trade, stop_loss=new_sl)

        return None

    async def apply_break_even(self, trade: Trade, current_price: float) -> Trade | None:
        """
        Déplace le SL à l'entrée si le seuil de profit est atteint.

        Args:
            trade: Le trade à modifier
            current_price: Prix actuel du marché

        Returns:
            Trade modifié ou None si pas de modification
        """
        if self._break_even_pips <= 0:
            return None

        pip_size = 0.01 if "JPY" in trade.symbol else 0.0001
        be_threshold = self._break_even_pips * pip_size
        entry = float(trade.entry_price)

        if trade.direction == Direction.BUY:
            # Si le prix a monté de plus que le seuil
            if current_price >= entry + be_threshold:
                # Ne déplacer le SL que s'il est encore sous l'entrée
                if float(trade.stop_loss) < entry:
                    return await self.modify_order(trade, stop_loss=entry)
        else:
            # Si le prix a baissé de plus que le seuil
            if current_price <= entry - be_threshold:
                if float(trade.stop_loss) > entry:
                    return await self.modify_order(trade, stop_loss=entry)

        return None

    # =========================================================================
    # Mock Implementation (quand MT5 indisponible)
    # =========================================================================

    def _mock_open_order(self, signal: Signal, volume: float) -> Trade:
        """Ouvre un ordre en mode mock."""
        self._mock_ticket += 1
        trade = Trade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=Decimal(str(volume)),
            signal_id=signal.id,
            strategy_name=signal.strategy_name,
            ticket=self._mock_ticket,
        )
        self._mock_trades[self._mock_ticket] = trade
        logger.info(
            "Mock order ouvert | %s | %s | volume=%s | ticket=%d",
            trade.symbol, trade.direction.value, trade.volume, trade.ticket,
        )
        return trade

    def _mock_close_order(self, trade: Trade) -> Trade:
        """Ferme un ordre en mode mock."""
        # Simuler un profit aléatoire (ici on prend juste le prix d'entrée)
        trade.close_price = trade.entry_price
        trade.profit = Decimal("0")
        trade.closed_at = None  # Sera mis par l'entity
        if trade.ticket in self._mock_trades:
            del self._mock_trades[trade.ticket]
        logger.info(
            "Mock order fermé | %s | ticket=%d",
            trade.symbol, trade.ticket,
        )
        return trade

    def _mock_modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """Modifie un ordre en mode mock."""
        if stop_loss is not None:
            trade.stop_loss = Decimal(str(stop_loss))
        if take_profit is not None:
            trade.take_profit = Decimal(str(take_profit))
        logger.info(
            "Mock order modifié | %s | SL=%s | TP=%s",
            trade.symbol, trade.stop_loss, trade.take_profit,
        )
        return trade

    # =========================================================================
    # Retry Helpers
    # =========================================================================

    async def _mt5_open_order_with_retry(
        self, signal: Signal, volume: float, max_retries: int = 3
    ) -> Trade:
        """Ouvre un ordre via MT5 avec retry sur erreurs transitoires."""
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            self._execution_metrics["total_attempts"] += 1
            try:
                trade = await self._mt5_open_order(signal, volume)
                self._execution_metrics["successes"] += 1
                return trade
            except MT5OrderError as exc:
                last_error = exc
                retcode = self._extract_retcode(exc)
                if retcode in _TRANSIENT_RETCODES and attempt < max_retries:
                    self._execution_metrics["retries"] += 1
                    delay = min(2 ** attempt, 10)
                    logger.warning(
                        "Retry ouverture ordre | tentative=%d/%d | retcode=%s | attente %ds",
                        attempt, max_retries, retcode, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._execution_metrics["failures"] += 1
                self._execution_metrics["last_error"] = str(exc)
                raise
        raise last_error  # type: ignore[misc]

    async def _mt5_close_order_with_retry(
        self, trade: Trade, max_retries: int = 3
    ) -> Trade:
        """Ferme un ordre via MT5 avec retry sur erreurs transitoires."""
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            self._execution_metrics["total_attempts"] += 1
            try:
                closed = await self._mt5_close_order(trade)
                self._execution_metrics["successes"] += 1
                return closed
            except MT5OrderError as exc:
                last_error = exc
                retcode = self._extract_retcode(exc)
                if retcode in _TRANSIENT_RETCODES and attempt < max_retries:
                    self._execution_metrics["retries"] += 1
                    delay = min(2 ** attempt, 10)
                    logger.warning(
                        "Retry fermeture ordre | ticket=%d | tentative=%d/%d "
                        "| retcode=%s | attente %ds",
                        trade.ticket,
                        attempt,
                        max_retries,
                        retcode,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._execution_metrics["failures"] += 1
                self._execution_metrics["last_error"] = str(exc)
                raise
        raise last_error  # type: ignore[misc]

    async def _mt5_modify_order_with_retry(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_retries: int = 3,
    ) -> Trade:
        """Modifie un ordre via MT5 avec retry sur erreurs transitoires."""
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            self._execution_metrics["total_attempts"] += 1
            try:
                modified = await self._mt5_modify_order(trade, stop_loss, take_profit)
                self._execution_metrics["successes"] += 1
                return modified
            except MT5OrderError as exc:
                last_error = exc
                retcode = self._extract_retcode(exc)
                if retcode in _TRANSIENT_RETCODES and attempt < max_retries:
                    self._execution_metrics["retries"] += 1
                    delay = min(2 ** attempt, 10)
                    logger.warning(
                        "Retry modification ordre | ticket=%d | tentative=%d/%d "
                        "| retcode=%s | attente %ds",
                        trade.ticket,
                        attempt,
                        max_retries,
                        retcode,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._execution_metrics["failures"] += 1
                self._execution_metrics["last_error"] = str(exc)
                raise
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _extract_retcode(exc: Exception) -> Any:
        """Extrait le retcode depuis un MT5OrderError."""
        message = str(exc)
        if "retcode=" in message:
            try:
                return int(message.split("retcode=")[1].split(" ")[0].split("|")[0].strip())
            except (ValueError, IndexError):
                pass
        return None

    # =========================================================================
    # MT5 Implementation (quand MT5 disponible)
    # =========================================================================

    async def _send_order(self, request: dict[str, Any]) -> Any:
        """Send an MT5 request off the event loop, preserving results and errors."""
        send = asyncio.to_thread(mt5.order_send, request)
        if self._order_send_timeout is None:
            return await send
        return await asyncio.wait_for(send, timeout=self._order_send_timeout)

    async def _mt5_open_order(self, signal: Signal, volume: float) -> Trade:
        """Ouvre un ordre via MT5."""
        # Vérifier que MT5 est initialisé
        if not await asyncio.to_thread(mt5.initialize):
            logger.error("MT5 initialize() a échoué")
            raise MT5OrderError("MT5 non initialisé")

        # Préparer la requête
        if signal.direction == Direction.BUY:
            order_type = mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(signal.entry_price),
            "sl": float(signal.stop_loss),
            "tp": float(signal.take_profit),
            "deviation": self._max_deviation,
            "magic": self._magic_number,
            "comment": signal.strategy_name,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = await self._send_order(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            reason = self._describe_retcode(retcode)
            logger.error(
                "MT5 order_send échec | %s | retcode=%s | %s",
                signal.symbol, retcode, reason,
            )
            raise MT5OrderError(
                f"Échec ouverture ordre {signal.symbol} | retcode={retcode} | {reason}"
            )

        # Créer le trade
        trade = Trade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=Decimal(str(result.price)),
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=Decimal(str(volume)),
            signal_id=signal.id,
            strategy_name=signal.strategy_name,
            ticket=result.order,
        )
        logger.info(
            "MT5 order ouvert | %s | ticket=%d | price=%s",
            trade.symbol, trade.ticket, trade.entry_price,
        )
        return trade

    @staticmethod
    def _describe_retcode(retcode: Any) -> str:
        """
        Traduit un code retour MT5 en message lisible pour les logs.

        Couvre les erreurs fréquentes : requote, marché fermé, volume
        invalide, fonds insuffisants, etc.

        Note : certaines constantes ``TRADE_RETCODE_*`` ne sont pas
        disponibles dans toutes les versions du package ``MetaTrader5``.
        On utilise donc ``getattr`` avec un repli sûr pour ne jamais lever
        d'``AttributeError`` (ce qui masquerait l'erreur réelle d'exécution).
        """
        if retcode in (None, "", "None"):
            return "aucune réponse de MT5"

        def rc(name: str) -> Any:
            """Retourne la constante MT5 si elle existe, sinon un code sentinelle."""
            return getattr(mt5, name, -1)

        mapping = {
            rc("TRADE_RETCODE_REQUOTE"): "requote (prix modifié)",
            rc("TRADE_RETCODE_REJECT"): "requête rejetée",
            rc("TRADE_RETCODE_ERROR"): "erreur d'exécution",
            rc("TRADE_RETCODE_TIMEOUT"): "timeout",
            rc("TRADE_RETCODE_INVALID_PRICE"): "prix invalide",
            rc("TRADE_RETCODE_INVALID_STOPS"): "stops invalides",
            rc("TRADE_RETCODE_INVALID_VOLUME"): "volume invalide",
            rc("TRADE_RETCODE_MARKET_CLOSED"): "marché fermé",
            rc("TRADE_RETCODE_NO_MONEY"): "fonds insuffisants",
            rc("TRADE_RETCODE_MARGIN"): "marge insuffisante",
            rc("TRADE_RETCODE_PRICE_CHANGED"): "prix modifié",
            rc("TRADE_RETCODE_PRICE_OFF"): "prix hors limite",
            rc("TRADE_RETCODE_SERVER_DISABLES_AT"): "trading auto désactivé à l'exécution",
        }
        return mapping.get(retcode, f"code {retcode}")

    async def _mt5_close_order(self, trade: Trade) -> Trade:
        """Ferme un ordre via MT5."""
        if not await asyncio.to_thread(mt5.initialize):
            logger.error("MT5 initialize() a échoué")
            raise MT5OrderError("MT5 non initialisé")

        # Récupérer la position
        positions = await asyncio.to_thread(mt5.positions_get, ticket=trade.ticket)
        if not positions:
            logger.warning("Position %d introuvable", trade.ticket)
            raise MT5PositionNotFoundError(f"Position {trade.ticket} introuvable")

        pos = positions[0]
        if trade.direction == Direction.BUY:
            close_type = mt5.ORDER_TYPE_SELL
            tick = await asyncio.to_thread(mt5.symbol_info_tick, trade.symbol)
            close_price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            tick = await asyncio.to_thread(mt5.symbol_info_tick, trade.symbol)
            close_price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": trade.symbol,
            "volume": float(trade.volume),
            "type": close_type,
            "price": close_price,
            "position": trade.ticket,
            "deviation": self._max_deviation,
            "magic": self._magic_number,
            "comment": "Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = await self._send_order(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            reason = self._describe_retcode(retcode)
            logger.error(
                "MT5 close_order échec | ticket=%s | retcode=%s | %s",
                trade.ticket, retcode, reason,
            )
            raise MT5OrderError(
                f"Échec fermeture position {trade.ticket} | retcode={retcode} | {reason}"
            )

        trade.close_price = Decimal(str(result.price))
        trade.profit = Decimal(str(pos.profit))
        logger.info(
            "MT5 order fermé | %s | ticket=%d | profit=%s",
            trade.symbol, trade.ticket, trade.profit,
        )
        return trade

    @staticmethod
    def _align_price_to_tick(
        price: Decimal | float,
        tick_size: Decimal,
        digits: int,
    ) -> Decimal:
        """
        Aligne un prix sur la grille de tick réelle du symbole.

        1. Quantification au tick size (``trade_tick_size``, repli sur ``point``).
        2. Arrondi final aux ``digits`` du symbole.

        Un prix non aligné sur le tick size rend la requête SL/TP entière
        invalide (retcode 10013 = TRADE_RETCODE_INVALID).
        """
        if tick_size <= 0:
            tick_size = Decimal("0.00001") if digits >= 5 else Decimal("0.001")
        d = Decimal(str(price))
        # ROUND_HALF_UP : ne dégrade pas la protection du SL plus que nécessaire
        aligned = (d / tick_size).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * tick_size
        return Decimal(aligned).quantize(Decimal(1).scaleb(-digits))

    @staticmethod
    def _sltp_distance_rejection(
        is_buy: bool,
        sl: Decimal,
        tp: Decimal,
        bid: Decimal | None,
        ask: Decimal | None,
        min_dist: Decimal,
    ) -> str | None:
        """
        Vérifie la distance SL/TP par rapport au marché.

        BUY  : SL < BID (et TP > BID si TP défini).
        SELL : SL > ASK (et TP < ASK si TP défini).

        Retourne un motif de rejet si la distance est inférieure à
        ``min_dist`` (= max(stops_level, freeze_level) * point), sinon None.
        Distingue explicitement ce cas d'une requête invalide (10013).
        """
        if bid is None or ask is None or min_dist <= 0:
            return None
        if is_buy:
            if not (sl < bid) or (bid - sl) < min_dist:
                return "SL_TOO_CLOSE_TO_MARKET"
            if tp > 0 and ((tp - bid) < min_dist or not (tp > bid)):
                return "TP_TOO_CLOSE_TO_MARKET"
        else:
            if not (sl > ask) or (sl - ask) < min_dist:
                return "SL_TOO_CLOSE_TO_MARKET"
            if tp > 0 and ((ask - tp) < min_dist or not (tp < ask)):
                return "TP_TOO_CLOSE_TO_MARKET"
        return None

    async def _mt5_modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """Modifie un ordre via MT5 (TRADE_ACTION_SLTP)."""
        if not await asyncio.to_thread(mt5.initialize):
            logger.error("MT5 initialize() a échoué")
            raise MT5OrderError("MT5 non initialisé")

        # 1. Position réelle : confirmer que le ticket est bien une POSITION
        #    MT5 ouverte (et non un order ticket).
        positions = await asyncio.to_thread(mt5.positions_get, ticket=trade.ticket)
        if not positions:
            logger.error(
                "MT5 modify_order : position introuvable | ticket=%s | symbol=%s",
                trade.ticket, trade.symbol,
            )
            raise MT5PositionNotFoundError(f"Position {trade.ticket} introuvable")
        pos = positions[0]
        pos_type_buy = int(pos.type) == int(getattr(mt5, "POSITION_TYPE_BUY", 0))

        # 2. Infos symbole + tick courant (normalisation / distances).
        info = await asyncio.to_thread(mt5.symbol_info, trade.symbol)
        tick = await asyncio.to_thread(mt5.symbol_info_tick, trade.symbol)
        if info is not None:
            digits = int(info.digits)
            point = Decimal(str(info.point))
            tick_size = Decimal(str(getattr(info, "trade_tick_size", 0) or 0)) or point
            stops_level = Decimal(int(getattr(info, "trade_stops_level", 0) or 0))
            freeze_level = Decimal(int(getattr(info, "trade_freeze_level", 0) or 0))
        else:
            digits, point, tick_size = 5, Decimal("0.00001"), Decimal("0.00001")
            stops_level = freeze_level = Decimal(0)
        bid = Decimal(str(tick.bid)) if tick is not None else None
        ask = Decimal(str(tick.ask)) if tick is not None else None

        # 3. Nouveaux SL/TP : normalisation au digits + tick size réel.
        #    TP non demandé -> conserver le TP réellement présent chez le
        #    broker (ne pas pousser une valeur ARTY périmée).
        if stop_loss is not None:
            new_sl = self._align_price_to_tick(Decimal(str(stop_loss)), tick_size, digits)
        else:
            new_sl = self._align_price_to_tick(trade.stop_loss, tick_size, digits)
        if take_profit is not None:
            new_tp = self._align_price_to_tick(Decimal(str(take_profit)), tick_size, digits)
        else:
            new_tp = self._align_price_to_tick(Decimal(str(pos.tp)), tick_size, digits)

        cur_sl = self._align_price_to_tick(Decimal(str(pos.sl)), tick_size, digits)
        cur_tp = self._align_price_to_tick(Decimal(str(pos.tp)), tick_size, digits)

        # 4. NO CHANGE : ne pas envoyer une modification identique. Le broker
        #    est déjà au niveau demandé -> considéré comme appliqué (le
        #    niveau peut être consommé sans erreur).
        if new_sl == cur_sl and new_tp == cur_tp:
            logger.info(
                "[SL MODIFY SKIPPED] %s | ticket=%s | reason=NO_CHANGE | sl=%s",
                trade.symbol, trade.ticket, new_sl,
            )
            if stop_loss is not None:
                trade.stop_loss = new_sl
            return trade

        # 5. Distance au marché (stops/freeze level) : distinguée du 10013,
        #    rejetée proprement AVANT order_send().
        min_dist = max(stops_level, freeze_level) * point
        rejection = self._sltp_distance_rejection(
            pos_type_buy, new_sl, new_tp, bid, ask, min_dist
        )
        if rejection is not None:
            logger.error(
                "[SL MODIFY REJECTED] %s | ticket=%s | reason=%s | candidate_sl=%s | "
                "bid=%s | ask=%s | stops_level=%s | freeze_level=%s",
                trade.symbol, trade.ticket, rejection,
                new_sl, bid, ask, stops_level, freeze_level,
            )
            raise MT5OrderError(
                f"Modification refusée (distance marché) position {trade.ticket} | "
                f"reason={rejection}"
            )

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": trade.symbol,
            "position": trade.ticket,
            "sl": float(new_sl),
            "tp": float(new_tp),
        }

        # 6. Diagnostic temporaire : requête complète + order_check().
        logger.info(
            "[MT5 SLTP REQUEST] ticket=%s | symbol=%s | position_type=%s | "
            "current_sl=%s | candidate_sl=%s | current_tp=%s | bid=%s | ask=%s | "
            "digits=%s | point=%s | tick_size=%s | stops_level=%s | freeze_level=%s | "
            "request=%s",
            trade.ticket, trade.symbol, "BUY" if pos_type_buy else "SELL",
            pos.sl, new_sl, pos.tp, bid, ask,
            digits, point, tick_size, stops_level, freeze_level, request,
        )
        try:
            check = await asyncio.to_thread(mt5.order_check, request)
            if check is not None:
                logger.info(
                    "[MT5 SLTP CHECK] retcode=%s | comment=%s | request=%s",
                    getattr(check, "retcode", None),
                    getattr(check, "comment", None),
                    getattr(check, "request", None),
                )
        except Exception as exc:  # noqa: BLE001 - diagnostic non bloquant
            logger.warning("order_check indisponible : %s", exc)

        result = await self._send_order(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            reason = self._describe_retcode(retcode)
            logger.error(
                "MT5 modify_order échec | ticket=%s | retcode=%s | %s",
                trade.ticket, retcode, reason,
            )
            raise MT5OrderError(
                f"Échec modification position {trade.ticket} | retcode={retcode} | {reason}"
            )

        if stop_loss is not None:
            trade.stop_loss = Decimal(str(new_sl))
        if take_profit is not None:
            trade.take_profit = Decimal(str(new_tp))

        logger.info(
            "MT5 order modifié | %s | SL=%s | TP=%s",
            trade.symbol, trade.stop_loss, trade.take_profit,
        )
        return trade
