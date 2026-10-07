"""Position sizing with an explicit small-account rule.

On a tiny demo balance, 1 % of equity can fall below the broker minimum volume.
The policy is explicit and always journalled: either refuse the trade, or cap the
real risk to a configured fraction. Every decision is reported separately so it
never pollutes the headline risk statistics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from arty_trading.config.operational import definitions, load_config
from arty_trading.modules.execution.risk_limits import permitted_volume


@dataclass(frozen=True)
class SizingDecision:
    volume: float
    requested_volume: float
    initial_risk_usd: float
    risk_fraction_requested: float
    risk_fraction_applied: float
    small_account: bool
    policy: str
    reason: str
    limits: dict[str, Any]

    @property
    def accepted(self) -> bool:
        return self.volume > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume": self.volume,
            "requested_volume": self.requested_volume,
            "initial_risk_usd": self.initial_risk_usd,
            "risk_fraction_requested": self.risk_fraction_requested,
            "risk_fraction_applied": self.risk_fraction_applied,
            "small_account": self.small_account,
            "policy": self.policy,
            "reason": self.reason,
            "limits": self.limits,
            "accepted": self.accepted,
        }


def size_from_risk(
    *,
    balance: float,
    risk_fraction: float,
    entry: float,
    stop: float,
    atr: float,
    symbol: str = "XAUUSD",
    minimum_volume: float | None = None,
    volume_step: float | None = None,
    existing_notional: float = 0.0,
    existing_margin: float = 0.0,
    cfg: dict[str, Any] | None = None,
) -> SizingDecision:
    """Risk-based volume, then the explicit small-account policy."""
    cfg = cfg or load_config("forward_demo.yaml")
    limits_cfg = load_config("execution.yaml")["risk_limits"]
    units = float(definitions()["instrument_units"][symbol]["contract_ounces_per_lot"])
    distance = abs(float(entry) - float(stop))
    if distance <= 0 or not math.isfinite(distance):
        return SizingDecision(0.0, 0.0, 0.0, risk_fraction, 0.0, False, "none",
                              "invalid_stop_distance", {})
    risk_usd = float(balance) * float(risk_fraction)
    risk_per_lot_usd = distance * units
    requested = risk_usd / risk_per_lot_usd if risk_per_lot_usd > 0 else 0.0
    floor = float(minimum_volume if minimum_volume is not None else limits_cfg["minimum_volume"])
    step = float(volume_step if volume_step is not None else limits_cfg["volume_step"])
    small_policy = str(cfg["risk"]["small_account_policy"])
    threshold = float(cfg["risk"]["small_account_balance_threshold"])
    # The economically meaningful trigger: the risk budget cannot buy the broker
    # minimum lot. A low balance is the usual cause, but not the only one.
    small = requested < floor
    applied_fraction = float(risk_fraction)
    reason = "risk_sized"

    if small and requested < floor:
        # 1 % of equity cannot buy the minimum lot: apply the explicit policy.
        capped_fraction = float(cfg["risk"]["small_account_capped_risk_fraction"])
        if small_policy == "refuse":
            return SizingDecision(
                0.0, requested, 0.0, float(risk_fraction), 0.0, True, small_policy,
                f"small_account_refused_below_minimum_volume_balance_{float(balance):.2f}"
                f"_below_{threshold:.2f}",
                {},
            )
        if small_policy != "cap_risk":
            raise ValueError(f"Unknown small account policy: {small_policy}")
        applied_fraction = capped_fraction
        capped_risk_usd = float(balance) * capped_fraction
        capped_volume = capped_risk_usd / risk_per_lot_usd if risk_per_lot_usd > 0 else 0.0
        # Round DOWN to the minimum lot that fits the capped risk. When no grid
        # point fits but the strict minimum still exceeds the cap, the honest
        # outcome is refusal (see the branch below), never a silent breach.
        lots = math.floor(capped_volume / step) * step
        while lots > 0 and lots * risk_per_lot_usd > capped_risk_usd:
            lots = round(lots - step, 10)
        if lots < floor:
            # Even the minimum lot exceeds the capped risk: no acceptable
            # volume exists. Say so explicitly instead of silently breaching
            # the cap by flooring to the minimum lot.
            return SizingDecision(
                0.0, requested, 0.0, float(risk_fraction), capped_fraction,
                True, small_policy, "small_account_capped_risk_below_minimum_volume", {},
            )
        reason = "small_account_risk_capped"
        volume, limits = permitted_volume(
            lots, float(entry), float(stop), float(atr), float(balance),
            existing_notional, existing_margin,
        )
        applied = (float(volume) * risk_per_lot_usd) / float(balance) if balance else 0.0
        return SizingDecision(
            volume, requested, float(volume) * risk_per_lot_usd, float(risk_fraction), applied,
            True, small_policy, reason, limits,
        )

    volume, limits = permitted_volume(
        requested, float(entry), float(stop), float(atr), float(balance),
        existing_notional, existing_margin,
    )
    if volume <= 0:
        return SizingDecision(
            0.0, requested, 0.0, float(risk_fraction), 0.0, small, small_policy,
            str(limits.get("reason", "rejected_by_risk_limits")), limits,
        )
    return SizingDecision(
        volume, requested, float(volume) * risk_per_lot_usd, float(risk_fraction),
        applied_fraction, small, small_policy, reason, limits,
    )
