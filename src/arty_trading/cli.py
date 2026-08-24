"""
CLI - Interface en ligne de commande.
"""

import argparse
import sys

import uvicorn

from arty_trading import __version__
from arty_trading.config import get_settings
from arty_trading.logging import setup_logging, get_logger
from arty_trading.core.enums import LogCategory


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        prog="arty",
        description="Arty - Plateforme de trading Forex (SMC/ICT + IA)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Commande: serve
    serve_parser = subparsers.add_parser("serve", help="Démarrer l'API FastAPI")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    # Commande: info
    subparsers.add_parser("info", help="Afficher la configuration")

    # Commande: version
    subparsers.add_parser("version", help="Afficher la version")

    args = parser.parse_args()
    settings = get_settings()
    setup_logging(level=settings.log_level, logs_dir=settings.logs_dir, app_env=settings.app_env)

    if args.command == "serve":
        uvicorn.run(
            "arty_trading.api.main:app",
            host=args.host or settings.api.host,
            port=args.port or settings.api.port,
            reload=settings.api.reload and settings.app_env == "development",
        )
    elif args.command == "info":
        logger = get_logger(LogCategory.SYSTEM)
        logger.info("=== Arty ===")
        logger.info("Version: %s", __version__)
        logger.info("Mode: %s", settings.trading_mode.value)
        logger.info("Marche specialise: %s uniquement", settings.active_symbol)
        logger.info(
            "Timeframes Gold: context=%s | htf=%s | entry=%s",
            settings.context_timeframe.value,
            settings.htf_timeframe.value,
            settings.entry_timeframe.value,
        )
        logger.info("Live trading: %s", settings.is_live_trading_enabled)
        logger.info("Trading actif MT5 (demo/live): %s", settings.is_trading_active)
    elif args.command == "version":
        print(f"Arty {__version__}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
