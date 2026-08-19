"""Profit Lock — sécurisation progressive des gains par paliers de R.

Chaque palier est un couple ``(trigger_r, lock_r)`` : lorsque le profit atteint
``trigger_r`` (en multiples du risque initial), le SL est déplacé pour garantir
``lock_r`` de gain. Les paliers sont configurables et jamais codés en dur dans
la logique métier.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from arty_trading.core.enums import Direction


@dataclass(frozen=True)
class ProfitLockLevel:
    """Palier de sécurisation : déclencheur et gain garanti, en R."""

    trigger_r: Decimal
    lock_r: Decimal


class ProfitLockManager:
    """Calcule le SL candidat du palier de profit lock atteint."""

    def __init__(self, levels: list[tuple[Decimal, Decimal]]) -> None:
        self._levels = sorted(
            (ProfitLockLevel(t, l) for t, l in levels), key=lambda lv: lv.trigger_r
        )

    @property
    def levels(self) -> list[ProfitLockLevel]:
        return list(self._levels)

    def candidate(
        self,
        direction: Direction,
        entry_price: Decimal,
        initial_risk: Decimal,
        r_multiple: Decimal,
    ) -> tuple[Decimal, int, ProfitLockLevel] | None:
        """Retourne ``(sl, index_palier, palier)`` du meilleur palier atteint.

        Le palier retenu est le plus haut dont le déclencheur est atteint ;
        l'appelant vérifie ensuite qu'il n'a pas déjà été appliqué (état).
        """
        if initial_risk <= 0:
            return None
        for index in range(len(self._levels) - 1, -1, -1):
            level = self._levels[index]
            if r_multiple >= level.trigger_r:
                offset = level.lock_r * initial_risk
                sl = (
                    entry_price + offset
                    if direction == Direction.BUY
                    else entry_price - offset
                )
                return sl, index, level
        return None

    def highest_level_index(self) -> int:
        """Index du dernier palier (utilisé pour l'activation du runner)."""
        return len(self._levels) - 1
