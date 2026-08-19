"""
Tests du diagnostic 10013 — construction de la requête SL/TP (TRADE_ACTION_SLTP).

Couvre : normalisation au tick size, distances BUY/SELL vs bid/ask,
stops/freeze level, NO_CHANGE, échec/succès MT5 (contrat confirm()).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from arty_trading.core.entities import Trade
from arty_trading.core.enums import Direction
from arty_trading.modules.execution import executor as executor_module
from arty_trading.modules.execution.executor import MT5OrderError, OrderExecutor

TRADE_ACTION_SLTP = 1
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID = 10013
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1


class FakeMT5:
    """Faux module MetaTrader5 minimal pour _mt5_modify_order."""

    TRADE_ACTION_SLTP = TRADE_ACTION_SLTP
    TRADE_RETCODE_DONE = TRADE_RETCODE_DONE
    POSITION_TYPE_BUY = POSITION_TYPE_BUY

    def __init__(self, pos, info, tick, send_retcode=TRADE_RETCODE_DONE):
        self.pos = pos
        self.info = info
        self.tick = tick
        self.send_retcode = send_retcode
        self.sent: list[dict] = []

    def initialize(self):
        return True

    def positions_get(self, ticket=None):
        return (self.pos,)

    def symbol_info(self, symbol):
        return self.info

    def symbol_info_tick(self, symbol):
        return self.tick

    def order_check(self, request):
        return SimpleNamespace(retcode=0, comment="ok", request=request)

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.send_retcode, price=0.0, comment="done")


def _info(stops=0, freeze=0, tick_size=0.00001, digits=5, point=0.00001):
    return SimpleNamespace(
        digits=digits,
        point=point,
        trade_tick_size=tick_size,
        trade_stops_level=stops,
        trade_freeze_level=freeze,
    )


def _pos(pos_type=POSITION_TYPE_BUY, sl=1.0950, tp=1.1100, ticket=152505018824):
    return SimpleNamespace(
        ticket=ticket, symbol="EURUSD", type=pos_type,
        price_open=1.1000, sl=sl, tp=tp, volume=0.1,
    )


def _trade(direction=Direction.BUY, sl="1.0950", tp="1.1100", ticket=152505018824):
    t = Trade(
        symbol="EURUSD",
        direction=direction,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        volume=Decimal("0.1"),
    )
    t.ticket = ticket
    return t


def _executor(fake):
    ex = OrderExecutor(mock_mode=False)
    # injection du faux MT5 dans le namespace du module executor
    executor_module.mt5 = fake
    executor_module.MT5_AVAILABLE = True
    return ex


@pytest.fixture(autouse=True)
def _restore_mt5():
    """Restaure l'état MT5 du module après chaque test."""
    original_mt5 = executor_module.mt5
    original_avail = executor_module.MT5_AVAILABLE
    yield
    executor_module.mt5 = original_mt5
    executor_module.MT5_AVAILABLE = original_avail


class TestAlignPriceToTick:
    def test_digits_5_alignment(self):
        assert OrderExecutor._align_price_to_tick(
            Decimal("1.160723"), Decimal("0.00001"), 5
        ) == Decimal("1.16072")

    def test_unaligned_float_representation(self):
        # Représentation flottante parasite -> alignée proprement
        assert OrderExecutor._align_price_to_tick(
            1.1607200000000001, Decimal("0.00001"), 5
        ) == Decimal("1.16072")

    def test_tick_size_5_points(self):
        # Grille de 0.0005 : 1.16072 -> point le plus proche 1.16050
        assert OrderExecutor._align_price_to_tick(
            Decimal("1.16072"), Decimal("0.0005"), 5
        ) == Decimal("1.16050")


class TestSltpDistanceRejection:
    def test_buy_sl_must_be_below_bid(self):
        rej = OrderExecutor._sltp_distance_rejection(
            True, Decimal("1.1050"), Decimal("0"),
            Decimal("1.1000"), Decimal("1.1002"), Decimal("0.0001"),
        )
        assert rej == "SL_TOO_CLOSE_TO_MARKET"

    def test_sell_sl_must_be_above_ask(self):
        rej = OrderExecutor._sltp_distance_rejection(
            False, Decimal("1.0950"), Decimal("0"),
            Decimal("1.1000"), Decimal("1.1002"), Decimal("0.0001"),
        )
        assert rej == "SL_TOO_CLOSE_TO_MARKET"

    def test_no_rejection_when_distances_ok(self):
        assert OrderExecutor._sltp_distance_rejection(
            True, Decimal("1.0950"), Decimal("1.1100"),
            Decimal("1.1000"), Decimal("1.1002"), Decimal("0.0001"),
        ) is None
        assert OrderExecutor._sltp_distance_rejection(
            False, Decimal("1.1050"), Decimal("1.0950"),
            Decimal("1.1000"), Decimal("1.1002"), Decimal("0.0001"),
        ) is None

    def test_no_check_when_market_data_unavailable(self):
        assert OrderExecutor._sltp_distance_rejection(
            True, Decimal("1.1050"), Decimal("0"), None, None, Decimal("0.0001"),
        ) is None


@pytest.mark.asyncio
class TestMt5ModifyOrder:
    async def test_a_valid_sltp_request(self):
        """Test A — la requête valide est correctement construite."""
        fake = FakeMT5(_pos(), _info(), SimpleNamespace(bid=1.10000, ask=1.10002))
        ex = _executor(fake)
        trade = await ex.modify_order(trade=_trade(), stop_loss=1.0960)
        assert len(fake.sent) == 1
        req = fake.sent[0]
        assert req["action"] == TRADE_ACTION_SLTP
        assert req["symbol"] == "EURUSD"
        assert req["position"] == 152505018824
        assert req["sl"] == pytest.approx(1.0960)
        # TP non demandé -> TP réellement présent chez le broker, pas la valeur ARTY
        assert req["tp"] == pytest.approx(1.1100)
        assert trade.stop_loss == Decimal("1.09600")

    async def test_b_buy_sl_above_bid_rejected(self):
        """Test B — BUY : SL >= BID rejeté AVANT order_send."""
        fake = FakeMT5(_pos(), _info(stops=1), SimpleNamespace(bid=1.10000, ask=1.10002))
        ex = _executor(fake)
        with pytest.raises(MT5OrderError, match="SL_TOO_CLOSE_TO_MARKET"):
            await ex.modify_order(trade=_trade(), stop_loss=1.1050)
        assert fake.sent == []  # rien envoyé au broker

    async def test_c_sell_sl_below_ask_rejected(self):
        """Test C — SELL : SL <= ASK rejeté AVANT order_send."""
        fake = FakeMT5(
            _pos(pos_type=POSITION_TYPE_SELL, sl=1.1050, tp=1.0900),
            _info(stops=1),
            SimpleNamespace(bid=1.10000, ask=1.10002),
        )
        ex = _executor(fake)
        trade = _trade(direction=Direction.SELL, sl="1.1050", tp="1.0900")
        with pytest.raises(MT5OrderError, match="SL_TOO_CLOSE_TO_MARKET"):
            await ex.modify_order(trade=trade, stop_loss=1.0950)
        assert fake.sent == []

    async def test_d_sl_aligned_on_tick_size(self):
        """Test D — SL aligné sur le tick size réel du symbole."""
        fake = FakeMT5(_pos(), _info(tick_size=0.0005), SimpleNamespace(bid=1.10000, ask=1.10002))
        ex = _executor(fake)
        await ex.modify_order(trade=_trade(), stop_loss=1.09672)
        assert fake.sent[0]["sl"] == pytest.approx(1.0965)  # grille de 0.0005

    async def test_e_stops_level_rejected_before_send(self):
        """Test E — distance < stops_level : rejet propre, pas de 10013 masqué."""
        # stops_level=10 points -> min_dist = 0.0001 ; SL à 0.00005 du bid
        fake = FakeMT5(_pos(), _info(stops=10), SimpleNamespace(bid=1.10000, ask=1.10002))
        ex = _executor(fake)
        with pytest.raises(MT5OrderError, match="SL_TOO_CLOSE_TO_MARKET"):
            await ex.modify_order(trade=_trade(), stop_loss=1.09995)
        assert fake.sent == []

    async def test_f_no_change_skipped(self):
        """Test F — SL identique au SL broker : pas d'envoi inutile."""
        fake = FakeMT5(
            _pos(sl=1.16072, tp=0.0), _info(), SimpleNamespace(bid=1.15500, ask=1.15502)
        )
        ex = _executor(fake)
        trade = await ex.modify_order(trade=_trade(sl="1.1000", tp="1.1100"), stop_loss=1.16072)
        assert fake.sent == []
        # le broker est déjà au niveau demandé -> trade synchronisé, pas d'erreur
        assert trade.stop_loss == Decimal("1.16072")

    async def test_g_failure_raises_level_stays_retryable(self):
        """Test G — échec MT5 -> exception (confirm() non appelé côté monitor)."""
        fake = FakeMT5(
            _pos(), _info(),
            SimpleNamespace(bid=1.10000, ask=1.10002),
            send_retcode=TRADE_RETCODE_INVALID,
        )
        ex = _executor(fake)
        with pytest.raises(MT5OrderError, match="10013"):
            await ex.modify_order(trade=_trade(), stop_loss=1.0960)

    async def test_g_success_updates_trade(self):
        """Test G — succès MT5 -> trade.stop_loss mis à jour (confirm() possible)."""
        fake = FakeMT5(_pos(), _info(), SimpleNamespace(bid=1.10000, ask=1.10002))
        ex = _executor(fake)
        trade = await ex.modify_order(trade=_trade(), stop_loss=1.0960)
        assert trade.stop_loss == Decimal("1.09600")
