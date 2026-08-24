"""Tests de la restriction du marche a XAUUSD uniquement.

Verifient que tous les symboles hors XAUUSD ne peuvent plus etre analyses ni
executes, et que la configuration active contient exactement XAUUSD.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from arty_trading.config.settings import Settings


class TestActiveConfigSymbols:
    """TEST 1, 8 : contenu exact et taille de la liste active."""

    def test_active_config_contains_exactly_xauusd(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_SYMBOLS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.symbols_list == ["XAUUSD"]

    def test_supported_symbols_exactly_xauusd(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_SYMBOLS", raising=False)
        monkeypatch.delenv("ENABLE_LEGACY_SYMBOLS", raising=False)
        settings = Settings(_env_file=None, enable_legacy_symbols=False)
        assert settings.supported_symbols == ["XAUUSD"]


class TestExcludedSymbols:
    """TEST 2, 3 : GBPUSD/USDJPY non analyses."""

    @pytest.mark.asyncio
    async def test_gbpusd_not_supported(self):
        settings = Settings(_env_file=None, enable_legacy_symbols=False)
        assert "GBPUSD" not in settings.supported_symbols

    @pytest.mark.asyncio
    async def test_usdjpy_not_supported(self):
        settings = Settings(_env_file=None, enable_legacy_symbols=False)
        assert "USDJPY" not in settings.supported_symbols

    @pytest.mark.asyncio
    async def test_gbpusd_rejected_by_final_gate(self):
        from arty_trading.application.execution_guards import (
            final_gate_before_execution,
        )

        settings = MagicMock()
        settings.supported_symbols = ["XAUUSD"]
        settings.get_instrument_profile.return_value = None
        r = await final_gate_before_execution(
            "GBPUSD", MagicMock(), MagicMock(), settings
        )
        assert r is False

    @pytest.mark.asyncio
    async def test_usdjpy_rejected_by_final_gate(self):
        from arty_trading.application.execution_guards import (
            final_gate_before_execution,
        )

        settings = MagicMock()
        settings.supported_symbols = ["XAUUSD"]
        settings.get_instrument_profile.return_value = None
        r = await final_gate_before_execution(
            "USDJPY", MagicMock(), MagicMock(), settings
        )
        assert r is False


class TestAllowedSymbols:
    """TEST 4, 5 : XAUUSD accepte, EURUSD refuse."""

    def test_eurusd_not_supported(self):
        settings = Settings(_env_file=None, enable_legacy_symbols=False)
        assert "EURUSD" not in settings.supported_symbols

    def test_xauusd_supported(self):
        settings = Settings(_env_file=None, enable_legacy_symbols=False)
        assert "XAUUSD" in settings.supported_symbols


class TestDemoTestAdaptiveRestriction:
    """TEST 6, 7, 9 : script demo_test_adaptive restreint a XAUUSD."""

    @pytest.fixture()
    def demo_mod(self):
        import importlib.util
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "demo_test_adaptive", os.path.join(root, "demo_test_adaptive.py")
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["demo_test_adaptive"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_scans_only_xauusd(self, demo_mod):
        assert demo_mod.SYMBOLS == ["XAUUSD"]
        assert set(demo_mod.SYMBOLS) == demo_mod.ALLOWED_SYMBOLS

    def test_symbol_blocked_eurusd(self, demo_mod, capsys):
        assert demo_mod.send_order("EURUSD", "buy", 0.1, 1.0, 1.1) is None
        assert "SYMBOL BLOCKED | EURUSD" in capsys.readouterr().out

    def test_symbol_blocked_gbpusd(self, demo_mod, capsys):
        assert demo_mod.send_order("GBPUSD", "buy", 0.1, 1.0, 1.1) is None
        assert "SYMBOL BLOCKED | GBPUSD" in capsys.readouterr().out

    def test_symbol_blocked_usdjpy(self, demo_mod, capsys):
        assert demo_mod.send_order("USDJPY", "sell", 0.1, 150.0, 149.0) is None
        assert "SYMBOL BLOCKED | USDJPY" in capsys.readouterr().out

    def test_scan_once_asserts_on_unallowed_symbol(self, demo_mod, monkeypatch):
        monkeypatch.setattr(demo_mod, "SYMBOLS", ["GBPUSD"])
        import asyncio

        detector = MagicMock()
        generator = MagicMock()
        with pytest.raises(AssertionError):
            asyncio.run(
                demo_mod.scan_once(detector, generator, 10000.0, 0.01, [])
            )


def test_env_file_default_symbols_are_xauusd():
    """Le fichier .env actif ne doit lister que XAUUSD (si present)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        pytest.skip("pas de fichier .env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEFAULT_SYMBOLS="):
                value = line.split("=", 1)[1].strip()
                assert [s.strip().upper() for s in value.split(",")] == ["XAUUSD"]
                return
    pytest.fail("DEFAULT_SYMBOLS absent du .env")
