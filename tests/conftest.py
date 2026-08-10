"""Configuration partagée des tests.

Garantit que la suite de tests est **indépendante d'un fichier ``.env``
local** : le cache du singleton ``Settings`` (``get_settings``) est vidé
avant et après chaque test afin d'éviter toute contamination entre les tests.
Chaque test injecte explicitement les variables d'environnement dont il a
besoin (via ``patch.dict`` ou ``monkeypatch``) au lieu de compter sur un
``.env`` présent sur la machine.
"""

import pytest

from arty_trading.config.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Vide le cache du singleton Settings avant et après chaque test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
