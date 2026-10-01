"""Test DEMO MT5 - Chaîne complète sur compte démo réel.

Ce script teste :
1. Connexion MT5
2. Récupération de bougies
3. Détection SMC
4. Génération de signal
5. Validation du risque
6. Calcul de position
7. Ouverture et fermeture d'un trade

Usage :
    python test_demo_live.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from arty_trading.config import get_settings
from arty_trading.core.enums import TradingMode
from arty_trading.infrastructure.mt5 import MT5Connector, MT5MarketDataProvider
from arty_trading.logging import setup_logging, get_logger
from arty_trading.modules.execution import OrderExecutor
from arty_trading.modules.risk import RiskManager
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.smc import SMCDetector

logger = get_logger()


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step(label: str, value: object) -> None:
    print(f"    -> {label}: {value}")


async def main() -> int:
    settings = get_settings()
    setup_logging(
        level=settings.log_level,
        logs_dir=settings.logs_dir,
        app_env=settings.app_env,
    )

    print_section("TEST DEMO MT5 - Arty Trading Bot")
    print_step("Mode", settings.trading_mode.value)
    print_step("Symboles", settings.symbols_list)
    print_step("Timeframe", settings.default_timeframe.value)
    print_step("Live trading", settings.is_live_trading_enabled)

    # ------------------------------------------------------------------
    # 1. Connexion MT5
    # ------------------------------------------------------------------
    print_section("1. Connexion MT5")
    connector = MT5Connector(settings=settings)
    ok = await connector.connect()
    print_step("Connecte", ok)

    if not ok:
        print("ERREUR: Impossible de se connecter a MT5.")
        print("Verifiez que MT5 est lance et que les identifiants sont corrects.")
        return 1

    account = await connector.get_account_info()
    print_step("Login", account.login)
    print_step("Serveur", account.server)
    print_step("Mode", account.mode.value)
    print_step("Balance", account.balance)
    print_step("Equity", account.equity)
    print_step("Leverage", account.leverage)
    print_step("Demo", account.is_demo)

    status = await connector.get_connection_status()
    print_step("Connexion MT5", status["connected"])

    # ------------------------------------------------------------------
    # 2. Donnees de marche
    # ------------------------------------------------------------------
    print_section("2. Donnees de marche")
    market = MT5MarketDataProvider()
    symbol = settings.symbols_list[0]
    timeframe = settings.default_timeframe
    count = 200

    candles = await market.get_latest_candles(symbol, timeframe, count)
    print_step("Bougies recues", len(candles))

    if not candles:
        print("ERREUR: Aucune bougie recue.")
        await connector.disconnect()
        return 1

    latest = candles[-1]
    print_step("Derniere bougie", latest.time.isoformat())
    print_step("Close", latest.close)
    print_step("Spread", latest.spread)

    spread = await market.get_spread(symbol)
    print_step("Spread actuel", spread)

    # ------------------------------------------------------------------
    # 3. Detection SMC
    # ------------------------------------------------------------------
    print_section("3. Detection SMC")
    detector = SMCDetector()
    detections = await detector.detect(candles, symbol)
    print_step("Detections SMC", len(detections))

    if detections:
        concepts_count: dict[str, int] = {}
        for d in detections:
            concept = d.get("concept", "unknown")
            concepts_count[concept] = concepts_count.get(concept, 0) + 1
        for concept, count in sorted(concepts_count.items()):
            print_step(f"  {concept}", count)

    # ------------------------------------------------------------------
    # 4. Generation de signal
    # ------------------------------------------------------------------
    print_section("4. Generation de signal")
    generator = SignalGenerator(
        min_confidence=settings.signals.min_confidence,
        active_strategy=settings.signals.active_strategy,
    )
    signal = await generator.generate(candles, detections)
    print_step("Signal", signal)

    if signal is None:
        print("INFO: Aucun signal genere (normal si pas de setup SMC).")
        print("Conseil: Baisser SIGNAL_MIN_CONFIDENCE ou activer plus de detecteurs.")
        await connector.disconnect()
        return 0

    print_step("Direction", signal.direction.value)
    print_step("Type", signal.signal_type.value)
    print_step("Entry", signal.entry_price)
    print_step("SL", signal.stop_loss)
    print_step("TP", signal.take_profit)
    print_step("Confiance", signal.confidence)
    print_step("R/R", signal.risk_reward_ratio)
    print_step("Strategie", signal.strategy_name)
    print_step("Concepts", signal.smc_concepts)
    print_step("Justification", signal.justification)

    # ------------------------------------------------------------------
    # 5. Gestion du risque
    # ------------------------------------------------------------------
    print_section("5. Gestion du risque")
    risk = RiskManager(settings=settings.risk, market_data=market)

    can_open = await risk.can_open_trade(symbol)
    print_step("Peut ouvrir", can_open)

    valid = await risk.validate_signal(signal, account)
    print_step("Signal valide", valid)

    if not valid:
        print("INFO: Signal rejete par le risk manager.")
        print("Verifiez: max_open_positions, daily_loss, drawdown, spread.")
        await connector.disconnect()
        return 0

    volume = await risk.calculate_position_size(signal, account)
    print_step("Volume calcule", volume)

    # ------------------------------------------------------------------
    # 6. Execution du trade
    # ------------------------------------------------------------------
    print_section("6. Execution du trade")
    executor = OrderExecutor()

    trade = await executor.open_order(signal, volume)
    print_step("Ticket", trade.ticket)
    print_step("Entry", trade.entry_price)
    print_step("SL", trade.stop_loss)
    print_step("TP", trade.take_profit)
    print_step("Volume", trade.volume)

    # ------------------------------------------------------------------
    # 7. Fermeture du trade
    # ------------------------------------------------------------------
    print_section("7. Fermeture du trade")
    closed = await executor.close_order(trade)
    print_step("Ticket ferme", closed.ticket)
    print_step("Close price", closed.close_price)
    print_step("Profit", closed.profit)
    print_step("Resultat", "WIN" if closed.profit and closed.profit > 0 else "LOSS")

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------
    print_section("RESUME")
    print(f"  Symboles testes: {symbol}")
    print(f"  Bougies analysees: {len(candles)}")
    print(f"  Detections SMC: {len(detections)}")
    print(f"  Signal genere: {signal is not None}")
    if signal:
        print(f"  Direction: {signal.direction.value}")
        print(f"  Confiance: {signal.confidence:.2f}")
        print(f"  R/R: {signal.risk_reward_ratio:.2f}")
    print(f"  Trade ouvert: {trade is not None}")
    if trade:
        print(f"  Ticket: {trade.ticket}")
        print(f"  Profit: {closed.profit}")
    print(f"  Connexion MT5: {status['connected']}")

    # ------------------------------------------------------------------
    # 8. Points faibles a ameliorer
    # ------------------------------------------------------------------
    print_section("POINTS FAIBLES A AMELIORER")

    if len(detections) > 30:
        print("  [DETECTEURS] Beaucoup de faux positifs ( > 30 detections )")
        print("      -> Ajouter un filtre de force de detection")
        print("      -> Ajuster les seuils de chaque detecteur")

    if signal is None:
        print("  [STRATEGIE] Aucun signal genere")
        print("      -> Baisser SIGNAL_MIN_CONFIDENCE (actuellement: {:.2f})".format(
            settings.signals.min_confidence
        ))
        print("      -> Verifier que les detecteurs SMC fonctionnent sur donnees reelles")

    if signal and not valid:
        print("  [RISK] Signal rejete par le risk manager")
        print("      -> Verifier spread, daily_loss, drawdown, max_open_positions")

    if trade and closed.profit and closed.profit < 0:
        print("  [EXECUTION] Trade perdant")
        print("      -> Verifier SL/TP, timing d'entree, slippage")

    if not status["connected"]:
        print("  [MT5] Connexion instable")
        print("      -> Verifier les parametres de connexion, le reseau, MT5")

    print("\n  Points generaux a ameliorer :")
    print("  1. Ajouter un backtest sur donnees historiques avant le live")
    print("  2. Tester sur plusieurs symboles et timeframes")
    print("  3. Mesurer le win rate sur 50+ trades en demo")
    print("  4. Verifier le comportement des SL/TP en cas de gap")
    print("  5. Tester la reconnexion MT5 pendant un trade ouvert")
    print("  6. Ajouter un journal de trading pour analyser chaque trade")
    print("  7. Tester le filtre de news avec un vrai calendrier economique")

    await connector.disconnect()
    print_section("TEST TERMINE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
