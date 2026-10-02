"""Load operational definitions; missing configuration fails closed."""

from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from arty_trading.core.entities import Candle

CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"


@lru_cache(maxsize=16)
def _read_config(path: Path, modified: int) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Invalid configuration: {path}")
    return result


def load_config(name: str) -> dict[str, Any]:
    path = CONFIG_ROOT / name
    return deepcopy(_read_config(path, path.stat().st_mtime_ns))


def definitions() -> dict[str, Any]:
    import math

    cfg = load_config("definitions.yaml")
    positive = [
        ("swing", "window"),
        ("swing", "external_window"),
        ("swing", "confirmation_bars"),
        ("displacement", "confirmation_bars"),
        ("ob", "max_age_bars"),
        ("ob", "mitigation_lookback"),
        ("fvg", "max_age_bars"),
        ("sweep", "search_bars"),
    ]
    if not isinstance(cfg["atr_period"], int) or cfg["atr_period"] < 1:
        raise ValueError("ATR period must be a positive integer")
    if cfg["atr_warmup"] not in {"available_mean", "require_full"}:
        raise ValueError("Unknown ATR warmup policy")
    for section, key in positive:
        value = cfg[section][key]
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{section}.{key} must be a positive integer")
    for section, key in [
        ("displacement", "body_atr"),
        ("fvg", "gap_atr"),
        ("sweep", "penetration_atr"),
        ("sweep", "reintegration_bars"),
        ("equal_levels", "tolerance_atr"),
        ("ob", "max_size_atr"),
        ("ob", "max_mitigations"),
        ("sweep", "rejection_ratio"),
    ]:
        value = cfg[section][key]
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {section}.{key}")
    return cfg


def operational_atr(candles: list[Candle]) -> Decimal:
    """Wilder ATR; configured fallback for short detector windows."""

    from arty_trading.utils.helpers import calculate_atr

    cfg = definitions()
    atr = calculate_atr(candles, period=cfg["atr_period"])
    if atr > 0 or cfg["atr_warmup"] == "require_full":
        return atr
    ranges = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:])
    ]
    return sum(ranges, Decimal("0")) / len(ranges) if ranges else Decimal("0")


def effective_definitions(symbol: str) -> dict[str, Any]:
    cfg = definitions()
    for section, overrides in cfg.get("symbols", {}).get(symbol.upper(), {}).items():
        cfg[section].update(overrides)
    return cfg


def apply_operational_definitions(detector: Any, symbol: str) -> None:
    """Common live/backtest configuration; instrument profiles cannot override YAML."""
    cfg = effective_definitions(symbol)
    detectors = getattr(detector, "detectors", {})
    if not isinstance(detectors, dict):
        return
    params = {
        "liquidity": {
            "_min_rejection_ratio": cfg["sweep"]["rejection_ratio"],
            "_displacement_atr_mult": cfg["sweep"]["displacement_atr"],
        },
        "fair_value_gap": {"_min_gap_atr": cfg["fvg"]["gap_atr"]},
        "order_blocks": {
            "_max_ob_atr_mult": cfg["ob"]["max_size_atr"],
            "_displacement_confirmation_bars": cfg["displacement"]["confirmation_bars"],
        },
    }
    for name, values in params.items():
        sub = detectors.get(name)
        for attr, value in values.items():
            if sub is not None and hasattr(sub, attr):
                setattr(sub, attr, value)
