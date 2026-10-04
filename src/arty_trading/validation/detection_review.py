"""Dev-only detector review and pinned pre-refactor comparison; no trading simulation."""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd

from arty_trading.config.operational import (
    apply_operational_definitions,
    effective_definitions,
    load_config,
    operational_atr,
)
from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.smc.base import find_swing_points
from arty_trading.utils.helpers import calculate_atr
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.market_data import (
    index_utc,
    resample_closed_bars,
    safe_output,
    source_frames,
    write_report,
)

CATEGORIES = ("swing", "displacement", "FVG", "OB", "sweep", "EQH", "EQL")
CONCEPTS = {
    "fair_value_gap": "FVG",
    "order_block": "OB",
    "liquidity_sweep": "sweep",
    "equal_high": "EQH",
    "equal_low": "EQL",
    "break_of_structure": "BOS",
    "internal_bos": "internal_BOS",
    "external_bos": "external_BOS",
    "change_of_character": "CHoCH",
    "market_structure_shift": "MSS",
    "inverse_fvg": "IFVG",
    "breaker_block": "breaker",
    "mitigation_block": "mitigation",
}


def dev_candles(directory: Path, symbol: str) -> tuple[list[Candle], dict[str, Any]]:
    cfg = load_config("validation.yaml")
    split = load_config("split.yaml")
    frames, provenance = source_frames(directory, symbol)
    sides = {side: index_utc(raw, cfg["audit"]["timestamp_unit"]) for side, raw in frames.items()}
    if not sides["bid"].index.equals(sides["ask"].index):
        raise ValueError("Bid/ask timestamps differ; run audit_data before detector review")
    for side, frame in sides.items():
        sides[side] = frame.loc[
            (frame.index >= pd.Timestamp(split["development"]["start"]))
            & (frame.index < pd.Timestamp(split["development"]["end"]))
        ].copy()
    bid = sides["bid"]
    # Match the production reader's derived view, retaining immutable source hashes.
    unchanged = MarketCalendar().annotate(bid).non_tradable
    bid = bid.loc[~unchanged]
    if bid.empty:
        raise ValueError("No dev observations")
    timeframe = cfg["review"]["timeframe"]
    bars = resample_closed_bars(bid, cfg["audit"]["timeframes"][timeframe])
    candles = [
        Candle(
            symbol=symbol,
            timeframe=TimeFrame(timeframe),
            time=time.to_pydatetime(),
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=0,
        )
        for time, row in bars.iterrows()
    ]
    provenance["removed_non_tradable_m1_rows_in_derived_view"] = int(unchanged.sum())
    provenance["bars"] = len(candles)
    return candles, provenance


def legacy_detectors(revision: str) -> tuple[Any, Any, dict[str, str]]:
    """Import trusted repository sources from the pinned commit without changing the worktree."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Reference must be an exact Git commit hash")
    root = Path(__file__).resolve().parents[3]
    package_name = "arty_detection_reference"
    package = ModuleType(package_name)
    setattr(package, "__path__", [])
    sys.modules[package_name] = package
    hashes = {}
    for name in (
        "helpers",
        "settings",
        "base",
        "structure",
        "fair_value_gap",
        "order_blocks",
        "liquidity",
    ):
        relative = (
            f"utils/{name}.py"
            if name == "helpers"
            else f"config/{name}.py"
            if name == "settings"
            else f"modules/smc/{name}.py"
        )
        source = subprocess.check_output(
            ["git", "show", f"{revision}:src/arty_trading/{relative}"],
            cwd=root,
        )
        hashes[name] = hashlib.sha256(source).hexdigest()
        code = source.decode("utf-8").replace(
            "arty_trading.modules.smc.base", f"{package_name}.base"
        )
        code = code.replace("arty_trading.utils.helpers", f"{package_name}.helpers")
        module = ModuleType(f"{package_name}.{name}")
        sys.modules[module.__name__] = module
        exec(compile(code, f"git:{revision}/{name}.py", "exec"), module.__dict__)
    detectors = {
        "structure": sys.modules[f"{package_name}.structure"].StructureDetector(),
        "fair_value_gap": sys.modules[f"{package_name}.fair_value_gap"].FairValueGapDetector(),
        "order_blocks": sys.modules[f"{package_name}.order_blocks"].OrderBlockDetector(),
        "liquidity": sys.modules[f"{package_name}.liquidity"].LiquidityDetector(),
    }
    wrapper = type("ReferenceDetector", (), {"detectors": detectors})()
    # Preserve reference XAUUSD profile values using the frozen source's Settings.
    gold = sys.modules[f"{package_name}.settings"].GoldMarketConfig()
    detectors["fair_value_gap"]._min_gap_atr = gold.min_fvg_atr
    detectors["liquidity"]._min_rejection_ratio = gold.sweep_min_rejection_ratio
    detectors["liquidity"]._displacement_atr_mult = gold.sweep_displacement_atr_mult
    detectors["order_blocks"]._max_ob_atr_mult = gold.max_ob_atr_mult
    detectors["order_blocks"]._displacement_confirmation_bars = gold.displacement_confirmation_bars
    wrapper.reference_atr = sys.modules[f"{package_name}.helpers"].calculate_atr
    return wrapper, sys.modules[f"{package_name}.base"].find_swing_points, hashes


def event_key(event: dict[str, Any]) -> tuple[str, int, str, str]:
    return event["category"], event["origin_index"], event["direction"], str(event["price"])


def collect_events(
    candles: list[Candle], cfg: dict[str, Any], *, legacy: bool = False, progress: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    symbol = candles[0].symbol
    operational = effective_definitions(symbol)
    reference: dict[str, str] = {}
    if legacy:
        detector, swings_fn, reference = legacy_detectors(cfg["regression"]["reference_revision"])
    else:
        detector = SMCDetector()
        apply_operational_definitions(detector, symbol)
        swings_fn = find_swing_points
    events: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    window = cfg["review"]["window_bars"]
    step = window - cfg["review"]["overlap_bars"]
    if step <= 0:
        raise ValueError("Review window must exceed overlap")
    for batch, start in enumerate(range(0, len(candles), step)):
        view = candles[start : start + window]
        if len(view) < 5:
            continue
        local = []
        for swing in swings_fn(view):
            local.append(
                {
                    "category": "swing",
                    "direction": swing.type,
                    "price": float(swing.price),
                    "index": swing.index,
                    "details": {"strength": swing.strength, "window": swing.window},
                }
            )
        # Reference proxy: the old OB displacement criterion used a window-wide ATR.
        atr_fn = detector.reference_atr if legacy else calculate_atr
        old_atr = atr_fn(view, period=operational["atr_period"])
        if old_atr == 0:
            old_atr = sum((c.high - c.low for c in view), Decimal("0")) / len(view)
        for i, bar in enumerate(view):
            atr = old_atr if legacy else operational_atr(view[: i + 1])
            body = abs(bar.close - bar.open)
            threshold = atr * Decimal(str(operational["displacement"]["body_atr"]))
            qualifies = body >= threshold if legacy else body > threshold
            if atr > 0 and qualifies:
                local.append(
                    {
                        "category": "displacement",
                        "index": i,
                        "direction": "bullish" if bar.close > bar.open else "bearish",
                        "price": float(bar.close),
                        "details": {
                            "body": float(body),
                            "atr": float(atr),
                            "body_atr": float(body / atr),
                        },
                    }
                )
        # Call individual detectors directly: exceptions fail the audit instead of being swallowed.
        for name in ("structure", "fair_value_gap", "order_blocks", "liquidity"):
            for detection in detector.detectors[name].detect(view):
                item = detection.to_dict()
                item["category"] = CONCEPTS.get(item["concept"], item["concept"])
                local.append(item)
        for item in local:
            item["details"] = json.loads(
                json.dumps(item["details"], default=float, allow_nan=False)
            )
            origin = item.pop("index") + start
            item["origin_index"] = origin
            item["origin_time"] = candles[origin].time.isoformat()
            item["snapshot_end_index"] = start + len(view) - 1
            item["snapshot_end_time"] = view[-1].time.isoformat()
            item["snapshot_start_index"] = start
            for key, value in list(item["details"].items()):
                if key.endswith("_index") and isinstance(value, int):
                    item["details"][key] = value + start
            events.setdefault(event_key(item), item)
        if progress and batch % 50 == 0:
            print(
                f"{'reference' if legacy else 'current'} detector windows: {batch + 1}", flush=True
            )
    return list(events.values()), reference


def weekly_frequency(events: list[dict[str, Any]], candles: list[Candle]) -> pd.DataFrame:
    weeks = pd.date_range(
        pd.Timestamp(candles[0].time).normalize() - pd.Timedelta(days=candles[0].time.weekday()),
        pd.Timestamp(candles[-1].time),
        freq="7D",
    )
    counts: dict[tuple[str, str], int] = {}
    for event in events:
        if event["category"] not in CATEGORIES:
            continue
        date = datetime.fromisoformat(event["origin_time"])
        monday = (
            (pd.Timestamp(date).normalize() - pd.Timedelta(days=date.weekday())).date().isoformat()
        )
        key = monday, event["category"]
        counts[key] = counts.get(key, 0) + 1
    return pd.DataFrame(
        [
            {
                "week_utc": week.date().isoformat(),
                "type": category,
                "detections": counts.get((week.date().isoformat(), category), 0),
            }
            for week in weeks
            for category in CATEGORIES
        ]
    )


def plot_event(
    event: dict[str, Any], candles: list[Candle], cfg: dict[str, Any], path: Path
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    origin = event["origin_index"]
    start = max(0, origin - cfg["plot_before_bars"])
    end = min(len(candles), origin + cfg["plot_after_bars"] + 1)
    bars = candles[start:end]
    fig, axis = plt.subplots(figsize=(11, 5))
    for i, bar in enumerate(bars):
        color = "#17805c" if bar.close >= bar.open else "#c34242"
        axis.vlines(i, float(bar.low), float(bar.high), color=color, linewidth=0.8)
        height = float(abs(bar.close - bar.open))
        if height:
            axis.add_patch(
                Rectangle(
                    (i - 0.3, float(min(bar.open, bar.close))),
                    0.6,
                    height,
                    facecolor=color,
                    edgecolor=color,
                )
            )
        else:
            axis.hlines(float(bar.close), i - 0.3, i + 0.3, color=color)
    details = event["details"]
    top = details.get("gap_top", details.get("ob_top"))
    bottom = details.get("gap_bottom", details.get("ob_bottom"))
    if top is not None and bottom is not None:
        axis.axhspan(bottom, top, color="#5588cc", alpha=0.16)
    axis.axhline(event["price"], color="#555555", linestyle="--", linewidth=0.8)
    axis.axvline(origin - start, color="#2255aa", linestyle=":", label="origin bar")
    local_start = event["snapshot_start_index"]
    category = event["category"]
    atr_end = origin + 1
    if category == "sweep":
        deadline = effective_definitions(candles[0].symbol)["sweep"]["reintegration_bars"]
        for index in range(max(local_start, origin - deadline), origin + 1):
            extreme = float(
                candles[index].low if event["direction"] == "bullish" else candles[index].high
            )
            if extreme == details.get("sweep_low", details.get("sweep_high")):
                atr_end = index + 1
                break
    if category == "FVG":
        atr_end += 1  # third formation candle, not the FVG's middle-bar label
    if category in {"EQH", "EQL", "swing"}:
        atr_end += effective_definitions(candles[0].symbol)["swing"]["window"]
    atr = operational_atr(candles[local_start : min(atr_end, event["snapshot_end_index"] + 1)])
    if category == "displacement":
        atr = Decimal(str(details["atr"]))
    if category == "FVG" and details.get("gap_size_atr"):
        atr = Decimal(str(details["gap_size"])) / Decimal(str(details["gap_size_atr"]))
    if category in {"EQH", "EQL"}:
        fraction = effective_definitions(candles[0].symbol)["equal_levels"]["tolerance_atr"]
        if fraction:
            atr = Decimal(str(details["tolerance"])) / Decimal(str(fraction))
    bar = candles[origin]
    ratio = float(abs(bar.close - bar.open) / atr) if atr else None
    metric = f"body/ATR={ratio}"
    if category == "FVG":
        metric = f"gap/ATR={details.get('gap_size_atr')}"
    elif category == "OB" and atr:
        displacement_ratio = float(Decimal(str(details["displacement_size"])) / atr)
        metric = f"displacement/ATR={displacement_ratio:.3f}; fresh={details.get('fresh')}"
    elif category == "sweep" and atr:
        metric = f"penetration/ATR={float(Decimal(str(details['penetration'])) / atr):.3f}"
    axis.set_title(
        f"{event['category']} / {event['direction']} / {event['origin_time']}\n"
        f"ATR={float(atr):.4f}; {metric}\nsnapshot ends {event['snapshot_end_time']}",
        fontsize=10,
    )
    ticks = list(range(0, len(bars), max(1, len(bars) // 6)))
    axis.set_xticks(ticks, [bars[i].time.strftime("%m-%d %H:%M") for i in ticks], rotation=20)
    axis.set_ylabel(f"{candles[0].symbol} price")
    axis.set_xlabel("UTC / dev / no P&L")
    axis.grid(alpha=0.15)
    axis.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=cfg["dpi"])
    plt.close(fig)


def run_review(directory: Path, symbol: str, output: Path) -> dict[str, Any]:
    safe_output(directory, output)
    cfg = load_config("validation.yaml")
    candles, provenance = dev_candles(directory, symbol)
    events, _ = collect_events(candles, cfg, progress=True)
    rng = random.Random(cfg["review"]["seed"])
    selections = {}
    for category in CATEGORIES:
        candidates = [event for event in events if event["category"] == category]
        selected = rng.sample(candidates, min(cfg["review"]["samples_per_type"], len(candidates)))
        folder = output / category
        folder.mkdir(exist_ok=True)
        for i, event in enumerate(selected, 1):
            filename = f"{category}/{i:02d}.png"
            plot_event(event, candles, cfg["review"], output / filename)
            event["chart"] = filename
        selections[category] = {
            "available": len(candidates),
            "selected": selected,
            "shortfall": cfg["review"]["samples_per_type"] - len(selected),
        }
    frequency = weekly_frequency(events, candles)
    frequency.to_csv(output / "detection_frequency_weekly.csv", index=False)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "period": [candles[0].time.isoformat(), candles[-1].time.isoformat()],
        "config": cfg["review"],
        "definitions": effective_definitions(symbol),
        "provenance": provenance,
        "selections": selections,
        "human_review_status": "pending",
        "pnl_inspected": False,
        "method": "overlapping dev snapshots; unique origins, no live-signal or trading simulation",
    }
    write_report(output / "review_detections.json", report)
    lines = ["# Dev detector review", "", "No P&L. Random seed fixed. Human review pending.", ""]
    for category, data in selections.items():
        lines += [
            f"## {category}: {len(data['selected'])} charts, shortfall {data['shortfall']}",
            "",
        ]
        lines += [f"![{category}]({event['chart']})\n" for event in data["selected"]]
    (output / "index.md").write_text("\n".join(lines), encoding="utf-8")
    return report
