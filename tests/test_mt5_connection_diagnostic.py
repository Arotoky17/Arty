from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from arty_trading.modules.backtesting.mt5_diagnostic import diagnose_mt5


@pytest.mark.parametrize("connected", [True, False])
def test_mt5_connection_diagnostic(connected: bool) -> None:
    client = Mock()
    client.initialize.return_value = connected
    client.last_error.return_value = (1, "Success") if connected else (-10005, "IPC timeout")
    client.terminal_info.return_value = SimpleNamespace(connected=True, maxbars=100000)
    result = diagnose_mt5("terminal64.exe", client)
    assert result["initialized"] == connected
    client.initialize.assert_called_once_with("terminal64.exe", timeout=10000)
    if connected:
        assert result["connected"]
        client.shutdown.assert_called_once()
    else:
        assert result["error"] == [-10005, "IPC timeout"]
        client.terminal_info.assert_not_called()
    client.order_send.assert_not_called()
