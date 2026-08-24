"""Tests du gestionnaire de risque."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arty_trading.config.settings import RiskSettings
from arty_trading.core.entities import Signal, Trade, TradingAccount
from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingMode
from arty_trading.modules.risk import RiskManager


class MockRiskSettings(RiskSettings):
    """Mock simple pour éviter les problèmes de validation Pydantic."""

    def __init__(self, **kwargs):
        defaults = {
            "risk_per_trade": 0.01,
            "max_daily_risk": 0.03,
            "max_drawdown": 0.10,
            "max_open_positions": 3,
            "max_consecutive_losses": 3,
            "one_trade_per_symbol": True,
        }
        defaults.update(kwargs)
        # Les champs de RiskSettings utilisent des alias (ex: RISK_PER_TRADE),
        # on passe donc les alias à super().__init__().
        data = {
            "RISK_PER_TRADE": defaults["risk_per_trade"],
            "MAX_DAILY_RISK": defaults["max_daily_risk"],
            "MAX_DRAWDOWN": defaults["max_drawdown"],
            "MAX_OPEN_POSITIONS": defaults["max_open_positions"],
            "MAX_CONSECUTIVE_LOSSES": defaults["max_consecutive_losses"],
            "ONE_TRADE_PER_SYMBOL": defaults["one_trade_per_symbol"],
        }
        super().__init__(**data)


def make_account(balance: float = 10000.0, equity: float = 10000.0) -> TradingAccount:
    return TradingAccount(
        login=12345,
        server="Demo",
        name="Test",
        currency="USD",
        balance=Decimal(str(balance)),
        equity=Decimal(str(equity)),
        margin=Decimal("0"),
        free_margin=Decimal(str(equity)),
        leverage=100,
        mode=TradingMode.PAPER,
        is_connected=True,
    )


def make_signal(
    symbol: str = "EURUSD",
    direction: Direction = Direction.BUY,
    entry: float = 1.0800,
    sl: float = 1.0780,
    tp: float = 1.0840,
    confidence: float = 0.7,
) -> Signal:
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(sl)),
        take_profit=Decimal(str(tp)),
        confidence=confidence,
        strategy_name="Test",
        timeframe=TimeFrame.H1,
        justification="Test signal",
        smc_concepts=["BOS"],
    )


def make_trade(symbol: str = "EURUSD") -> Trade:
    return Trade(
        symbol=symbol,
        direction=Direction.BUY,
        entry_price=Decimal("1.0800"),
        stop_loss=Decimal("1.0780"),
        take_profit=Decimal("1.0840"),
        volume=Decimal("0.10"),
    )


def make_market_data(
    tick_size: float = 0.00001,
    tick_value: float = 1.0,
    contract_size: float = 100000,
    symbol: str = "EURUSD",
) -> AsyncMock:
    """Mock d'un provider de marché retournant des infos symbole réelles."""
    md = AsyncMock()
    md.get_symbol_info.return_value = {
        "name": symbol,
        "digits": 5,
        "point": tick_size,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_mode": 0,
        "spread": 5,
        "trade_tick_size": tick_size,
        "trade_tick_value": tick_value,
        "trade_contract_size": contract_size,
    }
    return md


def make_risk_settings(
    risk_per_trade: float = 0.01,
    max_daily_risk: float = 0.03,
    max_drawdown: float = 0.10,
    max_open_positions: int = 3,
    max_consecutive_losses: int = 3,
    one_trade_per_symbol: bool = True,
) -> MockRiskSettings:
    return MockRiskSettings(
        risk_per_trade=risk_per_trade,
        max_daily_risk=max_daily_risk,
        max_drawdown=max_drawdown,
        max_open_positions=max_open_positions,
        max_consecutive_losses=max_consecutive_losses,
        one_trade_per_symbol=one_trade_per_symbol,
    )


class TestRiskManagerInitialization:
    def test_default_init(self):
        rm = RiskManager()
        assert rm.open_positions_count == 0
        assert rm.consecutive_losses == 0
        assert rm.current_drawdown == 0.0

    def test_custom_settings(self):
        settings = make_risk_settings(risk_per_trade=0.02)
        rm = RiskManager(settings=settings)
        assert rm.settings.risk_per_trade == 0.02


class TestValidateSignal:
    @pytest.mark.asyncio
    async def test_valid_signal(self):
        rm = RiskManager(settings=make_risk_settings(), min_confidence=0.5, min_risk_reward=1.5)
        signal = make_signal(confidence=0.7)
        account = make_account()
        assert await rm.validate_signal(signal, account) is True

    @pytest.mark.asyncio
    async def test_reject_sl_equal_entry(self):
        rm = RiskManager(settings=make_risk_settings())
        signal = make_signal(sl=1.0800)
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_tp_equal_entry(self):
        rm = RiskManager(settings=make_risk_settings())
        signal = make_signal(tp=1.0800)
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_low_confidence(self):
        rm = RiskManager(settings=make_risk_settings(), min_confidence=0.8)
        signal = make_signal(confidence=0.5)
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_low_rr(self):
        rm = RiskManager(settings=make_risk_settings(), min_risk_reward=3.0)
        signal = make_signal(sl=1.0790, tp=1.0810)  # RR = 2.0
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_max_positions(self):
        rm = RiskManager(settings=make_risk_settings(max_open_positions=1))
        rm.register_trade(make_trade("GBPUSD"))
        signal = make_signal()
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_duplicate_symbol(self):
        rm = RiskManager(settings=make_risk_settings(one_trade_per_symbol=True))
        rm.register_trade(make_trade("EURUSD"))
        signal = make_signal(symbol="EURUSD")
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_consecutive_losses(self):
        rm = RiskManager(settings=make_risk_settings(max_consecutive_losses=2))
        trade = make_trade()
        rm.close_trade(trade, Decimal("-50"))
        rm.close_trade(trade, Decimal("-50"))
        signal = make_signal()
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_daily_risk_exceeded(self):
        rm = RiskManager(settings=make_risk_settings(max_daily_risk=0.01))
        # Daily loss = 50, max daily = 10000 * 0.01 = 100
        rm.close_trade(make_trade(), Decimal("-50"))
        # Now daily_loss = 50, still under 100
        # Add another loss to exceed
        rm.close_trade(make_trade(), Decimal("-60"))
        signal = make_signal()
        assert await rm.validate_signal(signal, make_account()) is False

    @pytest.mark.asyncio
    async def test_reject_drawdown_exceeded(self):
        rm = RiskManager(settings=make_risk_settings(max_drawdown=0.05))
        rm.update_equity(Decimal("10000"))
        rm.update_equity(Decimal("9000"))  # 10% drawdown > 5%
        signal = make_signal()
        assert await rm.validate_signal(signal, make_account()) is False


class TestCalculatePositionSize:
    @pytest.mark.asyncio
    async def test_standard_calculation(self):
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01))
        signal = make_signal(entry=1.0800, sl=1.0780)  # 20 pips SL
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        # risk = 100, sl_pips = 20, pip_value_per_lot = 10
        # volume = 100 / (20 * 10) = 0.5
        assert volume == 0.5

    @pytest.mark.asyncio
    async def test_jpy_pair(self):
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01))
        signal = make_signal(symbol="USDJPY", entry=150.00, sl=149.00)  # 100 pips SL (pip=0.01)
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        # risk = 100, sl_pips = 100, pip_value_per_lot = 10
        # volume = 100 / (100 * 10) = 0.1
        assert volume == 0.1

    @pytest.mark.asyncio
    async def test_minimum_volume(self):
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.001))
        signal = make_signal(entry=1.0800, sl=1.0780)
        account = make_account(balance=100)
        volume = await rm.calculate_position_size(signal, account)
        # Very small risk should return minimum 0.01
        assert volume >= 0.01

    @pytest.mark.asyncio
    async def test_zero_sl_distance(self):
        rm = RiskManager(settings=make_risk_settings())
        signal = make_signal(entry=1.0800, sl=1.0800)
        account = make_account()
        volume = await rm.calculate_position_size(signal, account)
        assert volume == 0.01

    @pytest.mark.asyncio
    async def test_uses_real_tick_value(self):
        """Utilise les vraies infos du symbole (tick size/value) du provider."""
        md = make_market_data(tick_size=0.00001, tick_value=1.0)
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01), market_data=md)
        signal = make_signal(entry=1.0800, sl=1.0780)  # 0.002 / 1e-5 = 200 ticks
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        # risk = 100, perte/lot = 200 * 1.0 = 200 => volume = 100 / 200 = 0.5
        assert volume == 0.5
        md.get_symbol_info.assert_awaited_once_with("EURUSD")

    @pytest.mark.asyncio
    async def test_xauusd_uses_gold_tick_size_and_value(self):
        """XAUUSD sizing utilise tick size/value MT5, pas les constantes EURUSD."""
        md = make_market_data(tick_size=0.01, tick_value=1.0, contract_size=100)
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01), market_data=md)
        signal = make_signal(symbol="XAUUSD", entry=4000.0, sl=3995.0)
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        # risk=100, SL=5.00, tick_size=0.01 => 500 ticks, loss/lot=500 => volume=0.20
        assert volume == 0.2
        md.get_symbol_info.assert_awaited_once_with("XAUUSD")

    @pytest.mark.asyncio
    async def test_custom_tick_value_changes_volume(self):
        """Un tick value différent modifie la taille calculée."""
        # tick_value = 0.5 => perte/lot = 200 * 0.5 = 100 => volume = 100 / 100 = 1.0
        md = make_market_data(tick_size=0.00001, tick_value=0.5)
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01), market_data=md)
        signal = make_signal(entry=1.0800, sl=1.0780)
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        assert volume == 1.0

    @pytest.mark.asyncio
    async def test_falls_back_when_no_market_data(self):
        """Sans provider, on conserve le calcul heuristique par constantes."""
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01))
        signal = make_signal(entry=1.0800, sl=1.0780)  # 20 pips, pip value 10
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        assert volume == 0.5

    @pytest.mark.asyncio
    async def test_falls_back_when_market_data_errors(self):
        """En cas d'erreur du provider, on retombe sur l'heuristique."""
        md = AsyncMock()
        md.get_symbol_info.side_effect = RuntimeError("MT5 down")
        rm = RiskManager(settings=make_risk_settings(risk_per_trade=0.01), market_data=md)
        signal = make_signal(entry=1.0800, sl=1.0780)
        account = make_account(balance=10000)
        volume = await rm.calculate_position_size(signal, account)
        assert volume == 0.5


class TestCanOpenTrade:
    @pytest.mark.asyncio
    async def test_can_open_empty(self):
        rm = RiskManager(settings=make_risk_settings())
        assert await rm.can_open_trade("EURUSD") is True

    @pytest.mark.asyncio
    async def test_cannot_open_max_positions(self):
        rm = RiskManager(settings=make_risk_settings(max_open_positions=1))
        rm.register_trade(make_trade("GBPUSD"))
        assert await rm.can_open_trade("EURUSD") is False

    @pytest.mark.asyncio
    async def test_cannot_open_duplicate_symbol(self):
        rm = RiskManager(settings=make_risk_settings(one_trade_per_symbol=True))
        rm.register_trade(make_trade("EURUSD"))
        assert await rm.can_open_trade("EURUSD") is False

    @pytest.mark.asyncio
    async def test_can_open_different_symbol(self):
        rm = RiskManager(settings=make_risk_settings(one_trade_per_symbol=True))
        rm.register_trade(make_trade("EURUSD"))
        assert await rm.can_open_trade("GBPUSD") is True

    @pytest.mark.asyncio
    async def test_can_open_duplicate_when_disabled(self):
        rm = RiskManager(settings=make_risk_settings(one_trade_per_symbol=False))
        rm.register_trade(make_trade("EURUSD"))
        assert await rm.can_open_trade("EURUSD") is True


class TestTradeTracking:
    def test_register_trade(self):
        rm = RiskManager(settings=make_risk_settings())
        trade = make_trade("EURUSD")
        rm.register_trade(trade)
        assert rm.open_positions_count == 1

    def test_close_trade_with_loss(self):
        rm = RiskManager(settings=make_risk_settings())
        trade = make_trade("EURUSD")
        rm.register_trade(trade)
        rm.close_trade(trade, Decimal("-50"))
        assert rm.open_positions_count == 0
        assert rm.consecutive_losses == 1
        assert rm.daily_loss == Decimal("50")

    def test_close_trade_with_profit(self):
        rm = RiskManager(settings=make_risk_settings())
        trade = make_trade("EURUSD")
        rm.register_trade(trade)
        rm.close_trade(trade, Decimal("50"))
        assert rm.open_positions_count == 0
        assert rm.consecutive_losses == 0

    def test_consecutive_losses_reset_on_win(self):
        rm = RiskManager(settings=make_risk_settings())
        trade = make_trade()
        rm.close_trade(trade, Decimal("-50"))
        rm.close_trade(trade, Decimal("-50"))
        assert rm.consecutive_losses == 2
        rm.close_trade(trade, Decimal("50"))
        assert rm.consecutive_losses == 0


class TestDrawdown:
    def test_update_equity_peak(self):
        rm = RiskManager(settings=make_risk_settings())
        rm.update_equity(Decimal("10000"))
        assert rm.current_drawdown == 0.0

    def test_drawdown_calculation(self):
        rm = RiskManager(settings=make_risk_settings())
        rm.update_equity(Decimal("10000"))
        rm.update_equity(Decimal("9000"))
        assert rm.current_drawdown == 0.1

    def test_drawdown_resets_on_new_peak(self):
        rm = RiskManager(settings=make_risk_settings())
        rm.update_equity(Decimal("10000"))
        rm.update_equity(Decimal("9000"))
        rm.update_equity(Decimal("11000"))
        assert rm.current_drawdown == 0.0


class TestDailyReset:
    def test_reset_daily(self):
        rm = RiskManager(settings=make_risk_settings())
        rm.close_trade(make_trade(), Decimal("-50"))
        assert rm.daily_loss == Decimal("50")
        rm.reset_daily()
        assert rm.daily_loss == Decimal("0")


class TestRiskReport:
    def test_risk_report(self):
        rm = RiskManager(settings=make_risk_settings())
        rm.register_trade(make_trade("EURUSD"))
        rm.close_trade(make_trade("GBPUSD"), Decimal("-50"))
        report = rm.get_risk_report()
        assert report["open_positions"] == 1
        assert report["max_open_positions"] == 3
        assert report["consecutive_losses"] == 1
        assert report["max_consecutive_losses"] == 3
        assert report["risk_per_trade_pct"] == 1.0
        assert report["max_daily_risk_pct"] == 3.0
        assert report["max_drawdown_pct"] == 10.0
        assert report["one_trade_per_symbol"] is True
