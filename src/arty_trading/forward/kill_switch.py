"""Kill switch for the forward demo run.

Trips on: daily loss, cumulative run loss, consecutive execution errors, stale or
disconnected feed, and abnormal spread. Once tripped the run stays halted for the
rest of the session and must be restarted explicitly after review.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from arty_trading.config.operational import load_config


@dataclass
class KillSwitchState:
    tripped: bool = False
    reasons: list[str] = field(default_factory=list)
    tripped_at_utc: str | None = None
    daily_anchor_utc: str | None = None
    daily_start_equity: float = 0.0
    run_start_equity: float = 0.0
    consecutive_execution_errors: int = 0
    last_tick_utc: datetime | None = None
    sessions: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "tripped": self.tripped,
            "reasons": list(self.reasons),
            "tripped_at_utc": self.tripped_at_utc,
            "sessions": self.sessions,
        }


class KillSwitch:
    """Fail-closed risk envelope evaluated before every order decision."""

    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg or load_config("forward_demo.yaml")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.state = KillSwitchState()

    # ------------------------------------------------------------------ setup
    def start(self, equity: float) -> None:
        now = self.clock()
        self.state.run_start_equity = float(equity)
        self.state.daily_start_equity = float(equity)
        self.state.daily_anchor_utc = now.date().isoformat()
        self.state.sessions += 1

    # ------------------------------------------------------------------ trip
    def _trip(self, reason: str) -> None:
        if reason not in self.state.reasons:
            self.state.reasons.append(reason)
        if not self.state.tripped:
            self.state.tripped = True
            self.state.tripped_at_utc = self.clock().isoformat()

    # ------------------------------------------------------------- monitoring
    def on_equity(self, equity: float) -> None:
        """Daily and cumulative loss budgets, with an automatic UTC day roll."""
        risk = self.cfg["risk"]
        now = self.clock()
        today = now.date().isoformat()
        if self.state.daily_anchor_utc != today:
            self.state.daily_anchor_utc = today
            self.state.daily_start_equity = float(equity)
        if self.state.run_start_equity > 0:
            run_loss = (self.state.run_start_equity - float(equity)) / self.state.run_start_equity
            if run_loss >= float(risk["max_run_loss_fraction"]):
                self._trip(f"run_loss_{run_loss:.4f}_limit_{risk['max_run_loss_fraction']}")
        if self.state.daily_start_equity > 0:
            daily_start = float(self.state.daily_start_equity)
            day_loss = (daily_start - float(equity)) / daily_start
            if day_loss >= float(risk["max_daily_loss_fraction"]):
                self._trip(f"daily_loss_{day_loss:.4f}_limit_{risk['max_daily_loss_fraction']}")

    def on_execution_result(self, ok: bool) -> None:
        limit = int(self.cfg["feed"]["max_consecutive_execution_errors"])
        if ok:
            self.state.consecutive_execution_errors = 0
            return
        self.state.consecutive_execution_errors += 1
        if self.state.consecutive_execution_errors >= limit:
            self._trip(
                f"consecutive_execution_errors_{self.state.consecutive_execution_errors}"
            )

    def on_feed(self, tick_utc: datetime | None, connected: bool) -> None:
        max_age = float(self.cfg["feed"]["max_staleness_seconds"])
        if not connected:
            self._trip("feed_disconnected")
            return
        if tick_utc is None:
            self._trip("feed_missing_tick")
            return
        now = self.clock()
        age = (now - tick_utc).total_seconds()
        if age > max_age:
            self._trip(f"feed_stale_{int(age)}s_limit_{int(max_age)}s")
        else:
            self.state.last_tick_utc = tick_utc

    def on_spread(self, spread_price: float | None) -> None:
        cap = float(self.cfg["spread"]["max_spread_price"])
        if spread_price is None:
            self._trip("spread_unavailable")
        elif float(spread_price) > cap:
            self._trip(f"spread_{float(spread_price):.3f}_limit_{cap}")

    # ----------------------------------------------------------------- query
    def may_trade(self) -> bool:
        return not self.state.tripped

    def halt_reason(self) -> str | None:
        return "; ".join(self.state.reasons) if self.state.tripped else None


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return start, start + timedelta(days=1)
