"""Only fake ticks, bars, accounts and order APIs. No MT5/network initialization."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as Simple

import pytest

from arty_trading.application.setup1_source import Setup1Source
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.forward.observation import ObservationJournal, ObservationSession
from arty_trading.forward.observation_mt5 import MAGIC, ObservationMT5, infer_timestamp_offset
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.setup1_policy import closed_bar_exit

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def candle(at=NOW - timedelta(minutes=5), **changes):
    data = dict(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=at,
        available_at=at + timedelta(minutes=5),
        open=Decimal(3000),
        high=Decimal(3001),
        low=Decimal(2999),
        close=Decimal(3000),
    )
    data.update(changes)
    return Candle(**data)


def signal():
    return Signal(
        symbol="XAUUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal(2998),
        stop_loss=Decimal(2993),
        take_profit=Decimal(3008),
        confidence=1,
        strategy_name="Setup 1",
        timeframe=TimeFrame.M5,
        metadata={"atr_at_bos": 2},
    )


class FakeAPI:
    TIMEFRAME_M5 = 5
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 8
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0

    def __init__(self):
        self.account = Simple(
            trade_mode=0,
            login=42,
            server="Exness-Demo",
            currency="USD",
            balance=10000,
            equity=10000,
            leverage=20,
            margin_free=10000,
        )
        self.info = Simple(
            name="XAUUSDm",
            trade_tick_value=1.0,
            trade_tick_size=0.01,
            trade_stops_level=0,
            trade_contract_size=100,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=200,
            point=0.01,
            filling_mode=2,
        )
        self.quote = Simple(
            bid=3000.0, ask=3000.2, time=int(NOW.timestamp()), time_msc=int(NOW.timestamp() * 1000)
        )
        self.sent = []
        self.pos = []
        self.pending = []
        self.rates = []
        self.connected = True
        self.margin = 1000

    def initialize(self):
        return True

    def account_info(self):
        return self.account

    def terminal_info(self):
        return Simple(connected=self.connected)

    def symbol_select(self, *_):
        return True

    def symbol_info(self, *_):
        return self.info

    def symbol_info_tick(self, *_):
        return self.quote

    def positions_get(self):
        return self.pos

    def orders_get(self):
        return self.pending

    def order_calc_margin(self, *_):
        return self.margin

    def order_check(self, *_):
        return Simple(retcode=0)

    def order_send(self, request):
        self.sent.append(request)
        if request["action"] == self.TRADE_ACTION_PENDING:
            self.pending = [Simple(ticket=123, magic=MAGIC, symbol="XAUUSDm")]
        elif request["action"] == self.TRADE_ACTION_REMOVE:
            self.pending = [o for o in self.pending if o.ticket != request["order"]]
        else:
            self.pos = [p for p in self.pos if p.ticket != request["position"]]
        return Simple(retcode=self.TRADE_RETCODE_PLACED, order=123)

    def history_deals_get(self, **_):
        return [Simple(price=3008, profit=100, commission=0, entry=1)]

    def copy_rates_from_pos(self, *_):
        return self.rates

    def shutdown(self):
        pass


class FakeSource:
    def __init__(self):
        self.swings, self.detections, self.active_zones = [], [], []
        self.last_reason, self.opposite_break = "no_signal", None
        self.next_signal = None
        self.calls = 0

    async def evaluate(self, _):
        self.calls += 1
        result = self.next_signal
        self.next_signal = None
        return result


def session(tmp_path, *, send=False):
    api = FakeAPI()
    broker = ObservationMT5(api, send_demo_orders=send)
    broker.connect()
    broker.offset = 0
    journal = ObservationJournal(tmp_path / "observation.sqlite")
    source = FakeSource()
    model = CostModel(
        spread_pips=20, limit_slippage_usd=0, market_stop_slippage_usd=0, entry_limit_slippage_usd=0
    )
    result = ObservationSession(broker, journal, clock=lambda: NOW, source=source, costs=model)
    result.start()
    return result, api, source


@pytest.mark.parametrize("mode", [1, 2, None])
def test_non_demo_refused_before_any_send(mode):
    api = FakeAPI()
    api.account.trade_mode = mode
    with pytest.raises(RuntimeError):
        ObservationMT5(api, send_demo_orders=True).connect()
    assert not api.sent


def test_default_adapter_cannot_send_and_account_change_guard():
    api = FakeAPI()
    adapter = ObservationMT5(api)
    adapter.connect()
    with pytest.raises(PermissionError):
        adapter._send({})
    adapter.send_demo_orders = True
    api.account.trade_mode = 2
    with pytest.raises(RuntimeError):
        adapter.place(signal(), 0.1)
    assert not api.sent


def test_offset_requires_fresh_advancing_consistent_ticks():
    samples = [
        (NOW.timestamp() + 10800, NOW),
        (NOW.timestamp() + 10801, NOW + timedelta(seconds=1)),
    ]
    assert infer_timestamp_offset(samples) == 10800
    assert infer_timestamp_offset([(s - 10800, t) for s, t in samples]) == 0
    with pytest.raises(ValueError):
        infer_timestamp_offset([samples[0], samples[0]])
    with pytest.raises(ValueError):
        infer_timestamp_offset([samples[0], (samples[1][0] + 20, samples[1][1])])


def test_bars_only_closed_and_mapped_symbol():
    api = FakeAPI()
    broker = ObservationMT5(api)
    broker.connect()
    broker.offset = 10800
    for delta in (-300, 0):
        api.rates.append(
            dict(
                time=int(NOW.timestamp()) + 10800 + delta,
                open=3000,
                high=3001,
                low=2999,
                close=3000,
                tick_volume=5,
                spread=20,
            )
        )
    bars = broker.bars(NOW)
    assert len(bars) == 1
    assert bars[0].available_at == NOW
    assert bars[0].symbol == "XAUUSD"


@pytest.mark.asyncio
async def test_mode_a_limit_not_filled_submission_bar_and_costs(tmp_path):
    run, api, source = session(tmp_path)
    source.next_signal = signal()
    first = candle(low=Decimal(2990))
    await run.on_bar(first, [first])
    assert run.pending and run.position is None
    assert not api.sent
    second = candle(first.time + timedelta(minutes=5), low=Decimal(2997), high=Decimal(3009))
    await run.on_bar(second, [first, second])
    assert run.position is not None  # TP forbidden on fill bar
    third = candle(second.time + timedelta(minutes=5), low=Decimal(2990))
    await run.on_bar(third, [first, second, third])
    assert run.position is None
    assert run.balance < 10000
    assert not api.sent
    rows = run.journal.db.execute("SELECT plumbing_only,payload FROM observation_events").fetchall()
    assert all(flag == 1 for flag, _ in rows)
    assert any('"model_cost_usd"' in payload for _, payload in rows)
    assert (
        run.journal.db.execute("SELECT name FROM sqlite_master WHERE name='trials'").fetchone()
        is None
    )
    run.journal.close()


@pytest.mark.asyncio
async def test_ten_bar_expiry_and_post_reopen_rejection(tmp_path):
    run, api, source = session(tmp_path)
    source.next_signal = signal()
    first = candle()
    await run.on_bar(first, [first])
    for i in range(1, 11):
        await run.on_bar(candle(first.time + timedelta(minutes=5 * i)), [first])
    assert run.pending is None
    assert not api.sent
    # NY 18:05 EDT is 22:05 UTC: blocked for 20 minutes after reopening.
    source.next_signal = signal()
    reopened = candle(NOW.replace(hour=22, minute=5))
    await run.on_bar(reopened, [reopened])
    assert run.pending is None
    assert run.last_rejected["reason"] == "market_pause_or_post_reopen"
    run.journal.close()


@pytest.mark.asyncio
async def test_startup_warmup_and_each_close_processed_once(tmp_path):
    run, api, source = session(tmp_path)
    api.rates = [
        dict(
            time=int(NOW.timestamp()) - 300,
            open=3000,
            high=3001,
            low=2999,
            close=3000,
            tick_volume=3,
            spread=20,
        )
    ]
    await run.poll()
    await run.poll()
    assert source.calls == 1 and not api.sent  # warmup detections only, never replay orders
    run.journal.close()


def test_kill_switch_losses_errors_staleness_and_disconnect(tmp_path):
    run, _, _ = session(tmp_path)
    run.kill.on_equity(9699)
    assert not run.kill.may_trade()
    run.journal.close()
    run, _, _ = session(tmp_path / "errors")
    for _ in range(3):
        run.kill.on_execution_result(False)
    assert not run.kill.may_trade()
    run.journal.close()
    run, _, _ = session(tmp_path / "stale")
    run.kill.on_feed(NOW - timedelta(seconds=181), True)
    assert not run.kill.may_trade()
    run.journal.close()
    run, _, _ = session(tmp_path / "disconnected")
    run.kill.on_feed(NOW, False)
    assert not run.kill.may_trade()
    run.journal.close()


def test_mode_b_pending_payload_margin_and_existing_exposure():
    api = FakeAPI()
    broker = ObservationMT5(api, send_demo_orders=True)
    broker.connect()
    broker.offset = 0
    broker.clock = lambda: NOW
    from arty_trading.forward.kill_switch import KillSwitch

    broker.kill = KillSwitch(clock=lambda: NOW)
    broker.kill.start(10000)
    assert broker.place(signal(), 0.1) == 123
    request = api.sent[0]
    assert request["type"] == api.ORDER_TYPE_BUY_LIMIT
    assert request["sl"] == 2993 and request["tp"] == 3008
    assert request["comment"] == "plumbing_only"
    api.pending = []
    api.margin = 6000
    with pytest.raises(ValueError, match="margin"):
        broker.place(signal(), 0.1)
    assert len(api.sent) == 1
    api.pos = [Simple(ticket=10, magic=MAGIC, symbol="XAUUSDm")]
    with pytest.raises(RuntimeError, match="exposure"):
        broker.place(signal(), 0.1)


def test_broker_margin_caps_volume_before_order():
    api = FakeAPI()
    broker = ObservationMT5(api, send_demo_orders=True)
    broker.connect()
    api.order_calc_margin = lambda kind, symbol, volume, price: volume * price * 100 / 10
    assert broker.cap_to_broker_margin(signal(), 0.33) == 0.16
    assert not api.sent


def test_shared_exit_priority_and_48_bar_limit():
    both = candle(low=Decimal(2990), high=Decimal(3010))
    assert (
        closed_bar_exit(
            signal(), both, newly_filled=True, stop_on_fill_bar=True, remaining_bars=48
        )[0]
        == "stop"
    )
    assert (
        closed_bar_exit(
            signal(), candle(), newly_filled=False, stop_on_fill_bar=True, remaining_bars=0
        )[0]
        == "holding_48_market_bars"
    )
    assert (
        closed_bar_exit(
            signal(),
            candle(),
            newly_filled=False,
            stop_on_fill_bar=True,
            remaining_bars=48,
            ny_flat=True,
        )[0]
        == "ny_17h_flat"
    )


class FakeDetector:
    def __init__(self, displacement=True, origin=27):
        self.displacement = displacement
        self.origin = origin

    async def detect(self, bars, symbol):
        return [
            {
                "concept": "external_bos",
                "direction": "bullish",
                "index": len(bars) - 1,
                "details": {"swing_strength": "external", "displacement": self.displacement},
            },
            {
                "concept": "order_block",
                "direction": "bullish",
                "index": self.origin,
                "details": {"ob_bottom": 2997, "ob_top": 3001},
            },
        ]


@pytest.mark.asyncio
async def test_shared_setup1_fresh_confirmation_midpoint_stop_tp_and_dedup():
    bars = [candle(NOW - timedelta(minutes=5 * (30 - i))) for i in range(30)]
    bars[-1] = bars[-1].model_copy(update={"close": Decimal(3010), "high": Decimal(3011)})
    source = Setup1Source(FakeDetector())
    result = await source.evaluate(bars)
    assert result is not None
    assert result.entry_price == Decimal(2999)
    assert result.stop_loss < Decimal(2997)
    assert result.take_profit - result.entry_price == 2 * (result.entry_price - result.stop_loss)
    assert await source.evaluate(bars) is None
    assert await Setup1Source(FakeDetector(False)).evaluate(bars) is None
    assert await Setup1Source(FakeDetector(origin=28)).evaluate(bars) is None


@pytest.mark.asyncio
async def test_demo_fill_reconciliation_expiry_race_and_deal_exit(tmp_path):
    run, api, source = session(tmp_path, send=True)
    source.next_signal = signal()
    first = candle()
    await run.on_bar(first, [first])
    assert len(api.sent) == 1 and run.pending
    # Broker filled the order before the cancellation could reach it.
    api.pending = []
    api.pos = [
        Simple(
            ticket=789,
            identifier=456,
            magic=MAGIC,
            symbol="XAUUSDm",
            time=int(NOW.timestamp()),
            price_open=2998,
            volume=0.2,
            profit=0,
            type=0,
        )
    ]
    run._cancel("expiry_race")
    assert run.pending is None and run.position["ticket"] == 789
    assert len(api.sent) == 1  # nonexistent order not removed, actual position retained
    api.pos = []  # protective TP closed position on broker
    run._reconcile()
    assert run.position is None
    events = run.journal.db.execute("SELECT payload FROM observation_events").fetchall()
    assert any("filled_while_cancelling" in e[0] for e in events)
    assert any("closed_broker" in e[0] and "deals" in e[0] for e in events)
    run.journal.close()


def test_engine_and_observation_share_setup1_source_without_starting_trial():
    from arty_trading.config.operational import load_config
    from arty_trading.modules.backtesting.engine import BacktestEngine

    engine = BacktestEngine(
        setup_id=load_config("setup1_preregistration.yaml")["setup_id"],
        symbol="XAUUSD",
        registry=Simple(),
        cost_model=CostModel(spread_pips=20),
    )
    assert isinstance(engine.setup1_source, Setup1Source)
    assert not hasattr(engine, "_trial_id")  # run never called, no registry API needed


def test_local_display_escapes_payload_and_protects_configuration(tmp_path, capsys):
    from tools.observe_live import render_state

    output = tmp_path / "live.html"
    render_state({"plumbing_only": True, "reason": "</pre><script>unsafe</script>"}, output)
    assert "<script>" not in output.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        render_state({}, "config/split.yaml")
    with pytest.raises(ValueError):
        ObservationJournal("config/observation.sqlite")


@pytest.mark.parametrize(
    "failure",
    ["symbol", "volume", "duplicate", "flag", "server", "stale", "stops", "target", "kill"],
)
def test_mode_b_pre_send_audit(failure):
    from arty_trading.forward.kill_switch import KillSwitch

    api = FakeAPI()
    broker = ObservationMT5(
        api, send_demo_orders=True, expected_server="Exness-Demo", clock=lambda: NOW
    )
    broker.connect()
    broker.offset = 0
    broker.kill = KillSwitch(clock=lambda: NOW)
    broker.kill.start(10000)
    candidate = signal()
    volume = 0.1
    if failure == "symbol":
        api.info.name = "XAUUSD"
    elif failure == "volume":
        volume = 0.105
    elif failure == "duplicate":
        broker.attempted.add(str(candidate.id))
    elif failure == "flag":
        broker.send_demo_orders = False
    elif failure == "server":
        api.account.server = "Exness-Other"
    elif failure == "stale":
        api.quote.time_msc -= 181000
    elif failure == "stops":
        api.info.trade_stops_level = 1000
    elif failure == "target":
        candidate = candidate.model_copy(update={"take_profit": Decimal(3010)})
    elif failure == "kill":
        broker.kill.on_feed(None, False)
    with pytest.raises((ValueError, RuntimeError, PermissionError)):
        broker.place(candidate, volume)
    assert not api.sent


def test_dynamic_tick_sizing_and_mt5_result_journal():
    from arty_trading.forward.kill_switch import KillSwitch

    api = FakeAPI()
    broker = ObservationMT5(api, send_demo_orders=True, clock=lambda: NOW)
    broker.connect()
    broker.offset = 0
    broker.kill = KillSwitch(clock=lambda: NOW)
    broker.kill.start(10000)
    api.info.trade_tick_value = 2
    api.info.volume_step = 0.03
    assert broker.risk_volume(signal()) == pytest.approx(0.09)
    events = []
    broker.event = lambda kind, **payload: events.append((kind, payload))
    broker.place(signal(), 0.09)
    result = next(payload for kind, payload in events if kind == "mt5_result")
    assert result["retcode"] == api.TRADE_RETCODE_PLACED
    assert result["requested_price"] == 2998
    assert result["filled_price"] is None
    assert result["spread"] == pytest.approx(0.2)


def test_expected_login_and_risk_budget_journal():
    api = FakeAPI()
    with pytest.raises(ValueError, match="login"):
        ObservationMT5(api, expected_login=477484456).connect()
    from arty_trading.forward.kill_switch import KillSwitch

    broker = ObservationMT5(api, send_demo_orders=True, expected_login=42, clock=lambda: NOW)
    broker.connect()
    broker.offset = 0
    broker.kill = KillSwitch(clock=lambda: NOW)
    broker.kill.start(10000)
    events = []
    broker.event = lambda kind, **payload: events.append((kind, payload))
    broker.place(signal(), 0.1)
    audit = next(payload for kind, payload in events if kind == "order_audit")
    assert audit["initial_risk_usd"] == pytest.approx(50)
    assert audit["risk_fraction_reference"] == pytest.approx(0.005)
    assert audit["within_one_percent"] is True
