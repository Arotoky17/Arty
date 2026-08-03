"""Tests du système de logging."""

import logging
from pathlib import Path

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger, log_error, log_signal, setup_logging


class TestLogging:
    def test_setup_logging_creates_log_dir(self, tmp_path):
        logs_dir = str(tmp_path / "logs")
        setup_logging(level="DEBUG", logs_dir=logs_dir, app_env="test")
        assert Path(logs_dir).exists()

    def test_get_logger_by_category(self):
        logger = get_logger(LogCategory.SIGNAL)
        assert "signal" in logger.name

    def test_log_signal_no_exception(self, caplog):
        setup_logging(level="DEBUG", logs_dir="logs", app_env="test")
        with caplog.at_level(logging.INFO):
            log_signal("EURUSD", "buy", 1.1000, 1.0950, 1.1100, 0.85, "smc_trend")
        assert "SIGNAL" in caplog.text

    def test_log_error_with_exception(self, caplog):
        setup_logging(level="DEBUG", logs_dir="logs", app_env="test")
        try:
            raise ValueError("test error")
        except ValueError as e:
            with caplog.at_level(logging.ERROR):
                log_error("Erreur test", exc=e)
        assert "Erreur test" in caplog.text
