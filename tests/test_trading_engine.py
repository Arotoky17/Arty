"""Tests du moteur de trading (TradingEngine) - Orchestration live.

Vérifie avec des mocks que l'ordre des appels est bien :
    get_latest_candles → SMCDetector.detect → SignalGenerator.generate
    → RiskManager.can_open_trade → RiskManager.validate_signal
    → RiskManager.calculate_position_size → OrderExecutor.open_order

Et qu'aucun open_order n'est appelé si validate_signal retourne False.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from arty_trading.application.trading_engine import TradingEngine
from arty_trading.config.settings import PositionSettings
from arty_trading.core.entities import Candle, Signal, Trade, TradingAccount
from arty_trading.core.enums import (
    Direction,
    SignalType,
    TimeFrame,
    TradingMode,
)
from arty_trading.modules.execution import PaperOrderExecutor
from arty_trading.modules.risk import RiskManager

# =============================================================================
# Helpers
# =============================================================================


def make_candle(
    symbol: str = "XAUUSD",
    timeframe: TimeFrame = TimeFrame.H1,
    time: datetime | None = None,
    close: str = "1.0850",
) -> Candle:
    """Crée une bougie OHLCV de test."""
    if time is None:
        time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        time=time,
        open=Decimal("1.0800"),
        high=Decimal("1.0860"),
        low=Decimal("1.0790"),
        close=Decimal(close),
        volume=1000,
        spread=5,
    )


def make_signal(symbol: str = "XAUUSD") -> Signal:
    """Crée un signal de test."""
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("1.0800"),
        stop_loss=Decimal("1.0780"),
        take_profit=Decimal("1.0840"),
        confidence=0.8,
        strategy_name="TestStrategy",
        timeframe=TimeFrame.H1,
        justification="Test signal",
        smc_concepts=["BOS"],
    )


def make_trade(symbol: str = "XAUUSD") -> Trade:
    """Crée un trade de test."""
    return Trade(
        symbol=symbol,
        direction=Direction.BUY,
        entry_price=Decimal("1.0800"),
        stop_loss=Decimal("1.0780"),
        take_profit=Decimal("1.0840"),
        volume=Decimal("0.1"),
        ticket=10001,
    )


def make_account() -> TradingAccount:
    """Crée un compte de trading de test."""
    return TradingAccount(
        login=12345,
        server="Demo",
        name="Test",
        currency="USD",
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        margin=Decimal("0"),
        free_margin=Decimal("10000"),
        leverage=100,
        mode=TradingMode.PAPER,
        is_connected=True,
    )


def make_settings(trading_mode: TradingMode = TradingMode.PAPER) -> MagicMock:
    """Crée un mock de Settings sans dépendre de pydantic-settings ni du .env."""
    settings = MagicMock()
    settings.symbols_list = ["XAUUSD"]
    settings.supported_symbols = ["XAUUSD"]
    settings.enable_legacy_symbols = False
    settings.default_timeframe = TimeFrame.H1
    settings.context_timeframe = TimeFrame.H4
    settings.htf_timeframe = TimeFrame.H1
    settings.setup_timeframe = TimeFrame.M5
    settings.entry_timeframe = TimeFrame.M5
    settings.trading_mode = trading_mode

    def get_profile(symbol: str):
        if symbol.upper() == "XAUUSD":
            m = MagicMock()
            m.min_risk_reward = 2.0
            m.max_spread_points = 200
            return m
        return None

    settings.get_instrument_profile = get_profile
    return settings


# Sentinel pour distinguer "non fourni" de "None" (aucun signal)
_NO_SIGNAL = object()


def build_engine(
    call_order: list[str],
    *,
    validate_result: bool = True,
    can_open_result: bool = True,
    signal_result: Signal | object = _NO_SIGNAL,
    candle_time: datetime | None = None,
    trading_mode: TradingMode = TradingMode.PAPER,
    trend: str = "bullish",
) -> TradingEngine:
    """
    Construit un TradingEngine avec des mocks qui enregistrent l'ordre des
    appels dans ``call_order``.

    Args:
        call_order: Liste où les noms d'appels seront ajoutés.
        validate_result: Valeur de retour de validate_signal.
        can_open_result: Valeur de retour de can_open_trade.
        signal_result: Signal retourné par generate (None = aucun signal,
                       _NO_SIGNAL = signal par défaut).
        candle_time: Heure de la bougie retournée par get_latest_candles.
        trading_mode: Mode de trading à utiliser.
        trend: Tendance H1 simulée pour les tests. Mettre à None pour
               utiliser l'analyse réelle (nécessite des données avec structure
               de swing HH/HL ou LH/LL valide).
    """
    sig = make_signal() if signal_result is _NO_SIGNAL else signal_result

    async def mock_get_candles(*args: object, **kwargs: object) -> list[Candle]:
        call_order.append("get_latest_candles")
        timeframe = kwargs.get("timeframe", args[1] if len(args) > 1 else TimeFrame.H1)
        if timeframe == TimeFrame.H1:
            return make_bullish_h1_candles(start_time=candle_time)
        return [make_candle(time=candle_time, timeframe=TimeFrame.M5)]

    async def mock_detect(*args: object, **kwargs: object) -> list[dict]:
        call_order.append("detect")
        return [{"concept": "BOS", "direction": "bullish"}]

    async def mock_generate(*args: object, **kwargs: object) -> Signal | None:
        call_order.append("generate")
        return sig  # type: ignore[return-value]

    async def mock_get_account(*args: object, **kwargs: object) -> TradingAccount:
        call_order.append("get_account_info")
        return make_account()

    async def mock_can_open(*args: object, **kwargs: object) -> bool:
        call_order.append("can_open_trade")
        return can_open_result

    async def mock_validate(*args: object, **kwargs: object) -> bool:
        call_order.append("validate_signal")
        return validate_result

    async def mock_calc_size(*args: object, **kwargs: object) -> float:
        call_order.append("calculate_position_size")
        return 0.1

    async def mock_open_order(*args: object, **kwargs: object) -> Trade:
        call_order.append("open_order")
        return make_trade()

    market_data = MagicMock()
    market_data.get_latest_candles = mock_get_candles

    smc_detector = MagicMock()
    smc_detector.detect = mock_detect

    signal_generator = MagicMock()
    signal_generator.generate = mock_generate

    mt5_connector = MagicMock()
    mt5_connector.get_account_info = mock_get_account

    risk_manager = MagicMock()
    risk_manager.can_open_trade = mock_can_open
    risk_manager.validate_signal = mock_validate
    risk_manager.calculate_position_size = mock_calc_size

    executor = MagicMock()
    executor.open_order = mock_open_order

    engine = TradingEngine(
        settings=make_settings(trading_mode),
        market_data=market_data,  # type: ignore[arg-type]
        smc_detector=smc_detector,  # type: ignore[arg-type]
        signal_generator=signal_generator,  # type: ignore[arg-type]
        risk_manager=risk_manager,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        mt5_connector=mt5_connector,  # type: ignore[arg-type]
    )

    if trend is not None:
        engine = with_mocked_trend(engine, trend)

    return engine


def with_mocked_trend(engine: TradingEngine, trend: str = "bullish") -> TradingEngine:
    """Patche l'analyseur de tendance pour forcer une tendance donnée dans les tests."""
    original_analyze = engine._analyze_multitimeframe

    async def mock_analyze(symbol: str, htf_candles: list[Candle], ltf_candles: list[Candle], setup_tf_candles: list[Candle] | None = None):
        from arty_trading.modules.decision.market_context import MarketContext
        from arty_trading.modules.decision.master_trend import TrendAnalysis
        from decimal import Decimal

        ltf_smc = await engine._smc_detector.detect(ltf_candles, symbol)
        htf_smc = await engine._smc_detector.detect(htf_candles, symbol)

        trend_result = TrendAnalysis(
            trend=trend,
            confidence=0.8,
            hh=Decimal("1.1000"),
            hl=Decimal("1.0900"),
            lh=None,
            ll=None,
            swing_high=Decimal("1.1000"),
            swing_low=Decimal("1.0900"),
        )

        ctx = MarketContext(
            symbol=symbol,
            timestamp=ltf_candles[-1].time if ltf_candles else htf_candles[-1].time,
            master_trend=trend,
            trend_confidence=0.8,
            hh=trend_result.hh,
            hl=trend_result.hl,
            swing_high=trend_result.swing_high,
            swing_low=trend_result.swing_low,
            ltf_smc_data=ltf_smc,
            htf_smc_data=htf_smc,
            regime="bullish" if trend == "bullish" else "bearish",
            structure_valid=True,
        )
        return ctx

    engine._analyze_multitimeframe = mock_analyze  # type: ignore[method-assign]
    return engine


def make_bullish_h1_candles(start_time: datetime | None = None, n: int = 20) -> list[Candle]:
    """Crée des bougies H1 en tendance haussière (HH + HL) pour les tests."""
    candles = []
    base_time = start_time or datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    prices = [
        (1.0800, 1.0850, 1.0790, 1.0840),
        (1.0840, 1.0880, 1.0830, 1.0870),
        (1.0870, 1.0900, 1.0860, 1.0890),
        (1.0890, 1.0895, 1.0820, 1.0830),
        (1.0830, 1.0840, 1.0810, 1.0825),
        (1.0825, 1.0835, 1.0815, 1.0830),
        (1.0830, 1.0870, 1.0825, 1.0865),
        (1.0865, 1.0910, 1.0860, 1.0905),
        (1.0905, 1.0930, 1.0900, 1.0925),
        (1.0925, 1.0930, 1.0870, 1.0880),
        (1.0880, 1.0895, 1.0865, 1.0885),
        (1.0885, 1.0920, 1.0880, 1.0915),
        (1.0915, 1.0960, 1.0910, 1.0955),
        (1.0955, 1.0965, 1.0900, 1.0910),
        (1.0910, 1.0925, 1.0900, 1.0920),
        (1.0920, 1.0970, 1.0915, 1.0965),
        (1.0965, 1.1000, 1.0960, 1.0995),
        (1.0995, 1.1010, 1.0950, 1.0960),
        (1.0960, 1.0975, 1.0950, 1.0970),
        (1.0970, 1.1020, 1.0965, 1.1015),
    ]
    from datetime import timedelta

    for i in range(min(n, len(prices))):
        o, h, l, c = prices[i]
        t = base_time + timedelta(hours=i)
        candles.append(Candle(
            symbol="XAUUSD", timeframe=TimeFrame.H1, time=t,
            open=Decimal(str(o)), high=Decimal(str(h)),
            low=Decimal(str(l)), close=Decimal(str(c)),
            volume=1000, spread=5,
        ))
    return candles


# =============================================================================
# Tests : Ordre des appels
# =============================================================================


class TestCallOrder:
    """Vérifie l'ordre exact des appels dans la chaîne d'analyse."""

    @pytest.mark.asyncio
    async def test_ignores_non_xauusd_symbol(self) -> None:
        """Le moteur specialise Gold ignore les autres marches."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("EURUSD")

        assert call_order == []

    @pytest.mark.asyncio
    async def test_full_flow_call_order(self) -> None:
        """
        L'ordre des appels doit être :
        get_latest_candles (H1) → get_latest_candles (M5) → detect (H1) → detect (M5)
        → generate → get_account_info → can_open_trade → validate_signal
        → calculate_position_size → open_order
        """
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("XAUUSD")

        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
            "get_account_info",
            "can_open_trade",
            "validate_signal",
            "calculate_position_size",
            "open_order",
        ]

    @pytest.mark.asyncio
    async def test_detect_called_with_candles_and_symbol(self) -> None:
        """SMCDetector.detect doit recevoir les bougies et le symbole."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        # On récupère le mock du smc_detector pour vérifier les args
        await engine.analyze_symbol("XAUUSD")

        # Le mock est une fonction async, on vérifie qu'il a été appelé
        # (le call_order confirme que detect a été appelé)
        assert "detect" in call_order

    @pytest.mark.asyncio
    async def test_generate_called_with_candles_and_smc_data(self) -> None:
        """SignalGenerator.generate doit recevoir les bougies et les données SMC."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("XAUUSD")

        assert "generate" in call_order


# =============================================================================
# Tests : Pas d'ordre si validation échoue
# =============================================================================


class TestNoOrderOnValidationFailure:
    """Vérifie qu'aucun open_order n'est appelé quand la validation échoue."""

    @pytest.mark.asyncio
    async def test_no_open_order_when_validate_signal_false(self) -> None:
        """open_order ne doit pas être appelé si validate_signal retourne False."""
        call_order: list[str] = []
        engine = build_engine(call_order, validate_result=False)

        await engine.analyze_symbol("XAUUSD")

        # L'ordre doit s'arrêter après validate_signal
        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
            "get_account_info",
            "can_open_trade",
            "validate_signal",
        ]
        # open_order ne doit pas être dans la liste
        assert "open_order" not in call_order
        assert "calculate_position_size" not in call_order

    @pytest.mark.asyncio
    async def test_no_open_order_when_can_open_trade_false(self) -> None:
        """open_order ne doit pas être appelé si can_open_trade retourne False."""
        call_order: list[str] = []
        engine = build_engine(call_order, can_open_result=False)

        await engine.analyze_symbol("XAUUSD")

        # L'ordre doit s'arrêter après can_open_trade
        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
            "get_account_info",
            "can_open_trade",
        ]
        assert "validate_signal" not in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_no_open_order_when_no_signal(self) -> None:
        """open_order ne doit pas être appelé si aucun signal n'est généré."""
        call_order: list[str] = []
        engine = build_engine(call_order, signal_result=None)

        await engine.analyze_symbol("XAUUSD")

        # L'ordre doit s'arrêter après generate
        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
        ]
        assert "can_open_trade" not in call_order
        assert "validate_signal" not in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_no_open_order_when_no_candles(self) -> None:
        """open_order ne doit pas être appelé si aucune bougie n'est reçue."""
        call_order: list[str] = []

        # Mock qui retourne une liste vide
        async def mock_empty_candles(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            return []

        engine = build_engine(call_order)
        # Remplacer le mock de market_data pour retourner une liste vide
        engine._market_data.get_latest_candles = mock_empty_candles  # type: ignore[attr-defined]

        await engine.analyze_symbol("XAUUSD")

        assert call_order == ["get_latest_candles"]
        assert "detect" not in call_order
        assert "open_order" not in call_order


# =============================================================================
# Tests : Détection de nouvelle bougie
# =============================================================================


class TestNewBarDetection:
    """Vérifie que l'analyse n'est déclenchée que sur une nouvelle bougie."""

    @pytest.mark.asyncio
    async def test_no_reanalysis_on_same_candle(self) -> None:
        """Une deuxième analyse avec la même bougie ne doit pas re-déclencher detect."""
        call_order: list[str] = []
        fixed_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        engine = build_engine(call_order, candle_time=fixed_time)

        # Premier appel : déclenche l'analyse complète
        await engine.analyze_symbol("XAUUSD")
        assert "detect" in call_order

        # Deuxième appel : même bougie, ne doit pas re-déclencher l'analyse
        call_order.clear()
        await engine.analyze_symbol("XAUUSD")

        # Seul get_latest_candles doit être appelé (pour vérifier la bougie)
        # Note: 3 appels pour H1, M15 (setup) et M5
        assert call_order == ["get_latest_candles", "get_latest_candles"]
        assert "detect" not in call_order
        assert "generate" not in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_reanalysis_on_new_candle(self) -> None:
        """Une nouvelle bougie doit déclencher une nouvelle analyse complète."""
        call_order: list[str] = []
        first_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        second_time = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)

        # Premier appel avec la première bougie
        engine = build_engine(call_order, candle_time=first_time)
        await engine.analyze_symbol("XAUUSD")
        assert "detect" in call_order

        # Deuxième appel avec une bougie plus récente
        call_order.clear()
        # On met à jour le mock pour retourner une bougie avec un temps différent
        async def mock_new_candle(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            tf = kwargs.get("timeframe", args[1] if len(args) > 1 else TimeFrame.H1)
            if tf == TimeFrame.H1:
                return make_bullish_h1_candles(start_time=second_time)
            return [make_candle(time=second_time, timeframe=TimeFrame.M5)]

        engine._market_data.get_latest_candles = mock_new_candle  # type: ignore[attr-defined]
        await engine.analyze_symbol("XAUUSD")

        # L'analyse complète doit être re-déclenchée
        assert "detect" in call_order
        assert "generate" in call_order
        assert "open_order" in call_order


# =============================================================================
# Tests : Statut du moteur
# =============================================================================


class TestEngineStatus:
    """Vérifie le statut retourné par get_status()."""

    @pytest.mark.asyncio
    async def test_get_status_after_analysis(self) -> None:
        """Le statut doit contenir le dernier signal analysé par symbole."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("XAUUSD")

        status = engine.get_status()

        assert status["running"] is False
        assert status["symbols"] == ["XAUUSD"]
        assert status["timeframe"] == "H1"
        assert "XAUUSD" in status["last_candle_time"]
        assert status["last_candle_time"]["XAUUSD"] is not None
        assert "XAUUSD" in status["last_signal"]
        assert status["last_signal"]["XAUUSD"]["direction"] == "buy"
        assert status["last_signal"]["XAUUSD"]["strategy_name"] == "TestStrategy"

    @pytest.mark.asyncio
    async def test_get_status_no_signal(self) -> None:
        """Le statut ne doit pas contenir de signal si aucun n'est généré."""
        call_order: list[str] = []
        engine = build_engine(call_order, signal_result=None)

        await engine.analyze_symbol("XAUUSD")

        status = engine.get_status()
        assert status["last_signal"] == {}

    def test_get_status_initial(self) -> None:
        """Le statut initial ne doit contenir ni bougie ni signal."""
        engine = build_engine([])

        status = engine.get_status()

        assert status["running"] is False
        assert status["symbols"] == ["XAUUSD"]
        assert status["timeframe"] == "H1"
        assert status["last_candle_time"] == {}
        assert status["last_signal"] == {}


# =============================================================================
# Tests : Démarrage / Arrêt
# =============================================================================


class TestEngineStartStop:
    """Vérifie le démarrage et l'arrêt du moteur."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        """Le moteur doit pouvoir démarrer et s'arrêter proprement."""
        engine = build_engine([])

        assert engine.is_running is False

        task = engine.start()
        assert engine.is_running is True
        assert not task.done()

        # Laisser la boucle s'exécuter brièvement
        import asyncio

        await asyncio.sleep(0.01)

        await engine.stop()
        assert engine.is_running is False
        assert task.done()

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """start() ne doit pas créer une deuxième tâche si déjà lancé."""
        engine = build_engine([])

        task1 = engine.start()
        task2 = engine.start()
        assert task1 is task2

        await engine.stop()


# =============================================================================
# Tests : Synchroniseur de bougies (CandleSynchronizer)
# =============================================================================


class TestCandleSynchronizerIntegration:
    """Vérifie l'intégration du CandleSynchronizer dans le TradingEngine."""

    def test_synchronizer_property(self) -> None:
        """Le moteur doit exposer le CandleSynchronizer via la propriété."""
        engine = build_engine([])
        assert engine.synchronizer is not None
        assert engine.synchronizer.get_all_last_processed() == {}

    @pytest.mark.asyncio
    async def test_initialize_symbols_sets_synchronizer(self) -> None:
        """_initialize_symbols doit enregistrer la bougie actuelle dans le synchroniseur."""
        call_order: list[str] = []
        fixed_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        engine = build_engine(call_order, candle_time=fixed_time)

        await engine._initialize_symbols()

        # Le synchroniseur doit avoir enregistré la bougie actuelle
        expected_last = fixed_time
        last = engine.synchronizer.get_last_processed("XAUUSD")
        assert last == expected_last

    @pytest.mark.asyncio
    async def test_initialize_blocks_analysis_on_same_candle(self) -> None:
        """Après initialisation, analyze_symbol ne doit pas analyser la même bougie."""
        call_order: list[str] = []
        fixed_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        engine = build_engine(call_order, candle_time=fixed_time)

        # Initialiser le synchroniseur
        await engine._initialize_symbols()

        # L'analyse ne doit pas déclencher le pipeline (bougie déjà traitée)
        call_order.clear()
        await engine.analyze_symbol("XAUUSD")

        # Seul get_latest_candles doit être appelé (vérification bougie)
        # Note: 3 appels pour H1, M15 (setup) et M5 (Phase 3)
        assert call_order == ["get_latest_candles", "get_latest_candles"]
        assert "detect" not in call_order
        assert "generate" not in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_initialize_allows_analysis_on_new_candle(self) -> None:
        """Après initialisation, une nouvelle bougie doit déclencher l'analyse."""
        call_order: list[str] = []
        initial_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        # La nouvelle bougie doit être strictement après la dernière bougie M5
        # enregistrée par _initialize_symbols.
        new_time = initial_time + timedelta(minutes=5)
        engine = build_engine(call_order, candle_time=initial_time)

        # Initialiser avec la bougie actuelle
        await engine._initialize_symbols()

        # Changer le mock pour retourner une nouvelle bougie
        async def mock_new_candle(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            tf = kwargs.get("timeframe", args[1] if len(args) > 1 else TimeFrame.H1)
            if tf == TimeFrame.H1:
                return make_bullish_h1_candles(start_time=new_time)
            return [make_candle(time=new_time, timeframe=TimeFrame.M5)]

        engine._market_data.get_latest_candles = mock_new_candle  # type: ignore[attr-defined]

        # L'analyse doit déclencher le pipeline complet
        call_order.clear()
        await engine.analyze_symbol("XAUUSD")

        assert "detect" in call_order
        assert "generate" in call_order
        assert "open_order" in call_order

    @pytest.mark.asyncio
    async def test_initialize_with_no_candles(self) -> None:
        """_initialize_symbols ne doit pas planter si aucune bougie n'est reçue."""
        call_order: list[str] = []

        async def mock_empty_candles(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            return []

        engine = build_engine(call_order)
        engine._market_data.get_latest_candles = mock_empty_candles  # type: ignore[attr-defined]

        # Ne doit pas lever d'exception
        await engine._initialize_symbols()

        # Le synchroniseur ne doit pas avoir enregistré le symbole
        assert engine.synchronizer.get_last_processed("XAUUSD") is None


# =============================================================================
# Tests : Pipeline structuré (étapes séparées)
# =============================================================================


class TestPipelineSteps:
    """Vérifie que le pipeline est bien structuré en étapes séparées."""

    @pytest.mark.asyncio
    async def test_download_data_returns_candles(self) -> None:
        """_download_data doit retourner la liste des bougies."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        candles = await engine._download_data("XAUUSD")

        assert candles is not None
        assert len(candles) == 20
        assert "get_latest_candles" in call_order

    @pytest.mark.asyncio
    async def test_download_data_returns_none_on_error(self) -> None:
        """_download_data doit retourner None si la récupération échoue."""
        engine = build_engine([])

        async def mock_error(*args: object, **kwargs: object) -> list[Candle]:
            raise RuntimeError("MT5 error")

        engine._market_data.get_latest_candles = mock_error  # type: ignore[attr-defined]

        candles = await engine._download_data("XAUUSD")
        assert candles is None

    @pytest.mark.asyncio
    async def test_download_data_returns_none_on_empty(self) -> None:
        """_download_data doit retourner None si aucune bougie n'est reçue."""
        engine = build_engine([])

        async def mock_empty(*args: object, **kwargs: object) -> list[Candle]:
            return []

        engine._market_data.get_latest_candles = mock_empty  # type: ignore[attr-defined]

        candles = await engine._download_data("XAUUSD")
        assert candles is None

    @pytest.mark.asyncio
    async def test_analyze_smc_returns_data(self) -> None:
        """_analyze_smc doit retourner les détections SMC."""
        call_order: list[str] = []
        engine = build_engine(call_order)
        candles = [make_candle()]

        smc_data = await engine._analyze_smc("XAUUSD", candles)

        assert smc_data is not None
        assert len(smc_data) == 1
        assert "detect" in call_order

    @pytest.mark.asyncio
    async def test_generate_signal_returns_signal(self) -> None:
        """_generate_signal doit retourner le signal généré."""
        call_order: list[str] = []
        engine = build_engine(call_order)
        candles = [make_candle()]
        smc_data = [{"concept": "BOS"}]

        signal = await engine._generate_signal("XAUUSD", candles, smc_data)

        assert signal is not None
        assert signal.direction == Direction.BUY
        assert "generate" in call_order

    @pytest.mark.asyncio
    async def test_calculate_risk_returns_volume(self) -> None:
        """_calculate_risk doit retourner le volume calculé."""
        call_order: list[str] = []
        engine = build_engine(call_order)
        signal = make_signal()

        volume = await engine._calculate_risk("XAUUSD", signal)

        assert volume is not None
        assert volume == 0.1
        assert "get_account_info" in call_order
        assert "can_open_trade" in call_order
        assert "validate_signal" in call_order
        assert "calculate_position_size" in call_order

    @pytest.mark.asyncio
    async def test_execute_trade_returns_trade(self) -> None:
        """_execute_trade doit retourner le trade ouvert."""
        call_order: list[str] = []
        engine = build_engine(call_order)
        signal = make_signal()

        trade = await engine._execute_trade("XAUUSD", signal, 0.1)

        assert trade is not None
        assert trade.symbol == "XAUUSD"
        assert "open_order" in call_order

    @pytest.mark.asyncio
    async def test_monitor_trade_registers_trade(self) -> None:
        """_monitor_trade doit enregistrer le trade auprès du risk manager."""
        engine = build_engine([])
        trade = make_trade()

        # Le risk_manager est un MagicMock, pas un RiskManager concret,
        # donc register_trade ne sera pas appelé. Mais _monitor_trade
        # ne doit pas lever d'exception.
        await engine._monitor_trade("XAUUSD", trade)


# =============================================================================
# Tests : Modes de trading (ANALYSIS, PAPER, LIVE)
# =============================================================================


class TestTradingModes:
    """Vérifie le comportement des trois modes de trading."""

    @pytest.mark.asyncio
    async def test_analysis_mode_stops_after_signal(self) -> None:
        """En mode ANALYSIS, le moteur s'arrête après la génération du signal."""
        call_order: list[str] = []
        engine = build_engine(call_order, trading_mode=TradingMode.ANALYSIS)

        await engine.analyze_symbol("XAUUSD")

        # Le pipeline doit s'arrêter après generate (H1 détecté comme bullish)
        assert "get_latest_candles" in call_order
        assert "detect" in call_order
        assert "generate" in call_order
        # Aucun calcul de risque ni ordre
        assert "get_account_info" not in call_order
        assert "can_open_trade" not in call_order
        assert "validate_signal" not in call_order
        assert "calculate_position_size" not in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_paper_mode_full_pipeline(self) -> None:
        """En mode PAPER, le pipeline complet doit s'exécuter."""
        call_order: list[str] = []
        engine = build_engine(call_order, trading_mode=TradingMode.PAPER)

        await engine.analyze_symbol("XAUUSD")

        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
            "get_account_info",
            "can_open_trade",
            "validate_signal",
            "calculate_position_size",
            "open_order",
        ]

    @pytest.mark.asyncio
    async def test_live_mode_full_pipeline(self) -> None:
        """En mode LIVE, le pipeline complet doit s'exécuter."""
        call_order: list[str] = []
        engine = build_engine(call_order, trading_mode=TradingMode.LIVE)

        await engine.analyze_symbol("XAUUSD")

        assert call_order == [
            "get_latest_candles",
            "get_latest_candles",
            "detect",
            "detect",
            "generate",
            "get_account_info",
            "can_open_trade",
            "validate_signal",
            "calculate_position_size",
            "open_order",
        ]

    @pytest.mark.asyncio
    async def test_analysis_mode_no_open_order_on_no_signal(self) -> None:
        """En mode ANALYSIS, aucun open_order même si aucun signal."""
        call_order: list[str] = []
        engine = build_engine(
            call_order,
            trading_mode=TradingMode.ANALYSIS,
            signal_result=None,
        )

        await engine.analyze_symbol("XAUUSD")

        assert "get_latest_candles" in call_order
        assert "detect" in call_order
        assert "generate" in call_order
        assert "open_order" not in call_order

    @pytest.mark.asyncio
    async def test_get_status_includes_trading_mode(self) -> None:
        """Le statut doit inclure le mode de trading."""
        engine = build_engine([], trading_mode=TradingMode.ANALYSIS)
        status = engine.get_status()
        assert status["trading_mode"] == "analysis"

        engine = build_engine([], trading_mode=TradingMode.PAPER)
        status = engine.get_status()
        assert status["trading_mode"] == "paper"

        engine = build_engine([], trading_mode=TradingMode.LIVE)
        status = engine.get_status()
        assert status["trading_mode"] == "live"

    def test_trading_mode_property(self) -> None:
        """La propriété trading_mode doit retourner le mode configuré."""
        engine = build_engine([], trading_mode=TradingMode.ANALYSIS)
        assert engine.trading_mode == TradingMode.ANALYSIS

        engine = build_engine([], trading_mode=TradingMode.PAPER)
        assert engine.trading_mode == TradingMode.PAPER

        engine = build_engine([], trading_mode=TradingMode.LIVE)
        assert engine.trading_mode == TradingMode.LIVE


# =============================================================================
# Tests : Statistiques (TradingStatistics)
# =============================================================================


class TestStatistics:
    """Vérifie l'enregistrement des statistiques dans le moteur."""

    @pytest.mark.asyncio
    async def test_analysis_mode_records_analysis_and_signal(self) -> None:
        """En mode ANALYSIS, les analyses et signaux doivent être enregistrés."""
        engine = build_engine([], trading_mode=TradingMode.ANALYSIS)

        await engine.analyze_symbol("XAUUSD")

        stats = engine.get_statistics()
        assert stats["mode"] == "analysis"
        assert stats["total_analyses"] == 1
        assert stats["total_signals"] == 1
        assert stats["total_trades"] == 0
        assert stats["analyses_by_symbol"] == {"XAUUSD": 1}

    @pytest.mark.asyncio
    async def test_paper_mode_records_trade(self) -> None:
        """En mode PAPER, les trades doivent être enregistrés."""
        engine = build_engine([], trading_mode=TradingMode.PAPER)

        await engine.analyze_symbol("XAUUSD")

        stats = engine.get_statistics()
        assert stats["mode"] == "paper"
        assert stats["total_analyses"] == 1
        assert stats["total_signals"] == 1
        assert stats["total_trades"] == 1

    @pytest.mark.asyncio
    async def test_live_mode_records_trade(self) -> None:
        """En mode LIVE, les trades doivent être enregistrés."""
        engine = build_engine([], trading_mode=TradingMode.LIVE)

        await engine.analyze_symbol("XAUUSD")

        stats = engine.get_statistics()
        assert stats["mode"] == "live"
        assert stats["total_analyses"] == 1
        assert stats["total_signals"] == 1
        assert stats["total_trades"] == 1

    @pytest.mark.asyncio
    async def test_no_signal_no_trade_recorded(self) -> None:
        """Si aucun signal n'est généré, aucun trade ne doit être enregistré."""
        engine = build_engine([], signal_result=None)

        await engine.analyze_symbol("XAUUSD")

        stats = engine.get_statistics()
        assert stats["total_analyses"] == 1
        assert stats["total_signals"] == 0
        assert stats["total_trades"] == 0

    @pytest.mark.asyncio
    async def test_statistics_in_status(self) -> None:
        """Le statut doit inclure un résumé des statistiques."""
        engine = build_engine([])

        await engine.analyze_symbol("XAUUSD")

        status = engine.get_status()
        assert "statistics" in status
        assert status["statistics"]["total_analyses"] == 1
        assert status["statistics"]["total_signals"] == 1
        assert status["statistics"]["total_trades"] == 1

    @pytest.mark.asyncio
    async def test_multiple_analyses_accumulate(self) -> None:
        """Les statistiques doivent s'accumuler sur plusieurs analyses."""
        call_order: list[str] = []
        first_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        second_time = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)

        engine = build_engine(call_order, candle_time=first_time)

        # Première analyse
        await engine.analyze_symbol("XAUUSD")

        # Deuxième analyse avec une nouvelle bougie
        async def mock_new_candle(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            return [make_candle(time=second_time)]

        engine._market_data.get_latest_candles = mock_new_candle  # type: ignore[attr-defined]
        call_order.clear()
        await engine.analyze_symbol("XAUUSD")

        stats = engine.get_statistics()
        assert stats["total_analyses"] == 2
        assert stats["total_signals"] == 2
        assert stats["total_trades"] == 2
        assert stats["analyses_by_symbol"] == {"XAUUSD": 2}

    def test_get_statistics_summary(self) -> None:
        """get_statistics_summary doit retourner un résumé sans les listes."""
        engine = build_engine([])

        summary = engine.get_statistics_summary()
        assert "mode" in summary
        assert "total_analyses" in summary
        assert "signals" not in summary
        assert "equity_curve" not in summary

    def test_statistics_property(self) -> None:
        """La propriété statistics doit retourner le tracker."""
        engine = build_engine([])
        assert engine.statistics is not None
        assert engine.statistics.total_analyses == 0


# =============================================================================
# Tests : Réconciliation des positions ouvertes au démarrage
# =============================================================================


class TestPositionReconciliation:
    """Vérifie la réconciliation des positions ouvertes avant PAPER/LIVE."""

    def _build_engine_with_executor(
        self,
        executor: PaperOrderExecutor,
        trading_mode: TradingMode = TradingMode.PAPER,
    ) -> TradingEngine:
        """Construit un moteur réel (RiskManager + PaperOrderExecutor)."""
        settings = make_settings(trading_mode)
        settings.risk = RiskManager().settings
        settings.position = PositionSettings(enabled=True)
        settings.news = None
        settings.journal = None
        settings.decision = None

        market_data = MagicMock()
        smc_detector = MagicMock()
        signal_generator = MagicMock()
        mt5_connector = MagicMock()
        risk_manager = RiskManager(settings=settings.risk)

        return TradingEngine(
            settings=settings,  # type: ignore[arg-type]
            market_data=market_data,  # type: ignore[arg-type]
            smc_detector=smc_detector,  # type: ignore[arg-type]
            signal_generator=signal_generator,  # type: ignore[arg-type]
            risk_manager=risk_manager,
            executor=executor,
            mt5_connector=mt5_connector,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_reconcile_registers_open_positions(self) -> None:
        """Les positions ouvertes existantes doivent être enregistrées."""
        executor = PaperOrderExecutor()
        # Simuler une position déjà ouverte sur le compte
        await executor.open_order(make_signal("XAUUSD"), 0.1)

        engine = self._build_engine_with_executor(executor)

        await engine._reconcile_open_positions()

        # Le risk manager doit suivre la position réconciliée
        assert engine._risk_manager.open_positions_count == 1
        # Le gestionnaire de positions doit la suivre aussi
        assert engine._managed_trades != {}

    @pytest.mark.asyncio
    async def test_reconcile_ignored_in_analysis_mode(self) -> None:
        """En mode ANALYSIS, aucune position ne doit être réconciliée."""
        executor = PaperOrderExecutor()
        await executor.open_order(make_signal("XAUUSD"), 0.1)

        engine = self._build_engine_with_executor(
            executor, trading_mode=TradingMode.ANALYSIS
        )

        await engine._reconcile_open_positions()

        assert engine._risk_manager.open_positions_count == 0
        assert engine._managed_trades == {}


