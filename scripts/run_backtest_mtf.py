"""
Backtest multi-timeframe (Phase 4) — H1 contexte / M15 setup / M5 entrée.

Pipeline identique au live :
    MarketData (synthétique Dataset B)
      → H1 (MarketContextBuilder, cache par bougie H1 fermée)
      → M15 (SetupTracker via setup_service, même fonction que le live)
      → M5 (SMCTrendStrategy via SignalGenerator)
      → SignalValidator (Phase 2, inchangé)
      → DecisionEngine (inchangé)
      → exécution simulée (BacktestEngine : sizing 1%, SL/TP, pessimiste)

Dataset B : générateur multi-régime symétrique (seed=42), régimes contrôlés :
BULL (1200 M5) / RANGE (900) / BEAR (1200) / HIGH_VOLATILITY (900) /
BULL_TO_BEAR (600) / BEAR_TO_BULL (600) — soit 5400 bougies M5 = 450 H1.

Aucune donnée future : à chaque clôture M5 (t), le contexte H1/M15 n'utilise
que les bougies fermées à t ; une bougie agrégée M15/H1 n'existe qu'une fois
toutes ses M5 clôturées. Ordre intrabar pessimiste : SL évalué avant TP.

Usage :
    python scripts/run_backtest_mtf.py [--seed 42] [--m5 5400]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal

# Performance : le pipeline loggue des millions de lignes INFO en backtest
# (console + fichiers rotatifs). Niveau WARNING pour le backtest uniquement —
# aucune incidence sur la logique de trading.
logging.getLogger("arty_trading").setLevel(logging.WARNING)

from arty_trading.config.settings import Settings
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
)
from arty_trading.modules.backtesting.mtf_engine import MTFBacktestEngine
from arty_trading.modules.backtesting.symbol_config import (
    configure_detector_for_symbol,
)
from arty_trading.modules.decision import DecisionEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector, SetupTracker
from arty_trading.modules.strategies import SMCTrendStrategy

SYMBOL = "XAUUSD"


async def run(seed: int, n_m5: int, min_confidence: float) -> MTFBacktestEngine:
    settings = Settings()
    m5, spans = generate_multi_regime_candles(n_m5=n_m5, seed=seed)
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)

    detector = SMCDetector()
    configure_detector_for_symbol(detector, SYMBOL)

    tracker = SetupTracker()
    validator = SignalValidator(min_risk_reward=1.5, max_spread=200)
    decision_engine = DecisionEngine(settings.decision)
    generator = SignalGenerator(
        min_confidence=min_confidence,
        active_strategy="SMC Trend Following",
        strategies=[SMCTrendStrategy()],
        validator=validator,
        decision_engine=decision_engine,
        setup_tracker=tracker,
        ob_quality=settings.ob_quality,
    )

    engine = MTFBacktestEngine(
        initial_balance=Decimal("10000"),
        risk_per_trade=0.01,
        symbol=SYMBOL,
        settings=settings,
        setup_tracker=tracker,
    )
    await engine.run_mtf_async(m5, m15, h1, generator, detector, regime_spans=spans)
    return engine


def _print_breakdown(title: str, breakdown: dict[str, dict]) -> None:
    if not breakdown:
        print(f"\n  {title}: (aucun trade)")
        return
    print(f"\n  {title}:")
    print(f"  {'clé':<24} {'trades':>6} {'win%':>7} {'PF':>6} {'avg R':>7} {'exp':>9}")
    for key, s in sorted(breakdown.items()):
        print(
            f"  {key:<24} {s['trades']:>6} {s['win_rate']*100:>6.1f}% "
            f"{s['profit_factor']:>6.2f} {s['avg_r']:>7.2f} {s['expectancy']:>9.2f}"
        )


async def main() -> int:
    import io
    import traceback

    parser = argparse.ArgumentParser(description="Backtest MTF Phase 4 (XAUUSD)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--m5", type=int, default=5400)
    parser.add_argument("--conf", type=float, default=0.30)
    args = parser.parse_args()

    lines: list[str] = []
    try:
        print(f"Dataset B | seed={args.seed} | M5={args.m5} | symbole={SYMBOL}")
        engine = await run(args.seed, args.m5, args.conf)
        stats = engine.get_summary()
        trades = engine.trades

        lines.append("=" * 78)
        lines.append("BACKTEST MULTI-TIMEFRAME (H1 contexte / M15 setup / M5 entrée)")
        lines.append("=" * 78)
        lines.append(f"trades={len(trades)} balance finale={stats['final_balance']} "
                     f"profit={stats['profit']}")

        from arty_trading.modules.backtesting.stats import calculate_stats

        st = calculate_stats(engine.trades, engine.equity_curve, Decimal("10000"))
        lines.append(
            f"win_rate={st.win_rate*100:.1f}% PF={st.profit_factor:.2f} "
            f"maxDD={st.max_drawdown*100:.1f}% expectancy={st.expectancy:.2f} "
            f"final={st.final_balance:.2f}"
        )
        for scenario in stats["cost_sensitivity"]:
            lines.append(
                f"costs x{scenario['cost_multiplier']}: "
                f"expectancy_R={scenario['expectancy_r']:.4f} "
                f"PF={scenario['profit_factor']} MDD={scenario['max_drawdown']:.4f}"
            )
        lines += _fmt_breakdown("Par direction", engine.direction_breakdown())
        lines += _fmt_breakdown("Par type de setup", engine.setup_type_breakdown())
        lines += _fmt_breakdown("Par tier Validator/Decision", engine.tier_breakdown())
        lines += _fmt_breakdown("Par score", engine.score_bucket_breakdown())
        lines += _fmt_breakdown("Par régime", engine.regime_breakdown())
        lines.append("=" * 78)
    except Exception:  # noqa: BLE001
        lines.append(traceback.format_exc())

    report = "\n".join(lines)
    print(report)
    io.open("_mtf_report.txt", "w", encoding="utf-8").write(report)
    return 0


def _fmt_breakdown(title: str, breakdown: dict[str, dict]) -> list[str]:
    lines = [f"\n  {title}:"]
    if not breakdown:
        lines.append("  (aucun trade)")
        return lines
    lines.append(f"  {'clé':<24} {'trades':>6} {'win%':>7} {'PF':>6} {'avg R':>7} {'exp':>9}")
    for key, s in sorted(breakdown.items()):
        lines.append(
            f"  {key:<24} {s['trades']:>6} {s['win_rate']*100:>6.1f}% "
            f"{s['profit_factor']:>6.2f} {s['avg_r']:>7.2f} {s['expectancy']:>9.2f}"
        )
    return lines


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
