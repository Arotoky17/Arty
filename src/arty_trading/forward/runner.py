"""Setup 1 execution policy for the forward demo.

This module contains **no detection logic**. Signals come from the shared
detectors and ``config/definitions.yaml`` through the injected ``signal_source``
(``SMCDetector`` + ``SignalGenerator``), exactly as the backtest does. What lives
here is only the operational policy the demo account requires: limit order at
the zone midpoint, 10-bar expiry, stop with buffer, TP at 2R, exit after 48 bars
or at NY 17:00, the 20-minute post-reopen window, non-tradable days and one
position at a time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from arty_trading.config.operational import definitions, load_config
from arty_trading.forward.journal import TradeRecord
from arty_trading.validation.market_calendar import MarketCalendar


class BrokerPort(Protocol):
    """Minimal execution surface; the MT5 ``OrderExecutor`` satisfies it."""

    def place_limit(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def cancel(self, ticket: int) -> bool: ...
    def close_position(self, ticket: int, reason: str) -> dict[str, Any]: ...
    def open_positions(self) -> list[dict[str, Any]]: ...
    def equity(self) -> float: ...
    def spread(self) -> float | None: ...
    def last_tick_utc(self) -> datetime | None: ...


@dataclass(frozen=True)
class Setup1Policy:
    """Execution constants, cross-checked against the preregistration."""

    pending_max: int
    expiry_bars: int
    max_holding_bars: int
    reward_risk: float
    stop_buffer_atr: float
    post_reopen_minutes: int
    ny_flat_hour: int
    min_stop_atr: float

    @classmethod
    def load(cls, cfg: dict[str, Any] | None = None) -> Setup1Policy:
        cfg = cfg or load_config("forward_demo.yaml")["execution"]
        policy = cls(
            pending_max=int(cfg["pending_max"]),
            expiry_bars=int(cfg["expiry_bars"]),
            max_holding_bars=int(cfg["max_holding_bars"]),
            reward_risk=float(cfg["reward_risk"]),
            stop_buffer_atr=float(cfg["stop_buffer_atr"]),
            post_reopen_minutes=int(cfg["post_reopen_minutes"]),
            ny_flat_hour=int(cfg["ny_flat_hour"]),
            min_stop_atr=float(cfg["min_stop_atr"]),
        )
        policy.assert_matches_preregistration(load_config("setup1_preregistration.yaml")["strategy"])
        return policy

    def assert_matches_preregistration(self, setup: dict[str, Any]) -> None:
        """Fail closed if the demo policy drifts from the preregistered setup."""
        checks = (
            ("expiry_bars", self.expiry_bars,
             int(load_config("execution.yaml")["fill"]["expiry_bars"])),
            ("max_holding_bars", self.max_holding_bars,
             int(setup["maximum_holding_market_bars"])),
            ("reward_risk", self.reward_risk, float(setup["reward_risk"])),
            ("stop_buffer_atr", self.stop_buffer_atr, float(setup["stop_buffer_atr"])),
            ("pending_orders_max", self.pending_max, int(setup["pending_orders_max"])),
            ("post_reopen_minutes", self.post_reopen_minutes,
             int(load_config("market_calendar.yaml")["post_reopen_minutes"])),
        )
        for name, live, prereg in checks:
            if live != prereg:
                raise ValueError(
                    f"Forward demo {name}={live} differs from the preregistered "
                    f"value {prereg}; the demo must follow the same specification"
                )


@dataclass
class Decision:
    action: str
    reason: str
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reason": self.reason, **self.detail}


class ForwardDemoRunner:
    """Single source of strategy, demo-specific execution and journaling."""

    def __init__(
        self,
        *,
        run_id: str,
        broker: BrokerPort,
        journal: Any,
        kill_switch: Any,
        sizing: Callable[..., Any],
        policy: Setup1Policy | None = None,
        clock: Callable[[], datetime] | None = None,
        symbol: str = "XAUUSD",
    ) -> None:
        self.run_id = run_id
        self.broker = broker
        self.journal = journal
        self.kill_switch = kill_switch
        self.sizing = sizing
        self.policy = policy or Setup1Policy.load()
        self.clock = clock or (lambda: datetime.now())
        self.symbol = symbol
        self.calendar = MarketCalendar()
        self.pending: list[dict[str, Any]] = []
        self.open_position: dict[str, Any] | None = None
        self.atr = 0.0
        self._equity = 0.0

    # ------------------------------------------------------------------ gates
    def session_gate(self, bar: Any) -> Decision | None:
        """Non-tradable day, daily pause and the post-reopen window."""
        state = self.calendar.state(bar.time)
        if state["non_tradable"]:
            return Decision("skip", f"market_closed:{state['reason']}", {})
        if state["entry_blocked"]:
            return Decision("skip", f"entry_blocked:{state['reason']}", {})
        return None

    # ------------------------------------------------------------- lifecycle
    def expire_pending(self, bar: Any) -> list[Decision]:
        """A limit order lives exactly ``expiry_bars`` closed M5 bars."""
        decisions: list[Decision] = []
        survivors = []
        for order in self.pending:
            if bar.time < order["expires_at"]:
                survivors.append(order)
                continue
            self.broker.cancel(order["ticket"])
            self.journal.update_signal_outcome(
                order["journal_row"], "expired", reason="expiry_bars_reached"
            )
            decisions.append(
                Decision("expire", "expiry_bars_reached", {"ticket": order["ticket"]})
            )
        self.pending = survivors
        return decisions

    def manage_open_position(self, bar: Any) -> list[Decision]:
        """Stop, target at 2R, 48-bar time exit and the NY 17:00 flat."""
        if self.open_position is None:
            return []
        position = self.open_position
        decisions: list[Decision] = []
        stop_hit = (
            bar.low <= position["stop_loss"]
            if position["direction"] == "BUY"
            else bar.high >= position["stop_loss"]
        )
        target_hit = (
            bar.high >= position["take_profit"]
            if position["direction"] == "BUY"
            else bar.low <= position["take_profit"]
        )
        reason = None
        # The stop always wins on its bar when both levels print: the trade
        # risked its full R before any target could fill. NY 17:00 then closes
        # the position with priority over the quiet 48-bar time exit.
        if stop_hit:
            reason = "stop_loss"  # stop has priority, as preregistered
        elif target_hit:
            reason = "take_profit"
        elif self._is_ny_flat(bar.time):
            reason = "ny_17h_flat"
        elif bar.time >= position["max_exit_at"]:
            reason = "holding_bars_reached"
        if reason:
            self.broker.close_position(position["ticket"], reason)
            self.journal.record_trade(
                self.run_id, self._trade_record(position, bar, reason),
                position.get("journal_row"),
            )
            self.open_position = None
            decisions.append(Decision("close", reason, {"ticket": position["ticket"]}))
        return decisions

    # ----------------------------------------------------------------- entry
    def on_signal(self, signal: Any, bar: Any, spread: float | None) -> Decision:
        """Submit the preregistered limit order, or journal why not."""
        gate = self.session_gate(bar) or self.risk_gate()
        direction = str(signal.direction.value).upper()
        if gate is not None:
            self.journal.record_signal(
                self.run_id, ts_utc=bar.time, symbol=self.symbol, direction=direction,
                theoretical_price=float(signal.entry_price),
                stop_loss=float(signal.stop_loss), take_profit=float(signal.take_profit),
                status="ignored", reason=gate.reason, spread_price=spread,
                signal_id=str(getattr(signal, "id", "")),
            )
            return gate
        decision = self.sizing(
            balance=self._equity,
            risk_fraction=self.kill_switch.cfg["risk"]["risk_fraction"],
            entry=float(signal.entry_price), stop=float(signal.stop_loss),
            atr=self.atr, symbol=self.symbol,
        )
        row = self.journal.record_signal(
            self.run_id, ts_utc=bar.time, symbol=self.symbol, direction=direction,
            theoretical_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss), take_profit=float(signal.take_profit),
            status="rejected" if not decision.accepted else "filled",
            reason=decision.reason, spread_price=spread,
            volume=decision.volume or None, signal_id=str(getattr(signal, "id", "")),
            sizing=decision.as_dict(),
        )
        if not decision.accepted:
            return Decision("reject", decision.reason, {"volume": decision.volume})
        placed = self.broker.place_limit(
            {
                "symbol": self.symbol, "direction": direction,
                "price": float(signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "take_profit": float(signal.take_profit),
                "volume": float(decision.volume),
            }
        )
        ok = bool(placed.get("accepted"))
        self.kill_switch.on_execution_result(ok)
        if not ok:
            self.journal.update_signal_outcome(
                row, "rejected", reason=f"broker_rejected:{placed.get('reason')}"
            )
            return Decision("reject", str(placed.get("reason")),
                            {"ticket": placed.get("ticket")})
        self.journal.update_signal_outcome(
            row, "filled", reason="limit_placed",
            fill_price=placed.get("fill_price"),
            slippage_price=placed.get("slippage_price"),
            latency_ms=placed.get("latency_ms"), volume=float(decision.volume),
        )
        self.pending.append(
            {
                "ticket": int(placed["ticket"]), "journal_row": row, "direction": direction,
                "entry_price": float(placed.get("fill_price") or signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "take_profit": float(signal.take_profit),
                "volume": float(decision.volume), "submitted_at": bar.time,
                "expires_at": bar.time + timedelta(minutes=5 * self.policy.expiry_bars),
            }
        )
        return Decision("submit", "limit_placed", {"ticket": placed["ticket"]})

    def on_fill(self, moment: datetime, ticket: int, fill_price: float) -> None:
        order = next((o for o in self.pending if o["ticket"] == ticket), None)
        if order is None:
            return
        self.pending = [o for o in self.pending if o["ticket"] != ticket]
        self.open_position = {
            **order,
            "entry_price": float(fill_price or order["entry_price"]),
            "filled_at": moment,
            "max_exit_at": moment + timedelta(minutes=5 * self.policy.max_holding_bars),
        }

    # ------------------------------------------------------------------ feed
    def poll(self) -> dict[str, Any]:
        """Refresh account/feed state into the kill switch."""
        equity = float(self.broker.equity())
        self._equity = equity
        self.kill_switch.on_equity(equity)
        self.kill_switch.on_feed(self.broker.last_tick_utc(), connected=True)
        self.kill_switch.on_spread(self.broker.spread())
        return {
            "equity": equity, "spread": self.broker.spread(),
            "may_trade": self.kill_switch.may_trade(),
            "halt_reason": self.kill_switch.halt_reason(),
        }

    def _trade_record(
        self, position: dict[str, Any], bar: Any, reason: str
    ) -> TradeRecord:
        direction = position["direction"]
        entry = position["entry_price"]
        price = float(getattr(bar, "close", entry))
        exit_price = (
            position["stop_loss"] if reason == "stop_loss"
            else position["take_profit"] if reason == "take_profit"
            else price
        )
        units = float(
            definitions()["instrument_units"][self.symbol]["contract_ounces_per_lot"]
        )
        gross = (
            (exit_price - entry) if direction == "BUY" else (entry - exit_price)
        ) * position["volume"] * units
        return TradeRecord(
            opened_at_utc=position["filled_at"], closed_at_utc=bar.time,
            direction=direction, entry_price=entry,
            exit_price=exit_price, volume=position["volume"],
            initial_risk_usd=abs(entry - position["stop_loss"]) * position["volume"] * units,
            gross_pnl=gross, costs_usd=0.0, exit_reason=reason,
            ticket=position.get("ticket"),
        )

    def _is_ny_flat(self, moment: datetime) -> bool:
        local = moment.astimezone(self.calendar.zone)
        return local.hour == self.policy.ny_flat_hour and local.minute == 0

    def risk_gate(self) -> Decision | None:
        if not self.kill_switch.may_trade():
            return Decision("skip", f"kill_switch:{self.kill_switch.halt_reason()}", {})
        if self.open_position is not None:
            return Decision("skip", "one_position_at_a_time", {})
        if len(self.pending) >= self.policy.pending_max:
            return Decision("skip", "pending_limit_reached", {})
        return None
