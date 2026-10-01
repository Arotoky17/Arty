"""
Notation de qualité d'un Order Block — module d'**évaluation** (aucune détection).

Ce module ne détecte rien : il reçoit un Order Block **déjà détecté** par le
moteur SMC existant et lui attribue une note ``0.0 → 1.0`` puis un grade
**A/B/C**. Objectif métier : écarter les OB médiocres et ne conserver que ceux à
fort potentiel de pips.

Clean Architecture : ce module n'importe **rien** depuis ``infrastructure/``
(aucun accès MT5, base de données, notification ou réseau). Ses dépendances se
limitent à ``pandas`` (structures de données), la librairie standard et le
logger applicatif.

Pondérations FIXES (non optimisables)
-------------------------------------

========================  ======  ===============================================
Critère                   Poids   Définition
========================  ======  ===============================================
``displacement``           0.25   |close(dernière bougie suivante) - close(OB)| / ATR >= seuil
``is_fresh``               0.20   aucune bougie postérieure n'a touché [min(o,c), max(o,c)]
``htf_confluence``         0.20   OB/FVG H1-H4 à <= mult x ATR du midpoint de l'OB
``has_liquidity_sweep``    0.15   au moins un sweep antérieur à l'OB
``has_fvg_adjacent``       0.10   FVG M5 à <= mult x ATR du midpoint de l'OB
``rejection_confirmed``    0.10   mèche totale de la dernière bougie M5 > corps
========================  ======  ===============================================

Grades (seuils fixes) : ``score >= 0.75`` → **A**, ``score >= 0.55`` → **B**,
sinon **C** (à ignorer). Le score est arrondi à 4 décimales pour rendre la
classification déterministe (évite les effets de bord des flottants).

Entrées acceptées
-----------------
``htf_obs``, ``htf_fvgs``, ``liquidity_sweeps`` et ``fvgs_m5`` acceptent des
dictionnaires SMC (format ``SMCDetection.to_dict()``) **ou** tout objet exposant
les mêmes informations (``details`` avec ``ob_top``/``ob_bottom`` ou
``gap_top``/``gap_bottom``, attributs ``high``/``low``, ``price``,
``timestamp``). Les éléments inexploitables sont ignorés sans lever d'exception.

Le module est volontairement défensif : une entrée inutilisable (bougie OB
incomplète, ATR absent…) produit un résultat **Grade C** plutôt qu'une
exception, afin de ne jamais interrompre le pipeline de trading.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.SMC)

# --- Pondérations fixes du score (somme = 1.0) ------------------------------
WEIGHT_DISPLACEMENT: float = 0.25
WEIGHT_FRESH: float = 0.20
WEIGHT_HTF_CONFLUENCE: float = 0.20
WEIGHT_LIQUIDITY_SWEEP: float = 0.15
WEIGHT_FVG_ADJACENT: float = 0.10
WEIGHT_REJECTION: float = 0.10

# --- Seuils de grade fixes --------------------------------------------------
GRADE_A_THRESHOLD: float = 0.75
GRADE_B_THRESHOLD: float = 0.55

#: Précision de stockage du score (classification déterministe).
SCORE_PRECISION: int = 4


class OBGrade(str, Enum):  # noqa: UP042 — interface publique imposée (str + Enum)
    """Grade de qualité d'un Order Block (A = meilleur, C = à ignorer)."""

    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class OrderBlockQuality:
    """Résultat de la notation d'un Order Block.

    Attributes:
        grade: Grade A, B ou C.
        score: Note 0.0 → 1.0 (somme des pondérations des critères satisfaits).
        is_fresh: ``True`` si aucune bougie postérieure n'a touché la zone OB.
        displacement_atr: Déplacement après l'OB en multiple d'ATR.
        htf_confluence: ``True`` si un OB/FVG H1-H4 est proche du midpoint OB.
        has_liquidity_sweep: ``True`` si un sweep de liquidité précède l'OB.
        has_fvg_adjacent: ``True`` si une FVG M5 est collée à l'OB.
        rejection_confirmed: ``True`` si la dernière bougie M5 a une mèche
            totale supérieure à son corps.
    """

    grade: OBGrade
    score: float
    is_fresh: bool
    displacement_atr: float
    htf_confluence: bool
    has_liquidity_sweep: bool
    has_fvg_adjacent: bool
    rejection_confirmed: bool

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (métadonnées de setup, logs, API)."""
        return {
            "ob_quality_grade": self.grade.value,
            "ob_quality_score": self.score,
            "ob_quality_is_fresh": self.is_fresh,
            "ob_displacement_atr": round(self.displacement_atr, 4),
            "ob_htf_confluence": self.htf_confluence,
            "ob_has_liquidity_sweep": self.has_liquidity_sweep,
            "ob_has_fvg_adjacent": self.has_fvg_adjacent,
            "ob_rejection_confirmed": self.rejection_confirmed,
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
    """Lit une cellule numérique d'une ligne pandas (``None`` si absente/NaN)."""
    try:
        raw = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    return _to_float(raw)


def _has_columns(frame: pd.DataFrame, columns: frozenset[str]) -> bool:
    """``True`` si le DataFrame contient toutes les colonnes demandées."""
    return columns.issubset(set(frame.columns))


def _touches_zone(frame: pd.DataFrame, zone_low: float, zone_high: float) -> bool:
    """``True`` si au moins une bougie du DataFrame chevauche la zone OB."""
    if frame.empty or not _has_columns(frame, frozenset({"high", "low"})):
        return False
    highs = pd.to_numeric(frame["high"], errors="coerce")
    lows = pd.to_numeric(frame["low"], errors="coerce")
    overlap = (lows <= zone_high) & (highs >= zone_low)
    return bool(overlap.fillna(False).any())


def _last_candle_rejection(frame: pd.DataFrame) -> bool:
    """``True`` si la dernière bougie a une mèche totale > son corps.

    Mèche totale = ``(high - low) - |close - open|`` (somme des deux mèches).
    """
    if frame.empty:
        return False
    last = frame.iloc[-1]
    open_ = _cell(last, "open")
    high = _cell(last, "high")
    low = _cell(last, "low")
    close = _cell(last, "close")
    if open_ is None or high is None or low is None or close is None:
        return False
    body = abs(close - open_)
    return (high - low) - body > body


def _fallback_atr(frame: pd.DataFrame, period: int) -> float:
    """ATR de secours (même définition que ``utils.helpers.calculate_atr``).

    Utilisé **uniquement** lorsque l'``atr_value`` fourni est invalide (<= 0) :
    True Range = ``max(high - low, |high - prev_close|, |low - prev_close|)``,
    moyenne simple sur ``period`` puis lissage de Wilder.
    """
    if frame.empty or period <= 0:
        return 0.0
    if not _has_columns(frame, frozenset({"high", "low", "close"})):
        return 0.0
    highs = pd.to_numeric(frame["high"], errors="coerce")
    lows = pd.to_numeric(frame["low"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = closes.shift(1)
    ranges = pd.concat(
        [highs - lows, (highs - previous_close).abs(), (lows - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    true_range = ranges.dropna()
    if len(true_range) < period:
        return 0.0
    atr = float(true_range.iloc[:period].mean())
    for value in true_range.iloc[period:]:
        atr = (atr * (period - 1) + float(value)) / period
    return atr


def _zone_pair_midpoint(mapping: Mapping[str, Any]) -> float | None:
    """Midpoint d'une zone OB/FVG (paire ``*_top`` / ``*_bottom``)."""
    for top_key, bottom_key in (
        ("ob_top", "ob_bottom"),
        ("gap_top", "gap_bottom"),
        ("zone_start", "zone_end"),
        ("premium_start", "premium_end"),
    ):
        top = _to_float(mapping.get(top_key))
        bottom = _to_float(mapping.get(bottom_key))
        if top is not None and bottom is not None:
            return (top + bottom) / 2.0
    return None


def _mapping_midpoint(mapping: Mapping[str, Any]) -> float | None:
    """Midpoint d'un OB/FVG décrit par un dictionnaire SMC."""
    high = _to_float(mapping.get("high"))
    low = _to_float(mapping.get("low"))
    if high is not None and low is not None:
        return (high + low) / 2.0

    # Zone décrite directement ou dans un sous-dictionnaire ``details``.
    for candidate in (mapping, mapping.get("details")):
        if isinstance(candidate, Mapping):
            pair_midpoint = _zone_pair_midpoint(candidate)
            if pair_midpoint is not None:
                return pair_midpoint

    for key in ("midpoint", "mid", "price", "equilibrium"):
        value = _to_float(mapping.get(key))
        if value is not None:
            return value
    return None


def _item_midpoint(item: Any) -> float | None:
    """Midpoint d'un OB/FVG fourni en dictionnaire ou sous forme d'objet."""
    if isinstance(item, Mapping):
        return _mapping_midpoint(item)

    high = _to_float(getattr(item, "high", None))
    low = _to_float(getattr(item, "low", None))
    if high is not None and low is not None:
        return (high + low) / 2.0

    details = getattr(item, "details", None)
    if isinstance(details, Mapping):
        midpoint = _mapping_midpoint(details)
        if midpoint is not None:
            return midpoint

    return _to_float(getattr(item, "price", None))


def _has_item_within(items: Sequence[Any], reference: float, tolerance: float) -> bool:
    """``True`` si un élément a son midpoint à ``<= tolerance`` de ``reference``."""
    for item in items:
        midpoint = _item_midpoint(item)
        if midpoint is not None and abs(midpoint - reference) <= tolerance:
            return True
    return False


def _to_timestamp(value: Any) -> pd.Timestamp | None:
    """Convertit une valeur en ``pd.Timestamp``, ou ``None`` si inexploitable."""
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    if isinstance(value, str):
        try:
            return pd.Timestamp(value)
        except (ValueError, TypeError):
            return None
    return None


def _item_timestamp(item: Any) -> pd.Timestamp | None:
    """Horodatage d'un élément (dict SMC ou objet), ou ``None``."""
    if isinstance(item, Mapping):
        candidates: tuple[Any, ...] = (
            item.get("timestamp"),
            item.get("time"),
            item.get("datetime"),
            item.get("date"),
        )
    else:
        candidates = (
            getattr(item, "timestamp", None),
            getattr(item, "time", None),
            getattr(item, "datetime", None),
        )
    for candidate in candidates:
        timestamp = _to_timestamp(candidate)
        if timestamp is not None:
            return timestamp
    return None


def _is_strictly_before(candidate: pd.Timestamp, reference: pd.Timestamp) -> bool:
    """``candidate < reference``, en tolérant les fuseaux mixtes (aware/naive)."""
    if (candidate.tzinfo is None) == (reference.tzinfo is None):
        return bool(candidate < reference)
    naive_candidate = (
        candidate.tz_localize(None) if candidate.tzinfo is not None else candidate
    )
    naive_reference = (
        reference.tz_localize(None) if reference.tzinfo is not None else reference
    )
    return bool(naive_candidate < naive_reference)


def _has_sweep_before(sweeps: Sequence[Any], ob_timestamp: pd.Timestamp | None) -> bool:
    """``True`` si un sweep est strictement antérieur à l'OB."""
    if ob_timestamp is None:
        return False
    for sweep in sweeps:
        sweep_timestamp = _item_timestamp(sweep)
        if sweep_timestamp is not None and _is_strictly_before(
            sweep_timestamp, ob_timestamp
        ):
            return True
    return False


def _grade_for_score(score: float) -> OBGrade:
    """Convertit un score 0.0-1.0 en grade A/B/C (seuils fixes)."""
    if score >= GRADE_A_THRESHOLD:
        return OBGrade.A
    if score >= GRADE_B_THRESHOLD:
        return OBGrade.B
    return OBGrade.C


def _rejected() -> OrderBlockQuality:
    """Résultat de repli : OB inexploitable → Grade C (jamais retenu)."""
    return OrderBlockQuality(
        grade=OBGrade.C,
        score=0.0,
        is_fresh=False,
        displacement_atr=0.0,
        htf_confluence=False,
        has_liquidity_sweep=False,
        has_fvg_adjacent=False,
        rejection_confirmed=False,
    )


class OrderBlockQualityScorer:
    """Note un Order Block **déjà détecté** par le moteur SMC (ne détecte rien).

    Args:
        atr_period: Période de l'ATR de secours, utilisé seulement si
            ``atr_value <= 0`` (défaut 14).
        displacement_threshold: Ratio de déplacement minimal en ATR (défaut 1.5).
        htf_confluence_atr_mult: Tolérance en ATR entre le midpoint de l'OB et
            celui d'un OB/FVG H1-H4 (défaut 1.0).
        fvg_adjacent_atr_mult: Tolérance en ATR entre le midpoint de l'OB et
            celui d'une FVG M5 (défaut 1.0).

    Raises:
        ValueError: si un paramètre est hors domaine (période <= 0, seuils < 0).
    """

    def __init__(
        self,
        atr_period: int = 14,
        displacement_threshold: float = 1.5,
        htf_confluence_atr_mult: float = 1.0,
        fvg_adjacent_atr_mult: float = 1.0,
    ) -> None:
        if atr_period <= 0:
            raise ValueError("atr_period doit être strictement positif")
        if displacement_threshold < 0:
            raise ValueError("displacement_threshold doit être positif ou nul")
        if htf_confluence_atr_mult < 0 or fvg_adjacent_atr_mult < 0:
            raise ValueError("les multiplicateurs ATR doivent être positifs ou nuls")

        self._atr_period = atr_period
        self._displacement_threshold = displacement_threshold
        self._htf_confluence_atr_mult = htf_confluence_atr_mult
        self._fvg_adjacent_atr_mult = fvg_adjacent_atr_mult

    @property
    def atr_period(self) -> int:
        """Période de l'ATR de secours."""
        return self._atr_period

    @property
    def displacement_threshold(self) -> float:
        """Ratio de déplacement minimal en ATR."""
        return self._displacement_threshold

    @property
    def htf_confluence_atr_mult(self) -> float:
        """Tolérance ATR pour la confluence HTF."""
        return self._htf_confluence_atr_mult

    @property
    def fvg_adjacent_atr_mult(self) -> float:
        """Tolérance ATR pour l'adjacence d'une FVG M5."""
        return self._fvg_adjacent_atr_mult

    def score(
        self,
        ob_candle: pd.Series,
        next_candles: pd.DataFrame,
        atr_value: float,
        htf_obs: list[Any],
        htf_fvgs: list[Any],
        liquidity_sweeps: list[Any],
        fvgs_m5: list[Any],
    ) -> OrderBlockQuality:
        """Note un Order Block et retourne son grade A/B/C.

        Args:
            ob_candle: Bougie OB (``open``, ``high``, ``low``, ``close``,
                ``timestamp``).
            next_candles: Bougies M5 postérieures à l'OB (peut être vide).
            atr_value: ATR M5 courant (si <= 0, un ATR de secours est calculé sur
                ``next_candles`` avec ``atr_period``).
            htf_obs: OB H1/H4 dans la même zone que l'OB.
            htf_fvgs: FVG H1/H4 dans la même zone.
            liquidity_sweeps: Sweeps de liquidité détectés.
            fvgs_m5: FVG M5 détectées.

        Returns:
            ``OrderBlockQuality`` — grade A/B/C, score 0.0-1.0 et détail des
            critères. Une entrée inexploitable produit un grade C (score 0.0)
            sans lever d'exception.
        """
        open_ = _cell(ob_candle, "open")
        high = _cell(ob_candle, "high")
        low = _cell(ob_candle, "low")
        close = _cell(ob_candle, "close")
        if open_ is None or high is None or low is None or close is None or high < low:
            logger.warning("OB ignoré (bougie OB incomplète ou incohérente) → Grade C")
            return _rejected()

        zone_low = min(open_, close)
        zone_high = max(open_, close)
        midpoint = (high + low) / 2.0

        atr = _to_float(atr_value) or 0.0
        if atr <= 0:
            atr = _fallback_atr(next_candles, self._atr_period)

        # --- displacement_atr : |close(dernière bougie) - close(OB)| / ATR ---
        displacement_atr = 0.0
        if atr > 0 and not next_candles.empty:
            last_close = _cell(next_candles.iloc[-1], "close")
            if last_close is not None:
                displacement_atr = abs(last_close - close) / atr
        displacement_ok = displacement_atr >= self._displacement_threshold

        # --- is_fresh : aucune bougie postérieure n'a touché la zone OB ------
        is_fresh = not _touches_zone(next_candles, zone_low, zone_high)

        # --- htf_confluence : OB/FVG H1-H4 proche du midpoint OB ------------
        htf_tolerance = self._htf_confluence_atr_mult * atr
        htf_confluence = _has_item_within(
            htf_obs, midpoint, htf_tolerance
        ) or _has_item_within(htf_fvgs, midpoint, htf_tolerance)

        # --- has_liquidity_sweep : sweep strictement antérieur à l'OB -------
        ob_timestamp = _to_timestamp(ob_candle.get("timestamp"))
        if ob_timestamp is None:
            ob_timestamp = _to_timestamp(ob_candle.get("time"))
        has_liquidity_sweep = _has_sweep_before(liquidity_sweeps, ob_timestamp)

        # --- has_fvg_adjacent : FVG M5 collée au midpoint OB ---------------
        has_fvg_adjacent = _has_item_within(
            fvgs_m5, midpoint, self._fvg_adjacent_atr_mult * atr
        )

        # --- rejection_confirmed : mèche > corps sur la dernière M5 ---------
        rejection_confirmed = _last_candle_rejection(next_candles)

        raw_score = (
            (WEIGHT_DISPLACEMENT if displacement_ok else 0.0)
            + (WEIGHT_FRESH if is_fresh else 0.0)
            + (WEIGHT_HTF_CONFLUENCE if htf_confluence else 0.0)
            + (WEIGHT_LIQUIDITY_SWEEP if has_liquidity_sweep else 0.0)
            + (WEIGHT_FVG_ADJACENT if has_fvg_adjacent else 0.0)
            + (WEIGHT_REJECTION if rejection_confirmed else 0.0)
        )
        score = round(min(max(raw_score, 0.0), 1.0), SCORE_PRECISION)
        grade = _grade_for_score(score)

        logger.debug(
            "OB noté | grade=%s | score=%.2f | displacement=%.2fatr | fresh=%s | "
            "htf=%s | sweep=%s | fvg=%s | rejection=%s",
            grade.value,
            score,
            displacement_atr,
            is_fresh,
            htf_confluence,
            has_liquidity_sweep,
            has_fvg_adjacent,
            rejection_confirmed,
        )

        return OrderBlockQuality(
            grade=grade,
            score=score,
            is_fresh=is_fresh,
            displacement_atr=displacement_atr,
            htf_confluence=htf_confluence,
            has_liquidity_sweep=has_liquidity_sweep,
            has_fvg_adjacent=has_fvg_adjacent,
            rejection_confirmed=rejection_confirmed,
        )
