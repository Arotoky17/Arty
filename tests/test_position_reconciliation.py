"""Tests de réconciliation des positions fermées côté broker.

Couvre le correctif anti-spam : une position fermée côté MT5 (TP, SL ou
clôture manuelle) doit être retirée du suivi interne après réconciliation,
sans être retentée indéfiniment, avec un seul log ``[POSITION_RECONCILED``.

Une erreur temporaire (timeout, requote...) reste retentée normalement et
n'est jamais confondue avec une position fermée.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from arty_trading.application.position_monitor import PositionMonitor
from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction


def _trade() -> Trade:
    trade = Trade(
        symbol="XAUUSD",
        direction=Direction.BUY,
        entry_price=Decimal("2350.00"),
        stop_loss=Decimal("2340.00"),
        take_profit=Decimal("2380.00"),
        volume=Decimal("0.10"),
    )
    trade.ticket = 152505263659
    return trade


def _monitor(executor, position_manager=None):
    pm = position_manager if position_manager is not None else MagicMock()
    md = MagicMock(
        get_tick=AsyncMock(return_value={"bid": 2370.00, "ask": 2370.50}),
        get_symbol_info=AsyncMock(return_value=None),
    )
    return PositionMonitor(
        pm, md, executor, MagicMock(), MagicMock(), None,
        AsyncMock(), AsyncMock(),
    )


def _modify_action() -> SimpleNamespace:
    return SimpleNamespace(kind="modify", stop_loss=2365.0, reason="profit_lock_3.0R")


def _executor_not_found() -> MagicMock:
    """Executor simulant une position fermée côté broker.

    ``position_exists`` retourne True au premier appel (check périodique du
    début de cycle : la position semble encore ouverte) puis False (elle a
    disparu entre-temps, confirmé lors de la réconciliation après l'échec).
    """
    ex = MagicMock()
    ex.modify_order = AsyncMock(
        side_effect=Exception("Position 152505263659 introuvable")
    )
    ex.close_partial_order = AsyncMock(
        side_effect=Exception("Position 152505263659 introuvable")
    )
    calls = {"n": 0}

    def _exists(ticket):
        calls["n"] += 1
        return calls["n"] == 1  # ouverte au check périodique, disparue ensuite

    ex.position_exists = MagicMock(side_effect=_exists)
    return ex


def _reconciled_records(caplog):
    return [r for r in caplog.records if "[POSITION_RECONCILED]" in r.message]


@pytest.mark.asyncio
async def test_not_found_position_removed_after_single_attempt(caplog) -> None:
    """Test 1 : retrait après UNE tentative échouée, pas de retry indéfini."""
    trade = _trade()
    ex = _executor_not_found()
    pm = MagicMock()
    pm.evaluate.return_value = [_modify_action()]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    with caplog.at_level("WARNING"):
        await mon.monitor(managed)

    assert "t1" not in managed  # retiré du tracking
    assert ex.modify_order.await_count == 1  # une seule tentative
    assert pm.forget.call_count == 1  # état interne supprimé


@pytest.mark.asyncio
async def test_periodic_reconciliation_without_modify_attempt(caplog) -> None:
    """Test 2 : détection périodique même sans tentative d'ajustement."""
    trade = _trade()
    ex = MagicMock()
    ex.position_exists = MagicMock(return_value=False)
    pm = MagicMock()
    pm.evaluate.return_value = []  # aucune action déclenchée ce cycle
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    with caplog.at_level("WARNING"):
        await mon.monitor(managed)

    assert "t1" not in managed
    assert pm.forget.call_count == 1
    assert _reconciled_records(caplog)


@pytest.mark.asyncio
async def test_single_reconciled_log_per_ticket(caplog) -> None:
    """Test 3 : un seul log [POSITION_RECONCILED] par ticket, pas un par cycle."""
    trade = _trade()
    ex = _executor_not_found()
    pm = MagicMock()
    pm.evaluate.return_value = [_modify_action()]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    with caplog.at_level("WARNING"):
        await mon.monitor(managed)
        # Cycles suivants : la position n'est plus suivie, plus rien ne se passe.
        for _ in range(5):
            await mon.monitor(managed)

    assert len(_reconciled_records(caplog)) == 1
    assert ex.modify_order.await_count == 1
    assert pm.forget.call_count == 1


@pytest.mark.asyncio
async def test_no_module_acts_after_reconciliation() -> None:
    """Test 4 : après réconciliation, plus aucune action sur le ticket."""
    trade = _trade()
    ex = _executor_not_found()
    pm = MagicMock()
    pm.evaluate.return_value = [_modify_action()]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    await mon.monitor(managed)
    for _ in range(3):
        await mon.monitor(managed)

    assert pm.evaluate.call_count == 1  # plus ré-évalué
    assert ex.modify_order.await_count == 1  # plus modifié
    assert ex.close_partial_order.await_count == 0


@pytest.mark.asyncio
async def test_transient_error_is_retried_not_reconciled() -> None:
    """Test 5 : une erreur temporaire reste retentée, jamais réconciliée."""
    trade = _trade()
    ex = MagicMock()
    ex.modify_order = AsyncMock(side_effect=Exception("timeout réseau broker"))
    ex.position_exists = MagicMock(return_value=True)  # position toujours ouverte
    pm = MagicMock()
    pm.evaluate.return_value = [_modify_action()]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    await mon.monitor(managed)
    await mon.monitor(managed)

    assert "t1" in managed  # toujours suivi : erreur transitoire
    assert ex.modify_order.await_count == 2  # retenté normalement
    assert pm.forget.call_count == 0  # jamais réconcilié


@pytest.mark.asyncio
async def test_open_position_never_removed() -> None:
    """Test 6 : une position réellement ouverte n'est jamais retirée."""
    trade = _trade()
    ex = MagicMock()
    ex.position_exists = MagicMock(return_value=True)  # existe côté broker
    ex.modify_order = AsyncMock(
        side_effect=Exception("Position 152505263659 introuvable")
    )
    pm = MagicMock()
    pm.evaluate.return_value = [_modify_action()]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    for _ in range(3):
        await mon.monitor(managed)

    assert "t1" in managed  # jamais retirée
    assert pm.forget.call_count == 0

    # Cas executor sans position_exists (paper/mock) : pas de retrait non plus.
    ex2 = MagicMock()
    del ex2.position_exists
    ex2.modify_order = AsyncMock(
        side_effect=Exception("Position 152505263659 introuvable")
    )
    mon2 = _monitor(ex2, position_manager=pm)
    managed2 = {"t1": _trade()}
    await mon2.monitor(managed2)
    assert "t1" in managed2


@pytest.mark.asyncio
async def test_partial_close_not_found_is_reconciled(caplog) -> None:
    """Cas partial_close : même réconciliation immédiate (anti-spam)."""
    trade = _trade()
    ex = _executor_not_found()
    pm = MagicMock()
    pm.evaluate.return_value = [
        SimpleNamespace(kind="partial_close", close_fraction=0.5,
                        reason="partial_profit_1.0R")
    ]
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    with caplog.at_level("WARNING"):
        await mon.monitor(managed)
        for _ in range(3):
            await mon.monitor(managed)

    assert "t1" not in managed
    assert ex.close_partial_order.await_count == 1
    assert len(_reconciled_records(caplog)) == 1


@pytest.mark.asyncio
async def test_reconciled_log_contains_expected_fields(caplog) -> None:
    """Le log de réconciliation contient les champs spécifiés."""
    trade = _trade()
    ex = MagicMock()
    ex.position_exists = MagicMock(return_value=False)
    pm = MagicMock()
    pm.evaluate.return_value = []
    mon = _monitor(ex, position_manager=pm)
    managed = {"t1": trade}

    with caplog.at_level("WARNING"):
        await mon.monitor(managed)

    msg = _reconciled_records(caplog)[0].message
    assert "symbol=XAUUSD" in msg
    assert "ticket=152505263659" in msg
    assert "reason=NOT_FOUND_ON_BROKER" in msg
    assert "action=REMOVED_FROM_TRACKING" in msg
    assert "closed_via=" in msg
