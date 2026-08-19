"""Structure Trailing SMC — SL sous le HL confirmé (BUY) / au-dessus du LH (SELL).

Le SL candidat est placé à distance d'un buffer ATR configurable de la
structure confirmée. La règle monotone (le SL ne descend jamais) est vérifiée
en amont par le ``PositionManager`` via ``sl_guard``.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.enums import Direction
from arty_trading.modules.execution.sl_guard import StructureContext


class StructureTrailingManager:
    """Calcule le SL candidat basé sur la structure SMC confirmée."""

    def __init__(self, buffer_atr: float = 0.2) -> None:
        self._buffer_atr = Decimal(str(buffer_atr))

    def candidate(
        self, direction: Direction, structure: StructureContext
    ) -> tuple[Decimal, str] | None:
        """Retourne ``(sl, motif)`` si une structure confirmée est disponible.

        BUY  : SL sous le Higher Low confirmé (``hl - buffer``).
        SELL : SL au-dessus du Lower High confirmé (``lh + buffer``).

        Le buffer est un multiple d'ATR ; sans ATR disponible, la structure
        est ignorée (aucun SL calculé sur une base non adaptée à la volatilité).
        """
        if structure.atr is None or structure.atr <= 0:
            return None
        buffer = self._buffer_atr * structure.atr
        if direction == Direction.BUY:
            if structure.hl is None:
                return None
            return structure.hl - buffer, "structure_trailing_new_hl"
        if structure.lh is None:
            return None
        return structure.lh + buffer, "structure_trailing_new_lh"
