"""
CLI - Interface en ligne de commande.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from arty_trading import __version__
from arty_trading.config import get_settings
from arty_trading.core.enums import LogCategory
from arty_trading.logging import get_logger, setup_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée CLI.

    Accepte un tableau d'arguments optionnel pour faciliter les tests et le
    lancement via ``python -m arty_trading``.
    """
    parser = argparse.ArgumentParser(
        prog="arty",
        description="Arty - Plateforme de trading Forex (SMC/ICT + IA)",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Démarrer l'API FastAPI")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    subparsers.add_parser("info", help="Afficher la configuration")
    subparsers.add_parser("version", help="Afficher la version")
    backtest = subparsers.add_parser("backtest", help="Contrôle historique MT5 H4/H1/M5")
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--source", choices=("mt5", "csv"), default=None)
    backtest.add_argument("--from", dest="date_from", required=True)
    backtest.add_argument("--to", dest="date_to", required=True)
    backtest.add_argument("--config", type=Path, required=True)
    backtest.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "backtest":
        from arty_trading.modules.backtesting.control import run_control

        try:
            report = asyncio.run(
                run_control(
                    args.symbol,
                    args.date_from,
                    args.date_to,
                    args.config,
                    args.output,
                    source=args.source,
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Backtest impossible: {exc}", file=sys.stderr)
            return 2
        print(f"{report['total_trades']} trades; validation={report['validation_status']}")
        return 0 if report["validation_status"] == "passed" else 1

    settings = get_settings()
    setup_logging(level=settings.log_level, logs_dir=settings.logs_dir, app_env=settings.app_env)

    if args.command == "serve":
        uvicorn.run(
            "arty_trading.api.main:app",
            host=args.host or settings.api.host,
            port=args.port or settings.api.port,
            reload=settings.api.reload and settings.app_env == "development",
        )
        return 0

    if args.command == "info":
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
        return 0

    if args.command == "version":
        print(f"Arty {__version__}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
