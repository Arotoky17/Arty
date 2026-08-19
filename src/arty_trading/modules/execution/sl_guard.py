"""Règle absolue du Position Management : le SL ne devient jamais moins protecteur.

Fournit :
- ``is_more_protective`` : vérifie qu'un SL candidat améliore la protection ;
- ``most_protective`` : choisit le SL le plus protecteur parmi des candidats ;
- ``StructureContext`` : snapshot immuable de la structure SMC confirmée
  (HL/LH, ATR, breaks) calculé à partir des bougies **passées uniquement**.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from arty_trading.core.enums import Direction


def is_more_protective(
    direction: Direction, candidate: Decimal, current: Decimal
) -> bool:
    """Retourne True si ``candidate`` protège mieux que ``current``.

    BUY  : le SL ne peut que monter.
    SELL : le SL ne peut que descendre.
    """
    if direction == Direction.BUY:
        return candidate > current
    return candidate < current


def most_protective(
    direction: Direction, *candidates: Decimal | None
) -> Decimal | None:
    """Retourne le candidat le plus protecteur (ignore les None)."""
    values = [c for c in candidates if c is not None]
    if not values:
        return None
    if direction == Direction.BUY:
        return max(values)
    return min(values)


@dataclass(frozen=True)
class StructureContext:
    """Snapshot de structure SMC confirmée, mis à jour par bougie (jamais par tick).

    Attributes:
        swing_low: dernier swing low confirmé (fractal, sans look-ahead).
        swing_high: dernier swing high confirmé.
        hl: Higher Low confirmé soutenant un BUY (None si indisponible).
        lh: Lower High confirmé soutenant un SELL (None si indisponible).
        atr: ATR courant du symbole (pour le buffer du SL structurel).
        bearish_break: clôture sous le dernier swing low confirmé
            (invalidation structurelle d'un BUY).
        bullish_break: clôture au-dessus du dernier swing high confirmé
            (invalidation structurelle d'un SELL).
    """

    swing_low: Decimal | None = None
    swing_high: Decimal | None = None
    hl: Decimal | None = None
    lh: Decimal | None = None
    atr: Decimal | None = None
    bearish_break: bool = False
    bullish_break: bool = False

    def is_broken_against(self, direction: Direction) -> bool:
        """Indique si la structure est invalidée contre la direction du trade."""
        if direction == Direction.BUY:
            return self.bearish_break
        return self.bullish_break
