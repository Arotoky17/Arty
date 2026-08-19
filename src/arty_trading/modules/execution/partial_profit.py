"""Partial Profit — prise partielle configurable, exécutée une seule fois par niveau.

Chaque niveau est un couple ``(trigger_r, fraction)`` : à ``trigger_r`` de
profit, ``fraction`` de la position restante est clôturée. Un niveau déjà
exécuté ne peut jamais redéclencher (état persisté par position).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PartialLevel:
    """Niveau de prise partielle : déclencheur en R et fraction à clôturer."""

    trigger_r: Decimal
    fraction: Decimal


class PartialProfitManager:
    """Détermine les niveaux de partial à exécuter, sans double déclenchement."""

    def __init__(self, levels: list[tuple[Decimal, Decimal]]) -> None:
        self._levels = sorted(
            (PartialLevel(t, f) for t, f in levels), key=lambda lv: lv.trigger_r
        )

    @property
    def levels(self) -> list[PartialLevel]:
        return list(self._levels)

    def pending(
        self, r_multiple: Decimal, executed: set[int]
    ) -> list[tuple[int, PartialLevel]]:
        """Retourne les niveaux ``(index, niveau)`` à exécuter maintenant.

        Args:
            r_multiple: profit courant en multiples du risque initial.
            executed: indexes des niveaux déjà exécutés pour cette position.
        """
        return [
            (index, level)
            for index, level in enumerate(self._levels)
            if index not in executed and r_multiple >= level.trigger_r
        ]
