"""
Suivi temps réel des Order Blocks « frais » (non mitigés) et « morts » (mitigés).

Un Order Block perd sa valeur dès que le prix revient le mitiger : le bot ne doit
plus le trader. Ce module tient l'état de vie de chaque OB suivi, bougie M5 après
bougie M5, **sans aucune dépendance broker** (ni MT5, ni base de données) : il est
donc utilisable à l'identique en **backtest** et en **live**.

Règles de mitigation
--------------------
- **OB bullish** : mitigé si une bougie M5 **postérieure** a son ``low <= OB.high``.
- **OB bearish** : mitigé si une bougie M5 **postérieure** a son ``high >= OB.low``.

La mitigation est **définitive** (un OB mitigé le reste) : ``mitigated_at`` est
figé sur l'horodatage de la bougie qui a mitigué l'OB.

Cycle de vie
------------
1. ``register(ob)`` : un OB détecté par le moteur SMC entre dans le suivi.
2. ``update(new_m5_candle)`` à chaque bougie M5 clôturée : ``age_bars`` +1 puis
   test de mitigation de tous les OB suivis. Le tracker élague automatiquement
   tous les ``PRUNE_EVERY`` (=100) updates.
3. ``get_fresh()`` : les OB encore exploitables (non mitigés **et** non expirés).
4. ``get_mitigated()`` : les OB morts (logs / debug).

Le module est volontairement déterministe (aucun accès horloge sauf défaut de
``created_at``) et tolérant aux données incomplètes : une bougie sans
``low``/``high`` exploitable n'incrémente pas les compteurs de mitigation et ne
lève jamais d'exception.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.SMC)

#: Directions acceptées par le tracker.
DIRECTIONS: tuple[str, ...] = ("bullish", "bearish")

#: Intervalle d'élagage automatique (en nombre d'appels à ``update``).
PRUNE_EVERY: int = 100


@dataclass
class TrackedOB:
    """Order Block suivi : zone, âge et état de mitigation.

    Attributes:
        ob_id: Identifiant stable (uuid par défaut, voir ``TrackedOB.create``).
        symbol: Symbole (ex. ``XAUUSD``).
        timeframe: Timeframe d'origine de l'OB (``M5``, ``H1``, ``H4``).
        direction: ``bullish`` ou ``bearish``.
        high: Borne haute de la zone OB.
        low: Borne basse de la zone OB.
        midpoint: Milieu de la zone ``(high + low) / 2``.
        created_at: Horodatage de création de l'OB.
        mitigated: ``True`` dès que l'OB a été mitigé (définitif).
        mitigated_at: Horodatage de la bougie ayant mitigé l'OB (``None`` si la
            bougie ne portait pas d'horodatage exploitable).
        grade: Grade de qualité (rempli par le scorer, ex. ``A``/``B``/``C``).
        age_bars: Nombre de bougies M5 écoulées depuis l'enregistrement.
    """

    ob_id: str
    symbol: str
    timeframe: str
    direction: str
    high: float
    low: float
    midpoint: float
    created_at: datetime
    mitigated: bool = False
    mitigated_at: datetime | None = None
    grade: str | None = None
    age_bars: int = 0

    @staticmethod
    def new_id() -> str:
        """Génère un identifiant stable d'Order Block."""
        return uuid.uuid4().hex

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        timeframe: str,
        direction: str,
        high: float,
        low: float,
        created_at: datetime | None = None,
        ob_id: str | None = None,
        grade: str | None = None,
    ) -> TrackedOB:
        """Fabrique un ``TrackedOB`` (``ob_id`` et ``midpoint`` calculés).

        Args:
            symbol: Symbole suivi.
            timeframe: Timeframe d'origine (``M5``, ``H1``, ``H4``).
            direction: ``bullish`` ou ``bearish``.
            high: Borne haute de la zone.
            low: Borne basse de la zone.
            created_at: Horodatage de création (défaut : maintenant en UTC).
            ob_id: Identifiant explicite (défaut : nouvel uuid).
            grade: Grade de qualité déjà connu (optionnel).

        Returns:
            Le ``TrackedOB`` prêt à être enregistré.
        """
        return cls(
            ob_id=ob_id or cls.new_id(),
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            high=float(high),
            low=float(low),
            midpoint=(float(high) + float(low)) / 2.0,
            created_at=created_at or datetime.now(UTC),
            grade=grade,
        )

    @property
    def is_fresh(self) -> bool:
        """``True`` tant que l'OB n'a pas été mitigé."""
        return not self.mitigated

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (logs, API, persistance)."""
        return {
            "ob_id": self.ob_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "high": self.high,
            "low": self.low,
            "midpoint": self.midpoint,
            "created_at": self.created_at.isoformat(),
            "mitigated": self.mitigated,
            "mitigated_at": self.mitigated_at.isoformat() if self.mitigated_at else None,
            "grade": self.grade,
            "age_bars": self.age_bars,
        }


# ---------------------------------------------------------------------------
# Utilitaires internes (purs, aucune I/O)
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Convertit une valeur en ``float`` fini, ou ``None`` si inexploitable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def _cell(row: pd.Series, key: str) -> float | None:
    """Lit une cellule numérique d'une bougie pandas (``None`` si absente/NaN)."""
    try:
        raw = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    return _to_float(raw)


def _to_datetime(value: Any) -> datetime | None:
    """Convertit un horodatage en ``datetime``, ou ``None`` si inexploitable."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            timestamp: datetime = pd.Timestamp(value).to_pydatetime()
        except (ValueError, TypeError):
            return None
        return timestamp
    return None


def _candle_timestamp(row: pd.Series) -> datetime | None:
    """Horodatage d'une bougie (``timestamp``, ``time`` ou ``date``)."""
    for key in ("timestamp", "time", "date"):
        try:
            raw = row[key]
        except (KeyError, IndexError, TypeError):
            continue
        timestamp = _to_datetime(raw)
        if timestamp is not None:
            return timestamp
    return None


def _timestamp_before(
    candidate: datetime, reference: datetime, *, inclusive: bool = False
) -> bool:
    """Compare des horodatages en tolérant les fuseaux mixtes (aware/naive)."""
    left = pd.Timestamp(candidate)
    right = pd.Timestamp(reference)
    if (left.tzinfo is None) != (right.tzinfo is None):
        left = left.tz_localize(None) if left.tzinfo is not None else left
        right = right.tz_localize(None) if right.tzinfo is not None else right
    return bool(left <= right) if inclusive else bool(left < right)


class OrderBlockTracker:
    """Suivi « frais / mitigé » des Order Blocks (pur, sans broker).

    Le tracker ne détecte rien : il reçoit des OB déjà détectés
    (``register``) et consomme les bougies M5 clôturées (``update``) pour
    maintenir leur état de vie. Utilisable en backtest (itération bougie par
    bougie) comme en live (callback M5), sans aucune dépendance MT5/DB.

    Args:
        max_age_bars: Durée de vie maximale d'un OB en bougies M5 (défaut 200).
            Au-delà, l'OB est expiré et supprimé par ``prune_expired``.

    Raises:
        ValueError: si ``max_age_bars`` n'est pas strictement positif.

    Note:
        L'instance n'est pas thread-safe : elle est conçue pour une boucle
        d'événements unique (asyncio), comme le reste du moteur.
    """

    #: Intervalle d'élagage automatique, en nombre d'appels à ``update``.
    prune_every: int = PRUNE_EVERY

    def __init__(self, max_age_bars: int = 200) -> None:
        if max_age_bars <= 0:
            raise ValueError("max_age_bars doit être strictement positif")
        self._max_age_bars = max_age_bars
        self._obs: dict[str, TrackedOB] = {}
        self._used: dict[str, str] = {}
        self._updates = 0

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------

    @property
    def max_age_bars(self) -> int:
        """Durée de vie maximale d'un OB en bougies M5."""
        return self._max_age_bars

    @property
    def updates(self) -> int:
        """Nombre d'appels à ``update`` depuis la création du tracker."""
        return self._updates

    @property
    def tracked_count(self) -> int:
        """Nombre d'OB actuellement suivis (frais + mitigés non élagués)."""
        return len(self._obs)

    def __len__(self) -> int:
        """Alias de ``tracked_count``."""
        return len(self._obs)

    def is_registered(self, ob: TrackedOB | str) -> bool:
        """Indique si l'OB est déjà suivi sans réinitialiser son état."""
        ob_id = ob.ob_id if isinstance(ob, TrackedOB) else ob
        return ob_id in self._obs

    def get_all(self) -> list[TrackedOB]:
        """Retourne tous les OB suivis, frais comme mitigés."""
        return list(self._obs.values())

    def is_used(self, ob_id: str) -> bool:
        """Indique si un OB a déjà produit un signal."""
        return ob_id in self._used

    def mark_as_used(self, ob_id: str, signal_id: str) -> bool:
        """Empêche la réutilisation d'un OB après la création d'un signal."""
        if ob_id not in self._obs:
            return False
        self._used[ob_id] = signal_id
        return True

    def register(self, ob: TrackedOB) -> None:
        """Enregistre un nouvel Order Block à suivre.

        Args:
            ob: Order Block détecté (voir ``TrackedOB.create``).

        Raises:
            ValueError: si ``ob_id`` est vide, si la direction est inconnue ou si
                ``high < low`` (erreur d'appel, pas une donnée de marché).

        Note:
            Un ``ob_id`` déjà suivi est ignoré (l'OB existant est conservé) afin
            de ne jamais réinitialiser un état de mitigation.
        """
        if not ob.ob_id:
            raise ValueError("ob_id requis")
        if ob.direction not in DIRECTIONS:
            raise ValueError(
                f"direction invalide: {ob.direction!r} (attendu: {DIRECTIONS})"
            )
        if ob.high < ob.low:
            raise ValueError("high doit être supérieur ou égal à low")
        if ob.ob_id in self._obs:
            logger.warning(
                "[OB TRACKER] ob_id déjà suivi (enregistrement ignoré) | %s", ob.ob_id
            )
            return

        self._obs[ob.ob_id] = ob
        logger.debug(
            "[OB TRACKED] %s | %s %s %s | zone=%.5f-%.5f | max_age=%d",
            ob.symbol,
            ob.timeframe,
            ob.direction,
            ob.ob_id,
            ob.low,
            ob.high,
            self._max_age_bars,
        )

    def register_with_history(
        self, ob: TrackedOB, candles_after: pd.DataFrame
    ) -> None:
        """Enregistre un OB et rejoue uniquement ses bougies M5 postérieures."""
        if self.is_registered(ob):
            return
        self.register(ob)
        for _, candle in candles_after.iterrows():
            self._update_tracked_ob(ob, candle)

    def update(self, new_m5_candle: pd.Series) -> None:
        """Met à jour l'état de mitigation de tous les OB suivis.

        ``age_bars`` est incrémenté pour chaque OB suivi, puis les OB encore
        frais sont testés contre la bougie M5 :

        - **bullish** : mitigé si ``low <= OB.high`` ;
        - **bearish** : mitigé si ``high >= OB.low``.

        Une bougie **antérieure** à ``created_at`` (flux multi-timeframe ou
        données désordonnées) ne peut pas mitiger un OB. Une bougie sans
        ``low``/``high`` exploitable est ignorée (seul l'âge avance).

        Args:
            new_m5_candle: Bougie M5 clôturée (``high``, ``low``, horodatage
                optionnel dans ``timestamp``/``time``/``date``).
        """
        self._updates += 1

        for ob in self._obs.values():
            self._update_tracked_ob(ob, new_m5_candle)

        if self._updates % self.prune_every == 0:
            removed = self.prune_expired()
            logger.debug(
                "[OB TRACKER] élagage automatique | updates=%d | supprimés=%d | restants=%d",
                self._updates,
                removed,
                len(self._obs),
            )

    def get_fresh(
        self, timeframe: str | None = None, *, exclude_used: bool = False
    ) -> list[TrackedOB]:
        """Retourne les OB non mitigés et non expirés.

        Args:
            timeframe: Filtre optionnel (``M5``, ``H1``, ``H4``, insensible à la
                casse). ``None`` → tous les timeframes.
            exclude_used: Exclure les OB ayant déjà produit un signal.

        Returns:
            Les OB encore exploitables, dans l'ordre d'enregistrement. Les objets
            retournés sont ceux du tracker : les modifier (ex. ``grade``) met à
            jour l'état suivi.
        """
        wanted = timeframe.upper() if timeframe else None
        return [
            ob
            for ob in self._obs.values()
            if not ob.mitigated
            and ob.age_bars <= self._max_age_bars
            and (wanted is None or ob.timeframe.upper() == wanted)
            and (not exclude_used or ob.ob_id not in self._used)
        ]

    def _update_tracked_ob(self, ob: TrackedOB, candle: pd.Series) -> None:
        """Applique une bougie à un OB, utilisé par update et register_with_history."""
        ob.age_bars += 1
        low = _cell(candle, "low")
        high = _cell(candle, "high")
        candle_time = _candle_timestamp(candle)
        if ob.mitigated or low is None or high is None:
            return
        if candle_time is not None and _timestamp_before(
            candle_time, ob.created_at, inclusive=True
        ):
            return
        if not self._is_mitigated(ob, low=low, high=high):
            return

        ob.mitigated = True
        ob.mitigated_at = candle_time
        logger.info(
            "[OB MITIGATED] %s | %s %s | zone=%.5f-%.5f | age=%d bars | at=%s",
            ob.symbol,
            ob.timeframe,
            ob.direction,
            ob.low,
            ob.high,
            ob.age_bars,
            candle_time.isoformat() if candle_time is not None else "unknown",
        )

    def get_mitigated(self) -> list[TrackedOB]:
        """Retourne les OB mitigés (non encore élagués), pour log/debug."""
        return [ob for ob in self._obs.values() if ob.mitigated]

    def prune_expired(self) -> int:
        """Supprime les OB dont ``age_bars > max_age_bars``.

        Returns:
            Le nombre d'OB supprimés.
        """
        expired = [
            ob_id
            for ob_id, ob in self._obs.items()
            if ob.age_bars > self._max_age_bars
        ]
        for ob_id in expired:
            del self._obs[ob_id]
        if expired:
            logger.debug(
                "[OB TRACKER] élagage | supprimés=%d | restants=%d | max_age=%d",
                len(expired),
                len(self._obs),
                self._max_age_bars,
            )
        return len(expired)

    def set_grade(self, ob_id: str, grade: str) -> bool:
        """Associe un grade de qualité (rempli par le scorer) à un OB suivi.

        Args:
            ob_id: Identifiant de l'OB.
            grade: Grade attribué (``A``, ``B``, ``C``…).

        Returns:
            ``True`` si l'OB a été trouvé et mis à jour, ``False`` sinon.
        """
        ob = self._obs.get(ob_id)
        if ob is None:
            return False
        ob.grade = grade
        return True

    @staticmethod
    def _is_mitigated(ob: TrackedOB, low: float, high: float) -> bool:
        """Applique la règle de mitigation directionnelle.

        - bullish : ``low <= ob.high``
        - bearish : ``high >= ob.low``
        """
        if ob.direction == "bullish":
            return low <= ob.high
        if ob.direction == "bearish":
            return high >= ob.low
        return False
