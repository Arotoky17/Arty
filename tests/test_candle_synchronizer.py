"""Tests du CandleSynchronizer.

Vérifie que le synchroniseur :
- Détecte correctement les nouvelles bougies
- Marque les bougies comme traitées
- S'initialise au démarrage sans déclencher d'analyse
- Ne ré-analyse jamais une bougie déjà traitée
"""

from __future__ import annotations

from datetime import UTC, datetime

from arty_trading.application.candle_synchronizer import CandleSynchronizer


# =============================================================================
# Helpers
# =============================================================================


def make_time(hour: int = 12) -> datetime:
    """Crée un datetime UTC de test."""
    return datetime(2024, 1, 1, hour, 0, 0, tzinfo=UTC)


# =============================================================================
# Tests : is_new_candle
# =============================================================================


class TestIsNewCandle:
    """Vérifie la détection de nouvelle bougie."""

    def test_new_symbol_returns_true(self) -> None:
        """Un symbole jamais vu doit retourner True (première bougie)."""
        sync = CandleSynchronizer()
        assert sync.is_new_candle("EURUSD", make_time(12)) is True

    def test_same_time_returns_false(self) -> None:
        """La même bougie (même time) doit retourner False après traitement."""
        sync = CandleSynchronizer()
        t = make_time(12)
        sync.mark_processed("EURUSD", t)
        assert sync.is_new_candle("EURUSD", t) is False

    def test_later_time_returns_true(self) -> None:
        """Une bougie plus récente doit retourner True."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        assert sync.is_new_candle("EURUSD", make_time(13)) is True

    def test_earlier_time_returns_false(self) -> None:
        """Une bougie plus ancienne doit retourner False."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        assert sync.is_new_candle("EURUSD", make_time(11)) is False

    def test_multiple_symbols_independent(self) -> None:
        """Les symboles sont indépendants."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        # GBPUSD n'a jamais été traité → True
        assert sync.is_new_candle("GBPUSD", make_time(12)) is True
        # EURUSD a été traité → False
        assert sync.is_new_candle("EURUSD", make_time(12)) is False


# =============================================================================
# Tests : mark_processed
# =============================================================================


class TestMarkProcessed:
    """Vérifie le marquage des bougies traitées."""

    def test_mark_processed_updates_last(self) -> None:
        """mark_processed met à jour la dernière bougie traitée."""
        sync = CandleSynchronizer()
        t = make_time(12)
        sync.mark_processed("EURUSD", t)
        assert sync.get_last_processed("EURUSD") == t

    def test_mark_processed_overwrites(self) -> None:
        """mark_processed écrase l'ancienne valeur."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        sync.mark_processed("EURUSD", make_time(13))
        assert sync.get_last_processed("EURUSD") == make_time(13)

    def test_mark_processed_different_symbols(self) -> None:
        """mark_processed gère plusieurs symboles indépendamment."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        sync.mark_processed("GBPUSD", make_time(13))
        assert sync.get_last_processed("EURUSD") == make_time(12)
        assert sync.get_last_processed("GBPUSD") == make_time(13)


# =============================================================================
# Tests : initialize
# =============================================================================


class TestInitialize:
    """Vérifie l'initialisation au démarrage."""

    def test_initialize_sets_last_processed(self) -> None:
        """initialize enregistre la bougie actuelle comme traitée."""
        sync = CandleSynchronizer()
        t = make_time(12)
        sync.initialize("EURUSD", t)
        assert sync.get_last_processed("EURUSD") == t

    def test_initialize_blocks_new_candle(self) -> None:
        """Après initialize, la même bougie ne doit pas être 'nouvelle'."""
        sync = CandleSynchronizer()
        t = make_time(12)
        sync.initialize("EURUSD", t)
        assert sync.is_new_candle("EURUSD", t) is False

    def test_initialize_allows_later_candle(self) -> None:
        """Après initialize, une bougie plus récente doit être 'nouvelle'."""
        sync = CandleSynchronizer()
        sync.initialize("EURUSD", make_time(12))
        assert sync.is_new_candle("EURUSD", make_time(13)) is True

    def test_initialize_idempotent(self) -> None:
        """initialize ne doit pas écraser un symbole déjà initialisé."""
        sync = CandleSynchronizer()
        sync.initialize("EURUSD", make_time(12))
        # Une deuxième initialisation avec une heure différente ne doit
        # pas écraser la première
        sync.initialize("EURUSD", make_time(14))
        assert sync.get_last_processed("EURUSD") == make_time(12)


# =============================================================================
# Tests : get_all_last_processed
# =============================================================================


class TestGetAllLastProcessed:
    """Vérifie la récupération globale."""

    def test_empty_initially(self) -> None:
        """Au départ, le dictionnaire est vide."""
        sync = CandleSynchronizer()
        assert sync.get_all_last_processed() == {}

    def test_returns_all_symbols(self) -> None:
        """Retourne tous les symboles traités."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        sync.mark_processed("GBPUSD", make_time(13))
        result = sync.get_all_last_processed()
        assert set(result.keys()) == {"EURUSD", "GBPUSD"}

    def test_returns_copy(self) -> None:
        """Retourne une copie (modification ne affecte pas l'original)."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        result = sync.get_all_last_processed()
        result["GBPUSD"] = make_time(13)
        # L'original ne doit pas être modifié
        assert "GBPUSD" not in sync.get_all_last_processed()


# =============================================================================
# Tests : reset
# =============================================================================


class TestReset:
    """Vérifie la réinitialisation."""

    def test_reset_single_symbol(self) -> None:
        """reset(symbol) ne réinitialise que ce symbole."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        sync.mark_processed("GBPUSD", make_time(13))
        sync.reset("EURUSD")
        assert sync.get_last_processed("EURUSD") is None
        assert sync.get_last_processed("GBPUSD") == make_time(13)

    def test_reset_all(self) -> None:
        """reset() sans argument réinitialise tout."""
        sync = CandleSynchronizer()
        sync.mark_processed("EURUSD", make_time(12))
        sync.mark_processed("GBPUSD", make_time(13))
        sync.reset()
        assert sync.get_all_last_processed() == {}

    def test_reset_nonexistent_symbol(self) -> None:
        """reset sur un symbole inexistant ne lève pas d'erreur."""
        sync = CandleSynchronizer()
        sync.reset("EURUSD")  # Ne doit pas lever d'erreur
        assert sync.get_last_processed("EURUSD") is None