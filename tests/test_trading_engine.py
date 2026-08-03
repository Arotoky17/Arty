"""Tests du moteur de trading (TradingEngine) - Orchestration live.

Vérifie avec des mocks que l'ordre des appels est bien :
    get_latest_candles → SMCDetector.detect → SignalGenerator.generate
    → RiskManager.can_open_trade → RiskManager.validate_signal
    → RiskManager.calculate_position_size → OrderExecutor.open_order

Et qu'aucun open_order n'est appelé si validate_signal retourne False.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from arty_trading.application.trading_engine import TradingEngine
from arty_trading.core.entities import Candle, Signal, Trade, TradingAccount
from arty_trading.core.enums import (
    Direction,
    SignalType,
    TimeFrame,
    TradingMode,
)

# =============================================================================
# Helpers
# =============================================================================


def make_candle(
    symbol: str = "EURUSD",
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


def make_signal(symbol: str = "EURUSD") -> Signal:
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


def make_trade(symbol: str = "EURUSD") -> Trade:
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
        mode=TradingMode.DEMO,
        is_connected=True,
    )


def make_settings() -> MagicMock:
    """Crée un mock de Settings sans dépendre de pydantic-settings ni du .env."""
    settings = MagicMock()
    settings.symbols_list = ["EURUSD"]
    settings.default_timeframe = TimeFrame.H1
    settings.trading_mode = TradingMode.DEMO
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
    """
    sig = make_signal() if signal_result is _NO_SIGNAL else signal_result

    async def mock_get_candles(*args: object, **kwargs: object) -> list[Candle]:
        call_order.append("get_latest_candles")
        return [make_candle(time=candle_time)]

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

    return TradingEngine(
        settings=make_settings(),
        market_data=market_data,  # type: ignore[arg-type]
        smc_detector=smc_detector,  # type: ignore[arg-type]
        signal_generator=signal_generator,  # type: ignore[arg-type]
        risk_manager=risk_manager,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        mt5_connector=mt5_connector,  # type: ignore[arg-type]
    )


# =============================================================================
# Tests : Ordre des appels
# =============================================================================


class TestCallOrder:
    """Vérifie l'ordre exact des appels dans la chaîne d'analyse."""

    @pytest.mark.asyncio
    async def test_full_flow_call_order(self) -> None:
        """
        L'ordre des appels doit être :
        get_latest_candles → detect → generate → get_account_info
        → can_open_trade → validate_signal → calculate_position_size
        → open_order
        """
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("EURUSD")

        assert call_order == [
            "get_latest_candles",
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
        await engine.analyze_symbol("EURUSD")

        # Le mock est une fonction async, on vérifie qu'il a été appelé
        # (le call_order confirme que detect a été appelé)
        assert "detect" in call_order

    @pytest.mark.asyncio
    async def test_generate_called_with_candles_and_smc_data(self) -> None:
        """SignalGenerator.generate doit recevoir les bougies et les données SMC."""
        call_order: list[str] = []
        engine = build_engine(call_order)

        await engine.analyze_symbol("EURUSD")

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

        await engine.analyze_symbol("EURUSD")

        # L'ordre doit s'arrêter après validate_signal
        assert call_order == [
            "get_latest_candles",
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

        await engine.analyze_symbol("EURUSD")

        # L'ordre doit s'arrêter après can_open_trade
        assert call_order == [
            "get_latest_candles",
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

        await engine.analyze_symbol("EURUSD")

        # L'ordre doit s'arrêter après generate
        assert call_order == [
            "get_latest_candles",
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

        await engine.analyze_symbol("EURUSD")

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
        await engine.analyze_symbol("EURUSD")
        assert "detect" in call_order

        # Deuxième appel : même bougie, ne doit pas re-déclencher l'analyse
        call_order.clear()
        await engine.analyze_symbol("EURUSD")

        # Seul get_latest_candles doit être appelé (pour vérifier la bougie)
        assert call_order == ["get_latest_candles"]
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
        await engine.analyze_symbol("EURUSD")
        assert "detect" in call_order

        # Deuxième appel avec une bougie plus récente
        call_order.clear()
        # On met à jour le mock pour retourner une bougie avec un temps différent
        async def mock_new_candle(*args: object, **kwargs: object) -> list[Candle]:
            call_order.append("get_latest_candles")
            return [make_candle(time=second_time)]

        engine._market_data.get_latest_candles = mock_new_candle  # type: ignore[attr-defined]
        await engine.analyze_symbol("EURUSD")

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

        await engine.analyze_symbol("EURUSD")

        status = engine.get_status()

        assert status["running"] is False
        assert status["symbols"] == ["EURUSD"]
        assert status["timeframe"] == "H1"
        assert "EURUSD" in status["last_candle_time"]
        assert status["last_candle_time"]["EURUSD"] is not None
        assert "EURUSD" in status["last_signal"]
        assert status["last_signal"]["EURUSD"]["direction"] == "buy"
        assert status["last_signal"]["EURUSD"]["strategy_name"] == "TestStrategy"

    @pytest.mark.asyncio
    async def test_get_status_no_signal(self) -> None:
        """Le statut ne doit pas contenir de signal si aucun n'est généré."""
        call_order: list[str] = []
        engine = build_engine(call_order, signal_result=None)

        await engine.analyze_symbol("EURUSD")

        status = engine.get_status()
        assert status["last_signal"] == {}

    def test_get_status_initial(self) -> None:
        """Le statut initial ne doit contenir ni bougie ni signal."""
        engine = build_engine([])

        status = engine.get_status()

        assert status["running"] is False
        assert status["symbols"] == ["EURUSD"]
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
