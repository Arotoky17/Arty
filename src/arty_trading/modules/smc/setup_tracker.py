"""
Suivi d'état des setups de trading SMC/ICT.

State machine explicite pour le cycle de vie d'un setup :

    DETECTED (WAITING) → ARMED/WATCHING (ZONE_REACHED / WAITING_RETEST)
    → READY (REJECTION_CONFIRMED / ENTRY_READY)
    → ACCEPTED (FINAL_VALIDATION)
    → EXECUTED (TRADE → CONSUMED)

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

    DETECTED = "detected"
    ARMED = "armed"
    WATCHING = "watching"
    READY = "ready"
    ACCEPTED = "accepted"
    EXECUTED = "executed"
    SETUP_EXPIRED = "setup_expired"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"

    # États internes détaillés (compatibilité avec la state machine existante)
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


@dataclass
class Setup:
    """Représente un setup de trading potentiel en cours de suivi."""

    setup_id: str
    symbol: str
    direction: Direction
    state: SetupState = SetupState.DETECTED
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
    atr_at_detection: float = 0.0
    htf_trend_at_detection: str = "neutral"
    confidence_estimate: float = 0.0
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
            "atr_at_detection": self.atr_at_detection,
            "htf_trend_at_detection": self.htf_trend_at_detection,
            "confidence_estimate": self.confidence_estimate,
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

    _ALLOWED_TRANSITIONS: dict[SetupState, set[SetupState]] = {
        SetupState.DETECTED: {SetupState.ARMED, SetupState.WATCHING, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.ARMED: {SetupState.WATCHING, SetupState.READY, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.WATCHING: {SetupState.READY, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.READY: {SetupState.ACCEPTED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.ACCEPTED: {SetupState.EXECUTED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.EXECUTED: {SetupState.CONSUMED},
        SetupState.SETUP_EXPIRED: set(),
        SetupState.CONSUMED: set(),
        SetupState.INVALIDATED: set(),
        # États internes détaillés (compatibilité)
        SetupState.WAITING: {SetupState.ZONE_REACHED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.ZONE_REACHED: {SetupState.LIQUIDITY_SWEEP, SetupState.WAITING_RETEST, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.LIQUIDITY_SWEEP: {SetupState.STRUCTURE_CONFIRMED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.STRUCTURE_CONFIRMED: {SetupState.DISPLACEMENT_CONFIRMED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.DISPLACEMENT_CONFIRMED: {SetupState.WAITING_RETEST, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.WAITING_RETEST: {SetupState.RETEST_DETECTED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.RETEST_DETECTED: {SetupState.REJECTION_CONFIRMED, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.REJECTION_CONFIRMED: {SetupState.ENTRY_READY, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.ENTRY_READY: {SetupState.FINAL_VALIDATION, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.FINAL_VALIDATION: {SetupState.TRADE, SetupState.INVALIDATED, SetupState.SETUP_EXPIRED},
        SetupState.TRADE: {SetupState.CONSUMED},
    }

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
        return self.transition(setup, SetupState.INVALIDATED, reason)

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
        self._on_setup_transition: Callable[[Setup, SetupState, str], None] | None = None

    def set_on_setup_created(self, callback: Callable[[Setup], None] | None) -> None:
        """Enregistre un callback appelé à chaque création de setup (observabilité).

        Ne modifie aucune règle de trading : le callback est purement informatif.
        """
        self._on_setup_created = callback

    def set_on_setup_transition(self, callback: Callable[[Setup, SetupState, str], None] | None) -> None:
        """Enregistre un callback appelé à chaque transition d'état."""
        self._on_setup_transition = callback

    def _symbol_key(self, symbol: str) -> str:
        return symbol.upper()

    def _notify_transition(self, setup: Setup, new_state: SetupState, reason: str) -> None:
        if self._on_setup_transition is not None:
            self._on_setup_transition(setup, new_state, reason)

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
        atr: float = 0.0,
        htf_trend: str = "neutral",
        confidence_estimate: float = 0.0,
    ) -> Setup:
        """
        Crée un nouveau setup en état DETECTED.

        Returns:
            Le setup créé.
        """
        self._counter += 1
        setup_id = f"{self._symbol_key(symbol)}_{direction.value}_{self._counter}"

        from datetime import timedelta, timezone
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_bars * 5)

        setup = Setup(
            setup_id=setup_id,
            symbol=self._symbol_key(symbol),
            direction=direction,
            state=SetupState.DETECTED,
            zone_price=zone_price,
            zone_high=zone_high,
            zone_low=zone_low,
            zone_concept=zone_concept,
            zone_index=zone_index,
            structure_event=structure_event,
            expires_at=expires_at,
            atr_at_detection=atr,
            htf_trend_at_detection=htf_trend,
            confidence_estimate=confidence_estimate,
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
                SetupState.INVALIDATED,
            )
        ]

    def get_setups_by_state(self, symbol: str, state: SetupState) -> list[Setup]:
        """Retourne les setups actifs dans un état donné."""
        return [s for s in self.get_active_setups(symbol) if s.state == state]

    def get_ready_setups(self, symbol: str) -> list[Setup]:
        """Retourne les setups prêts pour signal (READY, ACCEPTED, ENTRY_READY, FINAL_VALIDATION, REJECTION_CONFIRMED)."""
        ready_states = {
            SetupState.READY,
            SetupState.ACCEPTED,
            SetupState.ENTRY_READY,
            SetupState.FINAL_VALIDATION,
            SetupState.REJECTION_CONFIRMED,
        }
        return [s for s in self.get_active_setups(symbol) if s.state in ready_states]

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
                self._machine.expire(s, "MAX_AGE")
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
                    SetupState.INVALIDATED,
                )
            ]
        else:
            for key in self._setups:
                self._setups[key] = [
                    s for s in self._setups[key]
                    if s.state not in (
                        SetupState.CONSUMED,
                        SetupState.SETUP_EXPIRED,
                        SetupState.INVALIDATED,
                    )
                ]

    def evaluate(
        self,
        symbol: str,
        candles: list[Any],
        smc_data: list[dict[str, Any]],
        htf_trend: str,
        atr: float,
        max_distance_atr_mult: float = 1.0,
        max_zone_age_bars: int = 20,
        current_price: float | None = None,
    ) -> list[Setup]:
        """
        Évalue tous les setups actifs et fait progresser leur state machine
        en fonction des conditions de marché actuelles.

        Transitions possibles :
        - DETECTED → ARMED/WATCHING quand le prix est suffisamment proche de la zone
        - ARMED/WATCHING → READY quand le prix est dans la zone avec confirmation
        - Tout état → INVALIDATED si la tendance H1 s'inverse ou si un BOS/CHoCH
          opposé se produit
        - Tout état → SETUP_EXPIRED si la zone est trop ancienne

        Args:
            symbol: Symbole à évaluer
            candles: Bougies du timeframe d'entrée (M5)
            smc_data: Détections SMC sur le timeframe d'entrée
            htf_trend: Tendance maître H1 ("bullish", "bearish", "neutral")
            atr: ATR actuel
            max_distance_atr_mult: Multiplicateur ATR pour la distance max
            max_zone_age_bars: Âge max d'une zone en bougies
            current_price: Prix courant (optionnel, déduit de la dernière bougie)

        Returns:
            Liste des setups ayant eu une transition
        """
        from arty_trading.utils.helpers import is_fresh_structure

        key = self._symbol_key(symbol)
        active = self.get_active_setups(symbol)
        if not active:
            return []

        price = current_price
        if price is None and candles:
            price = float(candles[-1].close)

        total_candles = len(candles) if candles else 0
        max_distance = atr * max_distance_atr_mult if atr > 0 else 0.0

        # Détecter les événements structurels opposés
        opposite_direction = None
        latest_opposite_event = None
        if htf_trend in ("bullish", "bearish"):
            opposite = "bearish" if htf_trend == "bullish" else "bullish"
            for detection in smc_data:
                if detection.get("direction") == opposite:
                    concept = detection.get("concept", "")
                    if concept in ("break_of_structure", "change_of_character", "market_structure_shift"):
                        if latest_opposite_event is None or detection.get("index", 0) > latest_opposite_event.get("index", 0):
                            latest_opposite_event = detection
                            opposite_direction = opposite

        # Vérifier si la tendance H1 a changé depuis la détection
        trend_reversed = False
        for setup in active:
            if setup.htf_trend_at_detection in ("bullish", "bearish"):
                if htf_trend != setup.htf_trend_at_detection:
                    trend_reversed = True
                    break

        transitions: list[Setup] = []

        for setup in active:
            # --- EXPIRATION PAR ÂGE DE ZONE ---
            zone_age = total_candles - 1 - setup.zone_index if total_candles > 0 and setup.zone_index >= 0 else 999
            if setup.zone_index > 0 and zone_age > max_zone_age_bars:
                if self._machine.expire(setup, "MAX_AGE"):
                    self._notify_transition(setup, SetupState.SETUP_EXPIRED, "MAX_AGE")
                    transitions.append(setup)
                continue

            # --- INVALIDATION PAR TENDANCE H1 REVERSEE ---
            if trend_reversed and setup.htf_trend_at_detection != htf_trend:
                if self._machine.invalidate(setup, "STRUCTURE_INVALIDATED"):
                    setup.no_trade_reasons.append("trend_reversal")
                    self._notify_transition(setup, SetupState.INVALIDATED, "STRUCTURE_INVALIDATED:trend_reversal")
                    transitions.append(setup)
                continue

            # --- INVALIDATION PAR BOS/CHoCH OPPOSE ---
            if latest_opposite_event is not None:
                setup_direction_str = "bullish" if setup.direction == Direction.BUY else "bearish"
                if setup_direction_str == htf_trend:
                    if self._machine.invalidate(setup, "STRUCTURE_INVALIDATED"):
                        setup.no_trade_reasons.append("opposite_bos")
                        self._notify_transition(setup, SetupState.INVALIDATED, "STRUCTURE_INVALIDATED:opposite_bos")
                        transitions.append(setup)
                    continue

            # --- EVALUATION DE LA DISTANCE A LA ZONE ---
            if setup.zone_high is not None and setup.zone_low is not None and price is not None:
                zone_top = setup.zone_high
                zone_bottom = setup.zone_low
                if price >= zone_bottom and price <= zone_top:
                    distance = 0.0
                elif price > zone_top:
                    distance = price - zone_top
                else:
                    distance = zone_bottom - price

                # Vérifier si le prix est dans la zone avec confirmation de rejet
                in_zone = distance == 0.0
                if in_zone and candles:
                    last = candles[-1]
                    if setup.direction == Direction.BUY:
                        rejection_confirmed = bool(last.is_bullish and float(last.close) >= zone_bottom)
                    else:
                        rejection_confirmed = bool(not last.is_bullish and float(last.close) <= zone_top)
                else:
                    rejection_confirmed = False

                close_enough = distance <= max_distance if max_distance > 0 else True

                # DETECTED → WATCHING : prix dans la zone (pas besoin d'attendre)
                if setup.state == SetupState.DETECTED and in_zone:
                    new_state = SetupState.WATCHING
                    if self._machine.transition(setup, new_state, "ZONE_REACHED"):
                        self._notify_transition(setup, new_state, "ZONE_REACHED")
                        transitions.append(setup)
                    continue

                # DETECTED → ARMED : prix proche de la zone mais pas dedans
                if setup.state == SetupState.DETECTED and close_enough and not in_zone:
                    new_state = SetupState.ARMED
                    if self._machine.transition(setup, new_state, f"ZONE_APPROACH:distance={distance:.6f}"):
                        self._notify_transition(setup, new_state, "ZONE_APPROACH")
                        transitions.append(setup)
                    continue

                # ARMED/WATCHING → READY : prix dans la zone + confirmation
                if setup.state in (SetupState.ARMED, SetupState.WATCHING) and in_zone and rejection_confirmed:
                    new_state = SetupState.READY
                    if self._machine.transition(setup, new_state, "RETEST_CONFIRMED"):
                        self._notify_transition(setup, new_state, "RETEST_CONFIRMED")
                        transitions.append(setup)
                    continue

                # ARMED/WATCHING → ZONE_TOO_FAR (invalidation)
                if setup.state in (SetupState.ARMED, SetupState.WATCHING) and not close_enough:
                    if self._machine.invalidate(setup, "ZONE_TOO_FAR"):
                        setup.no_trade_reasons.append("zone_too_far")
                        self._notify_transition(setup, SetupState.INVALIDATED, "ZONE_TOO_FAR")
                        transitions.append(setup)
                    continue

        return transitions

    def get_setup_candidates_for_signal(self, symbol: str) -> list[Setup]:
        """
        Retourne les setups pouvant générer un signal.

        Inclut les setups READY, ACCEPTED, ENTRY_READY, FINAL_VALIDATION,
        REJECTION_CONFIRMED, ainsi que les setups ARMED/WATCHING dont le prix
        est dans la zone (ils peuvent être confirmés par la stratégie).
        """
        signal_states = {
            SetupState.READY,
            SetupState.ACCEPTED,
            SetupState.ENTRY_READY,
            SetupState.FINAL_VALIDATION,
            SetupState.REJECTION_CONFIRMED,
            SetupState.ARMED,
            SetupState.WATCHING,
        }
        return [s for s in self.get_active_setups(symbol) if s.state in signal_states]
