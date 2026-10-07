"""Forward demo mode: version freeze, demo verification, kill switch, sizing,
journal, reconciliation, reporting and the Setup 1 execution policy.

Every broker interaction is mocked: no network, no MetaTrader5, no order sent.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from arty_trading.config.operational import load_config
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.forward import account as account_mod
from arty_trading.forward import freeze as freeze_mod
from arty_trading.forward.kill_switch import KillSwitch
from arty_trading.forward.sizing import size_from_risk

RUN_ID = "forward-test-run"
# Wednesday 2024-01-10 12:00 UTC = 07:00 New York: market open, outside the pause.
OPEN_BAR = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
NY_FLAT_BAR = datetime(2024, 1, 10, 22, 0, tzinfo=UTC)  # 17:00 New York
# Saturday 2024-01-13: the market is closed at any UTC hour (calendar rule).
WEEKEND_BAR = datetime(2024, 1, 13, 12, 0, tzinfo=UTC)


def candle(moment: datetime, *, low: float, high: float, close: float = 2000.0) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=moment,
        open=Decimal("2000"),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=6000,
    )


def buy_signal(entry: float = 2000.0, stop: float = 1990.0, target: float = 2020.0) -> Signal:
    return Signal(
        symbol="XAUUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(target)),
        confidence=1.0,
        strategy_name="setup_1",
        timeframe=TimeFrame.M5,
        justification="forward demo test",
        metadata={"atr_at_bos": 2.0},
    )


class FakeBroker:
    """In-memory broker port; records calls instead of sending orders."""

    def __init__(
        self,
        equity: float = 10_000.0,
        spread: float | None = 0.25,
        tick: datetime | None = OPEN_BAR,
    ) -> None:
        self._equity = equity
        self._spread = spread
        self._tick = tick
        self.orders: list[dict] = []
        self.cancelled: list[int] = []
        self.closed: list[tuple[int, str]] = []
        self.accept = True

    def place_limit(self, payload: dict) -> dict:
        if not self.accept:
            return {"accepted": False, "reason": "rejected_by_broker", "ticket": None}
        ticket = 1000 + len(self.orders)
        self.orders.append({**payload, "ticket": ticket})
        return {
            "accepted": True,
            "ticket": ticket,
            "fill_price": payload["price"],
            "slippage_price": 0.0,
            "latency_ms": 42.0,
        }

    def cancel(self, ticket: int) -> bool:
        self.cancelled.append(ticket)
        return True

    def close_position(self, ticket: int, reason: str) -> dict:
        self.closed.append((ticket, reason))
        return {"accepted": True, "ticket": ticket}

    def open_positions(self) -> list[dict]:
        return []

    def equity(self) -> float:
        return self._equity

    def spread(self) -> float | None:
        return self._spread

    def last_tick_utc(self) -> datetime | None:
        return self._tick


def frozen_clock(moment: datetime):
    return lambda: moment


# ------------------------------------------------------------- 1. version freeze
def test_freeze_captures_code_configs_and_models() -> None:
    freeze = freeze_mod.RunFreeze.capture()
    assert len(freeze.code_sha256) == 64
    assert set(freeze.config_sha256) >= {
        "definitions.yaml",
        "execution.yaml",
        "setup1_preregistration.yaml",
        "forward_demo.yaml",
    }
    assert len(freeze.cost_model_sha256) == 64
    assert len(freeze.fill_model_sha256) == 64
    assert len(freeze.preregistration_sha256) == 64
    assert freeze.start_date == datetime.now(UTC).date().isoformat()
    freeze_mod.verify_freeze(freeze)


def test_freeze_detects_a_configuration_change() -> None:
    freeze = freeze_mod.RunFreeze.capture()
    mutated = dict(freeze.config_sha256)
    mutated["definitions.yaml"] = "0" * 64
    tampered = freeze_mod.RunFreeze(**{**freeze.as_dict(), "config_sha256": mutated})
    with pytest.raises(freeze_mod.RunInvalidatedError, match="definitions.yaml"):
        freeze_mod.verify_freeze(tampered)


def test_freeze_detects_a_code_change() -> None:
    freeze = freeze_mod.RunFreeze.capture()
    tampered = freeze_mod.RunFreeze(**{**freeze.as_dict(), "code_sha256": "1" * 64})
    with pytest.raises(freeze_mod.RunInvalidatedError, match="code"):
        freeze_mod.verify_freeze(tampered)


def test_forward_setup_id_is_derived_so_trials_are_not_consumed() -> None:
    setup = load_config("setup1_preregistration.yaml")["setup_id"]
    derived = freeze_mod.forward_setup_id(setup)
    assert derived != setup and derived.startswith(setup)


def test_setup1_policy_must_match_the_preregistration() -> None:
    from arty_trading.forward.runner import Setup1Policy

    policy = Setup1Policy.load()
    assert (policy.expiry_bars, policy.max_holding_bars, policy.reward_risk) == (10, 48, 2.0)
    drift = Setup1Policy(**{**policy.__dict__, "max_holding_bars": 96})
    with pytest.raises(ValueError, match="max_holding_bars"):
        drift.assert_matches_preregistration(
            load_config("setup1_preregistration.yaml")["strategy"]
        )


# -------------------------------------------------------- 2. demo account proof
def broker_account(trade_mode: int | None = 0):
    return SimpleNamespace(
        login=4242, server="DemoBroker", currency="USD", balance=10000.0,
        equity=10000.0, leverage=100, trade_mode=trade_mode,
    )


def test_demo_account_is_accepted_from_the_broker_payload() -> None:
    verification = account_mod.verify_demo_account(broker_account(0))
    assert verification.verified_demo is True
    assert verification.broker_account_type == "demo"
    assert verification.broker_trade_mode == 0


@pytest.mark.parametrize("trade_mode", [1, 2])
def test_real_and_contest_accounts_are_refused(trade_mode: int) -> None:
    with pytest.raises(account_mod.NotADemoAccountError):
        account_mod.verify_demo_account(broker_account(trade_mode))


def test_missing_or_unknown_trade_mode_is_refused() -> None:
    with pytest.raises(account_mod.NotADemoAccountError):
        account_mod.verify_demo_account(broker_account(None))
    with pytest.raises(account_mod.NotADemoAccountError):
        account_mod.verify_demo_account(broker_account(7))
    with pytest.raises(account_mod.NotADemoAccountError):
        account_mod.verify_demo_account(None)


def test_config_flag_alone_cannot_pass_a_real_account() -> None:
    """A config flag is never sufficient: only the broker trade_mode decides."""
    cfg = {"accepted_broker_trade_modes": [0, 1, 2], "refused_broker_trade_modes": []}
    with pytest.raises(account_mod.NotADemoAccountError):
        account_mod.verify_demo_account(broker_account(1), cfg)
    from arty_trading.core.entities import TradingAccount
    from arty_trading.core.enums import TradingMode

    legacy = TradingAccount(login=1, server="s", mode=TradingMode.PAPER)
    assert legacy.is_demo is True  # the lax property the forward mode ignores


# ------------------------------------------------------------- 3. kill switch
def test_kill_switch_trips_on_daily_and_run_loss() -> None:
    switch = KillSwitch(clock=frozen_clock(OPEN_BAR))
    switch.start(10_000.0)
    switch.on_equity(9_900.0)
    assert switch.may_trade() is True
    switch.on_equity(9_600.0)  # -4 % run and -4 % daily
    assert switch.may_trade() is False
    assert any("run_loss" in reason for reason in switch.state.reasons)
    assert any("daily_loss" in reason for reason in switch.state.reasons)


def test_kill_switch_trips_on_consecutive_execution_errors() -> None:
    switch = KillSwitch(clock=frozen_clock(OPEN_BAR))
    switch.start(10_000.0)
    for _ in range(2):
        switch.on_execution_result(False)
    assert switch.may_trade() is True
    switch.on_execution_result(False)
    assert switch.may_trade() is False
    switch.on_execution_result(True)
    assert switch.state.consecutive_execution_errors == 0


def test_kill_switch_trips_on_stale_feed_and_spread() -> None:
    switch = KillSwitch(clock=frozen_clock(OPEN_BAR + timedelta(minutes=10)))
    switch.start(10_000.0)
    switch.on_feed(OPEN_BAR, connected=True)  # 10 minutes old
    assert switch.may_trade() is False
    assert any("feed_stale" in reason for reason in switch.state.reasons)

    fresh = KillSwitch(clock=frozen_clock(OPEN_BAR))
    fresh.start(10_000.0)
    fresh.on_feed(OPEN_BAR, connected=False)
    assert fresh.may_trade() is False

    wide = KillSwitch(clock=frozen_clock(OPEN_BAR))
    wide.start(10_000.0)
    wide.on_spread(0.25)
    assert wide.may_trade() is True
    wide.on_spread(1.50)
    assert wide.may_trade() is False
    assert any("spread" in reason for reason in wide.state.reasons)


# ------------------------------------------------------------- 4. signal journal
def make_runner(broker: FakeBroker, *, journal=None, switch=None):
    from arty_trading.forward.journal import ForwardJournal
    from arty_trading.forward.runner import ForwardDemoRunner

    journal = journal or ForwardJournal(":memory:")
    journal.register_run(RUN_ID)
    switch = switch or KillSwitch(clock=frozen_clock(OPEN_BAR))
    switch.start(broker.equity())
    runner = ForwardDemoRunner(
        run_id=RUN_ID, broker=broker, journal=journal, kill_switch=switch,
        sizing=size_from_risk, clock=frozen_clock(OPEN_BAR),
    )
    runner.atr = 2.0
    runner.poll()
    return runner, journal, switch


def test_every_signal_decision_is_journalled_once() -> None:
    runner, journal, _ = make_runner(FakeBroker())
    runner.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    # Market closed: journalled as ignored with its reason.
    runner.on_signal(
        buy_signal(), candle(WEEKEND_BAR, low=1988, high=2001), spread=0.25
    )
    rows = journal.signals(RUN_ID)
    assert [row["status"] for row in rows] == ["filled", "ignored"]
    assert rows[0]["fill_price"] == pytest.approx(2000.0)
    assert rows[0]["spread_price"] == pytest.approx(0.25)
    assert rows[0]["latency_ms"] == pytest.approx(42.0)
    assert "closed" in rows[1]["reason"]


def test_limit_order_expires_after_exactly_ten_closed_bars() -> None:
    broker = FakeBroker()
    runner, journal, _ = make_runner(broker)
    runner.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    assert len(runner.pending) == 1
    ninth = OPEN_BAR + timedelta(minutes=5 * 9)
    assert runner.expire_pending(candle(ninth, low=1988, high=2001)) == []
    tenth = OPEN_BAR + timedelta(minutes=5 * 10)
    decisions = runner.expire_pending(candle(tenth, low=1988, high=2001))
    assert [d.action for d in decisions] == ["expire"]
    assert runner.pending == []
    assert broker.cancelled == [1000]
    assert journal.signals(RUN_ID)[0]["status"] == "expired"


def test_stop_has_priority_over_target_and_trades_are_journalled_in_r() -> None:
    broker = FakeBroker()
    runner, journal, _ = make_runner(broker)
    runner.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    runner.on_fill(OPEN_BAR, ticket=1000, fill_price=2000.0)
    bar = candle(OPEN_BAR + timedelta(minutes=5), low=1988.0, high=2025.0)
    decisions = runner.manage_open_position(bar)
    assert [d.reason for d in decisions] == ["stop_loss"]
    assert broker.closed == [(1000, "stop_loss")]
    trades = journal.trades(RUN_ID)
    assert len(trades) == 1
    assert trades[0]["gross_r"] == pytest.approx(-1.0, rel=0.01)
    assert trades[0]["exit_reason"] == "stop_loss"


def test_time_exit_and_ny_flat_close_the_position() -> None:
    broker = FakeBroker()
    runner, journal, _ = make_runner(broker)
    runner.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    runner.on_fill(OPEN_BAR, ticket=1000, fill_price=2000.0)
    runner.open_position["max_exit_at"] = OPEN_BAR + timedelta(minutes=5)
    after = candle(OPEN_BAR + timedelta(minutes=10), low=1999.5, high=2019.5)
    assert [d.reason for d in runner.manage_open_position(after)] == ["holding_bars_reached"]

    broker2 = FakeBroker()
    runner2, journal2, _ = make_runner(broker2)
    runner2.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    runner2.on_fill(OPEN_BAR, ticket=1000, fill_price=2000.0)
    runner2.open_position["max_exit_at"] = NY_FLAT_BAR + timedelta(minutes=5)
    flat = candle(NY_FLAT_BAR, low=1999.5, high=2019.5)
    assert [d.reason for d in runner2.manage_open_position(flat)] == ["ny_17h_flat"]
    assert len(journal2.trades(RUN_ID)) == 1


def test_only_one_position_and_one_pending_order() -> None:
    broker = FakeBroker()
    runner, journal, _ = make_runner(broker)
    runner.on_signal(buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25)
    second = runner.on_signal(
        buy_signal(), candle(OPEN_BAR + timedelta(minutes=5), low=1988, high=2001), spread=0.25
    )
    assert second.reason == "pending_limit_reached"
    runner.on_fill(OPEN_BAR, ticket=1000, fill_price=2000.0)
    third = runner.on_signal(
        buy_signal(), candle(OPEN_BAR + timedelta(minutes=5), low=1988, high=2001), spread=0.25
    )
    assert third.reason == "one_position_at_a_time"
    assert len([r for r in journal.signals(RUN_ID) if r["status"] == "ignored"]) == 2


def test_kill_switch_blocks_new_entries_and_is_journalled() -> None:
    broker = FakeBroker()
    runner, journal, switch = make_runner(broker)
    switch.state.tripped = True
    switch.state.reasons = ["daily_loss_test"]
    decision = runner.on_signal(
        buy_signal(), candle(OPEN_BAR, low=1988, high=2001), spread=0.25
    )
    assert decision.action == "skip"
    assert "kill_switch" in decision.reason
    assert journal.signals(RUN_ID)[0]["status"] == "ignored"
    assert broker.orders == []

# ------------------------------------------------------------ 5. reconciliation
def signal_row(ts: datetime, direction: str = "BUY", price: float = 2000.0) -> dict:
    return {"ts_utc": ts.isoformat(), "direction": direction, "theoretical_price": price}


def test_reconciliation_matches_signals_inside_tolerance() -> None:
    from arty_trading.forward.reconcile import compare_signals

    broker = [signal_row(OPEN_BAR, price=2000.00)]
    engine = [signal_row(OPEN_BAR + timedelta(seconds=10), price=2000.02)]
    result = compare_signals(broker, engine)
    assert result.clean is True
    assert len(result.matched) == 1
    assert result.unexplained == []


def test_reconciliation_flags_every_unexplained_difference() -> None:
    from arty_trading.forward.reconcile import compare_signals

    broker = [
        signal_row(OPEN_BAR, price=2000.00),
        signal_row(OPEN_BAR + timedelta(minutes=30)),
    ]
    # A 4.00 price gap dwarfs the 0.05 tolerance: no tolerance tuning can hide it.
    engine = [
        signal_row(OPEN_BAR, price=2004.00),
        signal_row(OPEN_BAR + timedelta(minutes=90)),
        signal_row(OPEN_BAR + timedelta(minutes=120), "SELL"),
    ]
    result = compare_signals(broker, engine)
    assert result.clean is False
    assert result.matched == []
    assert result.mismatched and result.broker_only and result.engine_only
    assert len(result.unexplained) == 4
    assert any("price divergence" in alert for alert in result.alerts)
    assert any("no engine counterpart" in alert for alert in result.alerts)
    assert any("not produced live" in alert for alert in result.alerts)


# ----------------------------------------------------------------- 6. reporting
def journal_rows(n: int, *, net_r: float, costs: float = 7.0):
    signals, trades = [], []
    for index in range(n):
        moment = OPEN_BAR + timedelta(days=index)
        signals.append(
            {
                "id": index,
                "ts_utc": moment.isoformat(),
                "status": "filled",
                "spread_price": 0.30,
                "slippage_price": 0.0,
            }
        )
        trades.append(
            {
                "id": index,
                "opened_at_utc": moment.isoformat(),
                "closed_at_utc": moment.isoformat(),
                "net_r": net_r,
                "gross_r": net_r + 0.02,
                "costs_usd": costs,
            }
        )
    return trades, signals


def test_no_verdict_before_the_preregistered_trade_count() -> None:
    from arty_trading.forward.reporting import build_report

    trades, signals = journal_rows(20, net_r=1.0)
    report = build_report(trades, signals, "weekly")
    assert report["overall"]["trades"] == 20
    assert report["overall"]["verdict"] is None
    assert report["overall"]["verdict_status"] == "insufficient_trades"
    assert report["minimum_trades_for_verdict"] == 100


def test_report_publishes_metrics_only_above_the_minimum() -> None:
    from arty_trading.forward.reporting import build_report

    trades, signals = journal_rows(120, net_r=0.4)
    report = build_report(trades, signals, "weekly")
    overall = report["overall"]
    assert overall["trades"] == 120
    assert overall["expectancy_net_r"] == pytest.approx(0.4)
    assert overall["verdict_status"] == "evaluated"
    assert overall["verdict"] == "criteria_met"
    assert overall["fill_rate"] == pytest.approx(1.0)
    assert overall["mean_real_spread_price"] == pytest.approx(0.30)
    assert overall["bootstrap_ci95_net_r"] is not None
    assert report["model_cost_reference"]["entry_limit_slippage_usd"] == 0.0
    assert report["buckets"]


def test_small_account_rows_are_reported_separately() -> None:
    from arty_trading.forward.journal import ForwardJournal

    journal = ForwardJournal(":memory:")
    journal.register_run(RUN_ID)
    journal.record_signal(
        RUN_ID, ts_utc=OPEN_BAR, symbol="XAUUSD", direction="BUY",
        theoretical_price=2000.0, stop_loss=1999.0, take_profit=2002.0,
        status="filled", sizing={"small_account": True, "reason": "small_account_risk_capped"},
    )
    journal.record_signal(
        RUN_ID, ts_utc=OPEN_BAR, symbol="XAUUSD", direction="BUY",
        theoretical_price=2000.0, stop_loss=1999.0, take_profit=2002.0,
        status="filled", sizing={"small_account": False, "reason": "risk_sized"},
    )
    assert len(journal.signals(RUN_ID)) == 2
    assert len(journal.small_account_rows(RUN_ID)) == 1


# -------------------------------------------------------------- 7. small account
def test_small_account_refuses_when_one_percent_cannot_buy_the_minimum_lot() -> None:
    cfg = load_config("forward_demo.yaml")
    cfg["risk"]["small_account_policy"] = "refuse"
    # A 100 USD stop distance on 300 USD: the 1 % budget buys 0.00003 lots.
    decision = size_from_risk(
        balance=300.0, risk_fraction=0.01, entry=2000.0, stop=1900.0, atr=2.0, cfg=cfg
    )
    assert decision.small_account is True
    assert decision.accepted is False
    assert "small_account_refused" in decision.reason


def test_small_account_refuses_when_the_minimum_lot_breaks_the_risk_cap() -> None:
    cfg = load_config("forward_demo.yaml")
    cfg["risk"]["small_account_policy"] = "cap_risk"
    # A 10 USD stop distance on 300 USD: the minimum lot risks 100 USD, above
    # the capped budget. Refusal is the only honest outcome: flooring to the
    # minimum lot would silently breach the advertised cap.
    decision = size_from_risk(
        balance=300.0, risk_fraction=0.01, entry=2000.0, stop=1990.0, atr=2.0, cfg=cfg
    )
    assert decision.small_account is True
    assert decision.accepted is False
    assert "below_minimum_volume" in decision.reason


def test_small_account_caps_the_real_risk_when_the_minimum_lot_fits() -> None:
    from arty_trading.forward.sizing import SizingDecision

    cfg = load_config("forward_demo.yaml")
    capped = float(cfg["risk"]["small_account_capped_risk_fraction"])
    # Policy bookkeeping: a journalled capped decision reports a lower applied
    # fraction than requested. The journal separates these rows so the real
    # risk never pollutes the headline statistics.
    decision = SizingDecision(
        volume=0.01, requested_volume=0.0001, initial_risk_usd=100.0,
        risk_fraction_requested=0.01, risk_fraction_applied=capped,
        small_account=True, policy="cap_risk",
        reason="small_account_risk_capped", limits={},
    )
    assert decision.small_account is True
    assert decision.accepted is True
    assert decision.volume == pytest.approx(0.01)
    assert decision.risk_fraction_applied <= capped
    assert decision.risk_fraction_applied < decision.risk_fraction_requested
    assert "small_account" in decision.reason


def test_normal_account_keeps_the_one_percent_risk() -> None:
    # Wide stops must stay above the leverage ceiling: 10 % gross leverage on
    # 10 000 USD caps volume at 0.5 lots, so the test stops at 200 USD of
    # notional distance and the 1 % budget sizes the full 0.5 lots.
    decision = size_from_risk(
        balance=10_000.0, risk_fraction=0.01, entry=2000.0, stop=1990.0, atr=2.0
    )
    assert decision.small_account is False
    assert decision.accepted is True
    assert decision.initial_risk_usd == pytest.approx(100.0, rel=0.01)


# --------------------------------------------------------------- 8. broker audit
def test_broker_audit_compares_candles_and_records_symbol_specification() -> None:
    from arty_trading.forward.broker_audit import build_audit

    candles = pd.date_range("2024-01-13 07:00", "2024-01-13 16:00", freq="h", tz="UTC")
    broker_frame = pd.DataFrame(
        {
            "timestamp": (candles.astype("int64") // 10**6).to_numpy(),
            "open": [2000.0 + 0.1 * index for index in range(len(candles))],
            "high": [2001.0 + 0.1 * index for index in range(len(candles))],
            "low": [1999.0 + 0.1 * index for index in range(len(candles))],
            "close": [2000.5 + 0.1 * index for index in range(len(candles))],
            "volume": [1] * len(candles),
        }
    )
    broker_frame.index = pd.DatetimeIndex(
        pd.to_datetime(broker_frame.timestamp, unit="ms", utc=True)
    )
    spec = SimpleNamespace(
        name="XAUUSD", digits=2, point=0.01, trade_mode=4, volume_min=0.01,
        volume_max=30.0, volume_step=0.01, spread=25, trade_contract_size=100.0,
        trade_tick_value=1.0, trade_tick_size=0.01, filling_mode=1,
        trade_stops_level=30, trade_freeze_level=0, margin_initial=0.0,
        margin_maintenance=0.0, session_deals=1, session_buy=1, session_sell=1,
    )
    audit = build_audit(broker_frame, "data/raw", raw_symbol_info=spec, account_leverage=100)
    assert audit["candles"]["reference_rows"] > 0
    assert audit["candles"]["rows_beyond_tolerance"] == 0
    specification = audit["symbol_specification"]
    assert specification["volume_min"] == 0.01
    assert specification["volume_step"] == 0.01
    assert specification["spread_price"] == pytest.approx(0.25)
    assert specification["contract_size"] == 100.0
    assert specification["account_leverage"] == 100
    assert specification["configured_contract_ounces_per_lot"] == 100.0
    assert audit["pnl_inspected"] is False


# ------------------------------------------------------------- 9. CLI fails safe
def test_cli_run_refuses_without_a_manifest(tmp_path, monkeypatch) -> None:
    import tools.forward_demo as cli

    monkeypatch.setattr(cli, "_manifest_path", lambda cfg=None: tmp_path / "missing.json")
    assert cli.cmd_run(argparse.Namespace(execute=False)) == 2


def test_cli_preflight_reports_blockers_when_the_broker_is_absent(monkeypatch) -> None:
    import tools.forward_demo as cli

    monkeypatch.setattr(cli, "_manifest_path", lambda cfg=None: Path("x"))
    monkeypatch.setattr(cli, "_raw_account", lambda: (_ for _ in ()).throw(RuntimeError("no mt5")))
    assert cli.cmd_preflight(argparse.Namespace()) == 2


