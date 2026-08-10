"""
Exécuteur d'ordres — implémente IOrderExecutor.

Gère l'ouverture, fermeture et modification d'ordres via MT5.
Inclut le trailing stop et le break-even automatique.
Le mode LIVE est désactivé par défaut pour la sécurité.
"""

from __future__ import annotations

from decimal import Decimal

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
    MT5_AVAILABLE = False


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

    @property
    def is_mock_mode(self) -> bool:
        return self._mock_mode

    @property
    def is_live_trading_enabled(self) -> bool:
        """Vérifie si le trading réel est autorisé."""
        return self._settings.is_live_trading_enabled

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

        # Exécution MT5 réelle
        return await self._mt5_open_order(signal, volume)

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

        return await self._mt5_close_order(trade)

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

        return await self._mt5_modify_order(trade, stop_loss, take_profit)

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
    # MT5 Implementation (quand MT5 disponible)
    # =========================================================================

    async def _mt5_open_order(self, signal: Signal, volume: float) -> Trade:
        """Ouvre un ordre via MT5."""
        # Vérifier que MT5 est initialisé
        if not mt5.initialize():
            logger.error("MT5 initialize() a échoué")
            return self._mock_open_order(signal, volume)

        # Préparer la requête
        order_type = mt5.ORDER_TYPE_BUY if signal.direction == Direction.BUY else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(signal.entry_price),
            "sl": float(signal.stop_loss),
            "tp": float(signal.take_profit),
            "deviation": 20,
            "magic": 123456,
            "comment": signal.strategy_name,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            logger.error("MT5 order_send échec | retcode=%s", retcode)
            return self._mock_open_order(signal, volume)

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

    async def _mt5_close_order(self, trade: Trade) -> Trade:
        """Ferme un ordre via MT5."""
        if not mt5.initialize():
            logger.error("MT5 initialize() a échoué")
            return self._mock_close_order(trade)

        # Récupérer la position
        positions = mt5.positions_get(ticket=trade.ticket)
        if not positions:
            logger.warning("Position %d introuvable", trade.ticket)
            return self._mock_close_order(trade)

        pos = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if trade.direction == Direction.BUY else mt5.ORDER_TYPE_BUY
        close_price = mt5.symbol_info_tick(trade.symbol).bid if trade.direction == Direction.BUY else mt5.symbol_info_tick(trade.symbol).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": trade.symbol,
            "volume": float(trade.volume),
            "type": close_type,
            "price": close_price,
            "position": trade.ticket,
            "deviation": 20,
            "magic": 123456,
            "comment": "Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            logger.error("MT5 close_order échec | retcode=%s", retcode)
            return self._mock_close_order(trade)

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
            return self._mock_modify_order(trade, stop_loss, take_profit)

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
            logger.error("MT5 modify_order échec | retcode=%s", retcode)
            return self._mock_modify_order(trade, stop_loss, take_profit)

        if stop_loss is not None:
            trade.stop_loss = Decimal(str(new_sl))
        if take_profit is not None:
            trade.take_profit = Decimal(str(new_tp))

        logger.info(
            "MT5 order modifié | %s | SL=%s | TP=%s",
            trade.symbol, trade.stop_loss, trade.take_profit,
        )
        return trade
