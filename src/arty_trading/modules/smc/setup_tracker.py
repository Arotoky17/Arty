"""
Suivi d'état des setups de trading SMC/ICT.

State machine explicite pour le cycle de vie d'un setup :

    WAITING → ZONE_REACHED → LIQUIDITY_SWEEP → STRUCTURE_CONFIRMED
    → DISPLACEMENT_CONFIRMED → WAITING_RETEST → RETEST_DETECTED
    → REJECTION_CONFIRMED → ENTRY_READY → FINAL_VALIDATION → TRADE

Transitions supplémentaires :
    - Tout état → SETUP_EXPIRED : conditions devenues obsolètes
    - Tout état → CONSUMED : setup déjà tradé
    - Tout état → INVALID : zone invalidée (violation structurelle)

La persistance est **en mémoire** (dict par symbole) pour la Phase 1.
Un TODO explicite marque l'emplacement où ajouter la persistance fichier/DB
si le bot doit tourner en continu sans interruption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from arty_trading.core.enums import Direction, NoTradeReason


class SetupState(str, Enum):
    """États possibles d'un setup SMC."""

    WAITING = "waiting"
    ZONE_REACHED = "zone_reached"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    STRUCTURE_CONFIRMED = "structure_confirmed"
    DISPLACEMENT_CONFIRMED = "displacement_confirmed"
    WAITING_RETEST = "waiting_retest"
    RETEST_DETECTED = "retest_detected"
    REJECTION_CONFIRMED = "rejection_confirmed"
    ENTRY_READY = "entry_ready"
    FINAL_VALIDATION = "final_validation"
    TRADE = "trade"
    SETUP_EXPIRED = "setup_expired"
    CONSUMED = "consumed"
    INVALID = "invalid"


@dataclass
class Setup:
    """Représente un setup de trading potentiel en cours de suivi."""

    setup_id: str
    symbol: str
    direction: Direction
    state: SetupState = SetupState.WAITING
    zone_price: float | None = None
    zone_high: float | None = None
    zone_low: float | None = None
    zone_concept: str | None = None
    zone_index: int = 0
    structure_event: dict | None = None
    displacement_index: int | None = None
    retest_index: int | None = None
    rejection_index: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    no_trade_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "state": self.state.value,
            "zone_price": self.zone_price,
            "zone_high": self.zone_high,
            "zone_low": self.zone_low,
            "zone_concept": self.zone_concept,
            "zone_index": self.zone_index,
            "structure_event": self.structure_event,
            "displacement_index": self.displacement_index,
            "retest_index": self.retest_index,
            "rejection_index": self.rejection_index,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "no_trade_reasons": list(self.no_trade_reasons),
            "metadata": dict(self.metadata),
        }


@dataclass
class StateTransition:
    """Représente une transition d'état."""

    from_state: SetupState
    to_state: SetupState
    reason: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


class SetupStateMachine:
    """
    Machine à états pour le suivi des setups de trading.

    Chaque transition est journalisée et les transitions invalides sont
    rejetées. La machine ne remonte jamais d'état (pas de retour en arrière
    sauf expiration/invalidation explicite).
    """

    _ALLOWED_TRANSITIONS: dict[SetupState, set[SetupState]] = field(default_factory=lambda: {
        SetupState.WAITING: {SetupState.ZONE_REACHED, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.ZONE_REACHED: {SetupState.LIQUIDITY_SWEEP, SetupState.WAITING_RETEST, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.LIQUIDITY_SWEEP: {SetupState.STRUCTURE_CONFIRMED, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.STRUCTURE_CONFIRMED: {SetupState.DISPLACEMENT_CONFIRMED, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.DISPLACEMENT_CONFIRMED: {SetupState.WAITING_RETEST, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.WAITING_RETEST: {SetupState.RETEST_DETECTED, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.RETEST_DETECTED: {SetupState.REJECTION_CONFIRMED, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.REJECTION_CONFIRMED: {SetupState.ENTRY_READY, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.ENTRY_READY: {SetupState.FINAL_VALIDATION, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.FINAL_VALIDATION: {SetupState.TRADE, SetupState.INVALID, SetupState.SETUP_EXPIRED},
        SetupState.TRADE: {SetupState.CONSUMED},
        SetupState.SETUP_EXPIRED: set(),
        SetupState.CONSUMED: set(),
        SetupState.INVALID: set(),
    })

    def __init__(self) -> None:
        self._transitions: list[StateTransition] = []

    def can_transition(self, from_state: SetupState, to_state: SetupState) -> bool:
        """Vérifie si une transition est autorisée."""
        return to_state in self._ALLOWED_TRANSITIONS.get(from_state, set())

    def transition(self, setup: Setup, to_state: SetupState, reason: str) -> bool:
        """
        Tente une transition d'état.

        Returns:
            True si la transition est autorisée et effectuée, False sinon.
        """
        if not self.can_transition(setup.state, to_state):
            return False

        from_state = setup.state
        setup.state = to_state
        setup.updated_at = datetime.utcnow()
        self._transitions.append(StateTransition(from_state, to_state, reason))
        return True

    def expire(self, setup: Setup, reason: str) -> bool:
        """Marque un setup comme expiré."""
        return self.transition(setup, SetupState.SETUP_EXPIRED, reason)

    def consume(self, setup: Setup, reason: str) -> bool:
        """Marque un setup comme consommé (trade exécuté)."""
        return self.transition(setup, SetupState.CONSUMED, reason)

    def invalidate(self, setup: Setup, reason: str) -> bool:
        """Marque un setup comme invalide."""
        return self.transition(setup, SetupState.INVALID, reason)

    @property
    def transitions(self) -> list[StateTransition]:
        return list(self._transitions)


class SetupTracker:
    """
    Gestionnaire de setups par symbole.

    Stocke les setups en mémoire (perdu au redémarrage). Pour la persistance
    fichier/DB, voir le TODO dans la docstring de la classe.
    """

    # TODO_PERSISTENCE : ajouter persistance fichier/DB ici pour que les setups
    # survivent au redémarrage. Pour l'instant, stockage en mémoire uniquement.

    def __init__(self) -> None:
        self._setups: dict[str, list[Setup]] = {}
        self._machine = SetupStateMachine()
        self._counter: int = 0
        # Observateur d'événements (utilisé par le debugger de décision pour
        # être notifié de la création d'un nouveau setup, sans modifier les
        # règles de la state machine).
        self._on_setup_created: Callable[[Setup], None] | None = None

    def set_on_setup_created(self, callback: Callable[[Setup], None] | None) -> None:
        """Enregistre un callback appelé à chaque création de setup (observabilité).

        Ne modifie aucune règle de trading : le callback est purement informatif.
        """
        self._on_setup_created = callback

    def _symbol_key(self, symbol: str) -> str:
        return symbol.upper()

    def create_setup(
        self,
        symbol: str,
        direction: Direction,
        zone_price: float | None = None,
        zone_high: float | None = None,
        zone_low: float | None = None,
        zone_concept: str | None = None,
        zone_index: int = 0,
        structure_event: dict | None = None,
        ttl_bars: int = 20,
    ) -> Setup:
        """
        Crée un nouveau setup en état WAITING.

        Returns:
            Le setup créé.
        """
        self._counter += 1
        setup_id = f"{self._symbol_key(symbol)}_{direction.value}_{self._counter}"

        # TODO_PERSISTENCE : calculer expires_at à partir du timestamp de la
        # dernière bougie + ttl_bars, pas datetime.utcnow().
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(minutes=ttl_bars * 5)

        setup = Setup(
            setup_id=setup_id,
            symbol=self._symbol_key(symbol),
            direction=direction,
            zone_price=zone_price,
            zone_high=zone_high,
            zone_low=zone_low,
            zone_concept=zone_concept,
            zone_index=zone_index,
            structure_event=structure_event,
            expires_at=expires_at,
        )
        key = self._symbol_key(symbol)
        self._setups.setdefault(key, []).append(setup)
        if self._on_setup_created is not None:
            self._on_setup_created(setup)
        return setup

    def get_active_setups(self, symbol: str) -> list[Setup]:
        """Retourne les setups actifs (non consommés, non expirés, non invalides)."""
        key = self._symbol_key(symbol)
        return [
            s for s in self._setups.get(key, [])
            if s.state not in (
                SetupState.CONSUMED,
                SetupState.SETUP_EXPIRED,
                SetupState.INVALID,
            )
        ]

    def get_setup_by_id(self, setup_id: str) -> Setup | None:
        """Retourne un setup par son ID."""
        for setups in self._setups.values():
            for s in setups:
                if s.setup_id == setup_id:
                    return s
        return None

    def is_duplicate(self, symbol: str, direction: Direction, zone_concept: str, zone_index: int) -> bool:
        """
        Vérifie si un setup identique existe déjà.

        Un setup est considéré comme duplicat si même symbole, même direction,
        même concept de zone, même index de bougie.
        """
        for s in self.get_active_setups(symbol):
            if (s.direction == direction
                    and s.zone_concept == zone_concept
                    and s.zone_index == zone_index):
                return True
        return False

    def expire_old_setups(self, symbol: str, current_time: datetime | None = None) -> list[Setup]:
        """
        Expire les setups dont la date d'expiration est dépassée.

        Returns:
            Liste des setups expirés.
        """
        now = current_time or datetime.utcnow()
        expired = []
        for s in self.get_active_setups(symbol):
            if s.expires_at and s.expires_at <= now:
                self._machine.expire(s, "setup_expired")
                expired.append(s)
        return expired

    def mark_consumed(self, setup: Setup, reason: str = "trade_executed") -> bool:
        """Marque un setup comme consommé après exécution du trade."""
        return self._machine.consume(setup, reason)

    def cleanup(self, symbol: str | None = None) -> None:
        """
        Supprime les setups terminés (consommés, expirés, invalides).

        Args:
            symbol: Symbole à nettoyer. Si None, nettoie tous les symboles.
        """
        if symbol is not None:
            key = self._symbol_key(symbol)
            self._setups[key] = [
                s for s in self._setups.get(key, [])
                if s.state not in (
                    SetupState.CONSUMED,
                    SetupState.SETUP_EXPIRED,
                    SetupState.INVALID,
                )
            ]
        else:
            for key in self._setups:
                self._setups[key] = [
                    s for s in self._setups[key]
                    if s.state not in (
                        SetupState.CONSUMED,
                        SetupState.SETUP_EXPIRED,
                        SetupState.INVALID,
                    )
                ]
