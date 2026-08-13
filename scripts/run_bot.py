"""Lanceur du bot Arty en mode demo (ordres réels MT5 sur compte démo).

Usage : python scripts/run_bot.py [--host HOST] [--port PORT]
"""
from __future__ import annotations

import argparse
import uvicorn

from arty_trading.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Lance le bot Arty (mode demo)")
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute")
    parser.add_argument("--port", type=int, default=8000, help="Port d'écoute")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Arty | mode={settings.trading_mode.value} | symbols={settings.symbols_list} | tf={settings.default_timeframe.value}")
    print(f"MT5 path={settings.mt5.path} | demo=MT5_AVAILABLE auto")

    uvicorn.run(
        "arty_trading.api.main:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()