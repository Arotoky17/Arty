"""Reconciliation: replay a period in the backtest engine on broker data and
compare it, signal by signal, with what the live demo run actually produced.

Any difference that cannot be explained by a declared tolerance raises an alert.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_PRICE_TOLERANCE = 0.05
DEFAULT_TIME_TOLERANCE = timedelta(seconds=90)


@dataclass
class ReconciliationResult:
    matched: list[dict[str, Any]] = field(default_factory=list)
    broker_only: list[dict[str, Any]] = field(default_factory=list)
    engine_only: list[dict[str, Any]] = field(default_factory=list)
    mismatched: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    @property
    def unexplained(self) -> list[str]:
        """Every difference beyond tolerance, plus broker/engine-only rows."""
        return list(self.alerts)

    @property
    def clean(self) -> bool:
        return not self.broker_only and not self.engine_only and not self.mismatched

    def as_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "matched": len(self.matched),
            "broker_only": len(self.broker_only),
            "engine_only": len(self.engine_only),
            "mismatched": len(self.mismatched),
            "alerts": list(self.alerts),
            "unexplained": self.unexplained,
        }


def _ts(row: dict[str, Any], key: str) -> datetime:
    value = row[key]
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _normalise(row: dict[str, Any], time_key: str) -> dict[str, Any]:
    return {
        "ts": _ts(row, time_key).astimezone(UTC),
        "direction": str(row["direction"]).lower(),
        "price": float(row.get("price") or row.get("theoretical_price") or 0.0),
        "stop_loss": float(row.get("stop_loss") or 0.0),
        "take_profit": float(row.get("take_profit") or 0.0),
    }


def compare_signals(
    broker_rows: list[dict[str, Any]],
    engine_rows: list[dict[str, Any]],
    *,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    time_tolerance: timedelta = DEFAULT_TIME_TOLERANCE,
) -> ReconciliationResult:
    """Signal-by-signal comparison; greedy nearest match inside the tolerances."""
    result = ReconciliationResult()
    broker = sorted((_normalise(r, "ts_utc") for r in broker_rows), key=lambda r: r["ts"])
    engine = sorted((_normalise(r, "ts_utc") for r in engine_rows), key=lambda r: r["ts"])
    tolerance_seconds = time_tolerance.total_seconds()
    used: set[int] = set()
    for row in broker:
        best_index: int | None = None
        best_gap = tolerance_seconds
        for index, other in enumerate(engine):
            if index in used or other["direction"] != row["direction"]:
                continue
            gap = abs((other["ts"] - row["ts"]).total_seconds())
            if gap <= best_gap:
                best_gap, best_index = gap, index
        if best_index is None:
            result.broker_only.append(row)
            result.alerts.append(
                f"broker signal at {row['ts'].isoformat()} ({row['direction']}) "
                "has no engine counterpart"
            )
            continue
        used.add(best_index)
        other = engine[best_index]
        delta = abs(other["price"] - row["price"])
        if delta > price_tolerance:
            entry = {
                "ts": row["ts"].isoformat(),
                "direction": row["direction"],
                "broker_price": row["price"],
                "engine_price": other["price"],
                "price_delta": delta,
            }
            result.mismatched.append(entry)
            result.alerts.append(
                f"price divergence {delta:.4f} > {price_tolerance} at "
                f"{row['ts'].isoformat()} ({row['direction']})"
            )
        else:
            result.matched.append(
                {
                    "ts": row["ts"].isoformat(),
                    "direction": row["direction"],
                    "price_delta": delta,
                }
            )
    for index, row in enumerate(engine):
        if index not in used:
            result.engine_only.append(row)
            result.alerts.append(
                f"engine signal at {row['ts'].isoformat()} ({row['direction']}) "
                "was not produced live"
            )
    return result


def reconcile_with_engine(
    broker_signals: list[dict[str, Any]],
    engine_signal_source: Callable[[], list[dict[str, Any]]],
    **tolerances: Any,
) -> ReconciliationResult:
    """Run the shared backtest engine on the same period, then compare.

    ``engine_signal_source`` must return the engine's own signal rows for the
    same window; it is injected so the comparison stays testable and so the
    strategy itself is never re-implemented here.
    """
    return compare_signals(broker_signals, engine_signal_source(), **tolerances)
