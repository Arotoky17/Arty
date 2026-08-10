"""
Exécuteur d'ordres Paper (PaperOrderExecutor) — Simulation complète sans MT5.

Ce module fournit un exécuteur qui implémente ``IOrderExecutor`` mais
**n'envoie aucun ordre réel à MetaTrader 5**. Il simule l'ouverture,
la fermeture et la modification d'ordres en mémoire.

Utilisé en mode ``TradingMode.PAPER`` pour permettre une simulation
complète du pipeline de trading (risque, exécution, monitoring) sans
risquer de fonds réels.

Tous les trades simulés sont suivis en interne et peuvent être récupérés
via ``get_open_trades()`` et ``get_closed_trades()`` pour le calcul de
statistiques.
"""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.core.interfaces import IOrderExecutor
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.EXECUTION)


class PaperOrderExecutor(IOrderExecutor):
    """
    Exécuteur d'ordres simulé (Paper Trading).

    Simule le cycle complet d'un ordre :
    - **Ouverture** : crée un ``Trade`` avec un ticket fictif.
    - **Fermeture** : calcule le profit/perte selon le prix de clôture
      fourni ou, à défaut, utilise le prix d'entrée (P&L = 0).
    - **Modification** : met à jour le SL/TP du trade en mémoire.

    Aucune interaction avec MetaTrader 5 n'est effectuée.

    Attributes:
        _mock_ticket: Compteur interne pour générer des tickets uniques
        _open_trades: Dictionnaire des trades ouverts (ticket → Trade)
        _closed_trades: Liste des trades fermés
    """

    def __init__(self, starting_ticket: int = 50000) -> None:
        """
        Initialise l'exécuteur paper.

        Args:
            starting_ticket: Numéro de ticket de départ pour les ordres
                simulés (défaut 50000 pour éviter les collisions avec
                les tickets MT5 réels).
        """
        self._mock_ticket: int = starting_ticket
        self._open_trades: dict[int, Trade] = {}
        self._closed_trades: list[Trade] = []

    # -------------------------------------------------------------------------
    # Propriétés
    # -------------------------------------------------------------------------

    @property
    def is_paper_mode(self) -> bool:
        """Indique toujours True — cet exécuteur est toujours en mode paper."""
        return True

    @property
    def open_trades_count(self) -> int:
        """Nombre de trades actuellement ouverts."""
        return len(self._open_trades)

    @property
    def closed_trades_count(self) -> int:
        """Nombre de trades fermés."""
        return len(self._closed_trades)

    # -------------------------------------------------------------------------
    # IOrderExecutor
    # -------------------------------------------------------------------------

    async def open_order(self, signal: Signal, volume: float) -> Trade:
        """
        Simule l'ouverture d'un ordre.

        Crée un ``Trade`` avec un ticket fictif et l'enregistre dans
        les trades ouverts. Aucun ordre n'est envoyé à MT5.

        Args:
            signal: Signal d'entrée (entry, SL, TP, direction).
            volume: Taille de position en lots.

        Returns:
            Le trade créé avec son ticket simulé.
        """
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
        ticket = trade.ticket
        if ticket is not None:
            self._open_trades[ticket] = trade

        logger.info(
            "Paper order ouvert | %s | %s | volume=%s | ticket=%s",
            trade.symbol,
            trade.direction.value,
            trade.volume,
            trade.ticket,
        )
        return trade

    async def close_order(self, trade: Trade) -> Trade:
        """
        Simule la fermeture d'un ordre.

        Calcule le profit/perte si ``close_price`` est renseigné dans le
        trade, sinon utilise le prix d'entrée (P&L = 0).

        Args:
            trade: Le trade à fermer.

        Returns:
            Le trade mis à jour (fermé).
        """
        # Récupérer le trade depuis les trades ouverts
        ticket = trade.ticket
        if ticket is not None:
            open_trade = self._open_trades.pop(ticket, trade)
        else:
            open_trade = trade

        # Calculer le profit si un prix de clôture est fourni
        if trade.close_price is not None:
            close_price = float(trade.close_price)
            entry_price = float(open_trade.entry_price)
            if open_trade.direction == Direction.BUY:
                profit = (close_price - entry_price) * float(open_trade.volume) * 100000
            else:
                profit = (entry_price - close_price) * float(open_trade.volume) * 100000
            open_trade.close_price = trade.close_price
            open_trade.profit = Decimal(str(round(profit, 2)))
        else:
            # Pas de prix de clôture — P&L = 0
            open_trade.close_price = open_trade.entry_price
            open_trade.profit = Decimal("0")

        open_trade.is_open = False
        from datetime import datetime

        open_trade.closed_at = datetime.now(UTC)

        self._closed_trades.append(open_trade)

        logger.info(
            "Paper order fermé | %s | ticket=%d | profit=%s",
            open_trade.symbol,
            open_trade.ticket,
            open_trade.profit,
        )
        return open_trade

    async def modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """
        Simule la modification du SL/TP d'un ordre.

        Args:
            trade: Le trade à modifier.
            stop_loss: Nouveau SL (None = inchangé).
            take_profit: Nouveau TP (None = inchangé).

        Returns:
            Le trade modifié.
        """
        ticket = trade.ticket
        if ticket is not None:
            open_trade = self._open_trades.get(ticket, trade)
        else:
            open_trade = trade
        if stop_loss is not None:
            open_trade.stop_loss = Decimal(str(stop_loss))
        if take_profit is not None:
            open_trade.take_profit = Decimal(str(take_profit))

        logger.info(
            "Paper order modifié | %s | SL=%s | TP=%s",
            open_trade.symbol,
            open_trade.stop_loss,
            open_trade.take_profit,
        )
        return open_trade

    async def get_open_positions(self) -> list[Trade]:
        """Retourne la liste des trades simulés actuellement ouverts.

        Implémente ``IOrderExecutor.get_open_positions`` : en mode PAPER, les
        positions ouvertes sont celles maintenues en mémoire par l'exécuteur.
        """
        return self.get_open_trades()

    async def close_partial_order(self, trade: Trade, fraction: float) -> Trade | None:
        """Réduit une position simulée et retourne la portion clôturée."""
        if not 0 < fraction < 1:
            raise ValueError("La fraction de clôture doit être entre 0 et 1")
        ticket = trade.ticket
        open_trade = self._open_trades.get(ticket, trade) if ticket is not None else trade
        closed_volume = open_trade.volume * Decimal(str(fraction))
        if closed_volume <= 0 or closed_volume >= open_trade.volume:
            return await self.close_order(open_trade)
        closed = open_trade.model_copy(update={"volume": closed_volume})
        closed.close_price = open_trade.entry_price
        closed.profit = Decimal("0")
        closed.is_open = False
        open_trade.volume -= closed_volume
        self._closed_trades.append(closed)
        logger.info("Paper TP partiel | ticket=%s | volume=%s", ticket, closed_volume)
        return closed

    # -------------------------------------------------------------------------
    # Méthodes supplémentaires pour le suivi
    # -------------------------------------------------------------------------

    def get_open_trades(self) -> list[Trade]:
        """Retourne la liste des trades actuellement ouverts."""
        return list(self._open_trades.values())

    def get_closed_trades(self) -> list[Trade]:
        """Retourne la liste des trades fermés."""
        return list(self._closed_trades)

    def get_trade_by_ticket(self, ticket: int) -> Trade | None:
        """
        Récupère un trade par son ticket (ouvert ou fermé).

        Args:
            ticket: Le numéro de ticket du trade.

        Returns:
            Le trade correspondant, ou None s'il n'existe pas.
        """
        if ticket in self._open_trades:
            return self._open_trades[ticket]
        for t in self._closed_trades:
            if t.ticket == ticket:
                return t
        return None

    def reset(self) -> None:
        """Réinitialise tous les trades (ouverts et fermés)."""
        self._open_trades.clear()
        self._closed_trades.clear()
