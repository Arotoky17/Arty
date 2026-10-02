"""
Moteur de backtesting.

Simule l'exécution de stratégies sur des données historiques
et calcule les statistiques de performance.
"""

from __future__ import annotations

import random
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from arty_trading.config.operational import effective_definitions, load_config
from arty_trading.core.entities import Candle, Signal, Trade
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.backtesting.stats import BacktestStats, calculate_stats
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.fill_model import FillModel
from arty_trading.modules.execution.sl_guard import StructureContext
from arty_trading.modules.smc.base import find_swing_points
from arty_trading.utils.helpers import calculate_atr, get_pip_size, pip_value
from arty_trading.validation.cost_sensitivity import cost_sensitivity
from arty_trading.validation.preregistration import assert_setup_run_allowed
from arty_trading.validation.split import DataSplit
from arty_trading.validation.trial_registry import TrialRegistry

logger = get_logger(LogCategory.BACKTEST)


class BacktestEngine:
    """
    Moteur de backtesting.

    Simule l'exécution d'une stratégie sur des données historiques.
    Parcourt les bougies une par une, génère des signaux, ouvre/ferme
    des trades virtuels, et calcule les statistiques de performance.

    Un ``PositionManager`` optionnel peut être injecté pour simuler le
    position management réel (Profit Lock, Partial, Runner, Structure
    Trailing) — évalué sur la clôture de la bougie **précédente** et la
    structure confirmée des bougies **passées**, sans look-ahead bias.
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("10000"),
        risk_per_trade: float = 0.01,
        symbol: str = "EURUSD",
        position_manager: Any | None = None,
        setup_id: str = "legacy",
        parameters: dict[str, Any] | None = None,
        registry: TrialRegistry | None = None,
        cost_model: CostModel | None = None,
        fill_model: FillModel | None = None,
    ) -> None:
        execution = load_config("execution.yaml")
        self.cost_model = cost_model or CostModel(**execution["cost"])
        self.fill_model = fill_model or FillModel(**execution["fill"])
        self.registry = registry or TrialRegistry()
        self.split = DataSplit(self.registry)
        self.setup_id = setup_id
        self.parameters = parameters or {}
        self._authorized_batches: list[tuple[list[Candle], tuple[str, ...]]] = []
        self._pending: list[tuple[Signal, int]] = []
        self._rng = random.Random(self.fill_model.seed)
        self._newly_filled: set[int | None] = set()
        self._orders_submitted = 0
        self._orders_filled = 0
        self._entry_times: dict[int | None, datetime] = {}
        self._initial_risks: dict[int | None, float] = {}
        self._exit_time: datetime | None = None
        self._costs_by_ticket: dict[int | None, float] = {}
        self._accrued_costs = [0.0]
        self.last_stats: BacktestStats | None = None
        self._initial_balance = initial_balance
        self._risk_per_trade = risk_per_trade
        self._symbol = symbol.upper()
        self._balance = initial_balance
        self._equity = initial_balance
        self._trades: list[Trade] = []
        self._open_trades: list[Trade] = []
        self._equity_curve: list[Decimal] = [initial_balance]
        self._ticket_counter = 1
        self._position_manager = position_manager
        # Phase 3E : journal par trade (type de setup, direction, résultat).
        self._trade_journal: list[dict[str, Any]] = []
        self._journal_by_ticket: dict[int | None, dict[str, Any]] = {}

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def equity(self) -> Decimal:
        return self._equity

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def open_trades(self) -> list[Trade]:
        return list(self._open_trades)

    @property
    def equity_curve(self) -> list[Decimal]:
        return list(self._equity_curve)

    async def run_async(
        self,
        candles: list[Candle],
        signal_generator: Any = None,
        smc_detector: Any = None,
    ) -> BacktestStats:
        self._begin_run(candles)
        if not candles:
            return self._finish_run()

        # Réinitialiser le journal pour chaque run.
        self._trade_journal = []
        self._journal_by_ticket = {}

        for i, candle in enumerate(candles):
            self._apply_position_management(i, candles)
            self._process_pending(candle)
            self._check_open_trades(candle)

            if signal_generator and smc_detector and i >= 20:
                recent_candles = candles[max(0, i - 20) : i + 1]
                try:
                    smc_data = await smc_detector.detect(recent_candles, self._symbol)
                except Exception:
                    smc_data = []

                if smc_data:
                    try:
                        signal = await signal_generator.generate(
                            recent_candles,
                            smc_data,
                            master_trend=self._estimate_trend(recent_candles),
                        )
                    except Exception:
                        signal = None

                    if signal:
                        self._submit_limit(signal, candle)

            self._update_equity(candle)
            self._append_equity()

        if self._open_trades:
            last_candle = candles[-1]
            for trade in list(self._open_trades):
                self._close_trade(trade, last_candle.close)
                if self._position_manager is not None:
                    self._position_manager.forget(trade)

        return self._finish_run()

    def load_holdout(self, setup_id: str, reason: str, **kwargs: Any) -> Any:
        if setup_id != self.setup_id:
            raise ValueError("Setup does not match engine")
        batch = self.split.load_holdout(setup_id, reason, **kwargs)
        self._authorized_batches.extend(
            (part, tuple(c.model_dump_json() for c in part)) for part in self.split.batches(batch)
        )
        return batch

    def _validate_batch(self, candles: list[Candle]) -> str:
        for batch, fingerprint in self._authorized_batches:
            if batch is candles:
                if fingerprint != tuple(c.model_dump_json() for c in candles):
                    raise ValueError("Authorized data changed")
                self._authorized_batches = [
                    (b, f) for b, f in self._authorized_batches if b is not batch
                ]
                return "holdout"
        self.split.assert_development(candles)
        return "dev"

    def _begin_run(
        self, candles: list[Candle], extra_batches: tuple[list[Candle], ...] = ()
    ) -> None:
        assert_setup_run_allowed(self.setup_id)
        partition = self._validate_batch(candles)
        for batch in extra_batches:
            if self._validate_batch(batch) != partition and batch:
                raise ValueError("Mixed dev/holdout timeframes")
        self._trial_id = self.registry.begin(
            self.setup_id,
            {
                "strategy": self.parameters,
                "definitions": effective_definitions(self._symbol),
                "split": self.split.config,
                "symbol": self._symbol,
                "initial_balance": str(self._initial_balance),
                "cost": asdict(self.cost_model),
                "fill": asdict(self.fill_model),
                "risk_per_trade": self._risk_per_trade,
            },
            candles[0].timeframe.value if candles else None,
            candles[0].time.isoformat() if candles else None,
            candles[-1].time.isoformat() if candles else None,
            partition,
        )
        self._balance = self._initial_balance
        self._equity = self._initial_balance
        self._trades = []
        self._open_trades = []
        self._equity_curve = [self._initial_balance]
        self._pending = []
        self._orders_submitted = self._orders_filled = 0
        self._entry_times = {}
        self._initial_risks = {}
        self._costs_by_ticket = {}
        self._accrued_costs = [0.0]
        self._rng = random.Random(self.fill_model.seed)

    def _finish_run(self) -> BacktestStats:
        self._pending.clear()
        self._equity = self._balance
        self._equity_curve[-1] = self._equity
        self._accrued_costs[-1] = sum(self._costs_by_ticket.values())
        # Partial exits belong to the same order and initial 1R denominator.
        grouped = {}
        for trade in self._trades:
            if trade.ticket not in grouped:
                grouped[trade.ticket] = trade.model_copy(update={"profit": Decimal("0")})
            grouped[trade.ticket].profit = (grouped[trade.ticket].profit or Decimal("0")) + (
                trade.profit or Decimal("0")
            )
        closed_orders = list(grouped.values())
        stats = calculate_stats(closed_orders, self._equity_curve, self._initial_balance)
        stats.orders_submitted = self._orders_submitted
        stats.unfilled_orders = self._orders_submitted - self._orders_filled
        stats.fill_rate = (
            self._orders_filled / self._orders_submitted if self._orders_submitted else 0.0
        )
        results = [
            float(t.profit or 0) / self._initial_risks[t.ticket]
            for t in closed_orders
            if self._initial_risks.get(t.ticket, 0) > 0
        ]
        stats.expectancy_r = sum(results) / len(results) if results else 0.0
        multipliers = (
            self.cost_model.sensitivity_multipliers
            or load_config("execution.yaml")["cost"]["sensitivity_multipliers"]
        )
        stats.cost_sensitivity = cost_sensitivity(
            [
                (
                    float(t.profit or 0),
                    self._costs_by_ticket.get(t.ticket, 0.0),
                    self._initial_risks.get(t.ticket, 0.0),
                )
                for t in closed_orders
            ],
            [float(value) for value in self._equity_curve],
            self._accrued_costs,
            multipliers,
        )
        stats.cost_assumptions = self.cost_model.assumptions()
        self.registry.finish(self._trial_id, stats, stats.expectancy_r)
        self.last_stats = stats
        return stats

    def _append_equity(self) -> None:
        self._equity_curve.append(self._equity)
        self._accrued_costs.append(sum(self._costs_by_ticket.values()))

    def _submit_limit(self, signal: Signal, candle: Candle) -> None:
        self._orders_submitted += 1
        self._pending.append((signal, self.fill_model.expiry_bars))

    def _process_pending(self, candle: Candle) -> None:
        self._exit_time = candle.time
        self._newly_filled = set()
        remaining = []
        for signal, ttl in self._pending:
            if self.fill_model.fills(signal, candle, float(get_pip_size(self._symbol)), self._rng):
                before = len(self._trades)
                self._open_trade_from_signal(signal, candle)
                if len(self._trades) > before:
                    self._orders_filled += 1
                    self._newly_filled.add(self._trades[-1].ticket)
            elif ttl > 1:
                remaining.append((signal, ttl - 1))
        self._pending = remaining

    def _estimate_trend(self, candles: list[Candle]) -> str:
        """Estime la tendance à partir des bougies (pour backtest simplifié)."""
        if len(candles) < 5:
            return "neutral"
        closes = [float(c.close) for c in candles[-5:]]
        up = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
        down = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
        if up >= 3:
            return "bullish"
        if down >= 3:
            return "bearish"
        return "neutral"

    def _open_trade_from_signal(self, signal: Signal, candle: Candle) -> None:
        risk_amount = float(self._balance) * self._risk_per_trade
        sl_distance = abs(float(signal.entry_price - signal.stop_loss))
        if sl_distance == 0:
            return

        pip_size = float(get_pip_size(self._symbol))
        sl_pips = sl_distance / pip_size
        if sl_pips == 0:
            return

        pip_val = pip_value(self._symbol, lot_size=1.0)
        volume = risk_amount / (sl_pips * pip_val)
        volume = round(volume, 2)
        if volume < 0.01:
            volume = 0.01

        trade = Trade(
            symbol=self._symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=Decimal(str(volume)),
            signal_id=signal.id,
            strategy_name=signal.strategy_name,
            ticket=self._ticket_counter,
        )
        self._entry_times[trade.ticket] = candle.time
        self._initial_risks[trade.ticket] = sl_pips * pip_val * float(trade.volume)
        self._ticket_counter += 1

        # Phase 3E : journal du trade (type de setup, direction, risque).
        from arty_trading.modules.smc.setup_classifier import classify_setup_type

        metadata = getattr(signal, "metadata", {}) or {}
        smc_concepts = list(getattr(signal, "smc_concepts", []) or [])
        zone_concept = metadata.get("zone_concept") or (smc_concepts[0] if smc_concepts else "")
        setup_type = metadata.get("setup_type")
        if not setup_type or setup_type == "UNKNOWN":
            setup_type = classify_setup_type(
                zone_concept=zone_concept,
                direction=signal.direction.value.lower()
                if hasattr(signal.direction, "value")
                else str(signal.direction),
                smc_data=[],
                zone_index=metadata.get("zone_index", 0),
            )
        entry = {
            "ticket": self._ticket_counter - 1,
            "setup_type": setup_type,
            "zone_concept": zone_concept,
            "direction": "BUY" if signal.direction == Direction.BUY else "SELL",
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "profit": 0.0,
            "r_multiple": 0.0,
        }
        self._trade_journal.append(entry)
        self._journal_by_ticket[trade.ticket] = entry

        self._open_trades.append(trade)
        self._trades.append(trade)
        if self._position_manager is not None:
            self._position_manager.register(trade)

    # ------------------------------------------------------------------
    # Position management (Profit Lock / Partial / Runner / Structure)
    # ------------------------------------------------------------------

    def _apply_position_management(self, i: int, candles: list[Candle]) -> None:
        """Applique les règles de gestion sur la clôture de la bougie précédente.

        Anti look-ahead : la décision utilise ``candles[i-1].close`` et une
        structure construite sur ``candles[:i]`` uniquement (swings fractaux
        confirmés par construction). Aucune donnée de la bougie courante.
        """
        if self._position_manager is None or i < 1 or not self._open_trades:
            return
        price = candles[i - 1].close
        structure = self._build_structure(candles[:i])
        for trade in list(self._open_trades):
            actions = self._position_manager.evaluate(trade, price, structure)
            for action in actions:
                if action.kind == "modify" and action.stop_loss is not None:
                    trade.stop_loss = action.stop_loss
                    self._position_manager.confirm(trade, action)  # exécution OK
                elif action.kind == "close":
                    self._close_trade(trade, price)
                    if self._position_manager is not None:
                        self._position_manager.forget(trade)
                    break
                elif action.kind == "partial_close" and action.close_fraction is not None:
                    self._close_partial(trade, action.close_fraction, price)
                    self._position_manager.confirm(trade, action)  # exécution OK

    @staticmethod
    def _build_structure(past_candles: list[Candle]) -> StructureContext:
        """Snapshot de structure confirmée sur bougies passées uniquement."""
        if not past_candles:
            return StructureContext()
        last_close = past_candles[-1].close
        swings = find_swing_points(past_candles, window=2)
        lows = [s.price for s in swings if s.type == "low"]
        highs = [s.price for s in swings if s.type == "high"]
        last_low = lows[-1] if lows else None
        last_high = highs[-1] if highs else None
        try:
            atr = calculate_atr(past_candles, period=14)
        except Exception:  # noqa: BLE001
            atr = None
        return StructureContext(
            swing_low=last_low,
            swing_high=last_high,
            hl=last_low,
            lh=last_high,
            atr=atr,
            bearish_break=bool(last_low is not None and last_close < last_low),
            bullish_break=bool(last_high is not None and last_close > last_high),
        )

    def _close_partial(self, trade: Trade, fraction: Decimal, price: Decimal) -> None:
        """Clôture partielle en backtest : enregistre la portion réalisée."""
        closed_volume = (trade.volume * fraction).quantize(Decimal("0.01"))
        if closed_volume <= 0 or closed_volume >= trade.volume:
            self._close_trade(trade, price)
            return
        partial = trade.model_copy(update={"volume": closed_volume})
        pip_size = float(get_pip_size(self._symbol))
        if trade.direction == Direction.BUY:
            pips = (float(price) - float(trade.entry_price)) / pip_size
        else:
            pips = (float(trade.entry_price) - float(price)) / pip_size
        pip_val = pip_value(self._symbol, lot_size=1.0)
        partial.close_price = price
        cost = self.cost_model.charge(
            float(closed_volume),
            pip_val,
            self._entry_times.get(trade.ticket, self._exit_time),
            self._exit_time,
        )
        partial.profit = Decimal(str(pips * pip_val * float(closed_volume) - cost))
        self._costs_by_ticket[trade.ticket] = self._costs_by_ticket.get(trade.ticket, 0.0) + cost
        partial.is_open = False
        self._balance += partial.profit
        trade.volume -= closed_volume
        self._trades.append(partial)

    def _check_open_trades(self, candle: Candle) -> None:
        self._exit_time = candle.time
        for trade in list(self._open_trades):
            hit_sl = False
            hit_tp = False
            close_price = float(trade.entry_price)

            if trade.direction == Direction.BUY:
                if candle.low <= float(trade.stop_loss):
                    hit_sl = True
                    close_price = min(float(candle.open), float(trade.stop_loss))
                elif (
                    candle.high >= float(trade.take_profit)
                    and trade.ticket not in self._newly_filled
                ):
                    hit_tp = True
                    close_price = float(trade.take_profit)
            else:
                if candle.high >= float(trade.stop_loss):
                    hit_sl = True
                    close_price = max(float(candle.open), float(trade.stop_loss))
                elif (
                    candle.low <= float(trade.take_profit)
                    and trade.ticket not in self._newly_filled
                ):
                    hit_tp = True
                    close_price = float(trade.take_profit)

            if hit_sl or hit_tp:
                self._close_trade(trade, Decimal(str(close_price)))

    def _close_trade(self, trade: Trade, close_price: Decimal) -> None:
        pip_size = float(get_pip_size(self._symbol))
        if trade.direction == Direction.BUY:
            pips = (float(close_price) - float(trade.entry_price)) / pip_size
        else:
            pips = (float(trade.entry_price) - float(close_price)) / pip_size

        pip_val = pip_value(self._symbol, lot_size=1.0)
        cost = self.cost_model.charge(
            float(trade.volume),
            pip_val,
            self._entry_times.get(trade.ticket, self._exit_time),
            self._exit_time,
        )
        profit = Decimal(str(pips * pip_val * float(trade.volume) - cost))
        self._costs_by_ticket[trade.ticket] = self._costs_by_ticket.get(trade.ticket, 0.0) + cost

        trade.close_price = close_price
        trade.profit = profit
        trade.is_open = False

        # Phase 3E : compléter le journal avec le résultat du trade.
        journal_entry = self._journal_by_ticket.get(trade.ticket)
        if journal_entry is not None:
            journal_entry["profit"] = float(
                sum((t.profit or Decimal("0")) for t in self._trades if t.ticket == trade.ticket)
            )
            risk = self._initial_risks.get(trade.ticket, 0)
            journal_entry["r_multiple"] = journal_entry["profit"] / risk if risk > 0 else 0.0

        self._balance += profit
        self._open_trades.remove(trade)

    def _update_equity(self, candle: Candle) -> None:
        floating = Decimal("0")
        for trade in self._open_trades:
            pip_size = float(get_pip_size(self._symbol))
            if trade.direction == Direction.BUY:
                pips = (float(candle.close) - float(trade.entry_price)) / pip_size
            else:
                pips = (float(trade.entry_price) - float(candle.close)) / pip_size
            pip_val = pip_value(self._symbol, lot_size=1.0)
            floating += Decimal(str(pips * pip_val * float(trade.volume)))

        self._equity = self._balance + floating

    def get_summary(self) -> dict[str, Any]:
        return {
            "initial_balance": str(self._initial_balance),
            "final_balance": str(self._balance),
            "total_trades": len(self._trades),
            "open_trades": len(self._open_trades),
            "profit": str(self._balance - self._initial_balance),
            "cost_sensitivity": self.last_stats.cost_sensitivity if self.last_stats else [],
            "cost_assumptions": self.cost_model.assumptions(),
        }

    @property
    def trade_journal(self) -> list[dict[str, Any]]:
        """Phase 3E : journal détaillé des trades (type, direction, résultat)."""
        return list(self._trade_journal)

    def setup_type_breakdown(self) -> dict[str, dict[str, Any]]:
        """
        Phase 3F : statistiques par type de setup et par direction.

        Returns:
            Dict {setup_type: {trades, wins, win_rate, total_profit, avg_r}}.
        """
        breakdown: dict[str, dict[str, Any]] = {}
        for entry in self._trade_journal:
            for key in (
                entry.get("setup_type", "UNKNOWN"),
                f"{entry.get('setup_type', 'UNKNOWN')}|{entry.get('direction', '')}",
            ):
                stats = breakdown.setdefault(
                    key, {"trades": 0, "wins": 0, "total_profit": 0.0, "total_r": 0.0}
                )
                stats["trades"] += 1
                stats["wins"] += 1 if entry.get("profit", 0.0) > 0 else 0
                stats["total_profit"] += entry.get("profit", 0.0)
                stats["total_r"] += entry.get("r_multiple", 0.0)

        for stats in breakdown.values():
            stats["win_rate"] = (
                round(stats["wins"] / stats["trades"], 3) if stats["trades"] else 0.0
            )
            stats["avg_r"] = (
                round(stats["total_r"] / stats["trades"], 3) if stats["trades"] else 0.0
            )
        return breakdown
