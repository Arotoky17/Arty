"""Real report acceptance checks and non-vacuous audit failure tests."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from arty_trading.cli import main
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.backtesting.control import ControlReplay
from arty_trading.modules.backtesting.control_audit import audit_violations, validate_report

REPORT = Path("data/backtests/baseline_post_fixes_2024H1.json")


@pytest.fixture
def real_report() -> dict[str, Any]:
    if not REPORT.exists():
        pytest.skip("Real MT5 2024-H1 history unavailable; control report not generated")
    report: dict[str, Any] = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["data_source"] in {"mt5", "mt5_export", "csv"}
    assert report["data_hash"]
    assert len(report["audit_trades"]) == report["total_trades"]
    return report


def test_backtest_produces_at_least_20_trades(real_report: dict[str, Any]) -> None:
    assert real_report["total_trades"] >= 20


def test_no_trade_before_ob_creation(real_report: dict[str, Any]) -> None:
    if (
        all(
            row["signal_source"] == "legacy_strategy" and row["ob_timestamp"] is None
            for row in real_report["audit_trades"]
        )
        and real_report["audit_trades"]
    ):
        pytest.skip("Legacy OB evidence pending Task 4; baseline is comparison only")
    assert real_report["audit_trades"], "An empty report cannot validate trade safety"
    assert not audit_violations(real_report["audit_trades"])


def test_no_confirmation_from_out_of_window_event(real_report: dict[str, Any]) -> None:
    if (
        all(
            row["signal_source"] == "legacy_strategy" and row["ob_timestamp"] is None
            for row in real_report["audit_trades"]
        )
        and real_report["audit_trades"]
    ):
        pytest.skip("Legacy OB evidence pending Task 4; baseline is comparison only")
    assert real_report["audit_trades"], "An empty report cannot validate trade safety"
    assert not audit_violations(real_report["audit_trades"])


def test_rejection_candle_touches_ob(real_report: dict[str, Any]) -> None:
    if (
        all(
            row["signal_source"] == "legacy_strategy" and row["ob_timestamp"] is None
            for row in real_report["audit_trades"]
        )
        and real_report["audit_trades"]
    ):
        pytest.skip("Legacy OB evidence pending Task 4; baseline is comparison only")
    assert real_report["audit_trades"], "An empty report cannot validate trade safety"
    assert not audit_violations(real_report["audit_trades"])


@pytest.fixture
def valid_trade() -> dict[str, Any]:
    return {
        "ob_timestamp": "2024-01-02T10:00:00+00:00",
        "entry_timestamp": "2024-01-02T10:15:00+00:00",
        "confirmation_timestamp": "2024-01-02T10:10:00+00:00",
        "window_start": "2024-01-02T10:05:00+00:00",
        "window_end": "2024-01-02T10:10:00+00:00",
        "window_timestamps": ["2024-01-02T10:05:00+00:00", "2024-01-02T10:10:00+00:00"],
        "confirmation_valid": True,
        "confirmation_type": "rejection_candle",
        "ob_low": 2000,
        "ob_high": 2002,
        "rejection_candle_low": 2001,
        "rejection_candle_high": 2003,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("entry_timestamp", "2024-01-02T09:00:00+00:00", "entry_before_or_at_ob_creation"),
        ("confirmation_timestamp", "2024-01-02T11:00:00+00:00", "confirmation_out_of_window"),
        (
            "confirmation_timestamp",
            "2024-01-02T10:07:00+00:00",
            "confirmation_not_in_available_candles",
        ),
        ("rejection_candle_low", 2010, "no_contact_with_ob"),
        ("confirmation_valid", False, "unconfirmed_legacy_entry"),
    ],
)
def test_audit_detects_violations(
    valid_trade: dict[str, Any],
    field: str,
    value: Any,
    reason: str,
) -> None:
    assert not audit_violations([valid_trade])
    valid_trade[field] = value
    assert reason in audit_violations([valid_trade])[0]["reasons"]


def test_audit_rejects_missing_evidence() -> None:
    assert audit_violations([{}])


def test_report_rejects_synthetic_source(valid_trade: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="real MT5"):
        validate_report({"data_source": "synthetic", "audit_trades": [valid_trade]})


def test_replay_initializes_live_risk_guards() -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    replay = ControlReplay(config, {})
    assert replay.settings.risk.max_consecutive_losses == 3
    assert replay.settings.risk.max_drawdown == 0.10


def test_cli_does_not_invent_report_when_history_is_missing(tmp_path: Path) -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    config["history_file"] = str(tmp_path / "missing_history.json")
    config_path = tmp_path / "baseline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    output = tmp_path / "result.json"
    code = main(
        [
            "backtest",
            "--symbol",
            "XAUUSD",
            "--from",
            "2024-01-01",
            "--to",
            "2024-06-30",
            "--config",
            str(config_path),
            "--output",
            str(output),
        ]
    )
    assert code == 2
    assert not output.exists()


def test_backtest_json_has_required_fields(real_report: dict[str, Any]) -> None:
    assert {
        "total_trades",
        "win_rate",
        "profit_factor",
        "expectancy_r",
        "max_drawdown_pct",
        "rejections_by_reason",
        "audit_samples",
    } <= real_report.keys()
    assert {"no_contact_with_ob", "insufficient_candles", "choch_out_of_bounds"} <= (
        real_report["rejections_by_reason"].keys()
    )


def test_csv_loader_parses_ohlcv(tmp_path: Path) -> None:
    from arty_trading.modules.backtesting.csv_history import read_side

    path = tmp_path / "history.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n1704153600000,2000,2003,1999,2001,42\n",
        encoding="utf-8",
    )
    frame = read_side([path])
    assert frame.iloc[0]["open"] == 2000
    assert frame.iloc[0]["high"] == 2003
    assert frame.iloc[0]["low"] == 1999
    assert frame.iloc[0]["close"] == 2001
    assert frame.iloc[0]["volume"] == 42
    assert str(frame.index.tz) == "UTC"


@pytest.mark.asyncio
async def test_replay_preserves_live_htf_and_immutable_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    from datetime import timedelta

    replay = ControlReplay(config, {})
    assert replay.generator._decision_engine is None  # Same default as api.main.
    candle = Candle(
        symbol="XAUUSD",
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 4, 12, tzinfo=UTC),
        open=Decimal("2000"),
        high=Decimal("2001"),
        low=Decimal("1999"),
        close=Decimal("2000"),
        volume=42,
        spread=10,
    )
    signal = Signal(
        symbol="XAUUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1998"),
        take_profit=Decimal("2006"),
        confidence=0.9,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.M5,
        justification="Unit fixture",
    )
    context = SimpleNamespace(
        is_neutral=lambda: False,
        _regime_blocks_trade=lambda: False,
        master_trend="bullish",
        h4_trend="bullish",
        ltf_smc_data=[],
        htf_smc_data=[],
    )
    monkeypatch.setattr(replay.builder, "build", AsyncMock(return_value=context))
    generate = AsyncMock(return_value=signal)
    monkeypatch.setattr(replay.generator, "generate", generate)
    monkeypatch.setattr(
        "arty_trading.modules.backtesting.control.update_setups_from_market_context",
        lambda *args: None,
    )
    await replay.process(
        candle, {tf: [candle.model_copy(update={"time": candle.time - timedelta(minutes=5 * i)})
                     for i in reversed(range(15))]
                 for tf in (TimeFrame.M5, TimeFrame.H1, TimeFrame.H4)}
    )
    assert generate.call_args.kwargs["htf_trend"] == "bullish"
    assert generate.call_args.kwargs["htf_trends"] == {"H1": "bullish", "H4": "bullish"}
    assert signal.entry_price == Decimal("2000")
    assert replay.position is not None, replay.rejections
    assert replay.position.entry_price == Decimal("2000.40")


@pytest.mark.skip(reason="OB signal traceability pending Task 4 (legacy fallback)")
def test_baseline_ob_traces_pending() -> None:
    pass


def test_backtest_produces_more_than_3_trades_after_breaker_fix(
    real_report: dict[str, Any],
) -> None:
    assert real_report["total_trades"] > 3
    assert real_report["effective_config"]["risk"]["CONSECUTIVE_LOSS_COOLDOWN_HOURS"] == 24


def test_rejection_reasons_are_logged(real_report: dict[str, Any]) -> None:
    reasons = real_report["rejections_by_reason"]
    assert reasons["circuit_breaker:consecutive_losses"] > 0
    assert reasons["circuit_breaker:drawdown"] > 0
    assert real_report["signals_generated"] >= real_report["trades_executed"] > 3
    assert real_report["legacy_ob_detected"] > 0


def test_legacy_does_not_produce_ob_traces(real_report: dict[str, Any]) -> None:
    legacy = [r for r in real_report["audit_trades"] if r["signal_source"] == "legacy_strategy"]
    assert legacy
    assert all(r["ob_timestamp"] is None and r["confirmation_timestamp"] is None for r in legacy)
    assert real_report["validation_status"] == "failed"


@pytest.mark.asyncio
async def test_replay_rearms_with_historical_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    start = datetime(2024, 1, 4, tzinfo=UTC)

    def bars(tf: TimeFrame, minutes: int, count: int) -> list[Candle]:
        return [
            Candle(
                symbol="XAUUSD",
                timeframe=tf,
                time=start + timedelta(minutes=i * minutes),
                open=Decimal("2000"),
                high=Decimal("2001"),
                low=Decimal("1998"),
                close=Decimal("2000"),
                volume=100,
                spread=10,
            )
            for i in range(count)
        ]

    history = {
        TimeFrame.M5: bars(TimeFrame.M5, 5, 1600),
        TimeFrame.H1: bars(TimeFrame.H1, 60, 134),
        TimeFrame.H4: bars(TimeFrame.H4, 240, 34),
    }
    replay = ControlReplay(config, history)
    context = SimpleNamespace(
        is_neutral=lambda: False,
        _regime_blocks_trade=lambda: False,
        master_trend="bullish",
        h4_trend="bullish",
        ltf_smc_data=[],
        htf_smc_data=[],
    )
    signal = Signal(
        symbol="XAUUSD",
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1998"),
        take_profit=Decimal("2006"),
        confidence=0.9,
        strategy_name="fixture",
        timeframe=TimeFrame.M5,
        justification="Synthetic lifecycle control",
    )
    monkeypatch.setattr(replay.builder, "build", AsyncMock(return_value=context))
    monkeypatch.setattr(replay.generator, "generate", AsyncMock(return_value=signal))
    monkeypatch.setattr(
        "arty_trading.modules.backtesting.control.update_setups_from_market_context",
        lambda *args: None,
    )
    await replay.run()
    assert len(replay.audits) > 3, replay.rejections
    assert replay.risk.get_risk_report()["consecutive_loss_breaker_rearms"] >= 1
    assert replay.rejections["circuit_breaker:consecutive_losses"] > 0
    entries = [datetime.fromisoformat(row["entry_timestamp"]) for row in replay.audits]
    third_exit = datetime.fromisoformat(replay.audits[2]["exit_timestamp"])
    assert entries[3] >= third_exit + timedelta(hours=24)
