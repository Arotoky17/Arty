"""SQLite registry: reserve before execution, finalize after execution."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arty_trading.modules.backtesting.stats import BacktestStats


class TrialRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("ARTY_TRIAL_REGISTRY", "data/validation.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS trials (
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, setup_id TEXT NOT NULL,
                parameter_hash TEXT NOT NULL, parameters TEXT NOT NULL, timeframe TEXT,
                period_start TEXT, period_end TEXT, partition TEXT NOT NULL,
                status TEXT NOT NULL, n_trades INTEGER, expectancy_r REAL,
                profit_factor REAL, sharpe REAL, max_drawdown REAL, fill_rate REAL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS holdout_access (
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, setup_id TEXT NOT NULL,
                reason TEXT NOT NULL, override_reason TEXT)""")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def begin(
        self,
        setup_id: str,
        parameters: dict[str, Any],
        timeframe: str | None,
        start: str | None,
        end: str | None,
        partition: str,
    ) -> int:
        encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO trials (timestamp,setup_id,parameter_hash,parameters,timeframe,"
                "period_start,period_end,partition,status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    setup_id,
                    digest,
                    encoded,
                    timeframe,
                    start,
                    end,
                    partition,
                    "running",
                ),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def finish(self, trial_id: int, stats: BacktestStats, expectancy_r: float) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE trials SET status='complete',n_trades=?,expectancy_r=?,"
                "profit_factor=?,sharpe=?,max_drawdown=?,fill_rate=? WHERE id=?",
                (
                    stats.total_trades,
                    expectancy_r,
                    stats.profit_factor,
                    stats.sharpe_ratio,
                    stats.max_drawdown,
                    stats.fill_rate,
                    trial_id,
                ),
            )

    def get_n_trials(self, setup_id: str | None = None) -> int:
        with self.connect() as db:
            if setup_id is None:
                return int(db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
            return int(
                db.execute("SELECT COUNT(*) FROM trials WHERE setup_id=?", (setup_id,)).fetchone()[
                    0
                ]
            )


def get_n_trials(path: str | Path | None = None, setup_id: str | None = None) -> int:
    return TrialRegistry(path).get_n_trials(setup_id)
