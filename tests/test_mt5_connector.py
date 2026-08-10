"""Tests du connecteur MetaTrader 5."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arty_trading.config.settings import MT5Settings, Settings
from arty_trading.core.enums import TradingMode
from arty_trading.infrastructure.mt5.connector import (
    MT5AccountError,
    MT5ConnectionError,
    MT5Connector,
    MT5TerminalError,
)


@pytest.fixture
def mock_settings() -> Settings:
    """Settings de test avec MT5 configuré."""
    return Settings(
        mt5=MT5Settings(
            login=12345,
            password="test_password",
            server="TestServer",
            path="",
            timeout=10000,
        ),
    )


@pytest.fixture
def mock_terminal_info():
    """Mock des infos du terminal MT5."""
    terminal = MagicMock()
    terminal.build = 4000
    terminal.connected = True
    terminal.trade_allowed = True
    terminal.community_account = False
    terminal.community_connection = False
    terminal.started = True
    terminal.dlls_allowed = True
    terminal.trade_expert = True
    terminal.code = 0
    return terminal


@pytest.fixture
def mock_account_info():
    """Mock des infos du compte MT5 (compte démo)."""
    account = MagicMock()
    account.login = 12345
    account.server = "TestServer"
    account.name = "Test User"
    account.currency = "USD"
    account.balance = 10000.0
    account.equity = 10000.0
    account.margin = 0.0
    account.margin_free = 10000.0
    account.leverage = 100
    account.trade_mode = 0  # 0 = démo
    return account


class TestMT5Connector:
    """Tests du connecteur MT5."""

    def test_mt5_connector_initialization(self, mock_settings):
        """Test l'initialisation du connecteur."""
        connector = MT5Connector(settings=mock_settings)
        assert connector is not None
        assert connector._mt5_settings.login == 12345
        assert connector._mt5_settings.server == "TestServer"

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_mt5_unavailable(self, mock_settings):
        """Test que connect() retourne False si MT5 n'est pas disponible."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", False):
            connector = MT5Connector(settings=mock_settings)
            result = await connector.connect()
            assert result is False

    @pytest.mark.asyncio
    async def test_is_connected_returns_false_when_not_connected(self, mock_settings):
        """Test is_connected() retourne False quand non connecté."""
        connector = MT5Connector(settings=mock_settings)
        assert await connector.is_connected() is False

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, mock_settings):
        """Test disconnect() sans connexion active."""
        connector = MT5Connector(settings=mock_settings)
        await connector.disconnect()
        assert connector._connected is False

    @pytest.mark.asyncio
    async def test_get_account_info_raises_when_not_connected(self, mock_settings):
        """Test get_account_info() lève une erreur si non connecté."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            connector = MT5Connector(settings=mock_settings)
            with pytest.raises(MT5ConnectionError):
                await connector.get_account_info()

    @pytest.mark.asyncio
    async def test_get_account_info_mock_mode(self, mock_settings):
        """Test get_account_info() en mode mock (MT5 non disponible)."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", False):
            connector = MT5Connector(settings=mock_settings)
            account = await connector.get_account_info()
            assert account.login == 0
            assert account.server == "mock"
            assert account.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_success_with_mock_mt5(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test connexion réussie avec MT5 mocké."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info
                mock_mt5.last_error.return_value = (0, "No error")

                connector = MT5Connector(settings=mock_settings)
                result = await connector.connect()

                assert result is True
                assert connector._connected is True
                mock_mt5.initialize.assert_called_once()
                mock_mt5.login.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure_on_initialize(self, mock_settings):
        """Test échec de connexion - initialize échoue."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = False
                mock_mt5.last_error.return_value = (-1, "Initialize failed")

                connector = MT5Connector(settings=mock_settings)
                result = await connector.connect()

                assert result is False
                assert connector._connected is False

    @pytest.mark.asyncio
    async def test_connect_failure_on_login(self, mock_settings, mock_terminal_info):
        """Test échec de connexion - login échoue."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = False
                mock_mt5.last_error.return_value = (-1, "Auth failed")

                connector = MT5Connector(settings=mock_settings)
                result = await connector.connect()

                assert result is False
                assert connector._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_connection(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test que disconnect() ferme la connexion."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                assert connector._connected is True

                await connector.disconnect()
                assert connector._connected is False
                assert connector._account is None
                mock_mt5.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test la reconnexion."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                result = await connector.reconnect()

                assert result is True
                assert connector._connected is True

    @pytest.mark.asyncio
    async def test_get_connection_status(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test get_connection_status() retourne le bon statut."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                status = await connector.get_connection_status()

                assert status["connected"] is True
                assert status["mt5_available"] is True
                assert status["login"] == 12345
                assert status["server"] == "TestServer"
                assert status["account"] is not None
                assert status["account"]["login"] == 12345
                assert status["account"]["is_demo"] is True

    @pytest.mark.asyncio
    async def test_get_terminal_info(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test get_terminal_info() retourne les infos du terminal."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                terminal = await connector.get_terminal_info()

                assert terminal is not None
                assert terminal["build"] == 4000
                assert terminal["connected"] is True
                assert terminal["trade_allowed"] is True

    @pytest.mark.asyncio
    async def test_get_terminal_info_returns_none_when_not_connected(self, mock_settings):
        """Test get_terminal_info() retourne None si non connecté."""
        connector = MT5Connector(settings=mock_settings)
        result = await connector.get_terminal_info()
        assert result is None

    @pytest.mark.asyncio
    async def test_real_account_forced_to_demo_without_permission(
        self, mock_settings, mock_terminal_info
    ):
        """Test qu'un compte réel est forcé en démo sans autorisation."""
        real_account = MagicMock()
        real_account.login = 12345
        real_account.server = "TestServer"
        real_account.name = "Test User"
        real_account.currency = "USD"
        real_account.balance = 10000.0
        real_account.equity = 10000.0
        real_account.margin = 0.0
        real_account.margin_free = 10000.0
        real_account.leverage = 100
        real_account.trade_mode = 1  # 1 = réel

        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = real_account

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                account = await connector.get_account_info()
                assert account.mode == TradingMode.PAPER
                assert account.is_demo is True

    @pytest.mark.asyncio
    async def test_is_connected_detects_lost_connection(
        self, mock_settings, mock_terminal_info, mock_account_info
    ):
        """Test que is_connected() détecte une perte de connexion."""
        with patch("arty_trading.infrastructure.mt5.connector.MT5_AVAILABLE", True):
            with patch("arty_trading.infrastructure.mt5.connector.mt5") as mock_mt5:
                mock_mt5.initialize.return_value = True
                mock_mt5.terminal_info.return_value = mock_terminal_info
                mock_mt5.login.return_value = True
                mock_mt5.account_info.return_value = mock_account_info

                connector = MT5Connector(settings=mock_settings)
                await connector.connect()
                assert await connector.is_connected() is True

                # Simuler une perte de connexion
                mock_terminal_info.connected = False
                assert await connector.is_connected() is False
                assert connector._connected is False