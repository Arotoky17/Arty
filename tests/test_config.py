"""Tests du module configuration."""

import os
from unittest.mock import patch

import pytest

from arty_trading.config.settings import RiskSettings, Settings, get_settings
from arty_trading.core.enums import TimeFrame, TradingMode


class TestSettings:
    """Tests de la configuration Pydantic."""

    def test_default_symbols(self):
        settings = Settings()
        assert settings.symbols_list == ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

    def test_default_trading_mode_is_demo(self):
        settings = Settings()
        assert settings.trading_mode == TradingMode.DEMO
        assert not settings.is_live_trading_enabled

    def test_live_trading_blocked_without_permission(self):
        with patch.dict(os.environ, {"TRADING_MODE": "real", "ALLOW_LIVE_TRADING": "false"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.trading_mode == TradingMode.DEMO
            get_settings.cache_clear()

    def test_live_trading_allowed_when_explicit(self):
        with patch.dict(os.environ, {"TRADING_MODE": "real", "ALLOW_LIVE_TRADING": "true"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.trading_mode == TradingMode.REAL
            assert settings.is_live_trading_enabled
            get_settings.cache_clear()

    def test_default_timeframe(self):
        settings = Settings()
        assert settings.default_timeframe == TimeFrame.H1

    def test_risk_settings_defaults(self):
        settings = Settings()
        assert settings.risk.risk_per_trade == 0.01
        assert settings.risk.max_open_positions == 3
        assert settings.risk.one_trade_per_symbol is True

    def test_risk_validation_rejects_invalid(self):
        with pytest.raises(ValueError):
            RiskSettings(RISK_PER_TRADE=1.5)

    def test_custom_symbols_from_env(self):
        with patch.dict(os.environ, {"DEFAULT_SYMBOLS": "EURUSD,GBPUSD"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.symbols_list == ["EURUSD", "GBPUSD"]
            get_settings.cache_clear()

    def test_get_settings_singleton(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        get_settings.cache_clear()
