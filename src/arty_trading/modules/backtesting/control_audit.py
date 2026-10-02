"""Fail-closed audit assertions for real-data control reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Audit timestamps must explicitly include their timezone")
    return parsed.astimezone(UTC)


def audit_violations(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inspect every executed trade; missing evidence is a failure, not a pass."""
    violations: list[dict[str, Any]] = []
    for index, trade in enumerate(trades):
        reasons: list[str] = []
        try:
            ob = timestamp(trade["ob_timestamp"])
            entry = timestamp(trade["entry_timestamp"])
            confirmation = timestamp(trade["confirmation_timestamp"])
            first = timestamp(trade["window_start"])
            last = timestamp(trade["window_end"])
            if entry <= ob:
                reasons.append("entry_before_or_at_ob_creation")
            if not ob < confirmation <= entry or not first <= confirmation <= last:
                reasons.append("confirmation_out_of_window")
            if confirmation not in {timestamp(value) for value in trade["window_timestamps"]}:
                reasons.append("confirmation_not_in_available_candles")
            if trade.get("confirmation_type") == "rejection_candle":
                low, high = (
                    float(trade["rejection_candle_low"]),
                    float(trade["rejection_candle_high"]),
                )
                ob_low, ob_high = float(trade["ob_low"]), float(trade["ob_high"])
                if low > ob_high or high < ob_low:
                    reasons.append("no_contact_with_ob")
            if not trade.get("confirmation_valid", False):
                reasons.append("unconfirmed_legacy_entry")
        except (KeyError, TypeError, ValueError):
            reasons.append("missing_or_invalid_audit_evidence")
        if reasons:
            violations.append({"trade_index": index, "reasons": reasons})
    return violations


def validate_report(report: dict[str, Any], minimum_trades: int = 20) -> None:
    """Raise for missing history, incomplete audits, low count or any violation."""
    if report.get("data_source") not in ("mt5", "mt5_export"):
        raise ValueError("The control report must use real MT5 historical data")
    if not report.get("data_hash"):
        raise ValueError("Missing historical data fingerprint")
    trades = report.get("audit_trades")
    if not isinstance(trades, list) or len(trades) != report.get("total_trades"):
        raise ValueError("Every trade must have an audit record")
    if len(trades) < minimum_trades:
        raise ValueError(f"Insufficient trades: {len(trades)} < {minimum_trades}")
    violations = audit_violations(trades)
    if violations:
        raise ValueError(f"Audit violations: {violations}")
