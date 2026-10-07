"""Live plumbing observation, no trial registry, no validation verdict or replay run."""

import hashlib
import json
import math
import random
import sqlite3
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arty_trading.application.setup1_source import Setup1Source
from arty_trading.config.operational import load_config
from arty_trading.core.enums import Direction
from arty_trading.forward.freeze import code_sha256
from arty_trading.forward.kill_switch import KillSwitch
from arty_trading.forward.observation_mt5 import MAGIC
from arty_trading.forward.runner import Setup1Policy
from arty_trading.forward.sizing import size_from_risk
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.fill_model import FillModel
from arty_trading.modules.execution.setup1_policy import closed_bar_exit
from arty_trading.validation.market_calendar import MarketCalendar
from arty_trading.validation.preregistration import configuration_sha256


def observation_cost_model():
    """Frozen development spread snapshot for plumbing, NOT a validated live model.

    The reference model correctly rejects 2026 without annual calibrations. This
    explicit observational snapshot preserves its price/hour calculation without
    pretending those missing calibrations exist or modifying reference config.
    """
    cfg = load_config("execution.yaml")["cost"]
    reference = CostModel(**cfg)
    return CostModel(
        **{
            **cfg,
            "spread_mode": "configured",
            "spread_calibration_path": None,
            "annual_calibration_paths": {},
            "normalized_hours": reference.normalized_hours,
        }
    )


class ObservationJournal:
    def __init__(self, path):
        path = Path(path)
        raw = Path("data/raw").resolve()
        config = Path("config").resolve()
        if (
            path.suffix not in {".sqlite", ".db"}
            or raw in path.resolve().parents
            or config in path.resolve().parents
        ):
            raise ValueError("Journal requires .sqlite/.db outside data/raw and config")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observation_runs "
            "(id TEXT PRIMARY KEY, plumbing_only INTEGER NOT NULL, manifest TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observation_events "
            "(id INTEGER PRIMARY KEY, run_id TEXT, utc TEXT, kind TEXT, "
            "plumbing_only INTEGER NOT NULL, payload TEXT)"
        )
        self.run_id = uuid.uuid4().hex

    def start(self, manifest):
        self.db.execute(
            "INSERT INTO observation_runs VALUES (?,1,?)",
            (self.run_id, json.dumps(manifest, default=str, sort_keys=True)),
        )
        self.db.commit()

    def write(self, kind, now, **payload):
        self.db.execute(
            "INSERT INTO observation_events "
            "(run_id,utc,kind,plumbing_only,payload) VALUES (?,?,?,1,?)",
            (self.run_id, now.isoformat(), kind, json.dumps(payload, default=str, sort_keys=True)),
        )
        self.db.commit()

    def close(self):
        self.db.close()


class ObservationSession:
    def __init__(self, broker, journal, *, clock=None, source=None, costs=None, fill=None):
        self.broker = broker
        self.journal = journal
        self.clock = clock or (lambda: datetime.now(UTC))
        self.source = source or Setup1Source()
        self.costs = costs or observation_cost_model()
        self.fill = fill or FillModel(**load_config("execution.yaml")["fill"])
        self.policy = Setup1Policy.load()
        self.cfg = load_config("forward_demo.yaml")
        self.calendar = MarketCalendar()
        self.kill = KillSwitch(self.cfg, self.clock)
        self.kill.start(10000)
        self.broker.kill = self.kill
        self.broker.clock = self.clock
        self.broker.event = self.event
        self.balance = 10000.0
        self.equity = 10000.0
        self.pending = None
        self.position = None
        self.last_bar = None
        self.last_rejected = None
        self.started = False
        self.history = []
        self.rng = random.Random(self.fill.seed)
        self.manifest = None

    def start(self):
        self.manifest = {
            "plumbing_only": True,
            "validation_evidence": False,
            "trial_consumed": False,
            "code_sha256": code_sha256(),
            "active_configuration_sha256": configuration_sha256(),
            "forward_configuration": self.cfg,
            "forward_configuration_sha256": hashlib.sha256(
                json.dumps(self.cfg, sort_keys=True).encode()
            ).hexdigest(),
            "mode": "B_demo_orders" if self.broker.send_demo_orders else "A_simulated",
            "broker_symbol": self.broker.symbol,
            "strategy_symbol": "XAUUSD",
            "reference_capital_usd": 10000,
            "risk_fraction": 0.01,
            "timestamp_offset_seconds": self.broker.offset,
            "clock_scope": "current API timestamp offset; not historical server DST evidence",
            "broker_identity": self.broker.identity,
            "broker_specification": self.broker.spec._asdict()
            if hasattr(self.broker.spec, "_asdict")
            else vars(self.broker.spec),
            "fill_model": asdict(self.fill),
            "cost_model": asdict(self.costs),
            "cost_scope": "development spread snapshot, plumbing only; not 2026 calibration",
            "source_difference": "Exness live bid/ask versus MetaQuotes MT5 reconstructed ask "
            "or Dukascopy observed quotes; no contemporary historical overlap measured",
            "started_utc": self.clock().isoformat(),
        }
        encoded = json.dumps(self.manifest, default=str, sort_keys=True).encode()
        self.manifest["observation_manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
        self.journal.start(self.manifest)

    def event(self, kind, **payload):
        self.journal.write(kind, self.clock(), **payload)

    def reject(self, reason, signal=None):
        self.last_rejected = {"utc": self.clock().isoformat(), "reason": reason}
        self.event(
            "signal",
            status="rejected",
            reason=reason,
            signal=signal.model_dump(mode="json") if signal else None,
        )

    def _cancel(self, reason):
        if self.pending is None:
            return
        ticket = self.pending.get("ticket")
        was_open = self.position is not None
        if self.broker.send_demo_orders:
            self.broker.cancel(ticket)
            self._reconcile()
        status = "filled_while_cancelling" if not was_open and self.position else "cancelled"
        self.event("order", status=status, reason=reason, ticket=ticket)
        self.pending = None

    def _sim_close(self, price, reason, exit_kind):
        position = self.position
        signal = position["signal"]
        side = 1 if signal.direction == Direction.BUY else -1
        volume = position["volume"]
        cost = self.costs.charge(
            volume,
            1.0,
            position["filled_at"],
            self.clock(),
            exit_kind=exit_kind,
            entry_price=position["entry"],
            exit_price=price,
        )
        gross = (price - position["entry"]) * side * volume * 100
        self.balance += gross - cost
        self.equity = self.balance
        self.event(
            "position",
            status="closed_simulated",
            reason=reason,
            gross_usd=gross,
            model_cost_usd=cost,
            net_usd=gross - cost,
        )
        self.position = None

    def _reconcile(self):
        """Broker is authoritative for demo fills and protective stop/target exits."""
        positions = self.broker.positions()
        orders = self.broker.orders()
        if any(p.magic != MAGIC or p.symbol != self.broker.symbol for p in positions) or any(
            o.magic != MAGIC or o.symbol != self.broker.symbol for o in orders
        ):
            raise RuntimeError("Unexpected external account activity; halt observation")
        if orders and self.pending is None:
            raise RuntimeError("Untracked pending broker order; reconciliation required")
        if len(positions) > 1 or len(orders) > 1:
            raise RuntimeError("One-position/pending ceiling violated")
        if positions:
            actual = positions[0]
            if self.position is None:
                if self.pending is None:
                    raise RuntimeError("Untracked broker position; reconciliation required")
                filled_at = datetime.fromtimestamp(actual.time - self.broker.offset, UTC)
                self.position = {
                    **self.pending,
                    "ticket": actual.ticket,
                    "broker_position_identifier": getattr(actual, "identifier", actual.ticket),
                    "entry": actual.price_open,
                    "filled_at": filled_at,
                    "remaining": self.policy.max_holding_bars,
                    "volume": actual.volume,
                }
                spec = self.broker.api.symbol_info(self.broker.symbol)
                stop = float(getattr(actual, "sl", self.pending["signal"].stop_loss))
                risk_usd = (
                    actual.volume
                    * abs(actual.price_open - stop)
                    / spec.trade_tick_size
                    * spec.trade_tick_value
                )
                self.event(
                    "fill_risk_audit",
                    ticket=actual.ticket,
                    initial_risk_usd=risk_usd,
                    risk_fraction_reference=risk_usd / 10000,
                    risk_budget_usd=100,
                    within_one_percent=risk_usd <= 100 + 1e-6,
                    actual_entry=actual.price_open,
                    stop_loss=stop,
                    volume=actual.volume,
                    tick_size=spec.trade_tick_size,
                    tick_value=spec.trade_tick_value,
                    scope="entry_to_stop_excluding_costs_and_stop_slippage",
                )
                if not math.isfinite(risk_usd) or stop <= 0 or risk_usd > 100 + 1e-6:
                    self.kill._trip("filled_risk_exceeds_one_percent_or_invalid_stop")
                self.event(
                    "signal",
                    status="filled_demo",
                    signal_id=str(self.pending["signal"].id),
                    ticket=actual.ticket,
                    requested_price=float(self.pending["signal"].entry_price),
                    actual_entry=actual.price_open,
                    slippage=actual.price_open - float(self.pending["signal"].entry_price),
                    spread=self.broker.tick().ask - self.broker.tick().bid,
                )
                self.pending = None
            self.position["broker_profit"] = actual.profit
        elif self.position is not None:
            # Never fabricate a stop/target exit price; retain broker deal records.
            history = self.broker.api.history_deals_get(
                position=self.position.get("broker_position_identifier", self.position["ticket"])
            )
            if history is None or not history:
                raise RuntimeError("Closed position has no available deal history")
            self.event(
                "position",
                status="closed_broker",
                ticket=self.position["ticket"],
                deals=[d._asdict() if hasattr(d, "_asdict") else vars(d) for d in history],
            )
            self.position = None
        if self.pending is not None and not orders and not positions:
            self.event("order", status="removed_broker", ticket=self.pending["ticket"])
            self.pending = None

    def _ny_flat(self, now):
        local = now.astimezone(self.calendar.zone)
        return local.hour >= self.policy.ny_flat_hour and local.hour < 18

    async def on_bar(self, bar, window):
        if self.last_bar is not None and bar.time <= self.last_bar:
            return
        self.last_bar = bar.time
        self.history = window
        state = self.calendar.state(bar.time)
        bar = bar.model_copy(
            update={"non_tradable": state["non_tradable"], "entry_blocked": state["entry_blocked"]}
        )
        if bar.non_tradable:
            self._cancel("market_closed")
            return
        # Previous orders may fill only on subsequent closed bars, never submission bar.
        new_fill = False
        if self.pending and not self.broker.send_demo_orders:
            order = self.pending
            if self.fill.fills(order["signal"], bar, 0.01, self.rng):
                self.position = {
                    **order,
                    "entry": float(order["signal"].entry_price),
                    "filled_at": bar.available_at,
                    "remaining": self.policy.max_holding_bars,
                }
                self.pending = None
                new_fill = True
                self.event("signal", status="filled_simulated", signal_id=str(order["signal"].id))
            else:
                order["remaining"] -= 1
                if order["remaining"] <= 0:
                    self._cancel("expiry_10_market_bars")
        elif self.pending:
            self.pending["remaining"] -= 1
            if self.pending["remaining"] <= 0:
                self._cancel("expiry_10_market_bars")
                self._reconcile()  # cancellation may race with an actual fill
        if self.position:
            pos = self.position
            signal = pos["signal"]
            if not new_fill and bar.available_at > pos["filled_at"]:
                pos["remaining"] -= 1
            if not self.broker.send_demo_orders:
                decision = closed_bar_exit(
                    signal,
                    bar,
                    newly_filled=new_fill,
                    stop_on_fill_bar=self.fill.stop_on_fill_bar,
                    remaining_bars=pos["remaining"],
                    ny_flat=self._ny_flat(bar.available_at),
                )
                if decision:
                    reason, price, kind = decision
                    self._sim_close(price, reason, kind)
            if self.position and pos["remaining"] <= 0:
                if self.broker.send_demo_orders:
                    self.broker.close(pos["ticket"])
                    self._reconcile()
                else:
                    self._sim_close(float(bar.close), "holding_48_market_bars", "market")
        signal = await self.source.evaluate(window)
        opposite = self.source.opposite_break
        if (
            self.pending
            and opposite
            and ((self.pending["signal"].direction == Direction.BUY) != (opposite == "bullish"))
        ):
            self._cancel("opposite_structure_break")
        self.kill.on_equity(self.equity)
        if signal is None:
            self.reject(self.source.last_reason)
            return
        if not self.kill.may_trade():
            self.reject(f"kill_switch:{self.kill.halt_reason()}", signal)
            return
        if self.calendar.state(bar.available_at)["entry_blocked"] or bar.entry_blocked:
            self.reject("market_pause_or_post_reopen", signal)
            return
        if self.pending or self.position:
            self.reject("one_position_or_pending_at_a_time", signal)
            return
        self.event("order_candidate", signal=signal.model_dump(mode="json"))
        # Fixed reference risk budget; capped on actual/simulated equity for leverage/margin.
        size = size_from_risk(
            balance=10000,
            risk_fraction=0.01,
            entry=float(signal.entry_price),
            stop=float(signal.stop_loss),
            atr=signal.metadata["atr_at_bos"],
            symbol="XAUUSD",
        )
        volume = size.volume
        cap = min(10000, self.equity) * 10 / (float(signal.entry_price) * 100)
        volume = round(min(volume, int((cap + 1e-10) / 0.01) * 0.01, 200), 2)
        if not size.accepted or volume < 0.01:
            self.reject(f"sizing:{size.reason}", signal)
            return
        ticket = None
        if self.broker.send_demo_orders:
            try:
                volume = self.broker.risk_volume(signal)
                volume = self.broker.cap_to_broker_margin(signal, volume)
                if volume < self.broker.spec.volume_min:
                    self.reject("broker_margin_below_minimum_volume", signal)
                    return
                ticket = self.broker.place(signal, volume)
                self.kill.on_execution_result(True)
            except Exception as error:
                self.kill.on_execution_result(False)
                self.reject(f"broker_order_refused:{error}", signal)
                # Reconcile before doing anything else; never resubmit uncertain orders.
                self._reconcile()
                return
        self.pending = {
            "signal": signal,
            "volume": volume,
            "ticket": ticket,
            "remaining": self.policy.expiry_bars,
            "submitted_at": bar.available_at,
        }
        self.event(
            "signal",
            status="pending_demo" if ticket else "pending_simulated",
            signal=signal.model_dump(mode="json"),
            volume=volume,
            ticket=ticket,
            sizing=size.as_dict(),
            applied_volume=volume,
        )

    async def poll(self):
        now = self.clock()
        tick = self.broker.tick()
        tick_utc = self.broker.tick_utc(tick)
        if tick_utc > now + timedelta(seconds=5):
            raise RuntimeError("Tick converted to future UTC; clock inconsistent")
        self.kill.on_feed(tick_utc, connected=True)
        self.kill.on_spread(tick.ask - tick.bid)
        if self.broker.send_demo_orders:
            self._reconcile()
            self.equity = self.broker.guard().equity
        elif self.position:
            pos = self.position
            side = 1 if pos["signal"].direction == Direction.BUY else -1
            price = tick.bid  # bid-based simulation, broker ask remains a diagnostic
            accrued = self.costs.charge(
                pos["volume"],
                1.0,
                pos["filled_at"],
                now,
                entry_price=pos["entry"],
                exit_price=price,
            )
            self.equity = (
                self.balance + (price - pos["entry"]) * side * pos["volume"] * 100 - accrued
            )
        else:
            self.equity = self.balance
        self.kill.on_equity(self.equity)
        if not self.kill.may_trade():
            self.stop(self.kill.halt_reason())
            return self.display(tick)
        bars = self.broker.bars(now)
        if not bars or now - bars[-1].available_at > timedelta(minutes=10):
            raise RuntimeError("M5 history stale or absent")
        if not self.started:
            # Startup history is warmup only; never replay it into simulated orders.
            self.last_bar = bars[-1].time
            self.history = bars
            self.started = True
            warmup = [c for c in bars if not self.calendar.state(c.time)["non_tradable"]]
            await self.source.evaluate(warmup)  # display only; no warmup orders/fills
            self.event("warmup", bars=len(bars), last_closed=self.last_bar)
        else:
            new = [c for c in bars if c.time > self.last_bar]
            if len(new) > 1 or (new and new[0].time - self.last_bar > timedelta(minutes=5)):
                # No catch-up execution against past prices after a missed close.
                raise RuntimeError("Missed M5 close; restart for causal warmup")
            for bar in new:
                window = [
                    c
                    for c in bars
                    if c.time <= bar.time and not self.calendar.state(c.time)["non_tradable"]
                ]
                await self.on_bar(bar, window)
        if self._ny_flat(now):
            self._cancel("ny_17h_flat")
            if self.broker.send_demo_orders:
                self._reconcile()
            if self.position:
                if self.broker.send_demo_orders:
                    self.broker.close(self.position["ticket"])
                    self._reconcile()
                else:
                    price = tick.bid
                    self._sim_close(price, "ny_17h_flat", "market")
        return self.display(tick)

    def display(self, tick):
        now = self.clock()
        model_spread = self.costs.spread_at(now, tick.bid) * self.costs.pip_usd
        pos = None
        if self.position:
            p = self.position
            s = p["signal"]
            side = 1 if s.direction == Direction.BUY else -1
            mark = (
                (tick.bid if side == 1 else tick.ask) if self.broker.send_demo_orders else tick.bid
            )
            distance = abs(p["entry"] - float(s.stop_loss))
            pos = {
                "entry": p["entry"],
                "stop": float(s.stop_loss),
                "tp": float(s.take_profit),
                "remaining_market_bars": p["remaining"],
                "unrealized_R_gross": (mark - p["entry"]) * side / distance if distance else None,
            }
        pending = None
        if self.pending:
            p = self.pending
            pending = {
                "level": float(p["signal"].entry_price),
                "ticket": p["ticket"],
                "expires_after_market_bars": p["remaining"],
            }
        return {
            "plumbing_only": True,
            "validation_evidence": False,
            "utc": now.isoformat(),
            "mode": "B" if self.broker.send_demo_orders else "A",
            "bid": tick.bid,
            "ask": tick.ask,
            "broker_spread_usd_per_oz": tick.ask - tick.bid,
            "model_spread_usd_per_oz": model_spread,
            "broker_minus_model_spread": tick.ask - tick.bid - model_spread,
            "price_source_difference": "Exness XAUUSDm vs MetaQuotes/Dukascopy; "
            "no synchronous price difference available without second feed",
            "market": self.calendar.state(now),
            "last_closed_M5": self.last_bar,
            "swings": [asdict(s) for s in self.source.swings[-10:]],
            "BOS": [
                d
                for d in self.source.detections
                if "bos" in d["concept"] or d["concept"] == "break_of_structure"
            ][-5:],
            "active_OB_FVG": self.source.active_zones[-10:],
            "pending": pending,
            "position": pos,
            "last_rejected": self.last_rejected,
            "equity": self.equity,
            "halt": self.kill.halt_reason(),
            "run_id": self.journal.run_id,
        }

    def stop(self, reason):
        self.event("halt", reason=reason)
        if self.broker.send_demo_orders:
            # Best effort only on owned exposure. Failed cleanup is prominently journalled.
            try:
                for order in self.broker.orders():
                    if order.magic == MAGIC and order.symbol == self.broker.symbol:
                        self.broker.cancel(order.ticket)
                for position in self.broker.positions():
                    if position.magic == MAGIC and position.symbol == self.broker.symbol:
                        self.broker.close(position.ticket)
                self._reconcile()
            except Exception as error:
                self.event(
                    "cleanup_failed",
                    reason=str(error),
                    action="Inspect MT5 manually; protective SL/TP may remain active",
                )
        else:
            self._cancel(reason)
            if self.position:
                self.event("position", status="unclosed_simulation_at_halt")
