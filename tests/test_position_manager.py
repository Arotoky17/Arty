"""Tests des règles de gestion de position en R."""

from decimal import Decimal

from arty_trading.config.settings import PositionSettings
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction
from arty_trading.modules.execution import PositionManager
from arty_trading.modules.execution.executor import OrderExecutor
from arty_trading.modules.execution.paper_executor import PaperOrderExecutor


def _trade() -> Trade:
    return Trade(
        symbol="EURUSD",
        direction=Direction.BUY,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0990"),
        take_profit=Decimal("1.1040"),
        volume=Decimal("0.10"),
    )


def _legacy_settings() -> PositionSettings:
    """Réplique la configuration historique (BE + partial + trailing fixe)."""
    return PositionSettings(enable_profit_lock=False)


def _default_settings() -> PositionSettings:
    return PositionSettings()


def test_stop_loss_hit_requests_a_full_close() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    assert manager.evaluate(trade, Decimal("1.0990"))[0].reason == "stop_loss"


async def test_paper_executor_reduces_the_position_for_partial_tp() -> None:
    executor = PaperOrderExecutor()
    trade = _trade()
    trade.ticket = 50001
    executor._open_trades[trade.ticket] = trade  # état contrôlé du simulateur
    closed = await executor.close_partial_order(trade, 0.5)
    assert closed is not None
    assert closed.volume == Decimal("0.05")
    assert trade.volume == Decimal("0.05")


# =============================================================================
# Nouveau système — Profit Lock progressif (remplace le break-even)
# =============================================================================


def test_profit_lock_secures_partial_gain_at_first_level() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1005"))  # +0.5R
    lock = [a for a in actions if a.reason == "profit_lock_0.5R"]
    assert len(lock) == 1
    assert lock[0].stop_loss == Decimal("1.1001")  # entry + 0.1R


def test_profit_lock_levels_are_one_shot() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1010"))  # +1.0R
    lock = [a for a in actions if a.reason == "profit_lock_1.0R"]
    assert len(lock) == 1
    assert lock[0].stop_loss == Decimal("1.1004")  # entry + 0.4R
    trade.stop_loss = lock[0].stop_loss
    actions = manager.evaluate(trade, Decimal("1.1012"))
    assert not any(a.reason == "profit_lock_1.0R" for a in actions)


def test_sl_never_becomes_less_protective_on_retrace() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1020"))  # +2.0R → SL = entry + 1.2R
    trade.stop_loss = Decimal("1.1012")
    actions = manager.evaluate(trade, Decimal("1.1004"))  # retrace
    modify = [a for a in actions if a.kind == "modify"]
    assert all(a.stop_loss > trade.stop_loss for a in modify)


def test_partial_profit_level_executes_once() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1010"))  # +1.0R
    partials = [a for a in actions if a.kind == "partial_close"]
    assert len(partials) == 1
    assert partials[0].close_fraction == Decimal("0.25")
    manager.confirm(trade, partials[0])  # exécution confirmée -> niveau consommé
    actions = manager.evaluate(trade, Decimal("1.1015"))
    assert not any(a.kind == "partial_close" for a in actions)


def test_runner_suppresses_classic_tp_and_continues() -> None:
    manager = PositionManager(_default_settings())  # RUNNER on, TP off
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1040"))  # +4R, au-delà du TP
    assert not any(a.reason == "take_profit" for a in actions)
    assert any(a.reason.startswith("profit_lock") for a in actions)


def test_runner_tp_enabled_closes_at_runner_tp_r() -> None:
    settings = PositionSettings(runner_tp_enabled=True, runner_tp_r=2.5)
    manager = PositionManager(settings)
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1025"))  # +2.5R
    assert any(a.reason == "runner_take_profit" for a in actions)


def test_runner_structure_break_exits_only_when_engaged() -> None:
    from arty_trading.modules.execution.sl_guard import StructureContext

    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    broken = StructureContext(
        swing_low=Decimal("1.0990"), swing_high=Decimal("1.1050"),
        hl=Decimal("1.0990"), lh=Decimal("1.1050"), atr=Decimal("0.0005"),
        bearish_break=True,
    )
    actions = manager.evaluate(trade, Decimal("1.1015"), broken)  # +1.5R
    assert not any(a.reason == "runner_structure_break" for a in actions)
    actions = manager.evaluate(trade, Decimal("1.1030"), broken)  # +3R
    assert any(a.reason == "runner_structure_break" for a in actions)


def test_structure_trailing_moves_sl_below_confirmed_hl() -> None:
    from arty_trading.modules.execution.sl_guard import StructureContext

    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    structure = StructureContext(
        swing_low=Decimal("1.1025"), swing_high=Decimal("1.1060"),
        hl=Decimal("1.1025"), lh=Decimal("1.1060"), atr=Decimal("0.0010"),
    )
    actions = manager.evaluate(trade, Decimal("1.1030"), structure)
    trailing = [a for a in actions if a.reason.startswith("structure_trailing")]
    assert len(trailing) == 1
    assert trailing[0].stop_loss == Decimal("1.1023")  # HL - 0.2*ATR (> lock +3R)


def test_sell_structure_trailing_moves_sl_above_confirmed_lh() -> None:
    from arty_trading.modules.execution.sl_guard import StructureContext

    trade = Trade(
        symbol="EURUSD",
        direction=Direction.SELL,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.1010"),
        take_profit=Decimal("1.0960"),
        volume=Decimal("0.10"),
    )
    manager = PositionManager(_default_settings())
    manager.register(trade)
    structure = StructureContext(
        swing_low=Decimal("1.0950"), swing_high=Decimal("1.0975"),
        hl=Decimal("1.0950"), lh=Decimal("1.0975"), atr=Decimal("0.0010"),
    )
    actions = manager.evaluate(trade, Decimal("1.0970"), structure)
    trailing = [a for a in actions if a.reason.startswith("structure_trailing")]
    assert len(trailing) == 1
    assert trailing[0].stop_loss == Decimal("1.0977")  # LH + 0.2*ATR (< lock +3R)


def test_initial_risk_and_sl_are_immutable() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    initial_risk = manager._states[str(trade.id)].initial_risk
    initial_sl = manager._states[str(trade.id)].initial_sl
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    manager.evaluate(trade, Decimal("1.1030"))
    state = manager._states[str(trade.id)]
    assert state.initial_risk == initial_risk
    assert state.initial_sl == initial_sl


def test_register_with_restored_state_keeps_initial_risk() -> None:
    manager = PositionManager(_default_settings())
    trade = _trade()
    trade.stop_loss = Decimal("1.1004")  # SL déjà déplacé (+1R lock)
    snapshot = {
        "initial_sl": "1.0990",
        "initial_risk": "0.0010",
        "initial_volume": "0.10",
        "profit_lock_level": 1,
        "partial_levels_done": [0],
        "runner_active": False,
    }
    manager.register(trade, restored=snapshot)
    state = manager._states[str(trade.id)]
    assert state.initial_risk == Decimal("0.0010")  # pas de faux R recalculé
    assert state.initial_sl == Decimal("1.0990")
    assert state.partial_levels_done == {0}
    actions = manager.evaluate(trade, Decimal("1.1010"))
    assert not any(a.kind == "partial_close" for a in actions)


# =============================================================================
# Mode legacy (PROFIT_LOCK_ENABLED=false) — comportement historique préservé
# =============================================================================


def test_legacy_break_even_partial_and_trailing_are_triggered_once() -> None:
    manager = PositionManager(_legacy_settings())
    trade = _trade()
    manager.register(trade)
    at_one_r = manager.evaluate(trade, Decimal("1.1010"))
    assert at_one_r[0].reason == "break_even"
    trade.stop_loss = Decimal("1.1000")
    at_two_r = manager.evaluate(trade, Decimal("1.1020"))
    assert at_two_r[0].kind == "partial_close"
    at_three_r = manager.evaluate(trade, Decimal("1.1030"))
    assert at_three_r[0].reason == "trailing_stop"


def test_legacy_target_requests_a_full_close() -> None:
    manager = PositionManager(_legacy_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1040"))
    assert any(a.reason == "take_profit" for a in actions)


def test_legacy_trailing_never_worsens_sl() -> None:
    manager = PositionManager(_legacy_settings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    manager.evaluate(trade, Decimal("1.1020"))
    actions = manager.evaluate(trade, Decimal("1.0995"))
    assert not any(a.reason == "trailing_stop" for a in actions)


def test_legacy_retrace_after_1_5_r_keeps_protection() -> None:
    manager = PositionManager(_legacy_settings())
    trade = _trade()
    manager.register(trade)
    manager.evaluate(trade, Decimal("1.1010"))
    manager.evaluate(trade, Decimal("1.1015"))
    trailing_actions_1 = manager.evaluate(trade, Decimal("1.1015"))
    assert any(a.reason == "trailing_stop" for a in trailing_actions_1)
    actions_after_retrace = manager.evaluate(trade, Decimal("1.1005"))
    assert not any(a.reason == "stop_loss" for a in actions_after_retrace)


# =============================================================================
# DÉCISION != EXÉCUTION — consommation d'un niveau après confirmation broker
# =============================================================================


def test_profit_lock_level_not_consumed_on_execution_failure() -> None:
    """MT5 refuse le modify : le niveau reste disponible (re-proposé)."""
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1005"))  # +0.5R
    lock = [a for a in actions if a.reason == "profit_lock_0.5R"]
    assert len(lock) == 1 and lock[0].lock_level == 0
    # Échec simulé : le monitor ne confirme PAS (pas d'appel à confirm).
    assert manager.snapshot(trade)["profit_lock_level"] == -1
    # Tick suivant : nouvelle tentative possible.
    retry = manager.evaluate(trade, Decimal("1.1006"))  # +0.6R
    assert any(a.reason == "profit_lock_0.5R" for a in retry)


def test_profit_lock_level_consumed_on_execution_success() -> None:
    """Modify réussi : le niveau est consommé et persisté."""
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1006"))  # +0.6R
    lock = [a for a in actions if a.reason == "profit_lock_0.5R"][0]
    trade.stop_loss = lock.stop_loss  # SL réellement appliqué par MT5
    manager.confirm(trade, lock)  # confirmation monitor
    assert manager.snapshot(trade)["profit_lock_level"] == 0
    # Le palier n'est plus re-proposé.
    assert not any(
        a.reason == "profit_lock_0.5R" for a in manager.evaluate(trade, Decimal("1.1006"))
    )


def test_partial_level_not_consumed_on_execution_failure() -> None:
    """MT5 refuse le partial : partial_levels_done reste vide, retry possible."""
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1010"))  # +1.0R
    partial = [a for a in actions if a.kind == "partial_close"][0]
    assert partial.partial_level == 0
    assert manager.snapshot(trade)["partial_levels_done"] == []
    # Échec simulé (pas de confirm) : re-proposé au tick suivant.
    retry = manager.evaluate(trade, Decimal("1.1012"))
    assert any(a.kind == "partial_close" for a in retry)


def test_partial_level_consumed_on_execution_success() -> None:
    """Partial réussi : le niveau est consommé et persisté."""
    manager = PositionManager(_default_settings())
    trade = _trade()
    manager.register(trade)
    actions = manager.evaluate(trade, Decimal("1.1010"))  # +1.0R
    partial = [a for a in actions if a.kind == "partial_close"][0]
    trade.volume -= trade.volume * Decimal("0.25")  # 25 % réellement fermés
    manager.confirm(trade, partial)
    assert manager.snapshot(trade)["partial_levels_done"] == [0]
    assert not any(
        a.kind == "partial_close" for a in manager.evaluate(trade, Decimal("1.1015"))
    )


# =============================================================================
# Validation du volume partial (volume_min / volume_step broker)
# =============================================================================


def _executor_with_constraints(monkeypatch, volume_min, volume_step):
    executor = OrderExecutor(mock_mode=True)
    closed_volumes: list[Decimal] = []

    async def fake_close_order(partial_trade: Trade) -> Trade:
        closed_volumes.append(partial_trade.volume)
        return partial_trade.model_copy(update={"is_open": False})

    monkeypatch.setattr(
        executor, "_partial_volume_constraints",
        lambda symbol: (Decimal(str(volume_min)), Decimal(str(volume_step))),
    )
    monkeypatch.setattr(executor, "close_order", fake_close_order)
    return executor, closed_volumes


async def test_partial_skipped_when_volume_below_broker_minimum(monkeypatch) -> None:
    """0.01 lot × 25 % = 0.0025 < volume_min : aucun ordre envoyé, None retourné."""
    executor, closed_volumes = _executor_with_constraints(monkeypatch, "0.01", "0.01")
    trade = _trade()
    trade.volume = Decimal("0.01")
    result = await executor.close_partial_order(trade, 0.25)
    assert result is None  # aucun INVALID_VOLUME envoyé inutilement
    assert closed_volumes == []  # aucun ordre parti
    assert trade.volume == Decimal("0.01")  # position intacte


async def test_partial_volume_respects_volume_step(monkeypatch) -> None:
    """Le volume envoyé est arrondi au step inférieur, jamais au-dessus."""
    executor, closed_volumes = _executor_with_constraints(monkeypatch, "0.01", "0.01")
    trade = _trade()
    trade.volume = Decimal("0.10")
    result = await executor.close_partial_order(trade, 0.33)  # brut = 0.033
    assert result is not None
    assert closed_volumes == [Decimal("0.03")]  # 0.033 arrondi au step 0.01
    assert trade.volume == Decimal("0.07")  # jamais plus que demandé


async def test_partial_closes_fully_when_reliquat_below_minimum(monkeypatch) -> None:
    """Reliquat < volume_min après partial : fermeture totale de la position."""
    executor, closed_volumes = _executor_with_constraints(monkeypatch, "0.05", "0.01")
    trade = _trade()
    trade.volume = Decimal("0.10")
    result = await executor.close_partial_order(trade, 0.75)  # brut 0.075 -> 0.07
    # reliquat 0.03 < volume_min 0.05 : fermeture totale attendue.
    assert result is not None
    assert closed_volumes == [Decimal("0.10")]  # position entière fermée
    assert not result.is_open


# =============================================================================
# Persistance immédiate du runner + reprise après redémarrage
# =============================================================================


async def test_runner_engagement_is_persisted_immediately(tmp_path) -> None:
    """runner_active=True doit atteindre le stockage dès l'engagement."""
    from unittest.mock import AsyncMock, MagicMock

    from arty_trading.application.position_monitor import PositionMonitor
    from arty_trading.application.position_state_store import PositionStateStore

    manager = PositionManager(_default_settings())
    trade = _trade()
    trade.ticket = 60001
    manager.register(trade)

    async def apply_modify(t, stop_loss=None, take_profit=None):
        t.stop_loss = Decimal(str(stop_loss))
        return t

    executor = MagicMock()
    executor.modify_order = AsyncMock(side_effect=apply_modify)
    market_data = MagicMock(
        get_tick=AsyncMock(return_value={"bid": 1.1030, "ask": 1.1031}),
        get_symbol_info=AsyncMock(return_value=None),
    )
    store = PositionStateStore(str(tmp_path))
    monitor = PositionMonitor(
        manager, market_data, executor, MagicMock(), MagicMock(), None,
        AsyncMock(), AsyncMock(), state_store=store,
    )
    await monitor.monitor({str(trade.id): trade})  # +3R -> runner engagé
    assert store.load(trade.ticket)["runner_active"] is True


def test_restart_restores_runner_state(tmp_path) -> None:
    """Redémarrage : l'état restauré conserve runner_active."""
    from arty_trading.application.position_state_store import PositionStateStore

    manager = PositionManager(_default_settings())
    trade = _trade()
    trade.ticket = 60002
    manager.register(trade)
    store = PositionStateStore(str(tmp_path))
    manager.evaluate(trade, Decimal("1.1030"))  # +3R -> runner engagé
    store.save(trade.ticket, manager.snapshot(trade))

    # Nouveau process : nouveau manager, état restauré via la réconciliation.
    manager2 = PositionManager(_default_settings())
    restored_trade = _trade()
    restored_trade.ticket = 60002
    restored_trade.stop_loss = Decimal("1.1020")  # SL déjà déplacé par MT5
    manager2.register(restored_trade, restored=store.load(restored_trade.ticket))
    snapshot = manager2.snapshot(restored_trade)
    assert snapshot["runner_active"] is True
    assert snapshot["initial_risk"] == "0.0010"  # R initial jamais recalculé
