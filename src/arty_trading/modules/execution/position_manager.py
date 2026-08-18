"""Gestion déterministe du cycle de vie d'une position en multiples de R."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from arty_trading.config.settings import PositionSettings
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction


@dataclass(frozen=True)
class PositionAction:
    """Action demandée au port d'exécution pour une position suivie."""

    kind: str
    stop_loss: Decimal | None = None
    close_fraction: Decimal | None = None
    reason: str = ""


@dataclass
class _PositionState:
    initial_risk: Decimal
    break_even_done: bool = False
    partial_done: bool = False


class PositionManager:
    """Détermine les actions BE, TP partiel, trailing et sorties SL/TP."""

    def __init__(self, settings: PositionSettings) -> None:
        self._settings = settings
        self._states: dict[str, _PositionState] = {}

    def register(self, trade: Trade) -> None:
        """Enregistre le risque initial, immuable, d'un trade ouvert."""
        risk = abs(trade.entry_price - trade.stop_loss)
        if risk > 0:
            self._states[str(trade.id)] = _PositionState(initial_risk=risk)

    def forget(self, trade: Trade) -> None:
        """Supprime l'état lorsqu'une position est clôturée."""
        self._states.pop(str(trade.id), None)

    def evaluate(self, trade: Trade, price: Decimal) -> list[PositionAction]:
        """Retourne les actions applicables au prix courant, dans leur ordre."""
        state = self._states.get(str(trade.id))
        if state is None or not trade.is_open:
            return []
        if self._stop_hit(trade, price):
            return [PositionAction("close", reason="stop_loss")]

        r_multiple = self._r_multiple(trade, price, state.initial_risk)
        actions: list[PositionAction] = []

        if self._settings.enable_break_even and not state.break_even_done:
            if r_multiple >= Decimal(str(self._settings.break_even_at_r)):
                state.break_even_done = True
                if trade.direction == Direction.BUY:
                    improves = trade.entry_price > trade.stop_loss
                else:
                    improves = trade.entry_price < trade.stop_loss
                if improves:
                    actions.append(PositionAction("modify", trade.entry_price, reason="break_even"))

        if self._settings.enable_partial_tp and not state.partial_done:
            if r_multiple >= Decimal(str(self._settings.partial_tp_at_r)):
                state.partial_done = True
                actions.append(
                    PositionAction(
                        "partial_close",
                        close_fraction=Decimal(str(self._settings.partial_close_percent)),
                        reason="partial_take_profit",
                    )
                )

        trailing_threshold = Decimal(str(self._settings.trailing_at_r))
        if self._settings.enable_trailing_stop and r_multiple >= trailing_threshold:
            distance = state.initial_risk * Decimal(
                str(self._settings.trailing_distance_r)
            )
            trailing_sl = (
                price - distance if trade.direction == Direction.BUY else price + distance
            )
            improves = (
                trailing_sl > trade.stop_loss
                if trade.direction == Direction.BUY
                else trailing_sl < trade.stop_loss
            )
            if improves:
                actions.append(PositionAction("modify", trailing_sl, reason="trailing_stop"))

        if self._target_hit(trade, price):
            if self._settings.enable_partial_tp and not state.partial_done:
                state.partial_done = True
                actions.append(
                    PositionAction(
                        "partial_close",
                        close_fraction=Decimal(str(self._settings.partial_close_percent)),
                        reason="partial_take_profit",
                    )
                )
            actions.append(PositionAction("close", reason="take_profit"))

        return actions

    @staticmethod
    def _r_multiple(trade: Trade, price: Decimal, risk: Decimal) -> Decimal:
        profit = (
            price - trade.entry_price
            if trade.direction == Direction.BUY
            else trade.entry_price - price
        )
        return profit / risk if risk else Decimal("0")

    @staticmethod
    def _stop_hit(trade: Trade, price: Decimal) -> bool:
        return (
            price <= trade.stop_loss
            if trade.direction == Direction.BUY
            else price >= trade.stop_loss
        )

    @staticmethod
    def _target_hit(trade: Trade, price: Decimal) -> bool:
        return (
            price >= trade.take_profit
            if trade.direction == Direction.BUY
            else price <= trade.take_profit
        )
