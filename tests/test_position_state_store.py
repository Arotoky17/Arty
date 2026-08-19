"""Tests de la persistance durable de l'état de gestion des positions."""

import json
from pathlib import Path

from arty_trading.application.position_state_store import PositionStateStore


def test_save_load_roundtrip(tmp_path: Path) -> None:
    store = PositionStateStore(str(tmp_path))
    state = {
        "initial_sl": "1.0990",
        "initial_risk": "0.0010",
        "initial_volume": "0.10",
        "profit_lock_level": 2,
        "partial_levels_done": [0],
        "runner_active": True,
    }
    store.save(12345, state)
    loaded = store.load(12345)
    assert loaded == state


def test_load_missing_returns_none(tmp_path: Path) -> None:
    store = PositionStateStore(str(tmp_path))
    assert store.load(99999) is None


def test_delete_removes_state(tmp_path: Path) -> None:
    store = PositionStateStore(str(tmp_path))
    store.save(1, {"initial_sl": "1.0"})
    store.delete(1)
    assert store.load(1) is None
    store.delete(1)  # idempotent


def test_corrupted_file_returns_none(tmp_path: Path) -> None:
    store = PositionStateStore(str(tmp_path))
    (tmp_path / "42.json").write_text("not json", encoding="utf-8")
    assert store.load(42) is None


def test_position_manager_snapshot_matches_persisted_state(tmp_path: Path) -> None:
    from decimal import Decimal

    from arty_trading.config.settings import PositionSettings
    from arty_trading.core.entities import Trade
    from arty_trading.core.enums import Direction
    from arty_trading.modules.execution import PositionManager

    manager = PositionManager(PositionSettings())
    trade = Trade(
        symbol="EURUSD",
        direction=Direction.BUY,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0990"),
        take_profit=Decimal("1.1040"),
        volume=Decimal("0.10"),
    )
    manager.register(trade)
    # Simulation du cycle : profit lock +1R proposé PUIS confirmé (MT5 OK).
    actions = manager.evaluate(trade, Decimal("1.1010"))
    trade.stop_loss = Decimal("1.1004")  # SL réellement appliqué par MT5
    lock_action = next(a for a in actions if a.lock_level is not None)
    manager.confirm(trade, lock_action)  # consommation après confirmation

    store = PositionStateStore(str(tmp_path))
    snapshot = manager.snapshot(trade)
    assert snapshot is not None
    store.save(trade.id, snapshot)

    # Redémarrage : nouveau manager, SL courant déjà déplacé
    manager2 = PositionManager(PositionSettings())
    trade2 = trade.model_copy()
    manager2.register(trade2, restored=store.load(trade.id))
    state = manager2._states[str(trade2.id)]
    assert state.initial_risk == Decimal("0.0010")
    assert state.profit_lock_level >= 1  # palier restauré, pas réexécuté
