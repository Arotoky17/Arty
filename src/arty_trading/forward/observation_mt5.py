"""Explicit MT5 demo adapter. API injected in tests; never silently mocks live data."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.forward.account import verify_demo_account

MAGIC = 610061


def infer_timestamp_offset(samples, tolerance=5):
    """Infer API timestamp offset from advancing fresh ticks, not terminal timezone.

    MT5 Python normally returns UTC epochs (offset zero). Some feeds expose server
    wall-clock epochs. No Athens assumption is applied to Exness. Two advancing
    observations are required; a stationary/stale tick cannot establish an offset.
    """
    if len(samples) < 2 or samples[-1][0] <= samples[0][0]:
        raise ValueError("Need two advancing fresh ticks to establish UTC clock")
    offsets = []
    for stamp, utc in samples:
        difference = stamp - utc.timestamp()
        offset = round(difference / 900) * 900
        if not -12 * 3600 <= offset <= 14 * 3600 or abs(difference - offset) > tolerance:
            raise ValueError("MT5 timestamp inconsistent with actual UTC clock")
        offsets.append(offset)
    if len(set(offsets)) != 1:
        raise ValueError("Inconsistent MT5 timestamp offsets")
    return offsets[0]


class ObservationMT5:
    def __init__(
        self,
        api,
        *,
        send_demo_orders=False,
        symbol="XAUUSDm",
        expected_server=None,
        expected_login=None,
        clock=None,
    ):
        self.expected_login = expected_login
        self.expected_server = expected_server
        self.clock = clock or (lambda: datetime.now(UTC))
        self.event = lambda kind, **payload: None
        self.kill = None
        self.attempted = set()
        self.api = api
        self.send_demo_orders = send_demo_orders
        self.symbol = symbol
        self.offset = None
        self.identity = None
        self.spec = None

    def connect(self):
        if not self.api.initialize():
            raise RuntimeError(f"MT5 initialize failed: {self.api.last_error()}")
        account = verify_demo_account(self.api.account_info())
        if account.currency != "USD" or abs(account.balance - 10000) > 1:
            raise ValueError("Require dedicated USD demo account with initial balance 10000")
        if self.expected_login is not None and account.login != self.expected_login:
            raise ValueError("Unexpected broker account login")
        self.identity = (account.login, account.server)
        if not all(math.isfinite(x) and x > 0 for x in (account.balance, account.equity)):
            raise ValueError("Invalid broker account balance/equity")
        if "exness" not in account.server.lower():
            raise ValueError("Require an Exness demo server")
        if not self.api.symbol_select(self.symbol, True):
            raise RuntimeError("Cannot select XAUUSDm")
        info = self.api.symbol_info(self.symbol)
        if info is None:
            raise RuntimeError("Missing broker symbol specification")
        if self.symbol != "XAUUSDm" or info.name != self.symbol:
            raise ValueError("Unexpected broker symbol")
        if self.expected_server and account.server != self.expected_server:
            raise ValueError("Unexpected broker server")
        for name in (
            "volume_min",
            "volume_step",
            "volume_max",
            "trade_tick_value",
            "trade_tick_size",
            "trade_contract_size",
        ):
            value = float(getattr(info, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid broker specification {name}")
        if info.volume_min > info.volume_max:
            raise ValueError("Invalid volume range")
        if not math.isfinite(info.point) or info.point <= 0:
            raise ValueError("Invalid broker point")
        self.spec = info
        # Dedicated account prevents netting with manual/unrelated positions.
        if self.send_demo_orders and (self.positions() or self.orders()):
            raise ValueError("Mode B requires account without positions or pending orders")
        return account

    def guard(self):
        account = verify_demo_account(self.api.account_info())
        if (account.login, account.server) != self.identity:
            raise RuntimeError("Account changed since startup")
        if not math.isfinite(account.equity) or account.equity <= 0:
            raise RuntimeError("Invalid account equity")
        terminal = self.api.terminal_info()
        if terminal is None or not terminal.connected:
            raise RuntimeError("MT5 terminal disconnected")
        return account

    def tick(self):
        self.guard()
        tick = self.api.symbol_info_tick(self.symbol)
        if tick is None or not all(math.isfinite(x) for x in (tick.bid, tick.ask)):
            raise RuntimeError("Invalid or missing MT5 tick")
        if tick.bid <= 0 or tick.ask < tick.bid:
            raise RuntimeError("Invalid broker quote")
        return tick

    def tick_utc(self, tick):
        if self.offset is None:
            raise RuntimeError("Timestamp conversion not verified")
        stamp = getattr(tick, "time_msc", tick.time * 1000) / 1000
        return datetime.fromtimestamp(stamp - self.offset, UTC)

    def bars(self, now, count=500):
        rows = self.api.copy_rates_from_pos(self.symbol, self.api.TIMEFRAME_M5, 0, count)
        if rows is None or len(rows) == 0:
            raise RuntimeError("No M5 history returned")
        result = []
        for row in rows:
            opened = datetime.fromtimestamp(int(row["time"]) - self.offset, UTC)
            closed = opened + timedelta(minutes=5)
            if opened > now + timedelta(minutes=5):
                raise RuntimeError("Bar clock inconsistent with verified tick clock")
            if closed > now:
                continue
            # Broker symbol mapping is at the adapter boundary, shared logic is XAUUSD.
            result.append(
                Candle(
                    symbol="XAUUSD",
                    timeframe=TimeFrame.M5,
                    time=opened,
                    available_at=closed,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(row["tick_volume"]),
                    spread=round(int(row["spread"]) * self.spec.point / 0.01),
                )
            )
        if len({c.time for c in result}) != len(result):
            raise RuntimeError("Duplicate M5 timestamps")
        return sorted(result, key=lambda c: c.time)

    def positions(self):
        result = self.api.positions_get()
        if result is None:
            raise RuntimeError("positions_get failed")
        return list(result)

    def orders(self):
        result = self.api.orders_get()
        if result is None:
            raise RuntimeError("orders_get failed")
        return list(result)

    def _send(self, request):
        if not self.send_demo_orders:
            raise PermissionError("Mode A has no permission to send any order")
        self.guard()  # technical type and identity checked again before every mutation
        quote = self.api.symbol_info_tick(self.symbol)
        spread = quote.ask - quote.bid if quote else None
        self.event(
            "mt5_request", request=request, requested_price=request.get("price"), spread=spread
        )
        try:
            result = self.api.order_send(request)
        except Exception as error:
            self.event("mt5_result", retcode=None, error=str(error), request=request, spread=spread)
            raise
        filled = getattr(result, "price", None)
        self.event(
            "mt5_result",
            request=request,
            spread=spread,
            retcode=getattr(result, "retcode", None),
            requested_price=request.get("price"),
            filled_price=filled if filled else None,
            slippage=(filled - request["price"]) if filled and "price" in request else None,
            result=result._asdict()
            if hasattr(result, "_asdict")
            else vars(result)
            if result
            else None,
        )
        if result is None or result.retcode not in {
            self.api.TRADE_RETCODE_DONE,
            self.api.TRADE_RETCODE_PLACED,
        }:
            # No retries: a timeout is ambiguous; reconcile before any new submission.
            raise RuntimeError(f"Execution failed/uncertain: {result}")
        return result

    def normalize_volume(self, volume):
        step = Decimal(str(self.spec.volume_step))
        value = (Decimal(str(volume)) // step) * step
        return float(value) if self.spec.volume_min <= value <= self.spec.volume_max else 0.0

    def risk_volume(self, signal):
        self.spec = self.api.symbol_info(self.symbol)
        if self.spec is None or self.spec.name != self.symbol:
            raise ValueError("Unexpected broker symbol specification")
        for name in (
            "trade_tick_size",
            "trade_tick_value",
            "volume_step",
            "volume_min",
            "volume_max",
            "trade_contract_size",
        ):
            value = float(getattr(self.spec, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid dynamic broker specification {name}")
        distance = abs(float(signal.entry_price) - float(signal.stop_loss))
        unit_risk = distance / self.spec.trade_tick_size * self.spec.trade_tick_value
        if not math.isfinite(unit_risk) or unit_risk <= 0:
            raise ValueError("Invalid dynamic tick risk")
        cap = (
            min(10000, self.guard().equity)
            * 10
            / (float(signal.entry_price) * self.spec.trade_contract_size)
        )
        return self.normalize_volume(min(100 / unit_risk, cap, self.spec.volume_max))

    def place(self, signal, volume):
        self.event("order_candidate", signal=signal.model_dump(mode="json"), volume=volume)
        if not self.send_demo_orders:
            raise PermissionError("--send-demo-orders required")
        if signal.symbol != "XAUUSD" or self.symbol != "XAUUSDm":
            raise ValueError("Unexpected signal/broker symbol")
        self.spec = self.api.symbol_info(self.symbol)
        if self.spec is None or self.spec.name != self.symbol:
            raise ValueError("Unexpected broker symbol specification")
        if (
            not math.isfinite(volume)
            or volume <= 0
            or not math.isclose(volume, self.normalize_volume(volume), abs_tol=1e-10, rel_tol=0)
        ):
            raise ValueError("Volume not normalized to broker grid")
        key = str(signal.id)
        if key in self.attempted:
            raise RuntimeError("Duplicate order candidate")
        if self.positions() or self.orders():
            raise RuntimeError("Account already has exposure or pending order")
        buy = signal.direction.value == "buy"
        tick = self.tick()
        price = float(signal.entry_price)
        if (buy and price >= tick.ask) or (not buy and price <= tick.bid):
            raise ValueError("Limit on wrong side of current broker quote")
        age = (self.clock() - self.tick_utc(tick)).total_seconds()
        if age < -5 or age > 180:
            raise RuntimeError("Stale/future broker tick")
        if self.kill is None or not self.kill.may_trade():
            raise RuntimeError("Kill switch unavailable or halted")
        self.kill.on_feed(self.tick_utc(tick), connected=True)
        self.kill.on_spread(tick.ask - tick.bid)
        account = self.guard()
        self.kill.on_equity(account.equity)
        if not self.kill.may_trade():
            raise RuntimeError("Kill switch halted")
        sl, tp = float(signal.stop_loss), float(signal.take_profit)
        if not all(math.isfinite(x) and x > 0 for x in (price, sl, tp)):
            raise ValueError("Invalid SL/TP")
        if not ((sl < price < tp) if buy else (tp < price < sl)) or not math.isclose(
            abs(tp - price), 2 * abs(price - sl), abs_tol=1e-8
        ):
            raise ValueError("Invalid SL/TP or fixed 2R")
        distance = self.spec.trade_stops_level * self.spec.point
        if (
            min(abs(price - sl), abs(tp - price), tick.ask - price if buy else price - tick.bid)
            < distance
        ):
            raise ValueError("Broker stops level violated")
        if (
            volume * abs(price - sl) / self.spec.trade_tick_size * self.spec.trade_tick_value
            > 100 + 1e-6
        ):
            raise ValueError("One percent risk exceeded")
        risk_usd = volume * abs(price - sl) / self.spec.trade_tick_size * self.spec.trade_tick_value
        self.event(
            "order_audit",
            initial_risk_usd=risk_usd,
            risk_fraction_reference=risk_usd / 10000,
            risk_budget_usd=100,
            within_one_percent=risk_usd <= 100 + 1e-6,
            spread=tick.ask - tick.bid,
            tick_age_seconds=age,
            tick_value=self.spec.trade_tick_value,
            tick_size=self.spec.trade_tick_size,
            stops_level=self.spec.trade_stops_level,
            volume=volume,
        )
        margin = self.api.order_calc_margin(
            self.api.ORDER_TYPE_BUY if buy else self.api.ORDER_TYPE_SELL, self.symbol, volume, price
        )
        if margin is None or not math.isfinite(margin) or margin < 0:
            raise RuntimeError("Cannot verify broker margin")
        raw = self.api.account_info()
        if margin > min(float(raw.margin_free), account.equity * 0.5):
            raise ValueError("Broker margin exceeds available or 50 percent equity ceiling")
        if price * volume * self.spec.trade_contract_size > min(10000, account.equity) * 10 + 1e-6:
            raise ValueError("Gross leverage ceiling exceeded")
        request = {
            "action": self.api.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": volume,
            "type": self.api.ORDER_TYPE_BUY_LIMIT if buy else self.api.ORDER_TYPE_SELL_LIMIT,
            "price": price,
            "sl": float(signal.stop_loss),
            "tp": float(signal.take_profit),
            "magic": MAGIC,
            "comment": "plumbing_only",
            "type_time": self.api.ORDER_TIME_GTC,
            "type_filling": self.api.ORDER_FILLING_RETURN,
        }
        check = self.api.order_check(request)
        self.event(
            "mt5_check",
            request=request,
            retcode=getattr(check, "retcode", None),
            spread=tick.ask - tick.bid,
        )
        if check is None or check.retcode != 0:
            raise ValueError(f"Broker order check refused: {check}")
        self.attempted.add(key)  # ambiguous sends must never be retried
        return self._send(request).order

    def cap_to_broker_margin(self, signal, volume):
        """Round down on the real margin surface before submitting any order."""
        account = self.guard()
        free = float(self.api.account_info().margin_free)
        if not math.isfinite(free) or free < 0:
            raise RuntimeError("Invalid available broker margin")
        ceiling = min(free, account.equity * 0.5)
        steps = int(Decimal(str(volume)) // Decimal(str(self.spec.volume_step)))
        kind = (
            self.api.ORDER_TYPE_BUY if signal.direction.value == "buy" else self.api.ORDER_TYPE_SELL
        )
        while steps > 0:
            candidate = float(Decimal(steps) * Decimal(str(self.spec.volume_step)))
            if candidate < self.spec.volume_min:
                break
            margin = self.api.order_calc_margin(
                kind, self.symbol, candidate, float(signal.entry_price)
            )
            if margin is None or not math.isfinite(margin) or margin < 0:
                raise RuntimeError("Cannot establish broker margin cap")
            if margin <= ceiling:
                return candidate
            steps -= 1
        return 0.0

    def cancel(self, ticket):
        orders = self.orders()
        order = next((o for o in orders if o.ticket == ticket), None)
        if order is None:
            return  # reconcile immediately after cancellation to catch a racing fill
        if order.magic != MAGIC or order.symbol != self.symbol:
            raise RuntimeError("Refuse to cancel unowned order")
        self._send({"action": self.api.TRADE_ACTION_REMOVE, "order": ticket})

    def close(self, ticket):
        position = next((p for p in self.positions() if p.ticket == ticket), None)
        if position is None:
            return
        if position.magic != MAGIC or position.symbol != self.symbol:
            raise RuntimeError("Refuse to close unowned position")
        tick = self.tick()
        buy = position.type == self.api.POSITION_TYPE_BUY
        # Select the supported filling policy, never assume RETURN on market execution.
        filling = (
            self.api.ORDER_FILLING_FOK
            if self.spec.filling_mode & 1
            else (
                self.api.ORDER_FILLING_IOC
                if self.spec.filling_mode & 2
                else self.api.ORDER_FILLING_RETURN
            )
        )
        self._send(
            {
                "action": self.api.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": self.symbol,
                "volume": position.volume,
                "type": self.api.ORDER_TYPE_SELL if buy else self.api.ORDER_TYPE_BUY,
                "price": tick.bid if buy else tick.ask,
                "deviation": 20,
                "magic": MAGIC,
                "comment": "plumbing_only close",
                "type_filling": filling,
            }
        )

    def shutdown(self):
        self.api.shutdown()
