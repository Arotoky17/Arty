"""
Synchroniseur de bougies (CandleSynchronizer).

Garantit qu'une bougie n'est **jamais** analysée deux fois par le moteur de
trading. Pour chaque symbole, il suit l'horodatage de la dernière bougie
*traitée* et détermine si une nouvelle bougie fermée est disponible.

Au démarrage du moteur, la méthode ``initialize`` permet d'enregistrer la
bougie actuelle sans la marquer comme « traitée » : le bot attendra
véritablement la **prochaine** bougie fermée avant de lancer l'analyse,
conformément au flux attendu :

    Nouvelle bougie → Téléchargement → Analyse → Validation → Risque
    → Trade → Monitoring → Attente prochaine bougie
"""

from __future__ import annotations

from datetime import datetime

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.SYSTEM)


class CandleSynchronizer:
    """
    Synchroniseur de bougies par symbole.

    Pour chaque symbole, on conserve l'horodatage de la dernière bougie
    **traitée** (i.e. qui a déjà fait l'objet d'une analyse complète). Tant
    qu'aucune bougie plus récente n'est observée, ``is_new_candle`` retourne
    ``False`` et le moteur sait qu'il doit attendre.

    Attributes:
        _last_processed: Dictionnaire ``symbol → datetime`` de la dernière
            bougie traitée.
    """

    def __init__(self) -> None:
        self._last_processed: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Vérifications
    # ------------------------------------------------------------------

    def is_new_candle(self, symbol: str, candle_time: datetime) -> bool:
        """
        Vérifie si ``candle_time`` correspond à une bougie non encore traitée.

        Retourne ``True`` dans deux cas :
        1. Le symbole n'a jamais été vu (premier appel).
        2. ``candle_time`` est strictement postérieur à la dernière bougie
           traitée pour ce symbole.

        Args:
            symbol: Symbole (ex: EURUSD).
            candle_time: Horodatage de la bougie à tester.

        Returns:
            ``True`` s'il s'agit d'une nouvelle bougie, ``False`` sinon.
        """
        last = self._last_processed.get(symbol)
        if last is None:
            return True
        return candle_time > last

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------

    def mark_processed(self, symbol: str, candle_time: datetime) -> None:
        """
        Marque la bougie ``candle_time`` comme traitée pour ``symbol``.

        À appérer **immédiatement après** avoir confirmé qu'il s'agit d'une
        nouvelle bougie, avant de lancer l'analyse. Cela garantit que, même
        si l'analyse échoue ou lève une exception, la bougie ne sera pas
        ré-analysée au cycle suivant.

        Args:
            symbol: Symbole (ex: EURUSD).
            candle_time: Horodatage de la bougie traitée.
        """
        self._last_processed[symbol] = candle_time
        logger.debug(
            "Bougie marquée comme traitée | %s | time=%s",
            symbol,
            candle_time.isoformat(),
        )

    def initialize(self, symbol: str, candle_time: datetime) -> None:
        """
        Initialise le synchroniseur pour un symbole au démarrage.

        Enregistre la bougie actuelle comme « déjà traitée » de sorte que le
        bot attende la **prochaine** bougie fermée avant d'analyser. Cela
        évite d'analyser une bougie partiellement formée au démarrage.

        Si le symbole a déjà été initialisé, l'appel est ignoré.

        Args:
            symbol: Symbole (ex: EURUSD).
            candle_time: Horodatage de la bougie actuelle.
        """
        if symbol in self._last_processed:
            return
        self._last_processed[symbol] = candle_time
        logger.info(
            "Symbole initialisé | %s | dernière bougie=%s | en attente de la prochaine",
            symbol,
            candle_time.isoformat(),
        )

    # ------------------------------------------------------------------
    # Accesseurs
    # ------------------------------------------------------------------

    def get_last_processed(self, symbol: str) -> datetime | None:
        """
        Retourne l'horodatage de la dernière bougie traitée pour ``symbol``.

        Args:
            symbol: Symbole (ex: EURUSD).

        Returns:
            ``datetime`` ou ``None`` si le symbole n'a jamais été traité.
        """
        return self._last_processed.get(symbol)

    def get_all_last_processed(self) -> dict[str, datetime]:
        """
        Retourne une copie du dictionnaire complet ``symbol → datetime``.

        Returns:
            Dictionnaire des dernières bougies traitées par symbole.
        """
        return dict(self._last_processed)

    def reset(self, symbol: str | None = None) -> None:
        """
        Réinitialise le synchroniseur.

        Args:
            symbol: Si fourni, ne réinitialise que ce symbole.
                Sinon, réinitialise tous les symboles.
        """
        if symbol is not None:
            self._last_processed.pop(symbol, None)
            logger.debug("Synchroniseur réinitialisé | %s", symbol)
        else:
            self._last_processed.clear()
            logger.debug("Synchroniseur réinitialisé (tous symboles)")