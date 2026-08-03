"""Tests du module de données de marché et du cache."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from arty_trading.core.enums import TimeFrame
from arty_trading.infrastructure.cache import InMemoryCache
from arty_trading.infrastructure.mt5.market_data import (
    MT5MarketDataProvider,
    MarketDataError,
    SymbolNotFoundError,
)


# =============================================================================
# Tests du cache
# =============================================================================


class TestInMemoryCache:
    """Tests du cache en mémoire avec TTL."""

    def test_set_and_get(self):
        """Test set/get basique."""
        cache = InMemoryCache(default_ttl=60.0)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_key(self):
        """Test get d'une clé inexistante."""
        cache = InMemoryCache()
        assert cache.get("missing") is None

    def test_delete(self):
        """Test suppression d'une clé."""
        cache = InMemoryCache()
        cache.set("key1", "value1")
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_clear(self):
        """Test vidage du cache."""
        cache = InMemoryCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert len(cache) == 0

    def test_contains(self):
        """Test opérateur in."""
        cache = InMemoryCache()
        cache.set("key1", "value1")
        assert "key1" in cache
        assert "key2" not in cache

    def test_custom_ttl(self):
        """Test TTL personnalisé par clé."""
        cache = InMemoryCache(default_ttl=60.0)
        cache.set("short", "val", ttl=0.01)
        assert cache.get("short") == "val"
        import time

        time.sleep(0.02)
        assert cache.get("short") is None

    def test_cleanup_removes_expired(self):
        """Test que cleanup supprime les entrées expirées."""
        cache = InMemoryCache(default_ttl=0.01)
        cache.set("key1", "val1")
        cache.set("key2", "val2")
        import time

        time.sleep(0.02)
        removed = cache.cleanup()
        assert removed == 2
        assert len(cache) == 0


# =============================================================================
# Tests du provider de données de marché
# =============================================================================


@pytest.fixture
def mock_rates():
    """Mock des rates MT5 (tableau numpy structuré)."""
    dtype = [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("tick_volume", "i8"),
        ("spread", "i8"),
    ]
    return np.array(
        [
            (1700000000, 1.0850, 1.0860, 1.0845, 1.0855, 1000, 5),
            (1700003600, 1.0855, 1.0870, 1.0850, 1.0865, 1200, 3),
            (1700007200, 1.0865, 1.0880, 1.0860, 1.0875, 800, 4),
        ],
        dtype=dtype,
    )


@pytest.fixture
def mock_symbol_info():
    """Mock des infos d'un symbole MT5."""
    info = MagicMock()
    info.name = "EURUSD"
    info.digits = 5
    info.point = 0.00001
    info.volume_min = 0.01
    info.volume_max = 100.0
    info.volume_step = 0.01
    info.trade_mode = 0
    info.spread = 5
    return info


@pytest.fixture
def mock_tick():
    """Mock d'un tick MT5."""
    tick = MagicMock()
    tick.bid = 1.08500
    tick.ask = 1.08505
    tick.last = 1.08502
    tick.volume = 100
    tick.time = 1700000000
    return tick


@pytest.fixture
def mock_symbols_list():
    """Mock de la liste des symboles MT5."""
    s1 = MagicMock()
    s1.name = "EURUSD"
    s1.path = "Forex\\Major"
    s1.digits = 5
    s1.point = 0.00001
    s1.spread = 5

    s2 = MagicMock()
    s2.name = "GBPUSD"
    s2.path = "Forex\\Major"
    s2.digits = 5
    s2.point = 0.00001
    s2.spread = 7

    return [s1, s2]


class TestMT5MarketDataProvider:
    """Tests du provider de données de marché MT5."""

    def test_initialization(self):
        """Test l'initialisation du provider."""
        provider = MT5MarketDataProvider()
        assert provider is not None
        assert provider._cache is not None

    def test_initialization_with_custom_ttl(self):
        """Test l'initialisation avec TTL personnalisés."""
        provider = MT5MarketDataProvider(
            cache_ttl=10.0,
            spread_ttl=2.0,
            symbol_info_ttl=100.0,
        )
        assert provider._candles_ttl == 10.0
        assert provider._spread_ttl == 2.0
        assert provider._symbol_info_ttl == 100.0

    @pytest.mark.asyncio
    async def test_get_historical_mt5_unavailable(self):
        """Test get_historical en mode dégradé (MT5 non disponible)."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            df = await provider.get_historical(
                "EURUSD", TimeFrame.H1, datetime(2024, 1, 1, tzinfo=timezone.utc)
            )
            assert isinstance(df, pd.DataFrame)
            assert len(df) == 0

    @pytest.mark.asyncio
    async def test_get_latest_candles_mt5_unavailable(self):
        """Test get_latest_candles en mode dégradé."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            candles = await provider.get_latest_candles("EURUSD", TimeFrame.H1, 100)
            assert candles == []

    @pytest.mark.asyncio
    async def test_get_spread_mt5_unavailable(self):
        """Test get_spread en mode dégradé."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            spread = await provider.get_spread("EURUSD")
            assert spread == 0

    @pytest.mark.asyncio
    async def test_get_symbol_info_mt5_unavailable(self):
        """Test get_symbol_info en mode dégradé."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            info = await provider.get_symbol_info("EURUSD")
            assert info["name"] == "EURUSD"
            assert info["digits"] == 5

    @pytest.mark.asyncio
    async def test_get_available_symbols_mt5_unavailable(self):
        """Test get_available_symbols en mode dégradé."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            symbols = await provider.get_available_symbols()
            assert symbols == []

    @pytest.mark.asyncio
    async def test_get_tick_mt5_unavailable(self):
        """Test get_tick en mode dégradé."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            tick = await provider.get_tick("EURUSD")
            assert tick is None

    @pytest.mark.asyncio
    async def test_get_latest_candles_with_mock_mt5(self, mock_rates, mock_symbol_info):
        """Test get_latest_candles avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_from_pos.return_value = mock_rates
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                candles = await provider.get_latest_candles("EURUSD", TimeFrame.H1, 3)

                assert len(candles) == 3
                assert candles[0].symbol == "EURUSD"
                assert candles[0].timeframe == TimeFrame.H1
                assert isinstance(candles[0].open, Decimal)
                mock_mt5.copy_rates_from_pos.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_historical_with_mock_mt5(self, mock_rates, mock_symbol_info):
        """Test get_historical avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_range.return_value = mock_rates
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                start = datetime(2024, 1, 1, tzinfo=timezone.utc)
                end = datetime(2024, 1, 2, tzinfo=timezone.utc)
                df = await provider.get_historical("EURUSD", TimeFrame.H1, start, end)

                assert len(df) == 3
                assert "time" in df.columns
                assert "open" in df.columns
                assert "close" in df.columns
                mock_mt5.copy_rates_range.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_spread_with_mock_mt5(self, mock_tick, mock_symbol_info):
        """Test get_spread avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.symbol_info_tick.return_value = mock_tick
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                spread = await provider.get_spread("EURUSD")

                # spread = (ask - bid) / point = (1.08505 - 1.08500) / 0.00001 = 5
                assert spread == 5

    @pytest.mark.asyncio
    async def test_get_symbol_info_with_mock_mt5(self, mock_symbol_info):
        """Test get_symbol_info avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_info.return_value = mock_symbol_info

                provider = MT5MarketDataProvider()
                info = await provider.get_symbol_info("EURUSD")

                assert info["name"] == "EURUSD"
                assert info["digits"] == 5
                assert info["point"] == 0.00001
                assert info["volume_min"] == 0.01
                assert info["volume_max"] == 100.0

    @pytest.mark.asyncio
    async def test_get_available_symbols_with_mock_mt5(self, mock_symbols_list):
        """Test get_available_symbols avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbols_get.return_value = mock_symbols_list

                provider = MT5MarketDataProvider()
                symbols = await provider.get_available_symbols()

                assert len(symbols) == 2
                assert symbols[0]["name"] == "EURUSD"
                assert symbols[1]["name"] == "GBPUSD"

    @pytest.mark.asyncio
    async def test_get_tick_with_mock_mt5(self, mock_tick, mock_symbol_info):
        """Test get_tick avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.symbol_info_tick.return_value = mock_tick

                provider = MT5MarketDataProvider()
                tick = await provider.get_tick("EURUSD")

                assert tick is not None
                assert tick["symbol"] == "EURUSD"
                assert tick["bid"] == 1.08500
                assert tick["ask"] == 1.08505

    @pytest.mark.asyncio
    async def test_cache_hit_for_latest_candles(self, mock_rates, mock_symbol_info):
        """Test que le cache retourne les données mises en cache."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_from_pos.return_value = mock_rates
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                # Premier appel - récupère depuis MT5
                candles1 = await provider.get_latest_candles("EURUSD", TimeFrame.H1, 3)
                assert len(candles1) == 3

                # Deuxième appel - doit utiliser le cache
                candles2 = await provider.get_latest_candles("EURUSD", TimeFrame.H1, 3)
                assert len(candles2) == 3

                # copy_rates_from_pos ne doit être appelé qu'une seule fois
                assert mock_mt5.copy_rates_from_pos.call_count == 1

    @pytest.mark.asyncio
    async def test_clear_cache(self, mock_rates, mock_symbol_info):
        """Test que clear_cache vide le cache."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_from_pos.return_value = mock_rates
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                await provider.get_latest_candles("EURUSD", TimeFrame.H1, 3)
                assert len(provider._cache) > 0

                provider.clear_cache()
                assert len(provider._cache) == 0

    @pytest.mark.asyncio
    async def test_get_latest_candles_empty_response(self, mock_symbol_info):
        """Test get_latest_candles quand MT5 retourne None."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_from_pos.return_value = None
                mock_mt5.last_error.return_value = (-1, "No data")

                provider = MT5MarketDataProvider()
                candles = await provider.get_latest_candles("EURUSD", TimeFrame.H1, 100)
                assert candles == []

    @pytest.mark.asyncio
    async def test_get_historical_empty_response(self, mock_symbol_info):
        """Test get_historical quand MT5 retourne None."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_range.return_value = None
                mock_mt5.last_error.return_value = (-1, "No data")

                provider = MT5MarketDataProvider()
                start = datetime(2024, 1, 1, tzinfo=timezone.utc)
                df = await provider.get_historical("EURUSD", TimeFrame.H1, start)
                assert len(df) == 0

    @pytest.mark.asyncio
    async def test_symbol_not_found(self):
        """Test SymbolNotFoundError quand le symbole n'existe pas."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_info.return_value = None

                provider = MT5MarketDataProvider()
                with pytest.raises(SymbolNotFoundError):
                    await provider.get_symbol_info("UNKNOWN")

    @pytest.mark.asyncio
    async def test_subscribe_ticks_mt5_unavailable(self):
        """Test subscribe_ticks lève une erreur si MT5 non disponible."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", False):
            provider = MT5MarketDataProvider()
            with pytest.raises(MarketDataError):
                async for _ in provider.subscribe_ticks("EURUSD"):
                    pass

    @pytest.mark.asyncio
    async def test_get_latest_candles_count_clamped(self, mock_rates, mock_symbol_info):
        """Test que count est limité entre 1 et 1000."""
        with patch("arty_trading.infrastructure.mt5.market_data.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.market_data.mt5") as mock_mt5:
                mock_mt5.symbol_select.return_value = True
                mock_mt5.symbol_info.return_value = mock_symbol_info
                mock_mt5.copy_rates_from_pos.return_value = mock_rates
                mock_mt5.last_error.return_value = (0, "No error")

                provider = MT5MarketDataProvider()
                # count=5000 doit être clampé à 1000
                await provider.get_latest_candles("EURUSD", TimeFrame.H1, 5000)
                # Vérifier que copy_rates_from_pos a été appelé avec count=1000
                args = mock_mt5.copy_rates_from_pos.call_args
                assert args[0][3] == 1000  # 4ème argument positionnel = count