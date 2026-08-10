"""Journal durable des trades : CSV, JSON et SQLite."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from arty_trading.core.entities import Trade


class TradeJournal:
    """Persiste les ouvertures et fermetures dans trois formats locaux."""

    def __init__(self, directory: str = "data/journal") -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._database = self._directory / "trades.sqlite"
        with sqlite3.connect(self._database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY, symbol TEXT, direction TEXT, entry_price TEXT,
                stop_loss TEXT, take_profit TEXT, volume TEXT, ticket INTEGER,
                opened_at TEXT, closed_at TEXT, close_price TEXT, profit TEXT,
                strategy TEXT, exit_reason TEXT, metadata TEXT)"""
            )

    def record(self, trade: Trade, exit_reason: str = "") -> None:
        """Ajoute ou remplace le trade, puis régénère les exports CSV et JSON."""
        item = self._serialize(trade, exit_reason)
        with sqlite3.connect(self._database) as connection:
            fields = ", ".join(item)
            values = ", ".join(f":{name}" for name in item)
            connection.execute(f"INSERT OR REPLACE INTO trades ({fields}) VALUES ({values})", item)
        rows = self.rows()
        (self._directory / "trades.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        with (self._directory / "trades.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(item))
            writer.writeheader()
            writer.writerows(rows)

    def rows(self) -> list[dict[str, Any]]:
        """Retourne les trades persistés dans l'ordre chronologique."""
        with sqlite3.connect(self._database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM trades ORDER BY opened_at")]

    @staticmethod
    def _serialize(trade: Trade, exit_reason: str) -> dict[str, Any]:
        return {
            "id": str(trade.id), "symbol": trade.symbol, "direction": trade.direction.value,
            "entry_price": str(trade.entry_price), "stop_loss": str(trade.stop_loss),
            "take_profit": str(trade.take_profit), "volume": str(trade.volume), "ticket": trade.ticket,
            "opened_at": trade.opened_at.isoformat(),
            "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            "close_price": str(trade.close_price) if trade.close_price is not None else None,
            "profit": str(trade.profit) if trade.profit is not None else None,
            "strategy": trade.strategy_name, "exit_reason": exit_reason,
            "metadata": json.dumps(trade.metadata, default=str),
        }
