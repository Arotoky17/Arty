"""Tests du module configuration."""

import os
from unittest.mock import patch

import pytest

from arty_trading.config.settings import (
    RiskSettings,
    Settings,
    SignalSettings,
    get_settings,
)
from arty_trading.core.enums import TimeFrame, TradingMode


class TestSettings:
    def test_debug_release_value_is_safe(self, monkeypatch):
        """Une variable DEBUG externe ne doit pas empêcher le démarrage."""
        monkeypatch.setenv("DEBUG", "release")
        assert Settings().debug is False

    """Tests de la configuration Pydantic."""

    def test_default_symbols(self, monkeypatch):
        """Le défaut ne doit pas dépendre du fichier .env local."""
        monkeypatch.delenv("DEFAULT_SYMBOLS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.symbols_list == ["XAUUSD"]

    def test_default_trading_mode_is_analysis(self, monkeypatch):
        """Le mode par défaut doit être ANALYSIS (sécurité).

        Important : ce test ne doit pas dépendre du fichier ``.env`` local
        (qui peut activer DEMO/autre). On force donc l'ignorance du ``.env``
        et l'absence de variable ``TRADING_MODE`` pour vérifier la vraie
        valeur par défaut.
        """
        monkeypatch.delenv("TRADING_MODE", raising=False)
        settings = Settings(_env_file=None)
        assert settings.trading_mode == TradingMode.ANALYSIS
        assert not settings.is_live_trading_enabled
        assert settings.is_analysis_mode
        assert not settings.is_paper_mode
        assert not settings.is_live_mode

    def test_paper_mode_from_env(self):
        """Le mode PAPER doit être chargé depuis l'environnement."""
        with patch.dict(os.environ, {"TRADING_MODE": "paper"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.trading_mode == TradingMode.PAPER
            assert settings.is_paper_mode
            assert not settings.is_analysis_mode
            assert not settings.is_live_mode
            get_settings.cache_clear()

    def test_live_trading_blocked_without_permission(self):
        """Le mode LIVE doit basculer en PAPER si ALLOW_LIVE_TRADING=false."""
        with patch.dict(os.environ, {"TRADING_MODE": "live", "ALLOW_LIVE_TRADING": "false"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.trading_mode == TradingMode.PAPER
            assert not settings.is_live_trading_enabled
            get_settings.cache_clear()

    def test_live_trading_allowed_when_explicit(self):
        """Le mode LIVE doit être activé si ALLOW_LIVE_TRADING=true."""
        with patch.dict(os.environ, {"TRADING_MODE": "live", "ALLOW_LIVE_TRADING": "true"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.trading_mode == TradingMode.LIVE
            assert settings.is_live_trading_enabled
            assert settings.is_live_mode
            get_settings.cache_clear()

    def test_default_timeframe(self):
        """Le timeframe doit être injecté explicitement via l'environnement."""
        with patch.dict(os.environ, {"DEFAULT_TIMEFRAME": "M5"}):
            get_settings.cache_clear()
            settings = Settings()
            assert settings.default_timeframe == TimeFrame.M5
            get_settings.cache_clear()

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


class TestSignalSettings:
    """Tests de la configuration du générateur de signaux."""

    def test_default_min_confidence(self):
        settings = SignalSettings()
        assert settings.min_confidence == 0.85

    def test_default_active_strategy(self):
        settings = SignalSettings()
        assert settings.active_strategy == "SMC Trend Following"

    def test_settings_includes_signal_config(self):
        settings = Settings()
        assert settings.signals.min_confidence == 0.85
        assert settings.signals.active_strategy == "SMC Trend Following"

    def test_min_confidence_from_env(self):
        with patch.dict(os.environ, {"SIGNAL_MIN_CONFIDENCE": "0.9"}):
            settings = SignalSettings()
            assert settings.min_confidence == 0.9

    def test_active_strategy_from_env(self):
        with patch.dict(os.environ, {"SIGNAL_ACTIVE_STRATEGY": "Breakout"}):
            settings = SignalSettings()
            assert settings.active_strategy == "Breakout"

    def test_min_confidence_validation_rejects_zero(self):
        with pytest.raises(ValueError):
            SignalSettings(SIGNAL_MIN_CONFIDENCE=0)

    def test_min_confidence_validation_rejects_negative(self):
        with pytest.raises(ValueError):
            SignalSettings(SIGNAL_MIN_CONFIDENCE=-0.1)

    def test_min_confidence_validation_rejects_above_one(self):
        with pytest.raises(ValueError):
            SignalSettings(SIGNAL_MIN_CONFIDENCE=1.5)

    def test_min_confidence_accepts_one(self):
        settings = SignalSettings(SIGNAL_MIN_CONFIDENCE=1.0)
        assert settings.min_confidence == 1.0
