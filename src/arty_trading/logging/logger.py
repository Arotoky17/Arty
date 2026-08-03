"""
Système de journalisation structuré pour Arty Trading.

Fournit :
- Logs console colorés (développement)
- Logs fichier rotatifs par catégorie
- Helpers pour signaux, trades et erreurs
- Intégration structlog pour le format JSON en production
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from arty_trading.core.enums import LogCategory


# Format des messages
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Taille max des fichiers de log (10 Mo) et nombre de backups
MAX_LOG_SIZE = 10 * 1024 * 1024
BACKUP_COUNT = 5

_loggers_initialized = False


def setup_logging(
    level: str = "INFO",
    logs_dir: str = "logs",
    app_env: str = "development",
) -> None:
    """
    Configure le système de logging global.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        logs_dir: Répertoire de stockage des fichiers de log
        app_env: Environnement (development = console colorée)
    """
    global _loggers_initialized
    if _loggers_initialized:
        return

    log_path = Path(logs_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configuration racine
    root_logger = logging.getLogger("arty_trading")
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Handler console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # Handlers fichier par catégorie
    for category in LogCategory:
        file_handler = RotatingFileHandler(
            log_path / f"{category.value}.log",
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
        # Filtre par catégorie via le nom du logger
        file_handler.addFilter(_CategoryFilter(category.value))
        root_logger.addHandler(file_handler)

    # Fichier erreurs dédié (tous niveaux ERROR+)
    error_handler = RotatingFileHandler(
        log_path / "errors.log",
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    root_logger.addHandler(error_handler)

    _loggers_initialized = True
    root_logger.info("Système de logging initialisé | env=%s | level=%s", app_env, level)


class _CategoryFilter(logging.Filter):
    """Filtre les logs par catégorie métier."""

    def __init__(self, category: str) -> None:
        super().__init__()
        self.category = category

    def filter(self, record: logging.LogRecord) -> bool:
        return self.category in record.name


def get_logger(category: LogCategory | str = LogCategory.SYSTEM) -> logging.Logger:
    """
    Retourne un logger nommé par catégorie.

    Args:
        category: Catégorie métier (LogCategory ou string)

    Returns:
        Logger configuré
    """
    cat = category.value if isinstance(category, LogCategory) else category
    return logging.getLogger(f"arty_trading.{cat}")


def log_signal(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    confidence: float,
    strategy: str,
    **extra: Any,
) -> None:
    """Enregistre un signal de trading."""
    logger = get_logger(LogCategory.SIGNAL)
    logger.info(
        "SIGNAL | %s %s | entry=%.5f sl=%.5f tp=%.5f conf=%.2f | strategy=%s | %s",
        direction.upper(),
        symbol,
        entry,
        sl,
        tp,
        confidence,
        strategy,
        extra,
    )


def log_trade(action: str, symbol: str, ticket: int | None = None, **extra: Any) -> None:
    """Enregistre une action sur un trade (open/close/modify)."""
    logger = get_logger(LogCategory.EXECUTION)
    logger.info(
        "TRADE %s | %s | ticket=%s | %s",
        action.upper(),
        symbol,
        ticket,
        extra,
    )


def log_error(message: str, exc: Exception | None = None, **extra: Any) -> None:
    """Enregistre une erreur avec stack trace optionnelle."""
    logger = get_logger(LogCategory.ERROR)
    if exc:
        logger.error("%s | %s", message, extra, exc_info=exc)
    else:
        logger.error("%s | %s", message, extra)
