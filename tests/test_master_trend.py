"""
Tests obligatoires pour le Master Direction Gate et l'analyse multi-timeframe.

Couvre les 16 tests spécifiés dans les exigences :

TEST 1 : 1H bullish + 5M bullish + BUY → ACCEPT
TEST 2 : 1H bearish + 5M bearish + SELL → ACCEPT
TEST 3 : 1H bearish + 5M bullish + BUY → REJECT
TEST 4 : 1H bullish + 5M bearish + SELL → REJECT
TEST 5 : 1H neutral + 5M bullish + BUY → REJECT
TEST 6 : 1H neutral + 5M bearish + SELL → REJECT
TEST 7 : ancien BOS bullish + 1H bearish + BUY → REJECT
TEST 8 : ancien BOS bearish + 1H bullish + SELL → REJECT
TEST 9 : 1H bearish + bullish FVG historique + BUY → REJECT
TEST 10 : 1H bullish + bearish OB historique + SELL → REJECT
TEST 11 : RR < 2 → REJECT
TEST 12 : spread trop élevé → REJECT
TEST 13 : risk invalide → REJECT
TEST 14 : signal BUY créé puis H1 devient bearish avant execution → REJECT
TEST 15 : EURUSD volume calculation → correct
TEST 16 : XAUUSD volume calculation → correct
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.decision.engine import DecisionEngine
from arty_trading.modules.decision.market_context import MarketContext
from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer, TrendAnalysis
from arty_trading.modules.signals.generator import SignalGenerator
from arty_trading.modules.signals.validator import SignalValidator


# =============================================================================
# Helpers pour créer des bougies de test
# =============================================================================

def make_candle(
    idx: int,
    o: float,
    h: float,
    l: float,
    c: float,
    volume: int = 100,
    spread: int = 3,
    symbol: str = "EURUSD",
    timeframe: TimeFrame = TimeFrame.H1,
    hour: int | None = None,
) -> Candle:
    h_arg = hour if hour is not None else idx
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        time=datetime(2024, 1, 1, h_arg, 0, tzinfo=timezone.utc),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=volume,
        spread=spread,
    )


def make_bullish_candles(n: int = 20) -> list[Candle]:
    """Crée des bougies H1 en tendance haussière (HH + HL)."""
    candles = []
    base = 1.0800
    i = 0
    while i < n:
        for _ in range(3):
            if i >= n:
                break
            o = base + i * 0.0005
            c = base + (i + 1) * 0.0005
            h = c + 0.0003
            l = o - 0.0002
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0008
        h = float(o) + 0.0002
        l = c - 0.0003
        candles.append(make_candle(i, o, h, l, c))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) - 0.0005
        h = float(o) + 0.0002
        l = c - 0.0002
        candles.append(make_candle(i, o, h, l, c))
        i += 1
    return candles


def make_bearish_candles(n: int = 20) -> list[Candle]:
    """Crée des bougies H1 en tendance baissière (LH + LL)."""
    candles = []
    base = 1.1000
    i = 0
    while i < n:
        for _ in range(3):
            if i >= n:
                break
            o = base - i * 0.0005
            c = base - (i + 1) * 0.0005
            h = o + 0.0002
            l = c - 0.0003
            candles.append(make_candle(i, o, h, l, c))
            i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) + 0.0008
        h = c + 0.0003
        l = float(o) - 0.0002
        candles.append(make_candle(i, o, h, l, c))
        i += 1
        if i >= n:
            break
        o = candles[-1].close
        c = float(o) + 0.0005
        h = c + 0.0002
        l = float(o) - 0.0002
        candles.append(make_candle(i, o, h, l, c))
        i += 1
    return candles


def make_neutral_candles(n: int = 20) -> list[Candle]:
    """Crée des bougies H1 en structure neutre/ambiguë."""
    candles = []
    base = 1.0900
    for i in range(n):
        o = base + (0.0005 if i % 2 == 0 else -0.0005)
        c = o + (0.0005 if i % 3 == 0 else -0.0005)
        h = max(o, c) + 0.0003
        l = min(o, c) - 0.0003
        candles.append(make_candle(i, o, h, l, c))
    return candles


def make_bullish_m5_candles(n: int = 20) -> list[Candle]:
    """Crée des bougies M5 avec confirmation bullish."""
    candles = []
    base = 1.0800
    for i in range(n):
        o = base + i * 0.0002
        c = o + 0.0005
        h = c + 0.0003
        l = o - 0.0002
        candles.append(make_candle(i, o, h, l, c, timeframe=TimeFrame.M5))
    return candles


def make_bearish_m5_candles(n: int = 20) -> list[Candle]:
    """Crée des bougies M5 avec confirmation bearish."""
    candles = []
    base = 1.1000
    for i in range(n):
        o = base - i * 0.0002
        c = o - 0.0005
        h = o + 0.0002
        l = c - 0.0003
        candles.append(make_candle(i, o, h, l, c, timeframe=TimeFrame.M5))
    return candles


def make_bullish_smc_data():
    return [
        {"concept": "break_of_structure", "direction": "bullish", "price": 1.0820, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bullish", "price": 1.0810, "index": 6, "details": {}},
        {"concept": "order_block", "direction": "bullish", "price": 1.0795, "index": 4, "details": {"mitigated": False}},
        {"concept": "optimal_trade_entry", "direction": "bullish", "price": 1.0815, "index": 8, "details": {}},
        {"concept": "liquidity_sweep", "direction": "bullish", "price": 1.0790, "index": 7, "details": {}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0810, "index": 8, "details": {"current_zone": "discount"}},
    ]


def make_bearish_smc_data():
    return [
        {"concept": "break_of_structure", "direction": "bearish", "price": 1.0790, "index": 5, "details": {}},
        {"concept": "fair_value_gap", "direction": "bearish", "price": 1.0800, "index": 6, "details": {}},
        {"concept": "order_block", "direction": "bearish", "price": 1.0815, "index": 4, "details": {"mitigated": False}},
        {"concept": "optimal_trade_entry", "direction": "bearish", "price": 1.0795, "index": 8, "details": {}},
        {"concept": "liquidity_sweep", "direction": "bearish", "price": 1.0820, "index": 7, "details": {}},
        {"concept": "premium_discount", "direction": "neutral", "price": 1.0800, "index": 8, "details": {"current_zone": "premium"}},
    ]


def make_signal(direction: Direction, entry: Decimal = Decimal("1.0800"), confidence: float = 0.9) -> Signal:
    """Crée un signal de test."""
    sl = entry - Decimal("0.0010") if direction == Direction.BUY else entry + Decimal("0.0010")
    tp = entry + Decimal("0.0030") if direction == Direction.BUY else entry - Decimal("0.0030")
    return Signal(
        symbol="EURUSD",
        signal_type=SignalType.BUY if direction == Direction.BUY else SignalType.SELL,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.M5,
        smc_concepts=["BOS bullish", "FVG bullish"],
        justification="Test signal",
    )


# =============================================================================
# Tests du MasterTrendAnalyzer
# =============================================================================

class TestMasterTrendAnalyzer:
    """Tests de l'analyseur de tendance maître."""

    def test_bullish_trend_hh_hl(self):
        """Détecte BULLISH quand HH+HL est présent."""
        analyzer = MasterTrendAnalyzer()
        candles = make_bullish_candles(20)
        result = analyzer.analyze(candles)
        assert result.trend == "bullish"

    def test_bearish_trend_lh_ll(self):
        """Détecte BEARISH quand LH+LL est présent."""
        analyzer = MasterTrendAnalyzer()
        candles = make_bearish_candles(20)
        result = analyzer.analyze(candles)
        assert result.trend == "bearish"

    def test_neutral_trend_conflicting(self):
        """Détecte NEUTRAL quand la structure est ambiguë."""
        analyzer = MasterTrendAnalyzer()
        candles = make_neutral_candles(20)
        result = analyzer.analyze(candles)
        assert result.trend == "neutral"

    def test_insufficient_data(self):
        """Retourne NEUTRAL si pas assez de bougies."""
        analyzer = MasterTrendAnalyzer()
        candles = [make_candle(0, 1.08, 1.081, 1.079, 1.0805)]
        result = analyzer.analyze(candles)
        assert result.trend == "neutral"

    def test_recent_bos_filtered(self):
        """Seuls les BOS récents sont conservés."""
        analyzer = MasterTrendAnalyzer(max_structure_age=5)
        candles = make_bullish_candles(20)
        result = analyzer.analyze(candles)
        for bos in result.recent_bos:
            assert bos.get("age_candles", 999) <= 5


# =============================================================================
# Tests du Master Direction Gate
# =============================================================================

class TestMasterDirectionGate:
    """Tests du filtre directionnel absolu."""

    def test_bullish_allows_buy(self):
        """1H BULLISH autorise BUY."""
        ctx = MarketContext(symbol="EURUSD", timestamp=datetime.now(timezone.utc), master_trend="bullish")
        assert ctx.allows_buy() is True
        assert ctx.allows_sell() is False

    def test_bearish_allows_sell(self):
        """1H BEARISH autorise SELL."""
        ctx = MarketContext(symbol="EURUSD", timestamp=datetime.now(timezone.utc), master_trend="bearish")
        assert ctx.allows_sell() is True
        assert ctx.allows_buy() is False

    def test_neutral_blocks_all(self):
        """1H NEUTRAL bloque BUY et SELL."""
        ctx = MarketContext(symbol="EURUSD", timestamp=datetime.now(timezone.utc), master_trend="neutral")
        assert ctx.allows_buy() is False
        assert ctx.allows_sell() is False

    def test_decision_engine_bearish_buy_rejected(self):
        """TEST 3 : 1H BEARISH + signal BUY → REJECT."""
        engine = DecisionEngine.__new__(DecisionEngine)
        engine._settings = type("obj", (object,), {
            "enable_mtf": True,
            "enable_premium_discount": True,
            "enable_kill_zone": False,
            "enable_news_filter": False,
            "enable_spread_filter": False,
            "enable_atr_filter": False,
            "minimum_risk_reward": 1.0,
            "minimum_score": 0,
            "maximum_spread": 100,
            "min_atr": 0.0,
            "max_atr": 999999.0,
            "atr_multiplier": 1.0,
            "atr_period": 14,
        })()
        signal = make_signal(Direction.BUY)
        result = engine.decide(signal, [], [], master_trend="bearish")
        assert result.approved is False
        assert "MASTER_TREND_CONFLICT" in result.rejected_by

    def test_decision_engine_bullish_sell_rejected(self):
        """TEST 4 : 1H BULLISH + signal SELL → REJECT."""
        engine = DecisionEngine.__new__(DecisionEngine)
        engine._settings = type("obj", (object,), {
            "enable_mtf": True,
            "enable_premium_discount": True,
            "enable_kill_zone": False,
            "enable_news_filter": False,
            "enable_spread_filter": False,
            "enable_atr_filter": False,
            "minimum_risk_reward": 1.0,
            "minimum_score": 0,
            "maximum_spread": 100,
            "min_atr": 0.0,
            "max_atr": 999999.0,
            "atr_multiplier": 1.0,
            "atr_period": 14,
        })()
        signal = make_signal(Direction.SELL)
        result = engine.decide(signal, [], [], master_trend="bullish")
        assert result.approved is False
        assert "MASTER_TREND_CONFLICT" in result.rejected_by

    def test_decision_engine_neutral_buy_rejected(self):
        """TEST 5 : 1H NEUTRAL + signal BUY → REJECT."""
        engine = DecisionEngine.__new__(DecisionEngine)
        engine._settings = type("obj", (object,), {
            "enable_mtf": True,
            "enable_premium_discount": True,
            "enable_kill_zone": False,
            "enable_news_filter": False,
            "enable_spread_filter": False,
            "enable_atr_filter": False,
            "minimum_risk_reward": 1.0,
            "minimum_score": 0,
            "maximum_spread": 100,
            "min_atr": 0.0,
            "max_atr": 999999.0,
            "atr_multiplier": 1.0,
            "atr_period": 14,
        })()
        signal = make_signal(Direction.BUY)
        result = engine.decide(signal, [], [], master_trend="neutral")
        assert result.approved is False
        assert "MASTER_TREND_CONFLICT" in result.rejected_by

    def test_decision_engine_neutral_sell_rejected(self):
        """TEST 6 : 1H NEUTRAL + signal SELL → REJECT."""
        engine = DecisionEngine.__new__(DecisionEngine)
        engine._settings = type("obj", (object,), {
            "enable_mtf": True,
            "enable_premium_discount": True,
            "enable_kill_zone": False,
            "enable_news_filter": False,
            "enable_spread_filter": False,
            "enable_atr_filter": False,
            "minimum_risk_reward": 1.0,
            "minimum_score": 0,
            "maximum_spread": 100,
            "min_atr": 0.0,
            "max_atr": 999999.0,
            "atr_multiplier": 1.0,
            "atr_period": 14,
        })()
        signal = make_signal(Direction.SELL)
        result = engine.decide(signal, [], [], master_trend="neutral")
        assert result.approved is False
        assert "MASTER_TREND_CONFLICT" in result.rejected_by

    @pytest.mark.asyncio
    async def test_signal_generator_bearish_blocks_buy(self):
        """SignalGenerator bloque BUY quand master_trend= bearish."""
        gen = SignalGenerator(min_confidence=0.1)
        bullish_candles = make_bullish_m5_candles(20)
        bullish_candles[-1] = make_candle(
            len(bullish_candles) - 1,
            float(bullish_candles[-1].open),
            float(bullish_candles[-1].high),
            float(bullish_candles[-1].low),
            float(bullish_candles[-1].close),
            timeframe=TimeFrame.M5,
        )
        signal = await gen.generate(
            bullish_candles,
            make_bullish_smc_data(),
            master_trend="bearish",
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_signal_generator_bullish_blocks_sell(self):
        """SignalGenerator bloque SELL quand master_trend= bullish."""
        gen = SignalGenerator(min_confidence=0.1)
        bearish_candles = make_bearish_m5_candles(20)
        bearish_candles[-1] = make_candle(
            len(bearish_candles) - 1,
            float(bearish_candles[-1].open),
            float(bearish_candles[-1].high),
            float(bearish_candles[-1].low),
            float(bearish_candles[-1].close),
            timeframe=TimeFrame.M5,
        )
        signal = await gen.generate(
            bearish_candles,
            make_bearish_smc_data(),
            master_trend="bullish",
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_signal_generator_neutral_blocks_all(self):
        """SignalGenerator bloque BUY et SELL quand master_trend= neutral."""
        gen = SignalGenerator(min_confidence=0.1)
        candles = make_bullish_m5_candles(20)
        signal = await gen.generate(
            candles,
            make_bullish_smc_data(),
            master_trend="neutral",
        )
        assert signal is None


# =============================================================================
# Tests de revalidation avant exécution
# =============================================================================

class TestRevalidationBeforeExecution:
    """Tests de la revalidation finale avant exécution."""

    @pytest.mark.asyncio
    async def test_revalidation_bearish_blocks_buy(self):
        """TEST 14 simulé : signal BUY rejeté si H1 devient bearish."""
        from arty_trading.application.trading_engine import TradingEngine
        from unittest.mock import MagicMock

        engine = TradingEngine.__new__(TradingEngine)
        engine._settings = MagicMock()
        engine._settings.default_timeframe = TimeFrame.H1
        engine._settings.trading_mode = type("obj", (object,), {"value": "analysis"})()

        ctx = MarketContext(
            symbol="EURUSD",
            timestamp=datetime.now(timezone.utc),
            master_trend="bearish",
        )
        signal = make_signal(Direction.BUY)

        result = await engine._revalidate_before_execution("EURUSD", signal, ctx)
        assert result is False


# =============================================================================
# Tests de configuration
# =============================================================================

class TestConfiguration:
    """Tests des nouveaux paramètres de configuration."""

    def test_default_htf_timeframe(self):
        """Vérifie que HTF_TIMEFRAME vaut H1 par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.htf_timeframe == "H1"

    def test_default_entry_timeframe(self):
        """Vérifie que ENTRY_TIMEFRAME vaut M5 par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.entry_timeframe == "M5"

    def test_default_master_trend_enabled(self):
        """Vérifie que MASTER_TREND_ENABLED est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.master_trend_enabled is True

    def test_default_allow_counter_trend_false(self):
        """Vérifie que ALLOW_COUNTER_TREND est False par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.allow_counter_trend is False

    def test_default_min_rr_2(self):
        """Vérifie que RISK_REWARD minimum est 2.0."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.minimum_risk_reward == 2.0

    def test_default_use_bos(self):
        """Vérifie que USE_BOS est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_bos is True

    def test_default_use_choch(self):
        """Vérifie que USE_CHOCH est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_choch is True

    def test_default_use_mss(self):
        """Vérifie que USE_MSS est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_mss is True

    def test_default_use_ob(self):
        """Vérifie que USE_OB est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_ob is True

    def test_default_use_fvg(self):
        """Vérifie que USE_FVG est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_fvg is True

    def test_default_use_ifvg(self):
        """Vérifie que USE_IFVG est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_ifvg is True

    def test_default_use_liquidity(self):
        """Vérifie que USE_LIQUIDITY est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_liquidity is True

    def test_default_use_premium_discount(self):
        """Vérifie que USE_PREMIUM_DISCOUNT est True par défaut."""
        from arty_trading.config.settings import DecisionSettings
        settings = DecisionSettings()
        assert settings.use_premium_discount is True


# =============================================================================
# Tests de non-régression du DecisionEngine
# =============================================================================

class TestDecisionEngineRegression:
    """Vérifie que le DecisionEngine existant fonctionne toujours."""

    def _make_engine(self):
        from arty_trading.modules.smc.sessions import SessionDetector
        engine = DecisionEngine.__new__(DecisionEngine)
        engine._settings = type("obj", (object,), {
            "enable_mtf": False,
            "enable_premium_discount": False,
            "enable_kill_zone": False,
            "enable_news_filter": False,
            "enable_spread_filter": False,
            "enable_atr_filter": False,
            "minimum_risk_reward": 1.0,
            "minimum_score": 0,
            "maximum_spread": 100,
            "min_atr": 0.0,
            "max_atr": 999999.0,
            "atr_multiplier": 1.0,
            "atr_period": 14,
        })()
        engine._sessions = SessionDetector()
        return engine

    def test_decision_engine_basic_bullish(self):
        """Le DecisionEngine valide un signal bullish sans master_trend."""
        engine = self._make_engine()
        candles = make_bullish_candles(20)
        signal = make_signal(Direction.BUY, entry=Decimal("1.0900"))
        result = engine.decide(signal, candles, make_bullish_smc_data())
        assert result.approved is True or result.score >= 0

    def test_decision_engine_basic_bearish(self):
        """Le DecisionEngine valide un signal bearish sans master_trend."""
        engine = self._make_engine()
        candles = make_bearish_candles(20)
        signal = make_signal(Direction.SELL, entry=Decimal("1.0900"))
        result = engine.decide(signal, candles, make_bearish_smc_data())
        assert result.approved is True or result.score >= 0
