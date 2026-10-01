"""Test rapide de connexion MT5 avec le compte démo."""
import asyncio
import sys

sys.path.insert(0, "src")

from arty_trading.config.settings import get_settings
from arty_trading.infrastructure.mt5.connector import MT5Connector, MT5_AVAILABLE


async def main():
    print(f"MT5_AVAILABLE: {MT5_AVAILABLE}")
    settings = get_settings()
    print(f"Login: {settings.mt5.login}")
    print(f"Server: {settings.mt5.server}")
    print(f"Trading mode: {settings.trading_mode}")

    connector = MT5Connector(settings=settings)
    print("Tentative de connexion...")
    result = await connector.connect()
    print(f"Connect result: {result}")

    if result:
        status = await connector.get_connection_status()
        print(f"Status: {status}")
        account = await connector.get_account_info()
        print(f"Account: login={account.login}, server={account.server}, balance={account.balance}, mode={account.mode}")
        await connector.disconnect()
        print("Deconnecte.")
    else:
        print("Echec de connexion - mode degrade.")


if __name__ == "__main__":
    asyncio.run(main())