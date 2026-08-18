"""Tests du position sizing avec contrainte de marge (demo_test_adaptive.compute_volume).

Verifient : calcul du volume par risque, contrainte order_calc_margin,
MARGIN_SAFETY_FACTOR=0.80, normalisation volume_step/min/max, INSUFFICIENT_MARGIN,
et l'absence de modification des regles de trading (SL/TP/RR/direction).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest


def _fake_mt5():
    mt5 = types.ModuleType("MetaTrader5")
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.TRADE_RETCODE_DONE = 10009
    mt5.TRADE_RETCODE_NO_MONEY = 10019
    mt5.ORDER_FILLING_FOK = 0
    mt5.ORDER_FILLING_IOC = 1
    mt5.ORDER_TIME_GTC = 0
    mt5.TRADE_ACTION_DEAL = 1
    mt5.ACCOUNT_TRADE_MODE_REAL = 0
    for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"):
        setattr(mt5, f"TIMEFRAME_{tf}", 0)
    mt5.symbol_info = MagicMock(return_value=None)
    mt5.symbol_info_tick = MagicMock(return_value=None)
    mt5.account_info = MagicMock(return_value=None)
    mt5.order_calc_margin = MagicMock(return_value=None)
    mt5.positions_get = MagicMock(return_value=())
    mt5.order_send = MagicMock(return_value=None)
    mt5.last_error = MagicMock(return_value=(0, "ok"))
    return mt5


@pytest.fixture()
def demo(monkeypatch):
    fake = _fake_mt5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "demo_test_adaptive_test", os.path.join(root, "demo_test_adaptive.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_test_adaptive_test"] = mod
    spec.loader.exec_module(mod)
    mod._mt5 = fake
    return mod


def _sym(minfo):
    s = MagicMock()
    s.trade_tick_size = minfo.get("tick_size", 0.00001)
    s.trade_tick_value = minfo.get("tick_value", 1.0)
    s.volume_min = minfo.get("vmin", 0.01)
    s.volume_max = minfo.get("vmax", 100.0)
    s.volume_step = minfo.get("step", 0.01)
    return s


def _acct(free_margin):
    a = MagicMock()
    a.margin_free = free_margin
    return a


BALANCE = 10000.0
RISK = 0.01


class TestVolumeCalculation:
    """TEST 1-4 : EURUSD/XAUUSD BUY/SELL -> volume correct."""

    def _setup(self, demo, tick_size, tick_value, margin_per_lot, free_margin, **kw):
        demo._mt5.symbol_info.return_value = _sym(
            {"tick_size": tick_size, "tick_value": tick_value, **kw}
        )
        demo._mt5.account_info.return_value = _acct(free_margin)
        demo._mt5.order_calc_margin.return_value = margin_per_lot

    def test_eurusd_buy(self, demo, capsys):
        # SL 20 pips = 0.002 => 200 ticks => loss/lot 200$ => vol = 100/200 = 0.5
        self._setup(demo, 0.00001, 1.0, 2000.0, 50000.0)
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == pytest.approx(0.5)
        demo._mt5.order_calc_margin.assert_called_once()
        assert "POSITION SIZING | EURUSD | BUY" in capsys.readouterr().out

    def test_eurusd_sell(self, demo):
        self._setup(demo, 0.00001, 1.0, 2000.0, 50000.0)
        vol = demo.compute_volume("EURUSD", "sell", BALANCE, RISK, 1.1000, 1.1020)
        assert vol == pytest.approx(0.5)

    def test_xauusd_buy(self, demo):
        # Gold: SL 5$ => 500 ticks, tick_value 1$ => loss/lot 500$ => vol 0.2
        self._setup(demo, 0.01, 1.0, 2000.0, 50000.0)
        vol = demo.compute_volume("XAUUSD", "buy", BALANCE, RISK, 4000.0, 3995.0)
        assert vol == pytest.approx(0.2)

    def test_xauusd_sell(self, demo):
        self._setup(demo, 0.01, 1.0, 2000.0, 50000.0)
        vol = demo.compute_volume("XAUUSD", "sell", BALANCE, RISK, 4000.0, 4005.0)
        assert vol == pytest.approx(0.2)


class TestMarginConstraint:
    """TEST 5, 6, 10, 11 : marge, reduction, blocage, safety factor."""

    def test_volume_reduced_by_margin(self, demo):
        # vol risque = 0.5 mais marge permet seulement 0.25
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1000.0)  # usable = 800
        demo._mt5.order_calc_margin.return_value = 3200.0    # max vol = 0.25
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == pytest.approx(0.25)

    def test_insufficient_margin_blocks_order(self, demo, capsys):
        # volume_min 0.01 exige 900$ mais usable=800$ -> bloque, aucun order_send
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1000.0)
        demo._mt5.order_calc_margin.return_value = 90000.0
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == 0.0
        out = capsys.readouterr().out
        assert "ORDER BLOCKED | EURUSD | BUY" in out
        assert "INSUFFICIENT_MARGIN" in out
        demo._mt5.order_send.assert_not_called()

    def test_margin_safety_factor_080(self, demo):
        # free=1000, factor 0.8 -> usable 800 ; margin/lot=1600 -> max 0.5
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1000.0)
        demo._mt5.order_calc_margin.return_value = 1600.0
        assert demo.MARGIN_SAFETY_FACTOR == 0.80
        # risque permet 1.0 (SL court) -> marge limite a 0.5
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0995)
        assert vol == pytest.approx(0.5)

    def test_margin_never_increases_volume(self, demo):
        # enorme marge -> volume reste le volume risque (0.5)
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1e9)
        demo._mt5.order_calc_margin.return_value = 1.0
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == pytest.approx(0.5)

    def test_margin_calculation_failure_blocks(self, demo, capsys):
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1000.0)
        demo._mt5.order_calc_margin.return_value = None
        assert demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1, 1.09) == 0.0
        assert "MARGIN_CALCULATION_FAILED" in capsys.readouterr().out


class TestVolumeNormalization:
    """TEST 7, 8, 9 : step / min / max."""

    def test_volume_step_respected(self, demo):
        # vol risque = 100/187.5 = 0.5333 -> step 0.01 -> 0.53
        demo._mt5.symbol_info.return_value = _sym({"tick_value": 0.9375})
        demo._mt5.account_info.return_value = _acct(1e9)
        demo._mt5.order_calc_margin.return_value = 1.0
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == pytest.approx(0.53)
        assert abs(vol / 0.01 - round(vol / 0.01)) < 1e-9

    def test_volume_min_respected(self, demo):
        # vol risque 0.004 < vmin 0.01 -> bloque (ne jamais depasser le risque)
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1e9)
        demo._mt5.order_calc_margin.return_value = 1.0
        vol = demo.compute_volume("EURUSD", "buy", 10.0, RISK, 1.1000, 1.0980)
        assert vol == 0.0

    def test_volume_max_respected(self, demo):
        # vol risque enorme mais vmax = 100
        demo._mt5.symbol_info.return_value = _sym({"tick_value": 0.001, "vmax": 100.0})
        demo._mt5.account_info.return_value = _acct(1e9)
        demo._mt5.order_calc_margin.return_value = 0.001
        vol = demo.compute_volume("EURUSD", "buy", BALANCE, RISK, 1.1000, 1.0980)
        assert vol == pytest.approx(100.0)


class TestTradingRulesUnchanged:
    """TEST 12, 13, 14, 15 : SL/TP/RR/direction inchanges, 10019 gere."""

    def test_sl_and_tp_not_modified(self, demo):
        demo._mt5.symbol_info.return_value = _sym({})
        demo._mt5.account_info.return_value = _acct(1e9)
        demo._mt5.order_calc_margin.return_value = 1000.0
        demo.compute_volume("EURUSD", "sell", BALANCE, RISK, 1.1000, 1.0980)
        # send_order transmet SL/TP tels quels
        demo._mt5.symbol_info_tick.return_value = MagicMock(ask=1.1001, bid=1.0999)
        demo.send_order("EURUSD", "sell", 0.5, 1.0980, 1.0960)
        req = demo._mt5.order_send.call_args[0][0]
        assert req["sl"] == 1.0980 and req["tp"] == 1.0960
        assert req["type"] == demo._mt5.ORDER_TYPE_SELL  # direction inchangee

    def test_retcode_10019_logged(self, demo, capsys):
        demo._mt5.symbol_info_tick.return_value = MagicMock(ask=1.1, bid=1.1)
        res = MagicMock(retcode=10019, comment="No money")
        demo._mt5.order_send.return_value = res
        assert demo.send_order("EURUSD", "buy", 0.5, 1.09, 1.12) is None
        assert "NO_MONEY" in capsys.readouterr().out

    def test_no_retry_with_bigger_volume(self, demo):
        demo._mt5.symbol_info_tick.return_value = MagicMock(ask=1.1, bid=1.1)
        res = MagicMock(retcode=10019, comment="No money")
        demo._mt5.order_send.return_value = res
        demo.send_order("EURUSD", "buy", 0.5, 1.09, 1.12)
        assert demo._mt5.order_send.call_count == 1

    def test_symbol_restriction_unchanged(self, demo):
        assert demo.ALLOWED_SYMBOLS == {"EURUSD", "XAUUSD"}
        assert demo.SYMBOLS == ["EURUSD", "XAUUSD"]

