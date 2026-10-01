"""Tests du module ``order_block_tracker`` — suivi frais / mitigé des OB.

Couvre :
- les 5 tests obligatoires de la spécification (register/get_fresh, mitigation
  bullish et bearish, élagage, incrément d'âge) ;
- l'élagage automatique tous les 100 updates, le filtre par timeframe ;
- les cas limites : bougies incomplètes/NaN, ordre chronologique, fuseaux
  mixtes, enregistrement dupliqué, validation des paramètres ;
- l'absence totale de dépendance broker (module pur, backtest + live).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from arty_trading.modules.smc.order_block_tracker import (
    DIRECTIONS,
    PRUNE_EVERY,
    OrderBlockTracker,
    TrackedOB,
)

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "arty_trading"
    / "modules"
    / "smc"
    / "order_block_tracker.py"
)

#: Zone OB de référence : 2009.0 (low) → 2010.0 (high).
OB_LOW, OB_HIGH = 2009.0, 2010.0


def m5_candle(low: float = 2011.0, high: float = 2013.0, bar: int = 1) -> pd.Series:
    """Bougie M5 horodatée (``bar`` = nombre de M5 depuis ``T0``)."""
    return pd.Series(
        {
            "open": (low + high) / 2.0,
            "high": high,
            "low": low,
            "close": (low + high) / 2.0,
            "timestamp": T0 + timedelta(minutes=5 * bar),
        }
    )


def far_candle(bar: int = 1) -> pd.Series:
    """Bougie au-dessus de la zone OB (ne peut pas mitiger l'OB de référence)."""
    return m5_candle(low=2011.0, high=2013.0, bar=bar)


def make_ob(
    direction: str = "bullish",
    high: float = OB_HIGH,
    low: float = OB_LOW,
    timeframe: str = "H1",
    created_at: datetime = T0,
    ob_id: str = "ob-1",
    symbol: str = "XAUUSD",
) -> TrackedOB:
    """OB suivi de référence (zone 2009-2010)."""
    return TrackedOB.create(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        high=high,
        low=low,
        created_at=created_at,
        ob_id=ob_id,
    )


# ---------------------------------------------------------------------------
# Tests obligatoires (spécification)
# ---------------------------------------------------------------------------


def test_register_and_get_fresh() -> None:
    """Un OB enregistré est suivi et retourné par ``get_fresh``."""
    tracker = OrderBlockTracker()
    ob = make_ob(ob_id="ob-1", timeframe="H1")

    tracker.register(ob)

    assert tracker.tracked_count == 1
    assert len(tracker) == 1
    fresh = tracker.get_fresh()
    assert [item.ob_id for item in fresh] == ["ob-1"]
    assert fresh[0].is_fresh is True
    assert fresh[0].grade is None
    assert tracker.get_mitigated() == []


def test_mitigation_on_touch_bullish() -> None:
    """OB bullish : mitigé dès qu'une bougie M5 a ``low <= OB.high``."""
    tracker = OrderBlockTracker()
    tracker.register(make_ob(direction="bullish"))

    tracker.update(far_candle(bar=1))
    assert tracker.get_fresh()[0].mitigated is False

    tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=2))  # low == OB.high

    ob = tracker.get_mitigated()[0]
    assert ob.mitigated is True
    assert ob.is_fresh is False
    assert ob.mitigated_at == T0 + timedelta(minutes=10)
    assert tracker.get_fresh() == []


def test_mitigation_on_touch_bearish() -> None:
    """OB bearish : mitigé dès qu'une bougie M5 a ``high >= OB.low``."""
    tracker = OrderBlockTracker()
    tracker.register(make_ob(direction="bearish"))

    tracker.update(m5_candle(low=2006.0, high=2008.0, bar=1))
    assert tracker.get_fresh()[0].mitigated is False

    tracker.update(m5_candle(low=2008.0, high=OB_LOW, bar=2))  # high == OB.low

    ob = tracker.get_mitigated()[0]
    assert ob.mitigated is True
    assert ob.mitigated_at == T0 + timedelta(minutes=10)
    assert tracker.get_fresh() == []


def test_prune_expired_removes_old_obs() -> None:
    """``prune_expired`` supprime uniquement les OB dont l'âge dépasse la limite."""
    tracker = OrderBlockTracker(max_age_bars=3)
    tracker.register(make_ob(ob_id="ob-old"))

    for bar in range(1, 4):
        tracker.update(far_candle(bar=bar))  # age 3 : toujours valide

    assert tracker.prune_expired() == 0
    assert len(tracker.get_fresh()) == 1

    tracker.update(far_candle(bar=4))  # age 4 > max_age_bars

    assert tracker.get_fresh() == []
    assert tracker.prune_expired() == 1
    assert tracker.tracked_count == 0


def test_age_bars_increments() -> None:
    """``age_bars`` est incrémenté à chaque ``update``."""
    tracker = OrderBlockTracker()
    tracker.register(make_ob())

    assert tracker.updates == 0

    for expected_age in (1, 2, 3):
        tracker.update(far_candle(bar=expected_age))

        assert tracker.updates == expected_age
        assert tracker.get_fresh()[0].age_bars == expected_age


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------


class TestRegistration:
    """``register`` : fabrique, validation et doublons."""

    def test_create_fills_uuid_midpoint_and_created_at(self) -> None:
        ob = TrackedOB.create(
            symbol="XAUUSD",
            timeframe="M5",
            direction="bullish",
            high=2010.0,
            low=2008.0,
        )

        assert len(ob.ob_id) == 32
        assert ob.midpoint == pytest.approx(2009.0)
        assert ob.created_at.tzinfo is not None
        assert ob.grade is None
        assert ob.mitigated is False
        assert ob.age_bars == 0

    def test_new_id_is_unique(self) -> None:
        assert TrackedOB.new_id() != TrackedOB.new_id()

    def test_duplicate_ob_id_is_ignored(self) -> None:
        """Un doublon ne réinitialise jamais l'état d'un OB déjà suivi."""
        tracker = OrderBlockTracker()
        first = make_ob(ob_id="dup")
        tracker.register(first)
        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))  # → mitigé

        other = make_ob(ob_id="dup", high=2020.0, low=2019.0)
        tracker.register(other)

        assert tracker.tracked_count == 1
        assert tracker.get_mitigated()[0] is first
        assert tracker.get_mitigated()[0].mitigated is True

    def test_empty_ob_id_rejected(self) -> None:
        """``create`` génère toujours un id : le garde-fou protège l'usage direct."""
        tracker = OrderBlockTracker()
        ob = TrackedOB(
            ob_id="",
            symbol="XAUUSD",
            timeframe="H1",
            direction="bullish",
            high=OB_HIGH,
            low=OB_LOW,
            midpoint=2009.5,
            created_at=T0,
        )

        with pytest.raises(ValueError):
            tracker.register(ob)

    def test_invalid_direction_rejected(self) -> None:
        tracker = OrderBlockTracker()
        with pytest.raises(ValueError):
            tracker.register(make_ob(direction="sideways"))

    def test_inverted_bounds_rejected(self) -> None:
        tracker = OrderBlockTracker()
        with pytest.raises(ValueError):
            tracker.register(make_ob(high=2009.0, low=2010.0))

    def test_invalid_max_age_bars_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrderBlockTracker(max_age_bars=0)

    def test_directions_and_prune_constants(self) -> None:
        assert DIRECTIONS == ("bullish", "bearish")
        assert PRUNE_EVERY == 100
        assert OrderBlockTracker().prune_every == PRUNE_EVERY
        assert OrderBlockTracker().max_age_bars == 200


# ---------------------------------------------------------------------------
# Mise à jour / mitigation
# ---------------------------------------------------------------------------


class TestUpdateRules:
    """``update`` : mitigation, âge, robustesse des bougies."""

    def test_mitigation_is_final(self) -> None:
        """``mitigated_at`` reste figé même si le prix retouche la zone."""
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))
        first_touch_time = tracker.get_mitigated()[0].mitigated_at

        tracker.update(m5_candle(low=2010.5, high=2012.0, bar=2))

        ob = tracker.get_mitigated()[0]
        assert ob.mitigated_at == first_touch_time
        assert len(tracker.get_mitigated()) == 1

    def test_age_increments_for_mitigated_obs(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))
        tracker.update(far_candle(bar=2))

        assert tracker.get_mitigated()[0].age_bars == 2

    def test_candle_without_prices_only_ages(self) -> None:
        """Bougie sans ``low``/``high`` : l'âge avance, pas de mitigation."""
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series(
            {"open": 2011.0, "close": 2012.0, "timestamp": T0 + timedelta(minutes=5)}
        )

        tracker.update(candle)

        assert tracker.get_fresh()[0].age_bars == 1
        assert tracker.get_fresh()[0].mitigated is False

    def test_nan_prices_do_not_mitigate(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series(
            {
                "high": float("nan"),
                "low": float("nan"),
                "timestamp": T0 + timedelta(minutes=5),
            }
        )

        tracker.update(candle)

        assert tracker.get_fresh()[0].mitigated is False

    def test_update_without_timestamp_leaves_mitigated_at_none(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series({"high": 2012.0, "low": 2010.0})

        tracker.update(candle)

        ob = tracker.get_mitigated()[0]
        assert ob.mitigated is True
        assert ob.mitigated_at is None

    def test_timestamp_read_from_time_key(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series(
            {"high": 2012.0, "low": 2010.0, "time": T0 + timedelta(minutes=15)}
        )

        tracker.update(candle)

        assert tracker.get_mitigated()[0].mitigated_at == T0 + timedelta(minutes=15)

    def test_timestamp_read_from_string(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series(
            {"high": 2012.0, "low": 2010.0, "date": "2024-01-01T12:30:00+00:00"}
        )

        tracker.update(candle)

        assert tracker.get_mitigated()[0].mitigated_at == T0 + timedelta(minutes=30)

    def test_unparseable_timestamp_leaves_mitigated_at_none(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob())
        candle = pd.Series({"high": 2012.0, "low": 2010.0, "timestamp": "not-a-date"})

        tracker.update(candle)

        assert tracker.get_mitigated()[0].mitigated_at is None

    def test_unusable_price_and_timestamp_values_are_ignored(self) -> None:
        """``None``, booléen, inf et horodatage non convertible : jamais d'exception."""
        tracker = OrderBlockTracker()
        tracker.register(make_ob())

        tracker.update(pd.Series({"high": True, "low": None, "timestamp": 1704110400}))
        assert tracker.get_fresh()[0].mitigated is False

        tracker.update(
            pd.Series({"high": float("inf"), "low": 2010.0, "timestamp": 1704110400})
        )
        assert tracker.get_fresh()[0].mitigated is False

        tracker.update(pd.Series({"high": 2012.0, "low": 2010.0, "timestamp": 1704110400}))

        ob = tracker.get_mitigated()[0]
        assert ob.mitigated is True
        assert ob.mitigated_at is None  # horodatage entier non exploitable

    def test_candle_older_than_ob_cannot_mitigate(self) -> None:
        """Flux désordonné / multi-TF : une bougie antérieure n'est pas postérieure."""
        tracker = OrderBlockTracker()
        tracker.register(make_ob(created_at=T0))
        older = pd.Series(
            {"high": 2012.0, "low": 2010.0, "timestamp": T0 - timedelta(minutes=5)}
        )

        tracker.update(older)

        assert tracker.get_fresh()[0].mitigated is False
        assert tracker.get_fresh()[0].age_bars == 1

    def test_mixed_timezone_candle_can_mitigate(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob(created_at=datetime(2024, 1, 1, 12, 0)))
        candle = pd.Series(
            {
                "high": 2012.0,
                "low": 2010.0,
                "timestamp": datetime(2024, 1, 1, 12, 5, tzinfo=UTC),
            }
        )

        tracker.update(candle)

        assert tracker.get_mitigated()[0].mitigated is True

    def test_empty_tracker_update_is_noop(self) -> None:
        tracker = OrderBlockTracker()
        tracker.update(far_candle())

        assert tracker.tracked_count == 0
        assert tracker.updates == 1
        assert tracker.get_fresh() == []

    def test_unknown_direction_is_never_mitigated(self) -> None:
        """Défense : une direction corrompue après enregistrement ne mitige pas."""
        tracker = OrderBlockTracker()
        ob = make_ob()
        tracker.register(ob)
        ob.direction = "sideways"

        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))

        assert tracker.get_fresh()[0].mitigated is False


# ---------------------------------------------------------------------------
# Filtres, élagage automatique, grade et sérialisation
# ---------------------------------------------------------------------------


class TestFreshFiltering:
    """``get_fresh`` : filtre par timeframe et exclusion des expirés."""

    def test_timeframe_filter_is_case_insensitive(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob(ob_id="h1", timeframe="H1"))
        tracker.register(make_ob(ob_id="m5", timeframe="M5"))
        tracker.register(make_ob(ob_id="h4", timeframe="H4"))

        assert [ob.ob_id for ob in tracker.get_fresh("H1")] == ["h1"]
        assert [ob.ob_id for ob in tracker.get_fresh("m5")] == ["m5"]
        assert [ob.ob_id for ob in tracker.get_fresh("H4")] == ["h4"]
        assert [ob.ob_id for ob in tracker.get_fresh()] == ["h1", "m5", "h4"]
        assert tracker.get_fresh("M15") == []

    def test_expired_ob_excluded_from_fresh_until_pruned(self) -> None:
        tracker = OrderBlockTracker(max_age_bars=2)
        tracker.register(make_ob())

        for bar in range(1, 4):
            tracker.update(far_candle(bar=bar))

        assert tracker.get_fresh() == []
        assert tracker.tracked_count == 1  # toujours suivi tant que non élagué
        assert tracker.prune_expired() == 1

    def test_mitigated_ob_excluded_from_filtered_fresh_view(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob(timeframe="H1"))

        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))

        assert tracker.get_fresh("H1") == []
        assert [ob.ob_id for ob in tracker.get_mitigated()] == ["ob-1"]


class TestAutoPrune:
    """Élagage automatique tous les ``PRUNE_EVERY`` updates."""

    def test_auto_prune_fires_at_hundredth_update(self) -> None:
        tracker = OrderBlockTracker(max_age_bars=5)
        tracker.register(make_ob())

        for bar in range(1, PRUNE_EVERY):
            tracker.update(far_candle(bar=bar))

        assert tracker.updates == 99
        assert tracker.tracked_count == 1  # pas encore élagué

        tracker.update(far_candle(bar=PRUNE_EVERY))

        assert tracker.updates == 100
        assert tracker.tracked_count == 0  # élagage automatique déclenché

    def test_auto_prune_keeps_young_obs(self) -> None:
        tracker = OrderBlockTracker(max_age_bars=200)
        tracker.register(make_ob())

        for bar in range(1, PRUNE_EVERY + 1):
            tracker.update(far_candle(bar=bar))

        assert tracker.tracked_count == 1
        assert tracker.get_fresh()[0].age_bars == PRUNE_EVERY


class TestGradeAndSerialization:
    """Grade (rempli par le scorer) et sérialisation."""

    def test_set_grade_updates_tracked_ob(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob(ob_id="graded"))

        assert tracker.set_grade("graded", "A") is True
        assert tracker.get_fresh()[0].grade == "A"
        assert tracker.set_grade("inconnu", "A") is False

    def test_to_dict_payload_of_fresh_ob(self) -> None:
        payload = make_ob(ob_id="dict-1", timeframe="H4", direction="bearish").to_dict()

        assert payload["ob_id"] == "dict-1"
        assert payload["symbol"] == "XAUUSD"
        assert payload["timeframe"] == "H4"
        assert payload["direction"] == "bearish"
        assert payload["high"] == pytest.approx(OB_HIGH)
        assert payload["low"] == pytest.approx(OB_LOW)
        assert payload["midpoint"] == pytest.approx(2009.5)
        assert payload["created_at"] == T0.isoformat()
        assert payload["mitigated"] is False
        assert payload["mitigated_at"] is None
        assert payload["grade"] is None
        assert payload["age_bars"] == 0

    def test_to_dict_payload_after_mitigation(self) -> None:
        tracker = OrderBlockTracker()
        tracker.register(make_ob(ob_id="dict-2"))

        tracker.update(m5_candle(low=OB_HIGH, high=2012.0, bar=1))

        payload = tracker.get_mitigated()[0].to_dict()
        assert payload["mitigated"] is True
        assert payload["mitigated_at"] == (T0 + timedelta(minutes=5)).isoformat()


# ---------------------------------------------------------------------------
# Pureté (backtest + live) et cycle de vie complet
# ---------------------------------------------------------------------------


class TestPurity:
    """Module pur : aucune dépendance broker ni infrastructure."""

    def test_module_has_no_broker_or_infrastructure_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert imported  # le module importe bien quelque chose
        forbidden_roots = {"MetaTrader5", "mt5", "infrastructure"}
        forbidden = [
            name
            for name in imported
            if name.startswith("arty_trading.infrastructure")
            or name.split(".")[0] in forbidden_roots
        ]
        assert forbidden == []

    def test_tracker_needs_no_broker_to_run(self) -> None:
        """Un cycle complet fonctionne sans MT5 (données pandas uniquement)."""
        tracker = OrderBlockTracker(max_age_bars=50)
        tracker.register(
            TrackedOB.create(
                symbol="XAUUSD",
                timeframe="M5",
                direction="bullish",
                high=OB_HIGH,
                low=OB_LOW,
                created_at=T0,
            )
        )

        for bar in range(1, 6):
            tracker.update(m5_candle(low=2011.0, high=2013.0, bar=bar))

        assert len(tracker.get_fresh("M5")) == 1


class TestLifecycle:
    """Le même tracker sert en backtest (boucle) et en live (callback M5)."""

    def test_backtest_loop_lifecycle(self) -> None:
        tracker = OrderBlockTracker(max_age_bars=10)
        tracker.register(make_ob(ob_id="bt-1"))

        for bar in range(1, 5):  # bougies hors zone
            tracker.update(m5_candle(low=2011.0, high=2013.0, bar=bar))

        assert [ob.ob_id for ob in tracker.get_fresh()] == ["bt-1"]
        assert tracker.get_fresh()[0].age_bars == 4

        tracker.update(m5_candle(low=2009.5, high=2011.0, bar=5))  # retest → mort

        assert tracker.get_fresh() == []
        mitigated = tracker.get_mitigated()[0]
        assert mitigated.age_bars == 5
        assert mitigated.mitigated_at == T0 + timedelta(minutes=25)

    def test_expiry_boundary_then_prune(self) -> None:
        tracker = OrderBlockTracker(max_age_bars=6)
        tracker.register(make_ob(ob_id="bt-2"))

        for bar in range(1, 7):
            tracker.update(far_candle(bar=bar))

        assert len(tracker.get_fresh()) == 1  # age == limite : encore valide

        tracker.update(far_candle(bar=7))

        assert tracker.get_fresh() == []
        assert tracker.prune_expired() == 1
        assert tracker.tracked_count == 0

    def test_multi_timeframe_bookkeeping(self) -> None:
        """OB H4 et OB M5 dans la même zone : suivis et mitigués indépendamment."""
        tracker = OrderBlockTracker()
        tracker.register(make_ob(ob_id="h4-ob", timeframe="H4"))
        tracker.register(make_ob(ob_id="m5-ob", timeframe="M5"))

        tracker.update(m5_candle(low=2008.5, high=2011.0, bar=1))

        assert tracker.get_fresh("H4") == []
        assert tracker.get_fresh("M5") == []
        assert len(tracker.get_mitigated()) == 2
        assert tracker.updates == 1
