"""
Confirmation M5 obligatoire avant d'entrer sur un Order Block (Grade A/B).

Ce module répond à une question unique : **le prix confirme-t-il l'OB avant
qu'on entre ?** Il évite d'entrer sur un OB que le prix traverse simplement.

Trois confirmations ICT sont disponibles, évaluées **après le contact** avec la
zone OB :

========================  =====================================================
Critère                   Définition
========================  =====================================================
``MICRO_BOS``             le prix casse le dernier swing high (bullish) /
                          low (bearish) M5 formé après le contact avec l'OB
``CHOCH``                 un CHoCH M5 se produit après le contact avec l'OB
``REJECTION_CANDLE``      la dernière bougie M5 fermée a une mèche > 1.5x son
                          corps, dans la direction opposée au mouvement entrant
========================  =====================================================

``check()`` retourne ``confirmed=True`` si **au moins un** des critères *requis*
est satisfait. Si plusieurs le sont, le ``type`` retourné suit l'ordre de
priorité ``MICRO_BOS > CHOCH > REJECTION_CANDLE`` (du plus fiable au plus
faible).

Module pur (aucune I/O, aucune dépendance broker) : il est utilisable en live
comme en backtest. Complémentaire de ``m5_confirmation.py`` (Phase 12), qui
vérifie le *retest + rejet de la zone* pour l'entrée elle-même.

Utilisation dans le pipeline existant (sans modifier la stratégie) : appeler
``check()`` **avant** ``SMCTrendFollowingStrategy.analyze`` et ignorer le setup
si ``confirmed`` est faux.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd
import structlog

from arty_trading.config.operational import definitions
from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.smc.order_block_tracker import TrackedOB

logger = get_logger(LogCategory.SMC)
choch_logger = structlog.get_logger("arty_trading.smc")
rejection_logger = structlog.get_logger("arty_trading.smc")

#: Fenêtre fractale (bougies de chaque côté) pour valider un swing M5.
SWING_WINDOW: int = definitions()["swing"]["window"]

#: Ratio mèche / corps minimal pour considérer une bougie comme un rejet.
REJECTION_WICK_BODY_RATIO: float = definitions()["confirmation"]["rejection_wick_body_ratio"]

#: Clés de ``m5_structure`` dans lesquelles chercher des évènements de structure.
STRUCTURE_KEYS: tuple[str, ...] = (
    "choch",
    "change_of_character",
    "latest_choch",
    "detections",
    "events",
    "structure",
    "items",
)

#: Concepts d'évènement interprétés comme un CHoCH.
CHOCH_CONCEPTS: tuple[str, ...] = (
    "choch",
    "change_of_character",
    "mss",
    "market_structure_shift",
)


class M5ConfirmationType(str, Enum):  # noqa: UP042 — interface publique imposée
    """Type de confirmation M5 obtenue."""

    MICRO_BOS = "micro_bos"
    CHOCH = "choch"
    REJECTION_CANDLE = "rejection_candle"


#: Ordre de priorité lorsqu'au moins deux critères sont satisfaits.
CONFIRMATION_PRIORITY: tuple[M5ConfirmationType, ...] = (
    M5ConfirmationType.MICRO_BOS,
    M5ConfirmationType.CHOCH,
    M5ConfirmationType.REJECTION_CANDLE,
)


@dataclass(frozen=True)
class ConfirmationResult:
    """Résultat de la vérification de confirmation M5.

    Attributes:
        confirmed: ``True`` si au moins un critère requis est satisfait.
        type: Critère retenu (priorité ``MICRO_BOS > CHOCH > REJECTION_CANDLE``),
            ou ``None`` si aucune confirmation.
        details: Détail complet du contrôle (critères évalués/satisfaits,
            indices, niveaux cassés, ratios, raisons).
    """

    confirmed: bool
    type: M5ConfirmationType | None
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (métadonnées de setup, logs, API)."""
        return {
            "m5_confirmed": self.confirmed,
            "m5_confirmation_type": self.type.value if self.type else None,
            "m5_confirmation_details": dict(self.details),
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


def _cell(frame: pd.DataFrame, index: int, key: str) -> float | None:
    """Lit une cellule numérique d'une bougie (``None`` si absente/NaN)."""
    if key not in frame.columns:
        return None
    try:
        raw = frame[key].iloc[index]
    except (KeyError, IndexError, TypeError):
        return None
    return _to_float(raw)


def _has_ohlc(frame: pd.DataFrame) -> bool:
    """``True`` si le DataFrame contient les colonnes OHLC nécessaires."""
    return set(frame.columns) >= {"open", "high", "low", "close"}


def _ob_zone(ob: TrackedOB) -> tuple[float, float] | None:
    """Resolve the OB zone, falling back to its own open/close body."""
    low = _to_float(getattr(ob, "low", None))
    high = _to_float(getattr(ob, "high", None))
    if low is None or high is None:
        open_price = _to_float(getattr(ob, "open", None))
        close = _to_float(getattr(ob, "close", None))
        if open_price is None or close is None:
            return None
        low, high = min(open_price, close), max(open_price, close)
    return (low, high) if low <= high else None


def _frame_timestamps(frame: pd.DataFrame) -> pd.Series | None:
    """Colonne d'horodatage de la frame (``timestamp``, ``time`` ou ``date``)."""
    for key in ("timestamp", "time", "date"):
        if key in frame.columns:
            return frame[key]
    return None


def _normalize_direction(value: Any) -> str | None:
    """Normalise une direction en ``bullish`` / ``bearish`` / ``None``."""
    text = str(value).strip().lower() if value is not None else ""
    if text in ("bullish", "buy", "long", "up"):
        return "bullish"
    if text in ("bearish", "sell", "short", "down"):
        return "bearish"
    return None


def _iter_structure_items(structure: Any) -> Iterator[Mapping[str, Any]]:
    """Parcourt récursivement ``m5_structure`` et produit les évènements (dicts).

    Formats acceptés (tolérant) :

    - ``{"choch": [{...}, {...}]}`` — liste sous une clé connue ;
    - ``{"choch": {...}}`` — évènement unique sous une clé connue ;
    - ``{"detections": [...]}`` / ``{"events": [...]}`` ;
    - un événement daté : ``{"concept": "choch", "timestamp": "2024-01-01T00:05:00Z"}``
      directement à la racine ;
    - une liste mélangeant les formes ci-dessus.
    """
    if isinstance(structure, Mapping):
        if any(key in structure for key in ("timestamp", "concept", "type", "index")):
            yield structure
        for key in STRUCTURE_KEYS:
            if key in structure:
                for item in _iter_structure_items(structure[key]):
                    if key in ("choch", "change_of_character", "latest_choch"):
                        yield {"concept": "choch", **item}
                    else:
                        yield item
    elif isinstance(structure, Sequence) and not isinstance(structure, (str, bytes)):
        for item in structure:
            yield from _iter_structure_items(item)


def _timestamp(value: Any) -> pd.Timestamp | None:
    """Normalize explicit dates to UTC; numeric candle indices are never dates."""
    if value is None or isinstance(value, (bool, int, float)):
        return None
    try:
        result = pd.Timestamp(value)
        if pd.isna(result):
            return None
        return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return None


def _swing_indices(
    frame: pd.DataFrame, key: str, window: int, *, is_high: bool = True
) -> list[int]:
    """Indices des swings confirmés (fractals) sur ``high`` ou ``low``.

    Un pivot n'est retenu que s'il est validé par ``window`` bougies de chaque
    côté (donc jamais sur les ``window`` dernières bougies) : aucun look-ahead.
    """
    if key not in frame.columns or window <= 0:
        return []
    values = [_to_float(value) for value in frame[key].tolist()]
    indices: list[int] = []

    for index in range(window, len(values) - window):
        pivot = values[index]
        if pivot is None:
            continue
        others: list[float] = []
        for other in range(index - window, index + window + 1):
            if other == index:
                continue
            other_value = values[other]
            if other_value is None:
                others = []
                break
            others.append(other_value)
        if others and all(pivot > value if is_high else pivot < value for value in others):
            indices.append(index)
    return indices


class M5ConfirmationChecker:
    """Vérifie qu'un Order Block est **confirmé sur M5** avant l'entrée.

    Le checker ne détecte ni OB ni structure : il consomme une zone déjà suivie
    (``TrackedOB``), les bougies M5 qui la suivent et les évènements de structure
    M5 déjà détectés, puis décide si au moins un des critères **requis** est
    satisfait.

    Args:
        require_micro_bos: Exiger une cassure du dernier swing M5 (défaut True).
        require_choch: Exiger un CHoCH M5 (défaut False).
        require_rejection_candle: Exiger une bougie de rejet M5 (défaut True).
        swing_window: Bougies de chaque côté requises pour valider un swing.
        min_candles_after_ob: Minimum de bougies M5 pour MICRO_BOS ou CHOCH
            (défaut 3). Le rejet seul nécessite une seule bougie.

    Note:
        Si aucun critère n'est requis, ``check()`` retourne ``confirmed=False``
        (invalidé par prudence : « au moins un critère requis » est vide).
        ``rejection_wick_body_ratio`` reste réglable sur l'instance.
    """

    #: Fenêtre fractale des swings M5 (réglable par instance).
    swing_window: int = SWING_WINDOW

    #: Ratio mèche/corps exigé pour la bougie de rejet (réglable par instance).
    rejection_wick_body_ratio: float = REJECTION_WICK_BODY_RATIO

    def __init__(
        self,
        require_micro_bos: bool = True,
        require_choch: bool = False,
        require_rejection_candle: bool = True,
        swing_window: int = SWING_WINDOW,
        min_candles_after_ob: int = 3,
    ) -> None:
        self._require_micro_bos = require_micro_bos
        self._require_choch = require_choch
        self._require_rejection_candle = require_rejection_candle
        self.swing_window = swing_window
        self.min_candles_after_ob = min_candles_after_ob

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------

    @property
    def require_micro_bos(self) -> bool:
        """Le critère ``MICRO_BOS`` est-il requis ?"""
        return self._require_micro_bos

    @property
    def require_choch(self) -> bool:
        """Le critère ``CHOCH`` est-il requis ?"""
        return self._require_choch

    @property
    def require_rejection_candle(self) -> bool:
        """Le critère ``REJECTION_CANDLE`` est-il requis ?"""
        return self._require_rejection_candle

    @property
    def required_criteria(self) -> tuple[M5ConfirmationType, ...]:
        """Critères requis, dans l'ordre de priorité."""
        flags = {
            M5ConfirmationType.MICRO_BOS: self._require_micro_bos,
            M5ConfirmationType.CHOCH: self._require_choch,
            M5ConfirmationType.REJECTION_CANDLE: self._require_rejection_candle,
        }
        return tuple(criterion for criterion in CONFIRMATION_PRIORITY if flags[criterion])

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def check(
        self,
        ob: TrackedOB,
        m5_candles_after_ob: pd.DataFrame,
        m5_structure: dict[str, Any],
        reference_timestamp: pd.Timestamp | None = None,
    ) -> ConfirmationResult:
        """Vérifie si l'OB est confirmé sur M5.

        Args:
            ob: Order Block suivi (``TrackedOB``) — sa ``direction`` et sa zone
                ``[low, high]`` définissent le sens attendu et la zone de contact.
            m5_candles_after_ob: Bougies M5 (``open``, ``high``, ``low``,
                ``close``) postérieures à l'OB, de la plus ancienne à la plus
                récente. Peut être vide.
            reference_timestamp: Date de création de l'OB. Si omise, utilise
                ``ob.created_at`` et émet un warning pour les anciens appelants.
            m5_structure: Évènements de structure M5 (BOS/CHoCH) — dictionnaire
                tolérant : ``{"choch": [...]}``, ``{"detections": [...]}``,
                évènement direct ``{"concept": "choch", "timestamp": "2024-01-01T00:05:00Z"}``, etc.

        Returns:
            ``ConfirmationResult`` — ``confirmed`` est vrai si au moins un
            critère requis est satisfait. Le ``type`` suit la priorité
            ``MICRO_BOS > CHOCH > REJECTION_CANDLE``.
        """
        if reference_timestamp is None:
            logger.warning("m5_confirmation_missing_reference_timestamp ob_id=%s", ob.ob_id)
            reference_timestamp = pd.Timestamp(ob.created_at)
        reference = _timestamp(reference_timestamp)
        direction = _normalize_direction(ob.direction)
        zone = _ob_zone(ob)
        details: dict[str, Any] = {
            "ob_id": ob.ob_id,
            "symbol": ob.symbol,
            "direction": ob.direction,
            "zone_low": zone[0] if zone is not None else None,
            "zone_high": zone[1] if zone is not None else None,
            "required": [criterion.value for criterion in self.required_criteria],
        }

        if m5_candles_after_ob is None or m5_candles_after_ob.empty:
            return self._reject(details, "no_candles")
        if direction is None:
            return self._reject(details, "invalid_direction")
        if zone is None:
            return self._reject(details, "invalid_ob_zone")
        if not _has_ohlc(m5_candles_after_ob):
            return self._reject(details, "missing_ohlc_columns")
        if not self.required_criteria:
            return self._reject(details, "no_required_criterion")

        candle_count = len(m5_candles_after_ob)
        needs_swings = self._require_micro_bos or self._require_choch
        if needs_swings and candle_count < self.min_candles_after_ob:
            details["have"] = candle_count
            details["need"] = self.min_candles_after_ob
            return self._reject(details, "insufficient_candles")

        swing_candle_count = 2 * self.swing_window + 1
        if needs_swings and candle_count < swing_candle_count:
            details["have"] = candle_count
            details["need"] = swing_candle_count
            return self._reject(details, "insufficient_candles_for_swing")

        if reference is None:
            return self._reject(details, "invalid_reference_timestamp")
        frame = m5_candles_after_ob.copy()
        if isinstance(frame.index, pd.DatetimeIndex):
            raw_times = frame.index.tolist()
        else:
            time_column = _frame_timestamps(frame)
            if time_column is None:
                return self._reject(details, "missing_candle_timestamps")
            raw_times = time_column.tolist()
        times = [_timestamp(value) for value in raw_times]
        if any(value is None for value in times):
            return self._reject(details, "invalid_candle_timestamps")
        frame.index = pd.DatetimeIndex(times)
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            return self._reject(details, "unordered_or_duplicate_candle_timestamps")
        frame = frame.loc[frame.index > reference]
        if frame.empty:
            return self._reject(details, "no_candles")
        if needs_swings and len(frame) < max(self.min_candles_after_ob, swing_candle_count):
            return self._reject(details, "insufficient_post_ob_candles")
        contact_index = self._find_contact_index(frame, ob)
        if contact_index is None:
            return self._reject(details, "no_contact_with_zone")

        details["contact_index"] = contact_index
        details["contact_timestamp"] = frame.index[contact_index].isoformat()
        details["contact_open"] = _cell(frame, contact_index, "open")
        details["contact_close"] = _cell(frame, contact_index, "close")

        satisfied: dict[M5ConfirmationType, dict[str, Any]] = {}

        if self._require_micro_bos:
            ok, detail = self._check_micro_bos(ob, direction, frame, contact_index)
            details["micro_bos"] = detail
            if ok:
                satisfied[M5ConfirmationType.MICRO_BOS] = detail

        if self._require_choch:
            ok, detail = self._check_choch(
                ob, direction, frame, contact_index, m5_structure, reference
            )
            details["choch"] = detail
            if ok:
                satisfied[M5ConfirmationType.CHOCH] = detail

        if self._require_rejection_candle:
            ok, detail = self._check_rejection_candle(ob, direction, frame, contact_index)
            details["rejection_candle"] = detail
            if ok:
                satisfied[M5ConfirmationType.REJECTION_CANDLE] = detail

        details["satisfied"] = [criterion.value for criterion in satisfied]
        confirmed_type = next(
            (criterion for criterion in CONFIRMATION_PRIORITY if criterion in satisfied),
            None,
        )
        details["reason"] = "confirmed" if confirmed_type is not None else "no_criterion_satisfied"

        logger.debug(
            "[M5 CONFIRMATION] %s | %s | confirmed=%s | type=%s | satisfaits=%s",
            ob.symbol,
            ob.ob_id,
            confirmed_type is not None,
            confirmed_type.value if confirmed_type else "-",
            details["satisfied"],
        )

        return ConfirmationResult(
            confirmed=confirmed_type is not None,
            type=confirmed_type,
            details=details,
        )

    @staticmethod
    def _reject(details: dict[str, Any], reason: str) -> ConfirmationResult:
        """Résultat négatif immédiat (entrées inexploitables)."""
        details["satisfied"] = []
        details["reason"] = reason
        return ConfirmationResult(confirmed=False, type=None, details=details)

    @staticmethod
    def _find_contact_index(frame: pd.DataFrame, ob: TrackedOB) -> int | None:
        """Retourne la première bougie qui chevauche la zone de l'OB."""
        zone = _ob_zone(ob)
        if zone is None:
            return None
        ob_low, ob_high = zone
        for index in range(len(frame)):
            low = _cell(frame, index, "low")
            high = _cell(frame, index, "high")
            if low is not None and high is not None and low <= ob_high and high >= ob_low:
                return index
        return None

    def _check_micro_bos(
        self,
        ob: TrackedOB,
        direction: str,
        frame: pd.DataFrame,
        contact_index: int,
    ) -> tuple[bool, dict[str, Any]]:
        """Cherche une cassure clôturée d'un swing formé après le contact."""
        del ob
        is_bullish = direction == "bullish"
        swing_column = "high" if is_bullish else "low"
        swings = [
            index
            for index in _swing_indices(frame, swing_column, self.swing_window, is_high=is_bullish)
            if frame.index[index] > frame.index[contact_index]
        ]
        if not swings:
            return False, {"reason": "no_post_contact_swing"}

        swing_index = swings[-1]
        swing_price = _cell(frame, swing_index, swing_column)
        if swing_price is None:
            return False, {"reason": "invalid_swing_price", "swing_index": swing_index}

        for index, break_timestamp in enumerate(frame.index):
            if break_timestamp <= frame.index[swing_index]:
                continue
            close = _cell(frame, index, "close")
            if close is None:
                continue
            broken = close > swing_price if is_bullish else close < swing_price
            if broken:
                return True, {
                    "swing_index": swing_index,
                    "swing_price": swing_price,
                    "break_index": index,
                    "break_close": close,
                    "swing_timestamp": frame.index[swing_index].isoformat(),
                    "break_timestamp": break_timestamp.isoformat(),
                }

        return False, {
            "reason": "swing_not_broken",
            "swing_index": swing_index,
            "swing_price": swing_price,
        }

    def _check_choch(
        self,
        ob: TrackedOB,
        direction: str,
        frame: pd.DataFrame,
        contact_index: int,
        structure: dict[str, Any],
        reference_timestamp: pd.Timestamp,
    ) -> tuple[bool, dict[str, Any]]:
        """Accepte uniquement un CHoCH orienté comme le trade, après contact."""
        for event in _iter_structure_items(structure):
            concept = (
                str(event.get("concept", event.get("type", event.get("event", ""))))
                .strip()
                .lower()
                .replace("-", "_")
                .replace(" ", "_")
            )
            if concept not in CHOCH_CONCEPTS:
                continue
            event_direction = _normalize_direction(event.get("direction", event.get("bias")))
            event_time = _timestamp(event.get("timestamp"))
            if event_time is None or event_time <= reference_timestamp:
                continue
            if event_time not in frame.index:
                choch_logger.warning(
                    "choch_timestamp_out_of_window",
                    timestamp=event_time.isoformat(),
                    ob_id=event.get("ob_id") or ob.ob_id,
                )
                continue
            if event_direction != direction or event_time <= frame.index[contact_index]:
                continue
            return True, {
                "event_timestamp": event_time.isoformat(),
                "direction": event_direction,
                "concept": concept,
            }
        return False, {"reason": "no_aligned_post_contact_choch"}

    def _check_rejection_candle(
        self,
        ob: TrackedOB,
        direction: str,
        frame: pd.DataFrame,
        contact_index: int,
    ) -> tuple[bool, dict[str, Any]]:
        """Vérifie que la dernière bougie rejette la zone contre l'entrée."""
        candle_index = len(frame) - 1
        if candle_index < contact_index:
            return False, {"reason": "no_candle_after_contact"}

        open_price = _cell(frame, candle_index, "open")
        high = _cell(frame, candle_index, "high")
        low = _cell(frame, candle_index, "low")
        close = _cell(frame, candle_index, "close")
        if None in (open_price, high, low, close):
            return False, {"reason": "invalid_last_candle"}

        assert open_price is not None and high is not None
        assert low is not None and close is not None
        zone = _ob_zone(ob)
        if zone is None:
            return False, {"reason": "invalid_ob_zone"}
        ob_low, ob_high = zone
        if low > ob_high or high < ob_low:
            rejection_logger.debug(
                "m5_rejection_ignored", reason="no_contact_with_ob", ob_id=ob.ob_id
            )
            return False, {"reason": "no_contact_with_ob"}

        body = abs(close - open_price)
        if direction == "bullish":
            wick = min(open_price, close) - low
            wick_direction = "lower"
        else:
            wick = high - max(open_price, close)
            wick_direction = "upper"

        ratio = wick / body if body > 0 else None
        confirmed = wick > 0 and (ratio is None or ratio > self.rejection_wick_body_ratio)
        return confirmed, {
            "candle_index": candle_index,
            "wick_direction": wick_direction,
            "wick": wick,
            "body": body,
            "wick_body_ratio": ratio,
        }
