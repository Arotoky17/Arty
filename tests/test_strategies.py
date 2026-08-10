"""Tests des stratégies et du générateur de signaux."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.strategies import (
    BreakoutStrategy,
    MomentumStrategy,
    ReversalStrategy,
    ScalpingStrategy,
    SMCTrendStrategy,
    SwingStrategy,
)


def make_candle(idx, o, h, l, c, volume=100, spread=3):
    return Candle(
        symbol="EURUSD",
        timeframe=TimeFrame.H1,
        time=datetime(2024, 1, 1, idx, 0, tzinfo=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
        spread=spread,
    )


def make_uptrend_candles(n=20):
    candles = []
    base = 1.0800
    i = 0
    while i < n:
        for _ in range(3):
            if i >= n:
                break
            o = base + i * 0.0010
            c = base + (i + 1) * 0.0010
            h = c + 0.0008
            l = o - 0.0002
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0015
        h = float(o) + 0.0003
        l = c - 0.0003
        candles.append(make_candle(i, o, h, l, c))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0010
        h = float(o) + 0.0002
        l = c - 0.0005
        candles.append(make_candle(i, o, h, l, c))
        i += 1
    return candles


def make_bullish_smc_data():
    return [
        {"concept": "break_of_structure", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 6, "details": {}},
        {"concept": "order_block", "direction": "bullish", "price": 1.0795, "index": 4, "details": {}},
        {"concept": "optimal_trade_entry", "direction": "bullish", "price": 1.0815, "index": 8, "details": {}},
        {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0790, "index": 7, "details": {}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0810, "index": 8, "details": {"current_zone": "discount"}},
    ]


def make_bearish_smc_data():
    return [
        {"concept": "break_of_structure", "direction": "bearish", "price": 1.0790, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bearish", "price": 1.0800, "index": 6, "details": {}},
        {"concept": "order_block", "direction": "bearish", "price": 1.0815, "index": 4, "details": {}},
        {"concept": "optimal_trade_entry", "direction": "bearish", "price": 1.0795, "index": 8, "details": {}},
        {"concept": "liquidity_sweep", "direction": "bearish", "price": 1.0820, "index": 7, "details": {}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0800, "index": 8, "details": {"current_zone": "premium"}},
    ]


def make_reversal_smc_data():
    return [
        {"concept": "change_of_character", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
        {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0790, "index": 4, "details": {}},
        {"concept": "market_structure_shift", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
        {"concept": "order_block", "direction": "bullish", "price": 1.0795, "index": 3, "details": {}},
    ]


class TestSMCTrendStrategy:
    def test_initialization(self):
        s = SMCTrendStrategy()
        assert s.name == "SMC Trend Following"
        assert s.enabled is True

    @pytest.mark.asyncio
    async def test_bullish_signal(self):
        signal = await SMCTrendStrategy(confidence_min=0.1).analyze(make_uptrend_candles(20), make_bullish_smc_data())
        assert signal is not None
        assert signal.signal_type == SignalType.BUY
        assert signal.direction == Direction.BUY

    @pytest.mark.asyncio
    async def test_bearish_signal(self):
        signal = await SMCTrendStrategy(confidence_min=0.1).analyze(make_uptrend_candles(20), make_bearish_smc_data())
        assert signal is not None
        assert signal.signal_type == SignalType.SELL

    @pytest.mark.asyncio
    async def test_no_signal_without_bos(self):
        assert await SMCTrendStrategy().analyze(make_uptrend_candles(20), []) is None

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await SMCTrendStrategy(enabled=False).analyze(make_uptrend_candles(20), make_bullish_smc_data()) is None

    @pytest.mark.asyncio
    async def test_empty_candles(self):
        assert await SMCTrendStrategy().analyze([], make_bullish_smc_data()) is None


class TestBreakoutStrategy:
    def test_initialization(self):
        assert BreakoutStrategy().name == "Breakout"

    @pytest.mark.asyncio
    async def test_bullish_breakout(self):
        candles = make_uptrend_candles(20)
        candles[-1] = make_candle(len(candles) - 1, float(candles[-1].open), float(candles[-1].high), float(candles[-1].low), float(candles[-1].close), volume=500)
        signal = await BreakoutStrategy().analyze(candles, make_bullish_smc_data())
        assert signal is not None
        assert signal.signal_type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await BreakoutStrategy(enabled=False).analyze(make_uptrend_candles(20), make_bullish_smc_data()) is None


class TestMomentumStrategy:
    def test_initialization(self):
        assert MomentumStrategy().name == "Momentum"

    @pytest.mark.asyncio
    async def test_bullish_momentum(self):
        candles = [make_candle(i, 1.08 + i * 0.001, 1.081 + i * 0.001, 1.079 + i * 0.001, 1.0808 + i * 0.001) for i in range(10)]
        smc_data = [{"concept": "fair_value_gap", "direction": "bullish", "price": 1.0820, "index": 3, "details": {}}]
        signal = await MomentumStrategy().analyze(candles, smc_data)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await MomentumStrategy(enabled=False).analyze(make_uptrend_candles(20), []) is None


class TestReversalStrategy:
    def test_initialization(self):
        assert ReversalStrategy().name == "Reversal"

    @pytest.mark.asyncio
    async def test_bullish_reversal(self):
        signal = await ReversalStrategy().analyze(make_uptrend_candles(20), make_reversal_smc_data())
        assert signal is not None
        assert signal.signal_type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_no_signal_without_choch(self):
        assert await ReversalStrategy().analyze(make_uptrend_candles(20), []) is None

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await ReversalStrategy(enabled=False).analyze(make_uptrend_candles(20), make_reversal_smc_data()) is None


class TestScalpingStrategy:
    def test_initialization(self):
        assert ScalpingStrategy().name == "Scalping"

    @pytest.mark.asyncio
    async def test_bullish_scalp(self):
        smc_data = [{"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 5, "details": {}}]
        signal = await ScalpingStrategy().analyze(make_uptrend_candles(10), smc_data)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await ScalpingStrategy(enabled=False).analyze(make_uptrend_candles(10), []) is None


class TestSwingStrategy:
    def test_initialization(self):
        assert SwingStrategy().name == "Swing Trading"

    @pytest.mark.asyncio
    async def test_bullish_swing(self):
        signal = await SwingStrategy(confidence_min=0.1).analyze(make_uptrend_candles(20), make_bullish_smc_data())
        assert signal is not None
        assert signal.signal_type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await SwingStrategy(enabled=False).analyze(make_uptrend_candles(20), make_bullish_smc_data()) is None


class TestSignalGenerator:
    def test_initialization(self):
        gen = SignalGenerator()
        assert len(gen.strategies) == 6
        assert "SMC Trend Following" in gen.strategies

    def test_default_min_confidence_is_085(self):
        gen = SignalGenerator()
        assert gen.min_confidence == 0.85

    def test_default_active_strategy_is_smc_trend(self):
        gen = SignalGenerator()
        assert gen.active_strategy == "SMC Trend Following"

    def test_only_smc_trend_enabled_by_default(self):
        """Pendant le développement, seule SMC Trend Following est activée."""
        gen = SignalGenerator()
        enabled = gen.get_enabled_strategies()
        assert enabled == ["SMC Trend Following"]
        # Les autres stratégies sont désactivées mais toujours présentes
        assert not gen.strategies["Breakout"].enabled
        assert not gen.strategies["Momentum"].enabled
        assert not gen.strategies["Reversal"].enabled
        assert not gen.strategies["Scalping"].enabled
        assert not gen.strategies["Swing Trading"].enabled

    def test_strategies_not_deleted(self):
        """Les stratégies désactivées ne sont pas supprimées."""
        gen = SignalGenerator()
        assert "Breakout" in gen.strategies
        assert "Momentum" in gen.strategies
        assert "Reversal" in gen.strategies
        assert "Scalping" in gen.strategies
        assert "Swing Trading" in gen.strategies

    def test_enable_disable(self):
        gen = SignalGenerator()
        gen.disable_strategy("SMC Trend Following")
        assert not gen.strategies["SMC Trend Following"].enabled
        gen.enable_strategy("SMC Trend Following")
        assert gen.strategies["SMC Trend Following"].enabled

    def test_enable_disable_all(self):
        gen = SignalGenerator()
        gen.disable_all()
        assert len(gen.get_enabled_strategies()) == 0
        gen.enable_all()
        # enable_all active toutes les stratégies, mais le garde-fou
        # sur active_strategy empêche les signaux non-SMC Trend
        assert len(gen.get_enabled_strategies()) == 6

    def test_set_active_strategy(self):
        gen = SignalGenerator()
        gen.set_active_strategy("Breakout")
        assert gen.active_strategy == "Breakout"
        assert gen.get_enabled_strategies() == ["Breakout"]

    def test_set_active_strategy_unknown_ignored(self):
        gen = SignalGenerator()
        gen.set_active_strategy("Unknown")
        assert gen.active_strategy == "SMC Trend Following"

    @pytest.mark.asyncio
    async def test_generate_best(self):
        """Le signal généré provient uniquement de SMC Trend Following."""
        gen = SignalGenerator(min_confidence=0.1)
        signal = await gen.generate(make_uptrend_candles(20), make_bullish_smc_data())
        assert signal is not None
        assert isinstance(signal, Signal)
        assert signal.confidence >= 0.1
        assert signal.strategy_name == "SMC Trend Following"

    @pytest.mark.asyncio
    async def test_generate_none(self):
        gen = SignalGenerator(min_confidence=0.8)
        assert await gen.generate(make_uptrend_candles(20), []) is None

    @pytest.mark.asyncio
    async def test_generate_empty(self):
        assert await SignalGenerator().generate([], []) is None

    @pytest.mark.asyncio
    async def test_generate_all(self):
        gen = SignalGenerator(min_confidence=0.1)
        signals = await gen.generate_all(make_uptrend_candles(20), make_bullish_smc_data())
        assert isinstance(signals, list)
        for i in range(1, len(signals)):
            assert signals[i - 1].confidence >= signals[i].confidence
        # Tous les signaux proviennent de SMC Trend Following
        for s in signals:
            assert s.strategy_name == "SMC Trend Following"

    @pytest.mark.asyncio
    async def test_generate_disabled_all(self):
        gen = SignalGenerator(min_confidence=0.1)
        gen.disable_all()
        assert await gen.generate(make_uptrend_candles(20), make_bullish_smc_data()) is None

    @pytest.mark.asyncio
    async def test_never_returns_signal_from_other_strategy(self):
        """Le SignalGenerator ne doit jamais retourner un signal d'une autre stratégie,
        même si cette stratégie est activée manuellement."""
        gen = SignalGenerator(min_confidence=0.1)
        # Activer manuellement Breakout (qui produirait un signal avec ces données)
        gen.enable_strategy("Breakout")
        assert gen.strategies["Breakout"].enabled
        # Mais le garde-fou empêche ses signaux
        signal = await gen.generate(make_uptrend_candles(20), make_bullish_smc_data())
        if signal is not None:
            assert signal.strategy_name == "SMC Trend Following"
            assert signal.strategy_name != "Breakout"

    @pytest.mark.asyncio
    async def test_rejects_signal_below_min_confidence(self):
        """Un signal dont la confiance est inférieure au seuil est rejeté (NO_SIGNAL)."""
        # Données SMC avec seulement BOS + FVG (confiance = 3/7 ≈ 0.43)
        smc_data = [
            {"concept": "break_of_structure", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
            {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 6, "details": {}},
        ]
        # Seuil à 0.5 : 0.43 < 0.5 → signal rejeté
        gen = SignalGenerator(min_confidence=0.5)
        signal = await gen.generate(make_uptrend_candles(20), smc_data)
        assert signal is None

    @pytest.mark.asyncio
    async def test_accepts_signal_at_min_confidence(self):
        """Un signal dont la confiance est >= au seuil est accepté."""
        gen = SignalGenerator(min_confidence=0.1)
        signal = await gen.generate(make_uptrend_candles(20), make_bullish_smc_data())
        assert signal is not None
        assert signal.confidence >= 0.1

    @pytest.mark.asyncio
    async def test_enable_all_does_not_bypass_guard(self):
        """Activer toutes les stratégies ne contourne pas le garde-fou."""
        gen = SignalGenerator(min_confidence=0.1)
        gen.enable_all()
        signal = await gen.generate(make_uptrend_candles(20), make_bullish_smc_data())
        if signal is not None:
            assert signal.strategy_name == "SMC Trend Following"

    @pytest.mark.asyncio
    async def test_generate_all_only_returns_active_strategy_signals(self):
        """generate_all ne retourne que les signaux de la stratégie active."""
        gen = SignalGenerator(min_confidence=0.1)
        gen.enable_all()  # Active toutes les stratégies
        signals = await gen.generate_all(make_uptrend_candles(20), make_bullish_smc_data())
        for s in signals:
            assert s.strategy_name == "SMC Trend Following"

    @pytest.mark.asyncio
    async def test_custom_active_strategy(self):
        """On peut configurer une autre stratégie active."""
        gen = SignalGenerator(min_confidence=0.1, active_strategy="Breakout")
        assert gen.active_strategy == "Breakout"
        assert gen.get_enabled_strategies() == ["Breakout"]
        # Les données bullish déclenchent Breakout avec volume élevé
        candles = make_uptrend_candles(20)
        candles[-1] = make_candle(
            len(candles) - 1,
            float(candles[-1].open),
            float(candles[-1].high),
            float(candles[-1].low),
            float(candles[-1].close),
            volume=500,
        )
        signal = await gen.generate(candles, make_bullish_smc_data())
        if signal is not None:
            assert signal.strategy_name == "Breakout"
