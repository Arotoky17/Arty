"""Runner — laisse courir la position gagnante au-delà du seuil de sécurisation.

Quand le runner est activé :
- le TP classique n'est plus une sortie obligatoire (sauf ``runner_tp_enabled``) ;
- ``runner_tp_r`` devient un niveau de sécurisation (Profit Lock), pas une fermeture ;
- la sortie se fait par SL (Profit Lock / Structure Trailing), TP optionnel,
  ou sortie structurelle si ``runner_exit_on_structure_break``.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.core.enums import Direction
from arty_trading.modules.execution.sl_guard import StructureContext


class RunnerManager:
    """Décide de la poursuite du runner et de ses conditions de sortie."""

    def __init__(
        self,
        enabled: bool,
        tp_enabled: bool,
        tp_r: float,
        exit_on_structure_break: bool,
    ) -> None:
        self._enabled = enabled
        self._tp_enabled = tp_enabled
        self._tp_r = Decimal(str(tp_r))
        self._exit_on_structure_break = exit_on_structure_break

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_engaged(self, r_multiple: Decimal) -> bool:
        """Le runner est engagé dès que le seuil ``runner_tp_r`` est atteint."""
        return self._enabled and r_multiple >= self._tp_r

    def tp_hit(self, r_multiple: Decimal) -> bool:
        """TP optionnel du runner atteint (uniquement si ``tp_enabled``)."""
        return self._enabled and self._tp_enabled and r_multiple >= self._tp_r

    def should_exit_on_structure(
        self,
        r_multiple: Decimal,
        structure: StructureContext | None,
        direction: Direction,
    ) -> bool:
        """Sortie structurelle : structure invalidée contre le trade, runner engagé.

        Ne sort jamais sur un micro-retracement : la rupture doit être une
        clôture au-delà du dernier swing confirmé (voir ``StructureContext``).
        """
        if not self._enabled or not self._exit_on_structure_break:
            return False
        if not self.is_engaged(r_multiple) or structure is None:
            return False
        return structure.is_broken_against(direction)
