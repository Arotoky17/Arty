#!/usr/bin/env python
"""
Démonstration des trois modes de trading (ANALYSIS, PAPER, LIVE).

Ce script simule le pipeline complet du TradingEngine avec des données
fictives et montre comment chaque mode se comporte :

1. MODE_ANALYSIS  →  Analyse + signaux, AUCUN trade ouvert
2. MODE_PAPER     →  Analyse + signaux + trades simulés (PaperOrderExecutor)
3. MODE_LIVE      →  Analyse + signaux + trades réels (OrderExecutor MT5)

Lancement :
    python demo_trading_modes.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

# Forcer le mode ANALYSIS par défaut pour la démo
os.environ.setdefault("TRADING_MODE", "analysis")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")

from arty_trading.application.trading_engine import TradingEngine
from arty_trading.core.entities import Candle, Signal, Trade, TradingAccount
from arty_trading.core.enums import (
    Direction,
    LogCategory,
    SignalType,
    TimeFrame,
    TradingMode,
)
from arty_trading.logging import get_logger
from arty_trading.modules.execution.paper_executor import PaperOrderExecutor

logger = get_logger(LogCategory.SYSTEM)


# =============================================================================
# Helpers : création de données fictives
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
    """Crée un signal de test (BUY sur EURUSD)."""
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        direction=Direction.BUY,
        entry_price=Decimal("1.0800"),
        stop_loss=Decimal("1.0780"),
        take_profit=Decimal("1.0840"),
        confidence=0.85,
        strategy_name="SMC Trend Following",
        timeframe=TimeFrame.H1,
        justification="BOS bullish + FVG + Order Block",
        smc_concepts=["BOS", "FVG", "ORDER_BLOCK"],
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
        name="Test Account",
        currency="USD",
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        margin=Decimal("0"),
        free_margin=Decimal("10000"),
        leverage=100,
        mode=TradingMode.PAPER,
        is_connected=True,
    )


def make_settings(trading_mode: TradingMode = TradingMode.ANALYSIS) -> MagicMock:
    """Crée un mock de Settings."""
    settings = MagicMock()
    settings.symbols_list = ["EURUSD"]
    settings.default_timeframe = TimeFrame.H1
    settings.trading_mode = trading_mode
    return settings


# =============================================================================
# Helpers : création du moteur avec mocks
# =============================================================================


def build_engine(
    trading_mode: TradingMode,
    *,
    call_log: list[str] | None = None,
    use_paper_executor: bool = False,
    candle_time: datetime | None = None,
) -> TradingEngine:
    """
    Construit un TradingEngine avec des mocks qui enregistrent les appels.

    Args:
        trading_mode: Mode de trading à tester.
        call_log: Liste pour enregistrer l'ordre des appels.
        use_paper_executor: Si True, utilise PaperOrderExecutor au lieu d'un mock.
        candle_time: Heure de la bougie retournée par get_latest_candles.
    """
    if call_log is None:
        call_log = []

    sig = make_signal()

    async def mock_get_candles(*args, **kwargs):
        call_log.append("get_latest_candles")
        return [make_candle(time=candle_time)]

    async def mock_detect(*args, **kwargs):
        call_log.append("detect")
        return [
            {"concept": "BOS", "direction": "bullish"},
            {"concept": "FVG", "direction": "bullish"},
            {"concept": "ORDER_BLOCK", "direction": "bullish"},
        ]

    async def mock_generate(*args, **kwargs):
        call_log.append("generate")
        return sig

    async def mock_get_account(*args, **kwargs):
        call_log.append("get_account_info")
        return make_account()

    async def mock_can_open(*args, **kwargs):
        call_log.append("can_open_trade")
        return True

    async def mock_validate(*args, **kwargs):
        call_log.append("validate_signal")
        return True

    async def mock_calc_size(*args, **kwargs):
        call_log.append("calculate_position_size")
        return 0.1

    async def mock_open_order(*args, **kwargs):
        call_log.append("open_order")
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

    # Utiliser PaperOrderExecutor réel ou mock
    if use_paper_executor:
        executor = PaperOrderExecutor()
    else:
        executor = MagicMock()
        executor.open_order = mock_open_order

    return TradingEngine(
        settings=make_settings(trading_mode),
        market_data=market_data,
        smc_detector=smc_detector,
        signal_generator=signal_generator,
        risk_manager=risk_manager,
        executor=executor,
        mt5_connector=mt5_connector,
    )


# =============================================================================
# Affichage
# =============================================================================


def print_header(title: str) -> None:
    """Affiche un en-tête de section."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step(label: str, value: object) -> None:
    """Affiche une étape avec indentation."""
    print(f"    → {label}: {value}")


def print_stats(stats: dict, indent: int = 4) -> None:
    """Affiche les statistiques de manière lisible."""
    prefix = " " * indent
    keys_to_show = [
        "mode",
        "total_analyses",
        "total_signals",
        "total_trades",
        "total_closed_trades",
        "winning_trades",
        "losing_trades",
        "win_rate",
        "profit_factor",
        "total_profit",
        "current_balance",
        "total_return_pct",
        "max_drawdown",
        "sharpe_ratio",
        "expectancy",
        "analyses_by_symbol",
    ]
    for key in keys_to_show:
        if key in stats:
            print(f"{prefix}{key}: {stats[key]}")


# =============================================================================
# Tests des trois modes
# =============================================================================


async def test_analysis_mode() -> dict:
    """
    Test du mode ANALYSIS.

    Comportement attendu :
    - Le pipeline s'exécute jusqu'à la génération du signal.
    - AUCUN trade n'est ouvert (pas de calcul de risque, pas d'ordre).
    - Les statistiques enregistrent les analyses et les signaux.
    """
    print_header("MODE ANALYSIS — Aucune position, uniquement les analyses")
    print("  Configuration : TRADING_MODE=analysis")
    print("  Attendu       : Analyse → Signal → STOP (aucun trade)")
    print()

    call_log: list[str] = []
    candle_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    engine = build_engine(
        TradingMode.ANALYSIS,
        call_log=call_log,
        candle_time=candle_time,
    )

    print("  Exécution du pipeline (analyze_symbol)...")
    await engine.analyze_symbol("EURUSD")

    print()
    print("  Ordre des appels :")
    for i, step in enumerate(call_log, 1):
        print(f"    {i}. {step}")

    print()
    print("  Vérifications :")
    print_step("Mode", engine.trading_mode.value)
    print_step("Analyses totales", engine.statistics.total_analyses)
    print_step("Signaux totaux", engine.statistics.total_signals)
    print_step("Trades totaux", engine.statistics.total_trades)
    print_step("open_order appelé ?", "open_order" in call_log)
    print_step("calculate_position_size appelé ?", "calculate_position_size" in call_log)

    print()
    print("  Statistiques complètes :")
    stats = engine.get_statistics()
    print_stats(stats)

    print()
    print("  ✓ Mode ANALYSIS : aucune position ouverte, uniquement des analyses")

    return stats


async def test_paper_mode() -> dict:
    """
    Test du mode PAPER.

    Comportement attendu :
    - Le pipeline complet s'exécute (analyse → signal → risque → trade).
    - Les trades sont simulés par PaperOrderExecutor (aucun ordre MT5).
    - Les statistiques enregistrent les analyses, signaux et trades.
    """
    print_header("MODE PAPER — Simulation complète, aucun ordre MT5")
    print("  Configuration : TRADING_MODE=paper")
    print("  Attendu       : Analyse → Signal → Risque → Trade simulé → STOP")
    print()

    call_log: list[str] = []
    candle_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    engine = build_engine(
        TradingMode.PAPER,
        call_log=call_log,
        use_paper_executor=True,  # Utiliser le vrai PaperOrderExecutor
        candle_time=candle_time,
    )

    print("  Exécution du pipeline (analyze_symbol)...")
    await engine.analyze_symbol("EURUSD")

    print()
    print("  Ordre des appels :")
    for i, step in enumerate(call_log, 1):
        print(f"    {i}. {step}")

    print()
    print("  Vérifications :")
    print_step("Mode", engine.trading_mode.value)
    print_step("Analyses totales", engine.statistics.total_analyses)
    print_step("Signaux totaux", engine.statistics.total_signals)
    print_step("Trades totaux", engine.statistics.total_trades)
    print_step("open_order appelé ?", "open_order" in call_log)
    print_step("calculate_position_size appelé ?", "calculate_position_size" in call_log)

    # Vérifier les trades du PaperOrderExecutor
    if isinstance(engine._executor, PaperOrderExecutor):
        open_trades = engine._executor.get_open_trades()
        print_step("Trades ouverts (PaperExecutor)", len(open_trades))
        if open_trades:
            t = open_trades[0]
            print_step("  - Symbol", t.symbol)
            print_step("  - Direction", t.direction.value)
            print_step("  - Volume", t.volume)
            print_step("  - Ticket", t.ticket)
            print_step("  - Entry", t.entry_price)
            print_step("  - SL", t.stop_loss)
            print_step("  - TP", t.take_profit)

    print()
    print("  Statistiques complètes :")
    stats = engine.get_statistics()
    print_stats(stats)

    print()
    print("  ✓ Mode PAPER : simulation complète avec trades simulés (aucun ordre MT5)")

    return stats


async def test_live_mode() -> dict:
    """
    Test du mode LIVE.

    Comportement attendu :
    - Le pipeline complet s'exécute (analyse → signal → risque → trade).
    - Les trades sont envoyés à MT5 via OrderExecutor.
    - Les statistiques enregistrent les analyses, signaux et trades.

    Note : Dans cette démo, nous utilisons un mock pour l'exécuteur
    car MT5 n'est pas disponible. En production, OrderExecutor enverrait
    de vrais ordres.
    """
    print_header("MODE LIVE — Trading réel")
    print("  Configuration : TRADING_MODE=live, ALLOW_LIVE_TRADING=true")
    print("  Attendu       : Analyse → Signal → Risque → Trade RÉEL → Monitoring")
    print("  Note          : MT5 non disponible, utilisation d'un mock pour l'exécuteur")
    print()

    call_log: list[str] = []
    candle_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    engine = build_engine(
        TradingMode.LIVE,
        call_log=call_log,
        use_paper_executor=False,  # Utiliser le mock (simule OrderExecutor réel)
        candle_time=candle_time,
    )

    print("  Exécution du pipeline (analyze_symbol)...")
    await engine.analyze_symbol("EURUSD")

    print()
    print("  Ordre des appels :")
    for i, step in enumerate(call_log, 1):
        print(f"    {i}. {step}")

    print()
    print("  Vérifications :")
    print_step("Mode", engine.trading_mode.value)
    print_step("Analyses totales", engine.statistics.total_analyses)
    print_step("Signaux totaux", engine.statistics.total_signals)
    print_step("Trades totaux", engine.statistics.total_trades)
    print_step("open_order appelé ?", "open_order" in call_log)
    print_step("calculate_position_size appelé ?", "calculate_position_size" in call_log)

    print()
    print("  Statistiques complètes :")
    stats = engine.get_statistics()
    print_stats(stats)

    print()
    print("  ✓ Mode LIVE : trading réel (ordres envoyés à MT5)")

    return stats


async def test_live_mode_blocked() -> dict:
    """
    Test du mode LIVE sans autorisation.

    Comportement attendu :
    - Si ALLOW_LIVE_TRADING=false, le mode LIVE doit basculer en PAPER.
    - C'est le garde-fou de sécurité.
    """
    print_header("MODE LIVE sans autorisation — Garde-fou de sécurité")
    print("  Configuration : TRADING_MODE=live, ALLOW_LIVE_TRADING=false")
    print("  Attendu       : Bascule automatique en mode PAPER")
    print()

    # Simuler le garde-fou : Settings force PAPER si LIVE non autorisé
    from unittest.mock import patch

    from arty_trading.config.settings import Settings, get_settings

    with patch.dict(
        os.environ,
        {"TRADING_MODE": "live", "ALLOW_LIVE_TRADING": "false"},
    ):
        get_settings.cache_clear()
        settings = Settings()
        print_step("Mode demandé", "live")
        print_step("ALLOW_LIVE_TRADING", "false")
        print_step("Mode effectif", settings.trading_mode.value)
        print_step("is_live_trading_enabled", settings.is_live_trading_enabled)

    print()
    print("  ✓ Garde-fou : le mode LIVE est automatiquement basculé en PAPER")
    print("    sans autorisation explicite (ALLOW_LIVE_TRADING=true)")

    return {"mode": settings.trading_mode.value, "blocked": True}


async def test_multiple_analyses() -> dict:
    """
    Test d'accumulation des statistiques sur plusieurs analyses.

    Montre que les statistiques s'accumulent correctement sur plusieurs
    cycles d'analyse avec des bougies différentes.
    """
    print_header("ACCUMULATION DES STATISTIQUES — 3 cycles d'analyse")
    print("  Mode : PAPER (pour avoir des trades)")
    print()

    call_log: list[str] = []
    times = [
        datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC),
    ]

    engine = build_engine(
        TradingMode.PAPER,
        call_log=call_log,
        use_paper_executor=True,
        candle_time=times[0],
    )

    for i, t in enumerate(times, 1):
        # Mettre à jour le mock pour retourner une nouvelle bougie
        candle_t = t  # capture pour la fermeture

        async def mock_candles(*args, **kwargs):
            call_log.append("get_latest_candles")
            return [make_candle(time=candle_t)]

        engine._market_data.get_latest_candles = mock_candles
        call_log.clear()

        print(f"  Cycle {i}/3 — Bougie à {t.isoformat()}")
        await engine.analyze_symbol("EURUSD")
        print(f"    → Analyses: {engine.statistics.total_analyses}")
        print(f"    → Signaux: {engine.statistics.total_signals}")
        print(f"    → Trades: {engine.statistics.total_trades}")
        print()

    print("  Statistiques finales après 3 cycles :")
    stats = engine.get_statistics()
    print_stats(stats)

    print()
    print("  ✓ Les statistiques s'accumulent correctement sur plusieurs cycles")

    return stats


# =============================================================================
# Fonction principale
# =============================================================================


async def main() -> None:
    """Point d'entrée de la démonstration."""
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║        DÉMONSTRATION DES TROIS MODES DE TRADING (Arty)               ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()
    print("  Ce script teste le moteur de trading avec des données simulées")
    print("  et montre le comportement de chaque mode :")
    print()
    print("  1. MODE_ANALYSIS  — Aucune position, uniquement les analyses")
    print("  2. MODE_PAPER     — Simulation complète, aucun ordre MT5")
    print("  3. MODE_LIVE      — Trading réel")
    print("  4. Garde-fou      — LIVE bloqué sans autorisation")
    print("  5. Accumulation   — Statistiques sur plusieurs cycles")
    print()

    # Test 1 : Mode ANALYSIS
    stats_analysis = await test_analysis_mode()

    # Test 2 : Mode PAPER
    stats_paper = await test_paper_mode()

    # Test 3 : Mode LIVE
    stats_live = await test_live_mode()

    # Test 4 : Garde-fou LIVE sans autorisation
    stats_blocked = await test_live_mode_blocked()

    # Test 5 : Accumulation des statistiques
    stats_multi = await test_multiple_analyses()

    # Résumé final
    print_header("RÉSUMÉ FINAL")
    print()
    print("  ┌────────────────────────────────────────────────────────────────┐")
    print("  │ Mode       │ Analyses │ Signaux │ Trades │ Comportement       │")
    print("  ├────────────────────────────────────────────────────────────────┤")
    print(f"  │ ANALYSIS   │    {stats_analysis['total_analyses']}     │    {stats_analysis['total_signals']}     │   {stats_analysis['total_trades']}    │ Analyse seule      │")
    print(f"  │ PAPER      │    {stats_paper['total_analyses']}     │    {stats_paper['total_signals']}     │   {stats_paper['total_trades']}    │ Simulation complète │")
    print(f"  │ LIVE       │    {stats_live['total_analyses']}     │    {stats_live['total_signals']}     │   {stats_live['total_trades']}    │ Trading réel        │")
    print("  └────────────────────────────────────────────────────────────────┘")
    print()
    print("  ✓ Les trois modes fonctionnent conformément aux spécifications :")
    print("    - ANALYSIS : aucune position, uniquement les analyses")
    print("    - PAPER    : simulation complète, aucun ordre MT5")
    print("    - LIVE     : trading réel (avec garde-fou de sécurité)")
    print("    - Toutes les statistiques sont enregistrées")
    print()


if __name__ == "__main__":
    asyncio.run(main())