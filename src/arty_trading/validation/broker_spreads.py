"""Offline broker quote diagnostics and explicit, inactive cost hypothesis."""

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BrokerSpreadHypothesis:
    """USD/oz floor selected independently; never fit from reconstructed ask."""

    spread_broker_reel_usd_per_oz: float
    evidence: str

    def __post_init__(self):
        value = self.spread_broker_reel_usd_per_oz
        if not math.isfinite(value) or value < 0 or not self.evidence.strip():
            raise ValueError("Finite nonnegative broker spread and evidence required")

    def effective_spread(self, reconstructed: float, stress: float = 1.0) -> float:
        if not math.isfinite(reconstructed) or reconstructed < 0:
            raise ValueError("Invalid reconstructed spread")
        if not math.isfinite(stress) or stress < 1:
            raise ValueError("Stress must be finite and >= 1")
        return max(reconstructed, self.spread_broker_reel_usd_per_oz) * stress


def analyze(frame: pd.DataFrame) -> dict:
    """Tick-weighted summaries, never an automatically approved calibration."""
    data = frame.copy()
    data["utc"] = pd.to_datetime(
        data["observed_utc"], format="%Y.%m.%d %H:%M:%S", utc=True, errors="coerce"
    )
    for column in ("bid", "ask", "point", "spread_usd_per_oz"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    valid = data["utc"].notna()
    for column in ("bid", "ask", "point", "spread_usd_per_oz"):
        valid &= data[column].map(lambda x: math.isfinite(x))
    valid &= (data.bid > 0) & (data.ask >= data.bid) & (data.point > 0)
    valid &= (data.ask - data.bid - data.spread_usd_per_oz).abs() <= 1e-7
    good = data.loc[valid].copy()

    def summary(group):
        spread = group.ask - group.bid
        return {
            "count": len(group), "median": float(spread.median()),
            "p75": float(spread.quantile(.75)), "p95": float(spread.quantile(.95)),
            "p99": float(spread.quantile(.99)), "max": float(spread.max()),
            "zero_count": int((spread == 0).sum()),
        } if len(group) else {"count": 0}

    return {
        "ask_origin": "observed_broker_quotes",
        "allowed_for_reference_cost_model": False,
        "status": "diagnostic_pending_clock_coverage_account_and_user_review",
        "weighting": "received ticks; OnTick can coalesce events; not time weighted",
        "clock": "workstation TimeGMT observation, not server timestamp conversion",
        "invalid_rows": int((~valid).sum()),
        "duplicate_rows": int(data.duplicated().sum()),
        "accounts": good[["broker", "account_mode", "symbol"]].drop_duplicates().to_dict("records"),
        "overall": summary(good),
        "hours_utc": {str(h): summary(g) for h, g in good.groupby(good.utc.dt.hour)},
        "days_utc": {str(d): summary(g) for d, g in good.groupby(good.utc.dt.date)},
        "missing_hours_utc": sorted(set(range(24)) - set(good.utc.dt.hour)),
    }
