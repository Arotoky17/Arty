"""Diagnostic après assouplissement des filtres."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from arty_trading.config.settings import Settings
from arty_trading.core.entities import Candle, Signal, TradingAccount
from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingMode
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.decision.market_context import MarketContext
from arty_trading.modules.decision.market_structure_engine import MarketStructureEngine
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalValidator
from arty_trading.utils.helpers import calculate_atr


def make_candle(idx: int, symbol: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.M5,
        time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=1000,
        spread=3,
    )


def make_eurusd_uptrend(n: int = 50) -> list[Candle]:
    candles = []
    base = 1.0800
    for i in range(n):
        o = base + i * 0.0005
        c = o + 0.0010
        h = c + 0.0005
        l = o - 0.0003
        candles.append(make_candle(i, "EURUSD", o, h, l, c))
        if i % 10 == 9:
            base = float(candles[-1].close) - 0.0015
    return candles


def make_signal(symbol: str, direction: Direction) -> Signal:
    entry = Decimal("1.0850") if direction == Direction.BUY else Decimal("1.0820")
    sl = Decimal("1.0830") if direction == Direction.BUY else Decimal("1.0840")
    tp = Decimal("1.0890") if direction == Direction.BUY else Decimal("1.0780")
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=0.9,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.M5,
    )


async def main():
    print("=" * 60)
    print("DIAGNOSTIC après assouplissement des filtres")
    print("=" * 60)

    settings = Settings()
    print(f"\ndebug_calibration_mode = {settings.debug_calibration_mode}")
    print(f"SignalSettings.min_confidence = {settings.signals.min_confidence}")
    print(f"DecisionSettings.minimum_score = {settings.decision.minimum_score}")
    print(f"DecisionSettings.minimum_risk_reward = {settings.decision.minimum_risk_reward}")
    print(f"DecisionSettings.enable_kill_zone = {settings.decision.enable_kill_zone}")

    eurusd_profile = settings.get_instrument_profile("EURUSD")
    xauusd_profile = settings.get_instrument_profile("XAUUSD")
    print(f"\nEURUSD.min_risk_reward = {eurusd_profile.min_risk_reward if eurusd_profile else 'N/A'}")
    print(f"XAUUSD.min_risk_reward = {xauusd_profile.min_risk_reward if xauusd_profile else 'N/A'}")

    candles = make_eurusd_uptrend(50)
    atr = calculate_atr(candles)
    print(f"\nATR(14) EURUSD M5 = {atr:.5f}")

    from arty_trading.modules.decision.market_structure_engine import MarketStructureSettings
    print(f"MarketStructureSettings.max_structure_age = {MarketStructureSettings().max_structure_age}")

    smc_data = [
        {"concept": "break_of_structure", "direction": "bullish", "price": 1.0850, "index": 10, "details": {}},
        {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0840, "index": 11, "details": {"gap_top": 1.0845, "gap_bottom": 1.0835}},
        {"concept": "order_block", "direction": "bullish", "price": 1.0825, "index": 9, "details": {"ob_top": 1.0830, "ob_bottom": 1.0820, "mitigation_count": 0}},
        {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0820, "index": 12, "details": {"type": "buy_side_liquidity_grab"}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0840, "index": 13, "details": {"current_zone": "discount"}},
    ]

    validator = SignalValidator(
        min_risk_reward=settings.validator.min_risk_reward,
        max_spread=settings.validator.max_spread,
    )
    signal = make_signal("EURUSD", Direction.BUY)
    result = validator.validate(signal, candles, smc_data, htf_trend="bullish", spread=3)
    print(f"\nSignalValidator : is_valid={result.is_valid}, score={result.score:.2f}")
    print(f"  failed_conditions = {result.failed_conditions}")

    decision_engine = DecisionEngine(settings.decision)
    decision = decision_engine.decide(
        signal, candles, smc_data,
        htf_trends={"H1": "bullish"},
        spread=3,
    )
    print(f"\nDecisionEngine : approved={decision.approved}, score={decision.score}, tier={decision.tier}")
    print(f"  rejected_by = {decision.rejected_by}")

    risk = RiskManager(settings.risk)
    account = TradingAccount(
        login=12345, server="Demo", balance=Decimal("10000"),
        equity=Decimal("10000"), margin=Decimal("0"), free_margin=Decimal("10000"),
        leverage=100, mode=TradingMode.PAPER, is_connected=True,
    )
    can_open = await risk.can_open_trade("EURUSD")
    is_valid = await risk.validate_signal(signal, account)
    print(f"\nRiskManager : can_open={can_open}, validate={is_valid}")

    print("\n" + "=" * 60)
    print("Résumé : filtres assoupliss")
    print("  - minimum_score: 80 -> 70")
    print("  - minimum_risk_reward: 2.0 -> 1.5 (EURUSD), 2.5 -> 2.0 (XAUUSD)")
    print("  - SignalSettings.min_confidence: 0.85 -> 0.75")
    print("  - MarketStructureSettings.max_structure_age: 20 -> 40")
    print("  - DecisionSettings.enable_kill_zone: True -> False")
    print("  - debug_calibration_mode flag disponible")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
