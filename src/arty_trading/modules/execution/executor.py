"""
Exécuteur d'ordres — implémente IOrderExecutor.

Gère l'ouverture, fermeture et modification d'ordres via MT5.
Inclut le trailing stop et le break-even automatique.
Le mode LIVE est désactivé par défaut pour la sécurité.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from arty_trading.config.settings import get_settings
from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import Direction, LogCategory, TradingMode
from arty_trading.core.interfaces import IOrderExecutor
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.EXECUTION)

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore[assignment]
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
        """Clôture une fraction d'une position MT5 et conserve le reliquat ouvert."""
        if not 0 < fraction < 1:
            raise ValueError("La fraction de clôture doit être entre 0 et 1")
        closed_volume = trade.volume * Decimal(str(fraction))
        if closed_volume <= 0 or closed_volume >= trade.volume:
            return await self.close_order(trade)
        partial = trade.model_copy(update={"volume": closed_volume})
        closed = await self.close_order(partial)
        trade.volume -= closed_volume
        logger.info("TP partiel | ticket=%s | volume=%s", trade.ticket, closed_volume)
        return closed

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

        if not mt5.initialize():
            logger.warning("MT5 initialize() a échoué - réconciliation vide")
            return []

        raw_positions = mt5.positions_get()
        if raw_positions is None:
            return []

        trades: list[Trade] = []
        for pos in raw_positions:
            try:
                trades.append(self._position_to_trade(pos))
            except Exception as exc:  # noqa: BLE001 - une position invalide ne doit pas bloquer
                logger.warning("Position ignorée | ticket=%s | %s", pos.ticket, exc)
        return trades

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

    async def _mt5_open_order(self, signal: Signal, volume: float) -> Trade:
        """Ouvre un ordre via MT5."""
        # Vérifier que MT5 est initialisé
        if not mt5.initialize():
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

        result = mt5.order_send(request)

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
        if not mt5.initialize():
            logger.error("MT5 initialize() a échoué")
            raise MT5OrderError("MT5 non initialisé")

        # Récupérer la position
        positions = mt5.positions_get(ticket=trade.ticket)
        if not positions:
            logger.warning("Position %d introuvable", trade.ticket)
            raise MT5OrderError(f"Position {trade.ticket} introuvable")

        pos = positions[0]
        if trade.direction == Direction.BUY:
            close_type = mt5.ORDER_TYPE_SELL
            close_price = mt5.symbol_info_tick(trade.symbol).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            close_price = mt5.symbol_info_tick(trade.symbol).ask

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

        result = mt5.order_send(request)

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

    async def _mt5_modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """Modifie un ordre via MT5."""
        if not mt5.initialize():
            logger.error("MT5 initialize() a échoué")
            raise MT5OrderError("MT5 non initialisé")

        new_sl = float(stop_loss) if stop_loss is not None else float(trade.stop_loss)
        new_tp = float(take_profit) if take_profit is not None else float(trade.take_profit)

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": trade.symbol,
            "position": trade.ticket,
            "sl": new_sl,
            "tp": new_tp,
        }

        result = mt5.order_send(request)

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
