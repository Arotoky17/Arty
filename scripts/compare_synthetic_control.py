"""Replay Dataset B through the same H4/H1/M5 legacy path; never a real baseline."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.control import ControlReplay
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)


async def main() -> None:
    config = yaml.safe_load(Path("config/baseline.yaml").read_text(encoding="utf-8"))
    candles, _ = generate_multi_regime_candles(
        n_m5=5400, seed=42, start_time=datetime(2024, 1, 1, tzinfo=UTC)
    )
    history = {TimeFrame.M5: candles}
    for timeframe in (TimeFrame.H1, TimeFrame.H4):
        history[timeframe] = aggregate_candles(candles, timeframe)
    logging.getLogger("arty_trading").setLevel(logging.WARNING)
    replay = ControlReplay(config, history)
    await replay.run()
    report = {
        "data_source": "synthetic",
        "purpose": "pipeline control only; not real-history acceptance or profitability evidence",
        "seed": 42,
        "bars": {tf.value: len(bars) for tf, bars in history.items()},
        "signals_generated": replay.signals_generated,
        "trades_executed": len(replay.audits),
        "legacy_ob_detected": len(replay.detector.ob_seen),
        "rejections_by_reason": dict(replay.rejections),
        "final_risk_state": replay.risk.get_risk_report(),
        "max_drawdown_pct": replay.drawdown * 100,
        "effective_risk": config["risk"],
    }
    output = Path("data/backtests/synthetic_legacy_control.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
