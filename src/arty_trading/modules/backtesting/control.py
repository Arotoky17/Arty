"""Real-history H4/H1/M5 replay with live risk guards and observational OB audits."""

from __future__ import annotations

import asyncio
import logging
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, cast

import pandas as pd
import yaml

from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.config.settings import OBQualitySettings, RiskSettings, Settings
from arty_trading.core.entities import Candle, Signal, Trade, TradingAccount
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.core.interfaces import IMarketDataProvider
from arty_trading.modules.backtesting.control_audit import audit_violations, validate_report
from arty_trading.modules.backtesting.control_history import digest, obtain_history
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.risk.manager import RiskManager
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SetupTracker, SMCDetector
from arty_trading.modules.smc.confirmation import M5ConfirmationChecker
from arty_trading.modules.smc.order_block_quality import OrderBlockQualityScorer
from arty_trading.modules.smc.order_block_tracker import TrackedOB
from arty_trading.modules.strategies import SMCTrendStrategy
from arty_trading.utils.helpers import get_pip_size


def candles_frame(candles: list[Candle]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "timestamp": c.time,
            }
            for c in candles
        ]
    )


class ReplayDetector:
    """Reuse detections only when an immutable closed candle window is unchanged."""

    def __init__(self) -> None:
        self.inner = SMCDetector()
        self.cache: dict[TimeFrame, tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}
        self.ob_seen: set[tuple[Any, ...]] = set()

    async def detect(self, candles: list[Candle], symbol: str) -> list[dict[str, Any]]:
        if not candles:
            return []
        timeframe = candles[-1].timeframe
        key = (symbol, candles[0].time, candles[-1].time, len(candles))
        cached = self.cache.get(timeframe)
        if cached is None or cached[0] != key:
            cached = (key, await self.inner.detect(candles, symbol))
            self.cache[timeframe] = cached
            for detection in cached[1]:
                if detection.get("concept") == "order_block":
                    index = detection.get("index")
                    if isinstance(index, int) and 0 <= index < len(candles):
                        self.ob_seen.add(
                            (
                                symbol,
                                timeframe.value,
                                candles[index].time,
                                detection.get("direction"),
                            )
                        )
        # Builder/strategy consumers only read detections. Its own H1/H4 caches
        # also retain them by reference; copying every nested value is unnecessary.
        return cached[1]


class ReplayQuotes:
    """Only the historical quote/specification methods used by RiskManager."""

    def __init__(self, broker: dict[str, Any]) -> None:
        self.broker = broker
        self.spread = 0

    async def get_spread(self, symbol: str) -> int:
        return self.spread

    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        point = float(self.broker["point"])
        return {
            "trade_tick_size": point,
            "trade_tick_value": point * float(self.broker["contract_size"]),
        }


class ControlReplay:
    def __init__(
        self,
        config: dict[str, Any],
        history: dict[TimeFrame, list[Candle]],
        quotes: list[dict[str, Any]] | None = None,
    ) -> None:
        from arty_trading.config.operational import load_config
        from arty_trading.modules.execution.fill_model import FillModel
        from arty_trading.validation.split import DataSplit
        from arty_trading.validation.trial_registry import TrialRegistry

        self.registry = TrialRegistry(config.get("trial_registry"))
        self.split = DataSplit(self.registry)
        self.fill_model = FillModel(**load_config("execution.yaml")["fill"])
        self.fill_rng = random.Random(self.fill_model.seed)
        self.config = config
        self.history = history
        self.ask_quotes = {
            datetime.fromtimestamp(row["time"], UTC): row
            for row in (quotes or [])
            if "ask_close" in row
        }
        self.broker = config["broker"]
        from arty_trading.modules.execution.cost_model import CostModel

        # Actual bid/ask spread is charged by historical quotes, never twice.
        self.cost_model = CostModel(
            spread_pips=0,
            slippage_pips=(
                float(self.broker["slippage_points"])
                * float(self.broker["point"])
                / float(get_pip_size(config["symbol"]))
            ),
            commission_per_lot_side=float(self.broker["commission_per_lot_round_trip"]) / 2,
        )
        self._authorized_history: dict[TimeFrame, tuple[str, ...]] | None = None
        self.balance = Decimal(str(config["initial_balance"]))
        self.equity = self.balance
        self.peak = self.balance
        self.drawdown = 0.0
        self.position: Trade | None = None
        self._current_candle: Candle | None = None
        self._cost_total = 0.0
        self._stress_equity: list[float] = [float(self.balance)]
        self._stress_costs: list[float] = [0.0]
        self.cost_sensitivity: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.rejections: Counter[str] = Counter(
            {
                "circuit_breaker:consecutive_losses": 0,
                "circuit_breaker:daily_loss": 0,
                "circuit_breaker:drawdown": 0,
                "validator:spread_acceptable": 0,
                "validator:session_authorized": 0,
                "confidence": 0,
            }
        )
        self.signals_generated = 0
        self.replay_time = datetime(1970, 1, 1, tzinfo=UTC)
        self.quotes = ReplayQuotes(self.broker)
        environment: dict[str, Any] = {"_env_file": None}
        risk_settings = RiskSettings(**environment, **config["risk"])
        self.settings = Settings(
            **environment,
            risk=risk_settings,
            ob_quality=OBQualitySettings(
                **environment,
                USE_OB_QUALITY_FILTER=False,
                OB_QUALITY_ENABLED=False,
            ),
        )
        self.risk = RiskManager(
            risk_settings,
            min_confidence=config["min_confidence"],
            min_risk_reward=config["min_risk_reward"],
            market_data=cast(IMarketDataProvider, self.quotes),
            clock=lambda: self.replay_time,
        )
        self.risk.update_equity(self.balance)
        self.tracker = SetupTracker()
        self.detector = ReplayDetector()
        self.generator = SignalGenerator(
            min_confidence=config["min_confidence"],
            active_strategy="SMC Trend Following",
            strategies=[SMCTrendStrategy(use_ob_quality_filter=False)],
            validator=SignalValidator(min_risk_reward=config["min_risk_reward"], max_spread=200),
            decision_engine=(
                DecisionEngine(self.settings.decision) if self.settings.decision.enabled else None
            ),
            setup_tracker=self.tracker,
            ob_quality=self.settings.ob_quality,
            use_ob_quality_filter=False,
        )
        self.current_h4: list[Candle] = []

        async def macro(symbol: str, tf: TimeFrame) -> list[Candle]:
            return self.current_h4

        self.builder = MarketContextBuilder(self.detector, macro)

    def ask_price(self, candle: Candle, field: str) -> Decimal:
        quote = self.ask_quotes.get(candle.time)
        if quote is not None:
            return Decimal(str(quote[f"ask_{field}"]))
        return Decimal(str(getattr(candle, field))) + Decimal(candle.spread) * Decimal(
            str(self.broker["point"])
        )

    def close_position(self, price: Decimal, time: datetime) -> None:
        trade = self.position
        assert trade is not None
        side = 1 if trade.direction == Direction.BUY else -1
        slip = Decimal(str(self.cost_model.slippage_pips)) * get_pip_size(trade.symbol)
        exit_price = price - side * slip
        profit = (exit_price - trade.entry_price) * side * trade.volume * Decimal(
            str(self.broker["contract_size"])
        ) - trade.volume * Decimal(str(2 * self.cost_model.commission_per_lot_side))
        self.balance += profit
        self.risk.close_trade(trade, profit)
        audit = self.audits[-1]
        entry_spread = audit.get("entry_spread_price", 0.0)
        exit_spread = entry_spread
        if self._current_candle is not None:
            exit_spread = float(
                self.ask_price(self._current_candle, "close") - self._current_candle.close
            )
        roundtrip_cost = float(trade.volume) * (
            ((entry_spread + exit_spread) / 2 + 2 * float(slip))
            * float(self.broker["contract_size"])
            + 2 * self.cost_model.commission_per_lot_side
        )
        self._cost_total += roundtrip_cost
        audit.update(
            {
                "exit_timestamp": time.isoformat(),
                "exit_price": float(exit_price),
                "profit": float(profit),
                "roundtrip_cost_usd": roundtrip_cost,
                "r_multiple": float(profit) / audit["initial_risk_usd"],
                "pips": float((exit_price - trade.entry_price) * side / get_pip_size(trade.symbol)),
            }
        )
        self.position = None

    def mark_equity(self, candle: Candle) -> None:
        floating = Decimal(0)
        if self.position is not None:
            trade = self.position
            side = 1 if trade.direction == Direction.BUY else -1
            price = candle.close if side == 1 else self.ask_price(candle, "close")
            floating = (
                (price - trade.entry_price)
                * side
                * trade.volume
                * Decimal(str(self.broker["contract_size"]))
            )
        self.equity = self.balance + floating
        self.peak = max(self.peak, self.equity)
        self.drawdown = max(self.drawdown, float((self.peak - self.equity) / self.peak))
        self.risk.update_equity(self.equity)
        self._stress_equity.append(float(self.equity))
        self._stress_costs.append(self._cost_total)

    def check_exit(self, candle: Candle) -> None:
        if self.position is None:
            return
        trade = self.position
        buy = trade.direction == Direction.BUY
        high = candle.high if buy else self.ask_price(candle, "high")
        low = candle.low if buy else self.ask_price(candle, "low")
        open_price = candle.open if buy else self.ask_price(candle, "open")
        if (buy and low <= trade.stop_loss) or (not buy and high >= trade.stop_loss):
            price = min(trade.stop_loss, open_price) if buy else max(trade.stop_loss, open_price)
            self.close_position(price, candle.time)
        elif (buy and high >= trade.take_profit) or (not buy and low <= trade.take_profit):
            self.close_position(trade.take_profit, candle.time)

    def audit_signal(
        self,
        signal: Signal,
        context: Any,
        m5: list[Candle],
        time: datetime,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "entry_timestamp": time.isoformat(),
            "ob_timestamp": None,
            "confirmation_timestamp": None,
            "ob_low": None,
            "ob_high": None,
            "window_start": None,
            "window_end": None,
            "window_timestamps": [],
            "rejection_candle_low": float(m5[-1].low) if m5 else None,
            "rejection_candle_high": float(m5[-1].high) if m5 else None,
            "grade": "UNKNOWN",
            "confirmation_valid": False,
        }
        setup_id = signal.metadata.get("setup_id")
        setup = self.tracker.get_setup_by_id(setup_id) if setup_id else None
        record["signal_source"] = "setup_tracker" if setup is not None else "legacy_strategy"
        record["zone_concept"] = setup.zone_concept if setup is not None else None
        record["smc_concepts"] = list(signal.smc_concepts)
        if setup is None or setup.zone_concept != "order_block":
            record["audit_reason"] = "missing_associated_order_block"
            return record
        if setup.zone_low is None or setup.zone_high is None:
            return record
        source = next((c for c in context.setup_candles if c.time == setup.created_at), None)
        ob = TrackedOB.create(
            symbol=signal.symbol,
            timeframe="H1",
            direction="bullish" if signal.direction == Direction.BUY else "bearish",
            low=setup.zone_low,
            high=setup.zone_high,
            created_at=setup.created_at + timedelta(hours=1),
        )
        after = [c for c in m5 if c.time > ob.created_at]
        frame = candles_frame(after)
        checker = M5ConfirmationChecker(True, True, True)
        result = checker.check(
            ob,
            frame,
            {"events": context.ltf_smc_data},
            reference_timestamp=pd.Timestamp(ob.created_at),
        )
        detail = result.details.get(result.type.value, {}) if result.type else {}
        confirmation = detail.get("event_timestamp", detail.get("break_timestamp"))
        if result.type and result.type.value == "rejection_candle" and after:
            confirmation = after[-1].time.isoformat()
        record.update(
            {
                "ob_origin_timestamp": setup.created_at.isoformat(),
                "ob_timestamp": ob.created_at.isoformat(),
                "ob_low": ob.low,
                "ob_high": ob.high,
                "confirmation_timestamp": confirmation,
                "confirmation_valid": result.confirmed,
                "confirmation_type": result.type.value if result.type else None,
                "window_start": after[0].time.isoformat() if after else None,
                "window_end": after[-1].time.isoformat() if after else None,
                "window_timestamps": [c.time.isoformat() for c in after],
                "rejection_candle_low": float(after[-1].low) if after else None,
                "rejection_candle_high": float(after[-1].high) if after else None,
            }
        )
        for detail in result.details.values():
            if isinstance(detail, dict) and detail.get("reason"):
                self.rejections[detail["reason"]] += 1
        if not result.confirmed:
            self.rejections[result.details["reason"]] += 1
        if source is not None:
            from arty_trading.utils.helpers import calculate_atr

            quality = OrderBlockQualityScorer().score(
                candles_frame([source]).iloc[0],
                candles_frame(after),
                float(calculate_atr(m5)),
                context.h4_smc_data,
                [],
                [],
                [],
            )
            record["grade"] = quality.grade.value
        return record

    async def process(self, candle: Candle, views: dict[TimeFrame, list[Candle]]) -> None:
        self.current_h4 = views[TimeFrame.H4]
        m5 = views[TimeFrame.M5]
        context = await self.builder.build(
            candle.symbol, views[TimeFrame.H1], m5, setup_tf_candles=views[TimeFrame.H1]
        )
        if context is None or context.is_neutral() or context._regime_blocks_trade():
            self.rejections["neutral_or_blocked_regime"] += 1
            return
        update_setups_from_market_context(self.tracker, candle.symbol, context, self.settings)
        signal = await self.generator.generate(
            m5,
            context.ltf_smc_data,
            htf_smc_data=context.htf_smc_data,
            htf_trend=context.master_trend,
            htf_trends={"H1": context.master_trend, "H4": context.h4_trend},
            master_trend=context.master_trend,
            market_context=context,
            spread=candle.spread,
        )
        if signal is None:
            self.rejections[self.generator.last_rejection_reason or "no_signal"] += 1
            if self.generator.last_rejection_stage == "confidence":
                self.rejections["confidence"] += 1
            validation = self.generator.last_validation
            if self.generator.last_rejection_reason == "validator_rejected" and validation:
                for reason in validation.failed_conditions:
                    self.rejections[f"validator:{reason}"] += 1
            return
        self.signals_generated += 1
        # Control replay submits market orders, so touch/cross is inapplicable.
        if self.fill_rng.random() >= self.fill_model.probability:
            self.rejections["unfilled_market_order"] += 1
            return
        side = 1 if signal.direction == Direction.BUY else -1
        entry_price = self.ask_price(candle, "close") if side == 1 else candle.close
        entry_price += (
            side * Decimal(str(self.cost_model.slippage_pips)) * get_pip_size(candle.symbol)
        )
        signal = signal.model_copy(update={"entry_price": entry_price})
        if (signal.entry_price - signal.stop_loss) * side <= 0 or (
            signal.take_profit - signal.entry_price
        ) * side <= 0:
            self.rejections["invalid_execution_levels"] += 1
            return
        account = TradingAccount(
            login=0,
            server="historical-replay",
            balance=self.balance,
            equity=self.equity,
            free_margin=self.equity,
        )
        if not await self.risk.can_open_trade(candle.symbol) or not await self.risk.validate_signal(
            signal, account
        ):
            self.rejections[f"risk:{self.risk.last_rejection_reason or 'position_limit'}"] += 1
            return
        raw_volume = await self.risk.calculate_position_size(signal, account)
        step = Decimal(str(self.broker["volume_step"]))
        volume = (Decimal(str(raw_volume)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        if volume < Decimal(str(self.broker["volume_min"])):
            self.rejections["volume_below_minimum"] += 1
            return
        record = self.audit_signal(signal, context, m5, candle.time + timedelta(minutes=5))
        risk = (
            abs(signal.entry_price - signal.stop_loss)
            * volume
            * Decimal(str(self.broker["contract_size"]))
        )
        record.update(
            {
                "entry_price": float(signal.entry_price),
                "initial_risk_usd": float(risk),
                "direction": signal.direction.value,
                "entry_spread_price": float(self.ask_price(candle, "close") - candle.close),
            }
        )
        self.audits.append(record)
        trade = Trade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=volume,
        )
        self.position = trade
        self.risk.register_trade(trade)
        setup_id = signal.metadata.get("setup_id")
        setup = self.tracker.get_setup_by_id(setup_id) if setup_id else None
        if setup is not None:
            self.tracker.mark_consumed(setup, reason="trade_executed")

    def _history_fingerprint(self) -> dict[TimeFrame, tuple[str, ...]]:
        return {tf: tuple(c.model_dump_json() for c in batch) for tf, batch in self.history.items()}

    def load_holdout(
        self, setup_id: str, reason: str, **kwargs: Any
    ) -> dict[TimeFrame, list[Candle]]:
        if setup_id != self.config.get("setup_id", "legacy_control"):
            raise ValueError("Setup does not match replay")
        self.history = self.split.load_holdout(setup_id, reason, **kwargs)
        if not isinstance(self.history, dict):
            raise ValueError("Control replay requires timeframe mapping")
        self._authorized_history = self._history_fingerprint()
        return self.history

    async def run(self) -> None:
        from arty_trading.config.operational import load_config
        from arty_trading.modules.backtesting.stats import BacktestStats
        from arty_trading.validation.cost_sensitivity import cost_sensitivity
        from arty_trading.validation.preregistration import assert_setup_run_allowed

        assert_setup_run_allowed(self.config.get("setup_id", "legacy_control"))

        if self._authorized_history is None:
            for batch in self.history.values():
                self.split.assert_development(batch)
            partition = "dev"
        else:
            if self._authorized_history != self._history_fingerprint():
                raise ValueError("Authorized history changed")
            partition = "holdout"
            self._authorized_history = None
        bars = self.history.get(TimeFrame.M5, [])
        trial_id = self.registry.begin(
            self.config.get("setup_id", "legacy_control"),
            self.config,
            "M5",
            bars[0].time.isoformat() if bars else None,
            bars[-1].time.isoformat() if bars else None,
            partition,
        )
        await self._run_replay()
        self.cost_sensitivity = cost_sensitivity(
            [
                (row["profit"], row.get("roundtrip_cost_usd", 0.0), row["initial_risk_usd"])
                for row in self.audits
            ],
            self._stress_equity,
            self._stress_costs,
            load_config("execution.yaml")["cost"]["sensitivity_multipliers"],
        )
        profits = [row["profit"] for row in self.audits]
        gains = sum(p for p in profits if p > 0)
        losses = -sum(p for p in profits if p < 0)
        results = [row["r_multiple"] for row in self.audits]
        stats = BacktestStats(
            total_trades=len(profits),
            profit_factor=gains / losses if losses else 0.0,
            max_drawdown=self.drawdown,
            fill_rate=len(profits) / self.signals_generated if self.signals_generated else 0.0,
        )
        # Trade-level Sharpe, unannualized (same frequency as registry R outcomes).
        if len(results) > 1:
            import statistics

            std = statistics.stdev(results)
            stats.sharpe_ratio = statistics.mean(results) / std if std else 0.0
        self.registry.finish(trial_id, stats, sum(results) / len(results) if results else 0.0)

    async def _run_replay(self) -> None:
        indices = {TimeFrame.H1: 0, TimeFrame.H4: 0}
        durations = {TimeFrame.H1: timedelta(hours=1), TimeFrame.H4: timedelta(hours=4)}
        previous_day = None
        for i, candle in enumerate(self.history[TimeFrame.M5]):
            self._current_candle = candle
            if i and i % 1000 == 0:
                print(f"Replay: {i} M5 bars; {len(self.audits)} trades", file=sys.stderr)
            close = candle.time + timedelta(minutes=5)
            self.replay_time = close
            if previous_day != close.date():
                self.risk.reset_daily()
                previous_day = close.date()
            self.quotes.spread = candle.spread
            self.check_exit(candle)
            self.mark_equity(candle)
            if self.position is None:
                state = self.risk.get_risk_report()
                if (
                    state["consecutive_loss_breaker_active"]
                    or self.risk.current_drawdown >= self.settings.risk.max_drawdown
                    or Decimal(state["daily_loss"])
                    >= (self.balance * Decimal(str(self.settings.risk.max_daily_risk)))
                ):
                    self.rejections["risk_circuit_breaker"] += 1
                    if state["consecutive_loss_breaker_active"]:
                        self.rejections["circuit_breaker:consecutive_losses"] += 1
                    if self.risk.current_drawdown >= self.settings.risk.max_drawdown:
                        self.rejections["circuit_breaker:drawdown"] += 1
                    if Decimal(state["daily_loss"]) >= (
                        self.balance * Decimal(str(self.settings.risk.max_daily_risk))
                    ):
                        self.rejections["circuit_breaker:daily_loss"] += 1
                    continue
            views = {
                TimeFrame.M5: self.history[TimeFrame.M5][
                    max(0, i + 1 - self.config["windows"]["M5"]) : i + 1
                ]
            }
            for tf, duration in durations.items():
                bars = self.history[tf]
                while indices[tf] < len(bars) and bars[indices[tf]].time + duration <= close:
                    indices[tf] += 1
                views[tf] = bars[
                    max(0, indices[tf] - self.config["windows"][tf.value]) : indices[tf]
                ]
            if (
                len(views[TimeFrame.H1]) >= 30
                and len(views[TimeFrame.H4]) >= 5
                and len(views[TimeFrame.M5]) >= 20
            ):
                if self.position is None:
                    await self.process(candle, views)
                    self.tracker.cleanup(candle.symbol)
        last = self.history[TimeFrame.M5][-1]
        if self.position is not None:
            price = (
                last.close
                if self.position.direction == Direction.BUY
                else self.ask_price(last, "close")
            )
            self.close_position(price, last.time + timedelta(minutes=5))
        self.mark_equity(last)


async def run_control(
    symbol: str,
    date_from: str,
    date_to: str,
    config_path: Path,
    output: Path,
    source: str | None = None,
) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if source is not None:
        config["source"] = source
    if config.get("use_ob_quality_filter") is not False or config.get("audit") is not True:
        raise ValueError("Control requires the legacy quality filter off and audit enabled")
    if symbol != config["symbol"] or symbol != "XAUUSD":
        raise ValueError("Control configuration and requested symbol must be XAUUSD")
    start = datetime.fromisoformat(date_from).replace(tzinfo=UTC)
    end = datetime.fromisoformat(date_to).replace(tzinfo=UTC) + timedelta(days=1)
    if end <= start:
        raise ValueError("Invalid period")
    raw, history = await asyncio.to_thread(obtain_history, config, symbol, start, end)
    if raw.get("broker"):
        config["broker"].update(raw["broker"])
    logging.getLogger("arty_trading").setLevel(logging.WARNING)
    replay = ControlReplay(config, history, raw["candles"]["M5"])
    config["effective_settings"] = replay.settings.model_dump(
        mode="json",
        include={"risk", "decision", "ob_quality", "gold", "validator"},
    )
    await replay.run()
    print(
        f"Replay diagnostics: OB={len(replay.detector.ob_seen)}; "
        f"signals={replay.signals_generated}; trades={len(replay.audits)}; "
        f"rejections={dict(replay.rejections)}",
        file=sys.stderr,
    )
    profits = [row["profit"] for row in replay.audits]
    gains = sum(value for value in profits if value > 0)
    losses = -sum(value for value in profits if value < 0)
    count = len(profits)
    report = {
        "period": f"{date_from} to {date_to}",
        "symbol": symbol,
        "seed": config["seed"],
        "data_source": raw["source"],
        "data_provenance": raw.get("provenance", {}),
        "volume_available": raw.get("volume_available", True),
        "removed_unchanged_flat_minutes": raw.get("removed_unchanged_flat_minutes", 0),
        "data_hash": digest(raw),
        "config_hash": digest(config),
        "effective_config": config,
        "total_trades": count,
        "signals_generated": replay.signals_generated,
        "trades_executed": count,
        "legacy_ob_detected": len(replay.detector.ob_seen),
        "legacy_ob_count_scope": "unique timeframe/origin/direction in reached detector windows",
        "signal_count_scope": "signals returned by generator, before execution and risk guards",
        "rejection_count_scope": (
            "reached stages only; circuit-breaker counts are blocked M5 bars; "
            "validator subreasons can overlap; confirmation reasons are observational audits"
        ),
        "win_rate": sum(value > 0 for value in profits) / count if count else 0,
        "profit_factor": gains / losses if losses else None,
        "expectancy_r": sum(row["r_multiple"] for row in replay.audits) / count if count else None,
        "cost_sensitivity": replay.cost_sensitivity,
        "cost_sensitivity_assumptions": {
            "method": "fixed_path_repricing",
            "spread": "historical bid/ask half spread at each side; not synthetic session spread",
            "news": "historical quotes retain realized event spread; no synthetic extra markup",
        },
        "max_drawdown_pct": replay.drawdown * 100,
        "avg_pips_per_trade": sum(row["pips"] for row in replay.audits) / count if count else None,
        "trades_par_grade": dict(Counter(row["grade"] for row in replay.audits)),
        "rejections_by_reason": {
            "no_contact_with_ob": 0,
            "insufficient_candles": 0,
            "choch_out_of_bounds": 0,
            **dict(replay.rejections),
        },
        "audit_samples": replay.audits[:5],
        "audit_trades": replay.audits,
        "audit_violations": audit_violations(replay.audits),
        "audit_coverage": {
            "total_trades": count,
            "trades_with_ob_evidence": sum(
                row["ob_timestamp"] is not None for row in replay.audits
            ),
            "trades_without_ob_evidence": sum(row["ob_timestamp"] is None for row in replay.audits),
        },
        "execution_assumptions": {
            "broker": config["broker"],
            "intrabar": "SL before TP",
            "swap": "not modelled",
            "historical_news": "not available",
        },
        "quality_filter": False,
        "final_risk_state": replay.risk.get_risk_report(),
        "audit_mode": "observational; does not remove legacy trades",
        "confirmation_diagnostics_scope": "executed trades with associated OB evidence",
    }
    try:
        validate_report(report, config["minimum_trades"])
        report["validation_status"] = "passed"
    except ValueError as exc:
        report["validation_status"] = "failed"
        report["validation_error"] = str(exc)
    import json

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return report
