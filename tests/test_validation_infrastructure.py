"""Research safeguards: persistent access, fail-closed runs and net execution."""

import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.backtesting.mtf_engine import MTFBacktestEngine
from arty_trading.modules.execution.cost_model import CostModel
from arty_trading.modules.execution.fill_model import FillModel
from arty_trading.validation.split import DataSplit, HoldoutAccessError
from arty_trading.validation.trial_registry import TrialRegistry


def candle(year=2024, index=0, low="1.079", high="1.081"):
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.M5,
        time=(
            datetime(year, 1, 16 if year == 2025 else 1, tzinfo=UTC)
            + timedelta(minutes=5 * index)
        ),
        open=Decimal("1.080"),
        close=Decimal("1.080"),
        low=Decimal(low),
        high=Decimal(high),
        volume=1,
    )


def signal():
    return Signal(
        symbol="EURUSD",
        timeframe=TimeFrame.M5,
        direction=Direction.BUY,
        signal_type=SignalType.BUY,
        entry_price=Decimal("1.080"),
        stop_loss=Decimal("1.078"),
        take_profit=Decimal("1.084"),
        confidence=0.9,
        strategy_name="baseline",
    )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from arty_trading.config.operational import load_config
    from arty_trading.validation import split as split_module

    # Synthetic dates for access-guard tests; production holdout remains unavailable.
    config = load_config("split.yaml")
    config["holdout"]["end"] = "2027-01-01T00:00:00+00:00"
    monkeypatch.setattr(split_module, "load_config", lambda name: config)
    return TrialRegistry(tmp_path / "trials.sqlite")


def test_holdout_explicit_flag_and_reason(registry):
    split = DataSplit(registry)
    with pytest.raises(HoldoutAccessError):
        split.load_holdout("s1", "evaluation", loader=lambda: [candle(2025)])
    with pytest.raises(HoldoutAccessError):
        split.load_holdout("s1", " ", enabled=True, loader=lambda: [])
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0] == 0


def test_holdout_persists_and_override_logged(registry):
    split = DataSplit(registry)
    split.load_holdout("s1", "final", enabled=True, loader=lambda: [candle(2025)])
    reopened = DataSplit(TrialRegistry(registry.path))
    with pytest.raises(HoldoutAccessError):
        reopened.load_holdout("s1", "again", enabled=True, loader=lambda: [])
    with pytest.raises(HoldoutAccessError):
        reopened.load_holdout("s1", "again", enabled=True, override=True, loader=lambda: [])
    reopened.load_holdout(
        "s1",
        "again",
        enabled=True,
        override=True,
        override_reason="authorized audit",
        loader=lambda: [candle(2026)],
    )
    with registry.connect() as db:
        rows = db.execute("SELECT reason,override_reason FROM holdout_access").fetchall()
    assert rows == [("final", None), ("again", "authorized audit")]


def test_failed_loader_consumes_access(registry):
    split = DataSplit(registry)

    def broken():
        raise OSError("missing data")

    with pytest.raises(OSError):
        split.load_holdout("s1", "final", enabled=True, loader=broken)
    with pytest.raises(HoldoutAccessError):
        split.load_holdout("s1", "retry", enabled=True, loader=lambda: [])


def test_concurrent_holdout_access(registry):
    def access(_):
        try:
            DataSplit(registry).load_holdout("s1", "final", enabled=True, loader=lambda: [])
            return True
        except HoldoutAccessError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(access, range(2))) == 1


@pytest.mark.asyncio
async def test_engine_rejects_unapproved_holdout(registry):
    engine = BacktestEngine(registry=registry, setup_id="s1")
    with pytest.raises(HoldoutAccessError):
        await engine.run_async([candle(2025)])
    assert registry.get_n_trials() == 0
    batch = engine.load_holdout("s1", "final", enabled=True, loader=lambda: [candle(2025)])
    await engine.run_async(batch)
    with pytest.raises(HoldoutAccessError):
        await engine.run_async(batch)
    with registry.connect() as db:
        assert db.execute("SELECT partition,status FROM trials").fetchone() == (
            "holdout",
            "complete",
        )


@pytest.mark.asyncio
async def test_mtf_rejects_holdout_in_context(registry):
    engine = MTFBacktestEngine(registry=registry)
    with pytest.raises(HoldoutAccessError):
        await engine.run_mtf_async([candle()], [candle(2025)], [], None, None)


@pytest.mark.asyncio
async def test_every_run_registered_even_empty(registry):
    engine = BacktestEngine(registry=registry, setup_id="s1", parameters={"x": 1})
    await engine.run_async([])
    await engine.run_async([candle()])
    assert registry.get_n_trials() == registry.get_n_trials("s1") == 2
    with registry.connect() as db:
        rows = db.execute("SELECT parameter_hash,status,n_trades FROM trials").fetchall()
    assert len(rows[0][0]) == 64
    assert rows[0][0] == rows[1][0]
    assert all(row[1:] == ("complete", 0) for row in rows)


@pytest.mark.asyncio
async def test_registry_write_failure_blocks_simulation(registry, monkeypatch):
    engine = BacktestEngine(registry=registry)

    def denied(*args):
        raise sqlite3.OperationalError("read only")

    monkeypatch.setattr(registry, "begin", denied)
    with pytest.raises(sqlite3.OperationalError):
        await engine.run_async([candle()])
    assert not engine.trades


def test_touch_vs_cross_and_probability():
    sig = signal()
    touched = candle(low="1.080")
    assert FillModel("touched", 1, 1, 0, 1).fills(sig, touched, 0.0001, random.Random(0))
    assert not FillModel("crossed", 1, 1, 0, 1).fills(sig, touched, 0.0001, random.Random(0))
    assert FillModel("crossed", 1, 1, 0, 1).fills(sig, candle(), 0.0001, random.Random(0))
    assert not FillModel("touched", 0, 0, 0, 1).fills(sig, candle(), 0.0001, random.Random(0))


def test_utc_hour_costs():
    model = CostModel(1, 0.2, 3.5, {0: 2})
    assert model.charge(1, 10, datetime(2024, 1, 1, tzinfo=UTC), None) == pytest.approx(31)
    assert model.charge(1, 10, datetime(2024, 1, 1, 12, tzinfo=UTC), None) == pytest.approx(21)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CostModel(-1, 0, 0),
        lambda: FillModel("bad", 0, 1, 0, 1),
        lambda: FillModel("touched", 0, 2, 0, 1),
    ],
)
def test_invalid_execution_configuration(factory):
    with pytest.raises(ValueError):
        factory()


def test_pending_no_same_bar_fill_and_expiry(registry):
    engine = BacktestEngine(registry=registry, fill_model=FillModel("touched", 0, 0, 0, 1))
    engine._begin_run([candle()])
    engine._submit_limit(signal(), candle())
    assert not engine.trades
    engine._process_pending(candle(index=1))
    stats = engine._finish_run()
    assert stats.orders_submitted == stats.unfilled_orders == 1
    assert stats.fill_rate == 0


def test_net_r_uses_original_risk_after_stop_moves(registry):
    engine = BacktestEngine(registry=registry, cost_model=CostModel(1, 0, 0))
    engine._begin_run([candle()])
    engine._submit_limit(signal(), candle())
    engine._process_pending(candle(index=1))
    trade = engine.trades[0]
    risk = engine._initial_risks[trade.ticket]
    trade.stop_loss = trade.entry_price
    engine._close_trade(trade, Decimal("1.084"))
    stats = engine._finish_run()
    assert stats.expectancy_r == pytest.approx(float(trade.profit) / risk)
    assert 0 < stats.expectancy_r < 2
    assert stats.fill_rate == 1


@pytest.mark.asyncio
async def test_holdout_mapping_one_access_for_all_timeframes(registry):
    engine = MTFBacktestEngine(registry=registry, setup_id="s1")
    data = engine.load_holdout(
        "s1", "final", enabled=True, loader=lambda: {"M5": [candle(2025)], "M15": [], "H1": []}
    )
    await engine.run_mtf_async(data["M5"], data["M15"], data["H1"], None, None)
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_authorized_data_cannot_be_mutated(registry):
    engine = BacktestEngine(registry=registry, setup_id="s1")
    batch = engine.load_holdout("s1", "final", enabled=True, loader=lambda: [candle(2025)])
    batch.append(candle(2026))
    with pytest.raises(ValueError, match="changed"):
        await engine.run_async(batch)


def test_config_is_read_and_invalid_values_rejected(tmp_path, monkeypatch):
    import yaml

    from arty_trading.config import operational
    from arty_trading.modules.smc.structure import StructureDetector

    cfg = operational.definitions()
    cfg["swing"]["window"] = 3
    (tmp_path / "definitions.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(operational, "CONFIG_ROOT", tmp_path)
    assert StructureDetector()._swing_window == 3
    cfg["swing"]["window"] = -1
    (tmp_path / "definitions.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError):
        operational.definitions()


def test_sweep_delayed_reintegration_deadline():
    from arty_trading.core.enums import SMCConcept
    from arty_trading.modules.smc.liquidity import LiquidityDetector

    bars = [candle(index=i, low="1.079", high="1.082") for i in range(20)]
    bars[5] = candle(index=5, low="1.079", high="1.083")
    bars[10] = candle(index=10, low="1.078", high="1.082")
    bars[15] = candle(index=15, low="1.077", high="1.079").model_copy(
        update={"open": Decimal("1.078"), "close": Decimal("1.0775")}
    )
    bars[16] = bars[15].model_copy(update={"time": bars[16].time})
    bars[17] = candle(index=17)
    sweeps = [
        d
        for d in LiquidityDetector().detect(bars)
        if d.concept == SMCConcept.LIQUIDITY_SWEEP and d.details["swept_index"] == 10
    ]
    assert len(sweeps) == 1 and sweeps[0].index == 17
    bars[17] = bars[15].model_copy(update={"time": bars[17].time})
    sweeps = [
        d
        for d in LiquidityDetector().detect(bars)
        if d.concept == SMCConcept.LIQUIDITY_SWEEP and d.details["swept_index"] == 10
    ]
    assert not sweeps


def test_raw_csv_holdout_requires_audited_loader(tmp_path, registry):
    from arty_trading.modules.backtesting.csv_history import read_side

    path = tmp_path / "holdout.csv"
    stamp = int(datetime(2025, 1, 16, tzinfo=UTC).timestamp() * 1000)
    path.write_text(f"timestamp,open,high,low,close\n{stamp},1,2,0.5,1\n")
    with pytest.raises(HoldoutAccessError):
        read_side([path])

    def loader():
        assert len(read_side([path])) == 1
        return [candle(2025)]

    DataSplit(registry).load_holdout("s1", "final", enabled=True, loader=loader)
    with pytest.raises(HoldoutAccessError):
        read_side([path])


@pytest.mark.asyncio
async def test_finish_registry_failure_returns_no_report(registry, monkeypatch):
    engine = BacktestEngine(registry=registry)

    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(registry, "finish", fail)
    with pytest.raises(sqlite3.OperationalError):
        await engine.run_async([candle()])
    assert registry.get_n_trials() == 1


def test_drawdown_percentage_is_independent_of_amount():
    from arty_trading.core.entities import Trade
    from arty_trading.modules.backtesting.stats import calculate_stats

    trade = Trade(
        symbol="EURUSD",
        direction=Direction.BUY,
        entry_price=Decimal("1"),
        stop_loss=Decimal("0.9"),
        take_profit=Decimal("1.2"),
        volume=Decimal("1"),
        strategy_name="fixture",
        profit=Decimal("0"),
    )
    stats = calculate_stats(
        [trade], [Decimal("100"), Decimal("50"), Decimal("1000"), Decimal("900")]
    )
    assert stats.max_drawdown == 0.5
    assert stats.max_drawdown_amount == Decimal("100")


def test_partial_exits_count_as_one_trade(registry):
    engine = BacktestEngine(registry=registry, cost_model=CostModel(1, 0, 0))
    engine._begin_run([candle()])
    engine._submit_limit(signal(), candle())
    engine._process_pending(candle(index=1))
    trade = engine.trades[0]
    engine._close_partial(trade, Decimal("0.5"), Decimal("1.082"))
    engine._close_trade(trade, Decimal("1.084"))
    stats = engine._finish_run()
    assert len(engine.trades) == 2
    assert stats.total_trades == 1
    assert stats.expectancy_r == pytest.approx(engine.trade_journal[0]["r_multiple"])
