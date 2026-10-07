"""SQLite journal of the forward demo run.

One row per signal considered (filled, expired, rejected or ignored) with the
theoretical price, stop, target, reason, fill price, spread, slippage and
latency; one row per closed trade with gross and net-of-real-costs R.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SIGNAL_STATUSES = ("filled", "expired", "rejected", "ignored")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    manifest_sha256 TEXT,
    broker_trade_mode INTEGER,
    broker_login INTEGER,
    broker_server TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    theoretical_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    fill_price REAL,
    spread_price REAL,
    slippage_price REAL,
    latency_ms REAL,
    volume REAL,
    signal_id TEXT,
    sizing TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    signal_row_id INTEGER REFERENCES signals(id),
    ticket INTEGER,
    direction TEXT NOT NULL,
    opened_at_utc TEXT NOT NULL,
    closed_at_utc TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    volume REAL NOT NULL,
    initial_risk_usd REAL NOT NULL,
    gross_pnl REAL NOT NULL,
    costs_usd REAL NOT NULL,
    gross_r REAL NOT NULL,
    net_r REAL NOT NULL,
    exit_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_run ON signals(run_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id, closed_at_utc);
"""


@dataclass(frozen=True)
class TradeRecord:
    opened_at_utc: datetime
    closed_at_utc: datetime
    direction: str
    entry_price: float
    exit_price: float
    volume: float
    initial_risk_usd: float
    gross_pnl: float
    costs_usd: float
    exit_reason: str
    ticket: int | None = None


class ForwardJournal:
    """Append-only journal."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # A single persistent connection: an in-memory database must not be
        # reopened per statement, or each connection would see an empty schema.
        self._db = sqlite3.connect(self.path, timeout=30)
        self._db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        return self._db

    def register_run(
        self,
        run_id: str,
        manifest_sha256: str | None = None,
        broker_trade_mode: int | None = None,
        broker_login: int | None = None,
        broker_server: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    datetime.now(UTC).isoformat(),
                    manifest_sha256,
                    broker_trade_mode,
                    broker_login,
                    broker_server,
                ),
            )

    def record_signal(
        self,
        run_id: str,
        *,
        ts_utc: datetime,
        symbol: str,
        direction: str,
        theoretical_price: float,
        stop_loss: float,
        take_profit: float,
        status: str,
        reason: str | None = None,
        fill_price: float | None = None,
        spread_price: float | None = None,
        slippage_price: float | None = None,
        latency_ms: float | None = None,
        volume: float | None = None,
        signal_id: str | None = None,
        sizing: dict[str, Any] | None = None,
    ) -> int:
        if status not in SIGNAL_STATUSES:
            raise ValueError(f"Unknown signal status: {status}")
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO signals (run_id,ts_utc,symbol,direction,theoretical_price,"
                "stop_loss,take_profit,status,reason,fill_price,spread_price,slippage_price,"
                "latency_ms,volume,signal_id,sizing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    ts_utc.astimezone(UTC).isoformat(),
                    symbol,
                    direction,
                    float(theoretical_price),
                    float(stop_loss),
                    float(take_profit),
                    status,
                    reason,
                    _opt(fill_price),
                    _opt(spread_price),
                    _opt(slippage_price),
                    _opt(latency_ms),
                    _opt(volume),
                    signal_id,
                    json.dumps(sizing, sort_keys=True) if sizing else None,
                ),
            )
            return int(cursor.lastrowid or 0)

    def update_signal_outcome(
        self,
        row_id: int,
        status: str,
        *,
        reason: str | None = None,
        fill_price: float | None = None,
        spread_price: float | None = None,
        slippage_price: float | None = None,
        latency_ms: float | None = None,
        volume: float | None = None,
    ) -> None:
        if status not in SIGNAL_STATUSES:
            raise ValueError(f"Unknown signal status: {status}")
        patches = {
            "status": status,
            "reason": reason,
            "fill_price": _opt(fill_price),
            "spread_price": _opt(spread_price),
            "slippage_price": _opt(slippage_price),
            "latency_ms": _opt(latency_ms),
            "volume": _opt(volume),
        }
        known = {
            key: value
            for key, value in patches.items()
            if value is not None or key in {"status", "reason"}
        }
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            current = db.execute(
                "SELECT status, reason, fill_price, spread_price, slippage_price,"
                " latency_ms, volume FROM signals WHERE id=?",
                (row_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"Unknown signal row: {row_id}")
            names = [key for key in known if current[key] is None or key in {"status", "reason"}]
            if not names:
                return
            db.execute(
                f"UPDATE signals SET {', '.join(f'{name}=?' for name in names)} WHERE id=?",
                (*[known[name] for name in names], row_id),
            )

    def record_trade(
        self, run_id: str, record: TradeRecord, signal_row_id: int | None = None
    ) -> int:
        gross_r = record.gross_pnl / record.initial_risk_usd if record.initial_risk_usd else 0.0
        net_pnl = record.gross_pnl - record.costs_usd
        net_r = net_pnl / record.initial_risk_usd if record.initial_risk_usd else 0.0
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO trades (run_id,signal_row_id,ticket,direction,opened_at_utc,"
                "closed_at_utc,entry_price,exit_price,volume,initial_risk_usd,gross_pnl,"
                "costs_usd,gross_r,net_r,exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    signal_row_id,
                    record.ticket,
                    record.direction,
                    record.opened_at_utc.astimezone(UTC).isoformat(),
                    record.closed_at_utc.astimezone(UTC).isoformat(),
                    float(record.entry_price),
                    float(record.exit_price),
                    float(record.volume),
                    float(record.initial_risk_usd),
                    float(record.gross_pnl),
                    float(record.costs_usd),
                    float(gross_r),
                    float(net_r),
                    record.exit_reason,
                ),
            )
            return int(cursor.lastrowid or 0)

    def signals(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM signals WHERE run_id=?", (run_id,))]

    def trades(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM trades WHERE run_id=?", (run_id,))]

    def small_account_rows(self, run_id: str) -> list[dict[str, Any]]:
        """Small-account decisions, reported separately from headline stats."""
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM signals WHERE run_id=? AND sizing IS NOT NULL "
                    "AND json_extract(sizing,'$.small_account') = 1",
                    (run_id,),
                )
            ]


def _opt(value: Any) -> float | None:
    return None if value is None else float(value)
