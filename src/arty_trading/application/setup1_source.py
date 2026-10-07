"""One causal Setup 1 signal implementation for closed M5 bars, live and replay."""

from decimal import Decimal

from arty_trading.config.operational import effective_definitions, load_config, operational_atr
from arty_trading.core.entities import Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.smc.base import find_swing_points


class Setup1Source:
    def __init__(self, detector=None):
        self.detector = detector or SMCDetector()
        self.rules = load_config("setup1_preregistration.yaml")["strategy"]
        self.cfg = effective_definitions("XAUUSD")
        self.detections = []
        self.swings = []
        self.active_zones = []
        self.last_reason = "warmup"
        self.opposite_break = None
        self.seen = set()

    async def evaluate(self, candles):
        candles = candles[-500:]  # same bounded causal window in live and Setup 1 replay
        self.opposite_break = None
        self.last_reason = "no_current_bos"
        if len(candles) < self.cfg["atr_period"] + 1:
            self.last_reason = "atr_warmup"
            return None
        # Detector indices remain relative to this immutable, fully closed window.
        self.detections = await self.detector.detect(candles, "XAUUSD")
        self.swings = find_swing_points(candles)
        n = len(candles) - 1
        breaks = [
            d
            for d in self.detections
            if d["index"] == n
            and d["concept"]
            in {
                "external_bos",
                "break_of_structure",
                "change_of_character",
                "market_structure_shift",
            }
        ]
        if breaks:
            self.opposite_break = breaks[-1]["direction"]
        zones = []
        for d in self.detections:
            concept, i, details = d["concept"], d["index"], d.get("details", {})
            if concept not in {"order_block", "fair_value_gap"}:
                continue
            if not 0 <= i < len(candles):
                continue
            if concept == "order_block":
                confirmation = i + self.cfg["displacement"]["confirmation_bars"]
                low, high = details.get("ob_bottom"), details.get("ob_top")
                max_age = self.cfg["ob"]["max_age_bars"]
                origin = i
            else:
                # FVG index is its middle candle; final third candle must close.
                confirmation = i + 1
                low, high = details.get("gap_bottom"), details.get("gap_top")
                max_age = self.cfg["fvg"]["max_age_bars"]
                origin = max(0, i - 1)
            if confirmation > n or n - origin > max_age or low is None or high is None:
                continue
            low, high = Decimal(str(low)), Decimal(str(high))
            if high <= low:
                continue
            after = candles[confirmation + 1 :]
            bullish = d["direction"] == "bullish"
            if concept == "order_block":
                stale = any(c.low <= high and c.high >= low for c in after)
            else:
                stale = any(c.low <= low if bullish else c.high >= high for c in after)
            if stale:
                continue
            zones.append(
                {
                    "concept": concept,
                    "direction": d["direction"],
                    "index": i,
                    "confirmation": confirmation,
                    "low": low,
                    "high": high,
                    "origin_time": candles[origin].time,
                }
            )
        self.active_zones = zones
        bos = next(
            (
                d
                for d in reversed(breaks)
                if d["concept"] in {"external_bos", "break_of_structure"}
                and d.get("details", {}).get("swing_strength") == "external"
                and d.get("details", {}).get("displacement") is True
            ),
            None,
        )
        if bos is None:
            return None
        key = (candles[-1].time, bos["direction"])
        if key in self.seen:
            self.last_reason = "bos_already_considered"
            return None
        self.seen.add(key)
        atr = operational_atr(candles)
        if atr <= 0:
            self.last_reason = "atr_warmup"
            return None
        same = [z for z in zones if z["direction"] == bos["direction"]]
        selected = None
        for name in self.rules["zone_priority"]:
            concept = {"OB": "order_block", "FVG": "fair_value_gap"}[name]
            candidates = [z for z in same if z["concept"] == concept]
            if candidates:
                selected = max(candidates, key=lambda z: (z["confirmation"], z["index"], -z["low"]))
                break
        if selected is None:
            self.last_reason = "no_fresh_confirmed_zone"
            return None
        buy = bos["direction"] == "bullish"
        side = Decimal(1 if buy else -1)
        entry = selected["low"] + (selected["high"] - selected["low"]) * Decimal(
            str(self.rules["entry_zone_fraction"])
        )
        # Submit at BOS close; first subsequent touch/cross is handled by FillModel.
        if (buy and entry >= candles[-1].close) or (not buy and entry <= candles[-1].close):
            self.last_reason = "limit_on_wrong_side_of_close"
            return None
        edge = selected["low"] if buy else selected["high"]
        stop = edge - side * atr * Decimal(str(self.rules["stop_buffer_atr"]))
        distance = abs(entry - stop)
        if distance < atr * Decimal(str(self.cfg["execution_constraints"]["minimum_stop_atr"])):
            self.last_reason = "minimum_stop_atr"
            return None
        self.last_reason = "setup1_limit_candidate"
        return Signal(
            symbol="XAUUSD",
            timeframe=TimeFrame.M5,
            signal_type=SignalType.BUY if buy else SignalType.SELL,
            direction=Direction.BUY if buy else Direction.SELL,
            entry_price=entry,
            stop_loss=stop,
            take_profit=entry + side * distance * Decimal(str(self.rules["reward_risk"])),
            confidence=1,
            strategy_name="Setup 1 BOS retest OB/FVG",
            created_at=candles[-1].available_at or candles[-1].time,
            metadata={
                "atr_at_bos": float(atr),
                "zone_concept": selected["concept"],
                "zone_origin": selected["origin_time"].isoformat(),
                "bos_time": candles[-1].time.isoformat(),
            },
        )
