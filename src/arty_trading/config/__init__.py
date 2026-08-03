"""
Module Configuration
====================
Configuration centralisée via Pydantic Settings.
Tous les paramètres sont modifiables via variables d'environnement ou fichier .env
"""

from arty_trading.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
