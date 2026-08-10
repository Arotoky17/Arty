"""Tests du filtre news et des exports du journal."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from arty_trading.application.trade_journal import TradeJournal
from arty_trading.config.settings import NewsSettings
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction
from arty_trading.modules.signals.news import EconomicCalendar


def test_red_news_blocks_matching_symbol(tmp_path) -> None:
    calendar = tmp_path / "calendar.json"
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    calendar.write_text(json.dumps([{"time": now.isoformat(), "currencies": ["USD"], "impact": "high"}]))
    settings = NewsSettings(enabled=True, calendar_file=str(calendar))
    assert EconomicCalendar(settings).is_blocked("EURUSD", now) is True


def test_journal_exports_csv_json_and_sqlite(tmp_path) -> None:
    journal = TradeJournal(str(tmp_path))
    journal.record(Trade(symbol="EURUSD", direction=Direction.BUY, entry_price=Decimal("1.1"), stop_loss=Decimal("1.0"), take_profit=Decimal("1.3"), volume=Decimal("0.1")))
    assert len(journal.rows()) == 1
    assert (tmp_path / "trades.csv").is_file()
    assert (tmp_path / "trades.json").is_file()
    assert (tmp_path / "trades.sqlite").is_file()
