"""Tests du module ``order_block_quality`` — notation (évaluation) des Order Blocks.

Couvre :
- les 6 tests obligatoires de la spécification (Grade A complet, Grade C sans
  displacement, retest → ``is_fresh`` False, confluence HTF, rejet par mèche,
  ``next_candles`` vide) ;
- les seuils de grade (A >= 0.75, B >= 0.55, C sinon) et les pondérations fixes ;
- les cas limites : bougie OB invalide/NaN, colonnes manquantes, sweep sans
  horodatage, fuseaux mixtes, ATR de secours, éléments non exploitables ;
- la contrainte Clean Architecture (aucun import depuis ``infrastructure/``).
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from arty_trading.modules.smc.order_block_quality import (
    GRADE_A_THRESHOLD,
    GRADE_B_THRESHOLD,
    WEIGHT_DISPLACEMENT,
    WEIGHT_FRESH,
    WEIGHT_FVG_ADJACENT,
    WEIGHT_HTF_CONFLUENCE,
    WEIGHT_LIQUIDITY_SWEEP,
    WEIGHT_REJECTION,
    OBGrade,
    OrderBlockQuality,
    OrderBlockQualityScorer,
)

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "arty_trading"
    / "modules"
    / "smc"
    / "order_block_quality.py"
)

ATR = 1.0
#: Bougie OB baissière : zone [min(open, close), max(open, close)] = [2000.0, 2001.0]
#: midpoint = (high + low) / 2 = (2001.2 + 1999.8) / 2 = 2000.5
OB_OPEN, OB_HIGH, OB_LOW, OB_CLOSE = 2001.0, 2001.2, 1999.8, 2000.0
OB_MIDPOINT = (OB_HIGH + OB_LOW) / 2.0


def make_frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """DataFrame M5 avec les colonnes OHLC attendues."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def make_empty_frame() -> pd.DataFrame:
    """DataFrame M5 vide (mais avec les bonnes colonnes)."""
    return make_frame([])


def rising_m5_series(n: int = 12) -> pd.DataFrame:
    """Série M5 haussière régulière (true range ~0.7) pour tester l'ATR de secours."""
    return make_frame(
        [
            (
                2002.0 + index * 0.5,
                2002.6 + index * 0.5,
                2001.9 + index * 0.5,
                2002.5 + index * 0.5,
            )
            for index in range(n)
        ]
    )


def make_ob_candle(
    open_: float = OB_OPEN,
    high: float = OB_HIGH,
    low: float = OB_LOW,
    close: float = OB_CLOSE,
    timestamp: object = T0,
) -> pd.Series:
    """Bougie OB (Series pandas)."""
    return pd.Series(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "timestamp": timestamp,
        }
    )


def bullish_next_candles(last_close: float = 2003.2) -> pd.DataFrame:
    """Bougies M5 postérieures : prix au-dessus de la zone (OB jamais retesté).

    Dernière bougie à longue mèche (corps 0.2, mèche totale 2.4) → rejet confirmé.
    """
    return make_frame(
        [
            (2001.8, 2003.4, 2001.5, 2003.0),
            (2003.0, last_close + 1.4, 2002.0, last_close),
        ]
    )


def htf_ob(top: float = 2001.0, bottom: float = 2000.0) -> dict:
    """OB H1 au format ``SMCDetection.to_dict()``."""
    return {
        "concept": "order_block",
        "direction": "bullish",
        "index": 3,
        "details": {"ob_top": top, "ob_bottom": bottom},
    }


def htf_fvg(top: float = 2000.8, bottom: float = 2000.2) -> dict:
    """FVG M5 au format ``SMCDetection.to_dict()``."""
    return {
        "concept": "fair_value_gap",
        "direction": "bullish",
        "index": 4,
        "details": {"gap_top": top, "gap_bottom": bottom},
    }


def sweep_before(offset_minutes: int = 30, **extra: object) -> dict:
    payload: dict = {
        "concept": "liquidity_sweep",
        "timestamp": T0 - timedelta(minutes=offset_minutes),
    }
    payload.update(extra)
    return payload


def score_ob(
    scorer: OrderBlockQualityScorer | None = None,
    *,
    ob_candle: pd.Series | None = None,
    next_candles: pd.DataFrame | None = None,
    atr_value: float = ATR,
    htf_obs: list | None = None,
    htf_fvgs: list | None = None,
    liquidity_sweeps: list | None = None,
    fvgs_m5: list | None = None,
) -> OrderBlockQuality:
    """Note un OB de référence avec des listes vides par défaut."""
    active = scorer or OrderBlockQualityScorer()
    return active.score(
        ob_candle if ob_candle is not None else make_ob_candle(),
        next_candles if next_candles is not None else bullish_next_candles(),
        atr_value,
        htf_obs or [],
        htf_fvgs or [],
        liquidity_sweeps or [],
        fvgs_m5 or [],
    )


# ---------------------------------------------------------------------------
# Tests obligatoires (spécification)
# ---------------------------------------------------------------------------


def test_grade_a_ob_with_all_confluences() -> None:
    """Tous les critères satisfaits → score 1.0 → Grade A."""
    quality = score_ob(
        htf_obs=[htf_ob()],
        fvgs_m5=[htf_fvg()],
        liquidity_sweeps=[sweep_before()],
    )

    assert quality.grade is OBGrade.A
    assert quality.score == pytest.approx(1.0)
    assert quality.is_fresh is True
    assert quality.displacement_atr == pytest.approx(3.2)
    assert quality.htf_confluence is True
    assert quality.has_liquidity_sweep is True
    assert quality.has_fvg_adjacent is True
    assert quality.rejection_confirmed is True


def test_grade_c_ob_without_displacement() -> None:
    """Pas de displacement + zone retestée + aucune confluence → Grade C.

    Composition du score : displacement 0.0 (1.2 ATR < seuil 1.5), ``is_fresh``
    0.0 (la zone a été retestée), confluences absentes, pas de rejet
    (dernière bougie à corps dominant) → score 0.0.
    """
    retested = make_frame(
        [
            (2001.6, 2002.0, 2000.4, 2001.0),  # retest de la zone OB
            (2000.9, 2001.2, 2000.9, 2001.2),  # corps 0.3, mèche totale 0.0
        ]
    )
    scorer = OrderBlockQualityScorer()
    quality = score_ob(scorer, next_candles=retested)

    assert quality.displacement_atr == pytest.approx(1.2)
    assert quality.displacement_atr < scorer.displacement_threshold
    assert quality.is_fresh is False
    assert quality.htf_confluence is False
    assert quality.has_fvg_adjacent is False
    assert quality.rejection_confirmed is False
    assert quality.score == 0.0
    assert quality.grade is OBGrade.C


def test_is_fresh_false_after_retest() -> None:
    """Une bougie postérieure qui touche la zone OB invalide ``is_fresh``."""
    assert score_ob().is_fresh is True

    retested = make_frame(
        [
            (2001.8, 2003.0, 2001.5, 2002.5),
            (2002.5, 2002.8, 2000.2, 2000.4),  # retour dans la zone [2000, 2001]
        ]
    )
    quality = score_ob(next_candles=retested)

    assert quality.is_fresh is False
    assert quality.htf_confluence is False


def test_htf_confluence_detects_h1_ob() -> None:
    """Un OB H1 proche du midpoint OB valide la confluence HTF."""
    near = htf_ob(top=2001.0, bottom=2000.0)  # midpoint 2000.5 (= OB)
    assert score_ob(htf_obs=[near]).htf_confluence is True

    far = htf_ob(top=2020.0, bottom=2019.0)  # midpoint 2019.5 (hors tolérance)
    assert score_ob(htf_obs=[far]).htf_confluence is False

    # Une FVG H1/H4 compte également comme confluence HTF.
    assert score_ob(htf_fvgs=[htf_fvg()]).htf_confluence is True


def test_rejection_confirmed_with_long_wick() -> None:
    """Mèche totale > corps → rejet confirmé ; corps dominant → non confirmé."""
    long_wick = make_frame([(2002.0, 2005.0, 2001.2, 2002.2)])  # corps 0.2, mèche 2.6
    assert score_ob(next_candles=long_wick).rejection_confirmed is True

    body_dominant = make_frame([(2002.0, 2003.6, 2001.9, 2003.4)])  # corps 1.4, mèche 0.3
    assert score_ob(next_candles=body_dominant).rejection_confirmed is False


def test_scorer_handles_empty_next_candles() -> None:
    """``next_candles`` vide : aucun crash, OB frais par définition.

    Seul ``is_fresh`` (0.20) est satisfait → Grade C, aucun displacement ni rejet.
    """
    quality = score_ob(next_candles=make_empty_frame())

    assert quality.grade is OBGrade.C
    assert quality.score == pytest.approx(WEIGHT_FRESH)
    assert quality.is_fresh is True
    assert quality.displacement_atr == 0.0
    assert quality.rejection_confirmed is False


# ---------------------------------------------------------------------------
# Pondérations, seuils de grade et paramètres
# ---------------------------------------------------------------------------


class TestWeightsAndGrades:
    """Pondérations fixes et seuils de grade."""

    def test_weights_sum_to_one(self) -> None:
        total = (
            WEIGHT_DISPLACEMENT
            + WEIGHT_FRESH
            + WEIGHT_HTF_CONFLUENCE
            + WEIGHT_LIQUIDITY_SWEEP
            + WEIGHT_FVG_ADJACENT
            + WEIGHT_REJECTION
        )
        assert total == pytest.approx(1.0)
        assert GRADE_B_THRESHOLD < GRADE_A_THRESHOLD
        assert OBGrade.A.value == "A"
        assert OBGrade.B.value == "B"
        assert OBGrade.C.value == "C"

    def test_grade_b_boundary_score(self) -> None:
        """``is_fresh`` + ``htf_confluence`` + sweep = 0.55 → exactement Grade B."""
        fresh_no_rejection = make_frame([(2001.2, 2001.45, 2001.15, 2001.4)])
        quality = score_ob(
            next_candles=fresh_no_rejection,
            htf_obs=[htf_ob()],
            liquidity_sweeps=[sweep_before()],
        )

        assert quality.score == pytest.approx(0.55)
        assert quality.grade is OBGrade.B
        assert quality.displacement_atr < 1.5

    def test_grade_a_boundary_without_displacement(self) -> None:
        """Sans displacement, les 5 autres critères (0.75) atteignent Grade A.

        Propriété des pondérations fixes : le displacement n'est pas bloquant à
        lui seul, il retire 0.25 (soit la marge de sécurité du Grade A).
        """
        fresh_long_wick = make_frame([(2001.1, 2001.48, 2001.03, 2001.15)])
        quality = score_ob(
            next_candles=fresh_long_wick,
            htf_obs=[htf_ob()],
            liquidity_sweeps=[sweep_before()],
            fvgs_m5=[htf_fvg()],
        )

        assert quality.displacement_atr < 1.5
        assert quality.rejection_confirmed is True
        assert quality.score == pytest.approx(0.75)
        assert quality.grade is OBGrade.A

    def test_low_score_is_grade_c(self) -> None:
        """``is_fresh`` (0.20) + sweep (0.15) = 0.35 → Grade C (à ignorer)."""
        fresh_no_rejection = make_frame([(2001.2, 2001.45, 2001.15, 2001.4)])
        quality = score_ob(
            next_candles=fresh_no_rejection,
            liquidity_sweeps=[sweep_before()],
        )

        assert quality.score == pytest.approx(0.35)
        assert quality.grade is OBGrade.C

    def test_score_never_exceeds_one(self) -> None:
        quality = score_ob(
            htf_obs=[htf_ob()],
            htf_fvgs=[htf_fvg()],
            fvgs_m5=[htf_fvg()],
            liquidity_sweeps=[sweep_before()],
        )
        assert 0.0 <= quality.score <= 1.0

    def test_to_dict_payload(self) -> None:
        quality = score_ob(htf_obs=[htf_ob()], liquidity_sweeps=[sweep_before()])
        payload = quality.to_dict()

        assert payload["ob_quality_grade"] == quality.grade.value
        assert payload["ob_quality_score"] == quality.score
        assert payload["ob_quality_is_fresh"] is quality.is_fresh
        assert payload["ob_htf_confluence"] is True
        assert payload["ob_has_liquidity_sweep"] is True
        assert payload["ob_displacement_atr"] == pytest.approx(3.2, abs=1e-4)


class TestScorerParameters:
    """Paramètres du constructeur (défauts et validations)."""

    def test_default_parameters(self) -> None:
        scorer = OrderBlockQualityScorer()
        assert scorer.atr_period == 14
        assert scorer.displacement_threshold == pytest.approx(1.5)
        assert scorer.htf_confluence_atr_mult == pytest.approx(1.0)
        assert scorer.fvg_adjacent_atr_mult == pytest.approx(1.0)

    def test_custom_threshold_accepts_smaller_displacement(self) -> None:
        retested = make_frame([(2001.6, 2002.0, 2000.4, 2001.0)])
        strict = score_ob(OrderBlockQualityScorer(), next_candles=retested)
        lenient = score_ob(
            OrderBlockQualityScorer(displacement_threshold=1.0), next_candles=retested
        )

        assert strict.displacement_atr == pytest.approx(lenient.displacement_atr)
        assert strict.grade is OBGrade.C
        assert lenient.score > strict.score

    def test_custom_htf_tolerance(self) -> None:
        far = htf_ob(top=2003.0, bottom=2002.0)  # midpoint 2002.5 (1.5 ATR de l'OB)
        assert score_ob(htf_obs=[far]).htf_confluence is False
        assert (
            score_ob(
                OrderBlockQualityScorer(htf_confluence_atr_mult=2.0), htf_obs=[far]
            ).htf_confluence
            is True
        )

    def test_custom_fvg_tolerance(self) -> None:
        far = htf_fvg(top=2003.0, bottom=2002.0)  # midpoint 2002.5
        assert score_ob(fvgs_m5=[far]).has_fvg_adjacent is False
        assert (
            score_ob(
                OrderBlockQualityScorer(fvg_adjacent_atr_mult=2.0), fvgs_m5=[far]
            ).has_fvg_adjacent
            is True
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"atr_period": 0},
            {"atr_period": -3},
            {"displacement_threshold": -0.1},
            {"htf_confluence_atr_mult": -1.0},
            {"fvg_adjacent_atr_mult": -1.0},
        ],
    )
    def test_invalid_parameters_raise(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            OrderBlockQualityScorer(**kwargs)


class TestRobustness:
    """Entrées dégradées : jamais d'exception, repli Grade C / critères False."""

    def test_invalid_ob_candle_returns_grade_c(self) -> None:
        incomplete = pd.Series({"open": 2001.0, "high": 2001.2, "low": 1999.8})
        quality = score_ob(ob_candle=incomplete)

        assert quality.grade is OBGrade.C
        assert quality.score == 0.0
        assert quality.displacement_atr == 0.0

    def test_inconsistent_ob_candle_returns_grade_c(self) -> None:
        """``high < low`` → bougie incohérente → Grade C."""
        broken = make_ob_candle(high=1999.0, low=2001.0)
        assert score_ob(ob_candle=broken).grade is OBGrade.C

    def test_nan_ob_candle_returns_grade_c(self) -> None:
        assert score_ob(ob_candle=make_ob_candle(close=float("nan"))).grade is OBGrade.C

    def test_nan_last_candle_disables_rejection_and_displacement(self) -> None:
        frame = make_frame([(2002.0, 2003.0, 2001.5, float("nan"))])
        quality = score_ob(next_candles=frame)

        assert quality.rejection_confirmed is False
        assert quality.displacement_atr == 0.0

    def test_frame_without_high_low_columns(self) -> None:
        """Colonnes absentes : zone considérée intacte, aucun crash."""
        only_close = pd.DataFrame({"open": [2002.0], "close": [2002.5]})
        quality = score_ob(next_candles=only_close)

        assert quality.is_fresh is True
        assert quality.rejection_confirmed is False

    def test_fallback_atr_when_atr_value_is_zero(self) -> None:
        quality = score_ob(
            OrderBlockQualityScorer(atr_period=5),
            next_candles=rising_m5_series(),
            atr_value=0.0,
        )
        assert quality.displacement_atr > 1.5

    def test_nan_atr_value_falls_back_to_atr(self) -> None:
        quality = score_ob(
            OrderBlockQualityScorer(atr_period=5),
            next_candles=rising_m5_series(),
            atr_value=float("nan"),
        )
        assert quality.displacement_atr > 1.5

    def test_fallback_atr_zero_without_enough_history(self) -> None:
        short = make_frame([(2002.0, 2002.6, 2001.9, 2002.5), (2002.5, 2003.0, 2002.4, 2002.9)])
        quality = score_ob(
            OrderBlockQualityScorer(atr_period=14), next_candles=short, atr_value=0.0
        )
        assert quality.displacement_atr == 0.0

    def test_sweep_after_ob_is_ignored(self) -> None:
        late = [{"timestamp": T0 + timedelta(minutes=5)}]
        assert score_ob(liquidity_sweeps=late).has_liquidity_sweep is False

    def test_sweep_without_timestamp_is_ignored(self) -> None:
        sweeps = [{"concept": "liquidity_sweep"}]
        assert score_ob(liquidity_sweeps=sweeps).has_liquidity_sweep is False

    def test_sweep_unparseable_timestamp_is_ignored(self) -> None:
        sweeps = [{"timestamp": "not-a-date"}]
        assert score_ob(liquidity_sweeps=sweeps).has_liquidity_sweep is False

    def test_sweep_object_timestamp_attribute(self) -> None:
        sweep = SimpleNamespace(timestamp=T0 - timedelta(minutes=15))
        assert score_ob(liquidity_sweeps=[sweep]).has_liquidity_sweep is True

    def test_sweep_object_time_attribute(self) -> None:
        sweep = SimpleNamespace(time=(T0 - timedelta(minutes=15)).isoformat())
        assert score_ob(liquidity_sweeps=[sweep]).has_liquidity_sweep is True

    def test_naive_ob_timestamp_with_aware_sweep(self) -> None:
        """Fuseaux mixtes : comparaison en heure murale, sans exception."""
        naive_ob = make_ob_candle(timestamp=datetime(2024, 1, 1, 12, 0))
        aware_sweep = {"timestamp": datetime(2024, 1, 1, 11, 30, tzinfo=UTC)}
        quality = score_ob(ob_candle=naive_ob, liquidity_sweeps=[aware_sweep])
        assert quality.has_liquidity_sweep is True

    def test_ob_timestamp_read_from_time_key(self) -> None:
        with_time = pd.Series(
            {
                "open": OB_OPEN,
                "high": OB_HIGH,
                "low": OB_LOW,
                "close": OB_CLOSE,
                "time": T0,
            }
        )
        quality = score_ob(ob_candle=with_time, liquidity_sweeps=[sweep_before()])
        assert quality.has_liquidity_sweep is True

    def test_ob_without_timestamp_has_no_sweep(self) -> None:
        without_ts = pd.Series({"open": OB_OPEN, "high": OB_HIGH, "low": OB_LOW, "close": OB_CLOSE})
        quality = score_ob(ob_candle=without_ts, liquidity_sweeps=[sweep_before()])
        assert quality.has_liquidity_sweep is False

    def test_htf_item_as_object_with_details(self) -> None:
        item = SimpleNamespace(details={"ob_top": 2001.0, "ob_bottom": 2000.0})
        assert score_ob(htf_obs=[item]).htf_confluence is True

    def test_htf_item_with_high_low_mapping(self) -> None:
        item = {"high": OB_HIGH, "low": OB_LOW}
        assert score_ob(htf_obs=[item]).htf_confluence is True

    def test_htf_item_as_object_with_price(self) -> None:
        item = SimpleNamespace(price=OB_MIDPOINT)
        assert score_ob(htf_obs=[item]).htf_confluence is True

    def test_mapping_zone_start_end_midpoint(self) -> None:
        item = {"details": {"zone_start": 2000.0, "zone_end": 2001.0}}
        assert score_ob(fvgs_m5=[item]).has_fvg_adjacent is True

    def test_items_without_midpoint_are_ignored(self) -> None:
        items: list = [object(), {"concept": "order_block"}]
        quality = score_ob(htf_obs=items, fvgs_m5=[None])
        assert quality.htf_confluence is False
        assert quality.has_fvg_adjacent is False

    def test_result_is_frozen(self) -> None:
        quality = score_ob()
        with pytest.raises(FrozenInstanceError):
            quality.score = 0.5  # type: ignore[misc]


class TestCleanArchitecture:
    """Le module ne doit dépendre d'aucun détail d'infrastructure."""

    def test_module_does_not_import_infrastructure(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert imported  # le module importe bien quelque chose
        forbidden = [
            name
            for name in imported
            if name.startswith("arty_trading.infrastructure")
            or name.split(".")[0] == "infrastructure"
        ]
        assert forbidden == []


class TestAdditionalEdges:
    """Cas limites complémentaires (branches défensives des utilitaires)."""

    def test_non_numeric_ob_value_returns_grade_c(self) -> None:
        """Valeur non numérique → conversion impossible → Grade C."""
        broken = make_ob_candle(close="n/a")  # type: ignore[arg-type]
        assert score_ob(ob_candle=broken).grade is OBGrade.C

    def test_fallback_atr_with_empty_frame(self) -> None:
        """``atr_value`` invalide + ``next_candles`` vide → pas d'ATR → pas de displacement."""
        quality = score_ob(next_candles=make_empty_frame(), atr_value=0.0)
        assert quality.displacement_atr == 0.0

    def test_fallback_atr_without_ohlc_columns(self) -> None:
        """``atr_value`` invalide + colonnes manquantes → pas d'ATR de secours."""
        only_close = pd.DataFrame({"close": [2002.0]})
        quality = score_ob(next_candles=only_close, atr_value=0.0)
        assert quality.displacement_atr == 0.0

    def test_mapping_price_key_midpoint(self) -> None:
        """Un dictionnaire SMC exposant ``midpoint``/``price`` est exploitable."""
        assert score_ob(htf_obs=[{"midpoint": OB_MIDPOINT}]).htf_confluence is True
        assert score_ob(htf_fvgs=[{"price": OB_MIDPOINT}]).htf_confluence is True

    def test_item_object_with_high_low_attributes(self) -> None:
        item = SimpleNamespace(high=OB_HIGH, low=OB_LOW)
        assert score_ob(htf_obs=[item]).htf_confluence is True

    def test_sweep_timestamp_as_pandas_timestamp(self) -> None:
        sweep = {"timestamp": pd.Timestamp(T0 - timedelta(minutes=10))}
        assert score_ob(liquidity_sweeps=[sweep]).has_liquidity_sweep is True
