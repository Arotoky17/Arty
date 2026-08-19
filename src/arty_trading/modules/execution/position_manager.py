"""Gestion déterministe du cycle de vie d'une position en multiples de R.

Nouveau système (Profit Lock + Partial Profit + Runner + Structure Trailing) :

- le **R initial** est calculé à partir du SL initial (jamais le SL courant) ;
- le **SL ne devient jamais moins protecteur** (règle monotone, ``sl_guard``) ;
- le Profit Lock sécurise progressivement les gains par paliers ;
- le Structure Trailing suit les HL/LH **confirmés** (snapshot par bougie) ;
- le Partial Profit s'exécute une seule fois par niveau ;
- le Runner laisse courir la position au-delà du seuil de sécurisation.

Mode legacy (``PROFIT_LOCK_ENABLED=false``) : comportement historique
break-even + TP partiel + trailing fixe, inchangé.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from arty_trading.config.settings import PositionSettings
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction
from arty_trading.modules.execution.partial_profit import PartialProfitManager
from arty_trading.modules.execution.profit_lock import ProfitLockManager
from arty_trading.modules.execution.runner import RunnerManager
from arty_trading.modules.execution.sl_guard import (
    StructureContext,
    is_more_protective,
    most_protective,
)
from arty_trading.modules.execution.structure_trailing import StructureTrailingManager

logger = logging.getLogger("arty_trading.position")


@dataclass(frozen=True)
class PositionAction:
    """Action demandée au port d'exécution pour une position suivie.

    ``lock_level`` / ``partial_level`` : index du niveau à consommer
    **uniquement après confirmation de l'exécution** (``confirm``).
    """

    kind: str
    stop_loss: Decimal | None = None
    close_fraction: Decimal | None = None
    reason: str = ""
    lock_level: int | None = None
    partial_level: int | None = None


@dataclass
class _PositionState:
    """État de gestion d'une position — persisté pour la reprise après redémarrage."""

    initial_sl: Decimal
    initial_risk: Decimal
    initial_volume: Decimal
    profit_lock_level: int = -1  # plus haut palier appliqué
    partial_levels_done: set[int] = field(default_factory=set)
    runner_active: bool = False
    break_even_done: bool = False  # legacy
    partial_done: bool = False  # legacy

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'état (JSON-compatible) pour la persistance disque."""
        return {
            "initial_sl": str(self.initial_sl),
            "initial_risk": str(self.initial_risk),
            "initial_volume": str(self.initial_volume),
            "profit_lock_level": self.profit_lock_level,
            "partial_levels_done": sorted(self.partial_levels_done),
            "runner_active": self.runner_active,
            "break_even_done": self.break_even_done,
            "partial_done": self.partial_done,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_PositionState":
        """Reconstruit un état persisté (reprise après redémarrage)."""
        return cls(
            initial_sl=Decimal(str(data["initial_sl"])),
            initial_risk=Decimal(str(data["initial_risk"])),
            initial_volume=Decimal(str(data["initial_volume"])),
            profit_lock_level=int(data.get("profit_lock_level", -1)),
            partial_levels_done={int(i) for i in data.get("partial_levels_done", [])},
            runner_active=bool(data.get("runner_active", False)),
            break_even_done=bool(data.get("break_even_done", False)),
            partial_done=bool(data.get("partial_done", False)),
        )


class PositionManager:
    """Détermine les actions Profit Lock, Structure Trailing, Partial et sorties."""

    def __init__(self, settings: PositionSettings) -> None:
        self._settings = settings
        self._states: dict[str, _PositionState] = {}
        # Trades dont l'état a changé et doit être persisté dès que possible.
        self._pending_persist: set[str] = set()
        self._profit_lock = ProfitLockManager(
            [(Decimal(str(t)), Decimal(str(l))) for t, l in settings.profit_lock_level_list]
        )
        self._partial_profit = PartialProfitManager(
            [(Decimal(str(t)), Decimal(str(f))) for t, f in settings.partial_profit_level_list]
        )
        self._structure_trailing = StructureTrailingManager(
            settings.structure_trailing_buffer_atr
        )
        self._runner = RunnerManager(
            enabled=settings.enable_runner,
            tp_enabled=settings.runner_tp_enabled,
            tp_r=settings.runner_tp_r,
            exit_on_structure_break=settings.runner_exit_on_structure_break,
        )

    # ------------------------------------------------------------------
    # Enregistrement / état
    # ------------------------------------------------------------------

    def register(self, trade: Trade, restored: dict[str, Any] | None = None) -> None:
        """Enregistre le risque initial, immuable, d'un trade ouvert.

        Args:
            trade: Trade ouvert (SL initial = SL courant à l'ouverture).
            restored: État persisté (reprise après redémarrage) — évite tout
                recalcul d'un faux R depuis un SL déjà déplacé.
        """
        if restored is not None:
            try:
                state = _PositionState.from_dict(restored)
                self._states[str(trade.id)] = state
                self._write_metadata(trade, state)
                return
            except (KeyError, ValueError, ArithmeticError) as exc:
                logger.warning(
                    "État persisté invalide, fallback SL courant | ticket=%s | %s",
                    trade.ticket, exc,
                )
        risk = abs(trade.entry_price - trade.stop_loss)
        if risk > 0:
            state = _PositionState(
                initial_sl=trade.stop_loss,
                initial_risk=risk,
                initial_volume=trade.volume,
            )
            self._states[str(trade.id)] = state
            self._write_metadata(trade, state)

    def forget(self, trade: Trade) -> None:
        """Supprime l'état lorsqu'une position est clôturée."""
        self._states.pop(str(trade.id), None)
        if isinstance(trade.metadata, dict):
            trade.metadata.pop("position_management", None)

    def snapshot(self, trade: Trade) -> dict[str, Any] | None:
        """Retourne l'état sérialisable d'une position (persistance)."""
        state = self._states.get(str(trade.id))
        return state.to_dict() if state else None

    @property
    def initial_risk(self) -> dict[str, Decimal]:
        """Carte des risques initiaux par trade (introspection/tests)."""
        return {k: s.initial_risk for k, s in self._states.items()}

    def confirm(self, trade: Trade, action: PositionAction) -> None:
        """Consomme un niveau UNIQUEMENT après exécution réussie côté broker.

        Règle ``DÉCISION != EXÉCUTION`` : ``evaluate`` propose, l'executor
        exécute, et seul un succès confirmé (``modify_order`` /
        ``close_partial_order`` sans erreur) permet de consommer un palier
        de profit lock ou de partial profit. En cas d'échec MT5, le niveau
        reste disponible et sera re-proposé au tick suivant.
        """
        state = self._states.get(str(trade.id))
        if state is None:
            return
        changed = False
        if action.kind == "modify" and action.lock_level is not None:
            if action.lock_level > state.profit_lock_level:
                state.profit_lock_level = action.lock_level
                changed = True
        elif action.kind == "partial_close" and action.partial_level is not None:
            if action.partial_level not in state.partial_levels_done:
                state.partial_levels_done.add(action.partial_level)
                changed = True
        if changed:
            self._write_metadata(trade, state)
            self._pending_persist.add(str(trade.id))

    def take_persist_flag(self, trade: Trade) -> bool:
        """Retourne True si l'état du trade doit être persisté (une fois)."""
        key = str(trade.id)
        if key in self._pending_persist:
            self._pending_persist.discard(key)
            return True
        return False

    @staticmethod
    def _write_metadata(trade: Trade, state: _PositionState) -> None:
        if isinstance(trade.metadata, dict):
            trade.metadata["position_management"] = state.to_dict()

    # ------------------------------------------------------------------
    # Évaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        trade: Trade,
        price: Decimal,
        structure: StructureContext | None = None,
    ) -> list[PositionAction]:
        """Retourne les actions applicables au prix courant, dans leur ordre."""
        state = self._states.get(str(trade.id))
        if state is None or not trade.is_open:
            return []
        if self._stop_hit(trade, price):
            return [PositionAction("close", reason="stop_loss")]

        r_multiple = self._r_multiple(trade, price, state.initial_risk)
        if self._settings.enable_profit_lock:
            return self._evaluate_modern(trade, price, r_multiple, state, structure)
        return self._evaluate_legacy(trade, price, r_multiple, state)

    # ------------------------------------------------------------------
    # Système actif : Profit Lock + Structure Trailing + Partial + Runner
    # ------------------------------------------------------------------

    def _evaluate_modern(
        self,
        trade: Trade,
        price: Decimal,
        r_multiple: Decimal,
        state: _PositionState,
        structure: StructureContext | None,
    ) -> list[PositionAction]:
        actions: list[PositionAction] = []
        direction = trade.direction

        # --- SL candidats : Profit Lock + Structure Trailing (le + protecteur)
        candidates: list[tuple[Decimal, str]] = []

        lock = self._profit_lock.candidate(
            direction, trade.entry_price, state.initial_risk, r_multiple
        )
        if lock is not None:
            sl, level_index, level = lock
            if level_index > state.profit_lock_level:
                candidates.append((sl, f"profit_lock_{level.trigger_r}R"))

        if self._settings.enable_structure_trailing and structure is not None:
            structural = self._structure_trailing.candidate(direction, structure)
            if structural is not None:
                candidates.append(structural)

        best_sl, best_reason = self._best_candidate(direction, candidates)

        # --- Règle monotone + distance minimale de mise à jour
        # NOTE : le niveau n'est PAS consommé ici — seule la confirmation
        # d'exécution (monitor -> executor -> confirm) le consomme.
        if best_sl is not None:
            if self._improves_enough(direction, best_sl, trade.stop_loss, state.initial_risk):
                actions.append(
                    PositionAction(
                        "modify",
                        best_sl,
                        reason=best_reason,
                        lock_level=self._lock_index_from_reason(best_reason),
                    )
                )
            elif not is_more_protective(direction, best_sl, trade.stop_loss):
                logger.debug(
                    "[SL UPDATE REJECTED] %s %s | candidate_sl=%s | current_sl=%s | reason=NOT_MORE_PROTECTIVE",
                    trade.symbol, direction.value, best_sl, trade.stop_loss,
                )

        # --- Partial Profit : chaque niveau exécuté une seule fois
        # NOTE : le niveau n'est PAS consommé ici — confirmé après exécution.
        if self._settings.enable_partial_profit:
            partials = self._partial_profit.pending(r_multiple, state.partial_levels_done)
            for index, level in partials:
                actions.append(
                    PositionAction(
                        "partial_close",
                        close_fraction=level.fraction,
                        reason=f"partial_profit_{level.trigger_r}R",
                        partial_level=index,
                    )
                )

        # --- Runner : engagement, TP optionnel, sortie structurelle
        if self._runner.is_engaged(r_multiple) and not state.runner_active:
            state.runner_active = True
            self._write_metadata(trade, state)
            self._pending_persist.add(str(trade.id))  # persistance immédiate
            logger.info(
                "[RUNNER] %s %s | profit=%sR | status=RUNNING",
                trade.symbol, direction.value, round(float(r_multiple), 2),
            )
        if self._runner.tp_hit(r_multiple):
            actions.append(PositionAction("close", reason="runner_take_profit"))
        elif self._runner.should_exit_on_structure(r_multiple, structure, direction):
            actions.append(PositionAction("close", reason="runner_structure_break"))
        elif self._target_hit(trade, price) and not self._runner.enabled:
            # TP classique conservé uniquement hors runner
            actions.append(PositionAction("close", reason="take_profit"))

        return actions

    def _best_candidate(
        self, direction: Direction, candidates: list[tuple[Decimal, str]]
    ) -> tuple[Decimal | None, str]:
        """Choisit le candidat le plus protecteur, avec sa raison."""
        best_sl = most_protective(direction, *(sl for sl, _ in candidates))
        if best_sl is None:
            return None, ""
        for sl, reason in candidates:  # raison du candidat retenu
            if sl == best_sl:
                return best_sl, reason
        return best_sl, ""

    def _lock_index_from_reason(self, reason: str) -> int | None:
        """Retrouve l'index du palier depuis une raison ``profit_lock_<t>R``."""
        for index, level in enumerate(self._profit_lock.levels):
            if reason == f"profit_lock_{level.trigger_r}R":
                return index
        return None

    def _improves_enough(
        self,
        direction: Direction,
        candidate: Decimal,
        current: Decimal,
        initial_risk: Decimal,
    ) -> bool:
        """Vérifie la règle monotone + la distance minimale de mise à jour."""
        if not is_more_protective(direction, candidate, current):
            return False
        min_distance = Decimal(str(self._settings.min_sl_update_r)) * initial_risk
        if direction == Direction.BUY:
            return candidate - current >= min_distance
        return current - candidate >= min_distance

    # ------------------------------------------------------------------
    # Mode legacy (PROFIT_LOCK_ENABLED=false) — comportement historique
    # ------------------------------------------------------------------

    def _evaluate_legacy(
        self,
        trade: Trade,
        price: Decimal,
        r_multiple: Decimal,
        state: _PositionState,
    ) -> list[PositionAction]:
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

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

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
