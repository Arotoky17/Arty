"""
Diagnostic CONTINUATION|SELL — backtest XAUUSD (Phase 3).

Rejoue EXACTEMENT le pipeline de ``run_backtest.py`` (mêmes bougies seed=42,
mêmes composants, filtres InstrumentProfile actifs) et journalise pour chaque
trade SELL le contexte SMC complet au moment du signal, l'excursion de prix
(MFE/MAE en R) et le résultat.

NB : le chemin backtest est mono-timeframe H1 (pas de M15/M5) — le « bias »
est le ``master_trend`` estimé sur les 5 dernières clôtures H1
(``BacktestEngine._estimate_trend``).

Usage :
    python scripts/diagnose_sells.py [--symbol XAUUSD] [--candles 600]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle, Signal
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.signals import SignalGenerator, SignalValidator
from arty_trading.modules.smc import SMCDetector
from arty_trading.modules.strategies import SMCTrendStrategy

sys.path.insert(0, "scripts")
from run_backtest import configure_detector_for_symbol, generate_candles  # noqa: E402


def concepts_summary(smc_data: list[dict]) -> dict[str, Any]:
    """Résume les concepts SMC présents au moment du signal."""
    summary: dict[str, Any] = {}
    for concept in (
        "break_of_structure",
        "change_of_character",
        "liquidity_sweep",
        "fair_value_gap",
        "order_block",
        "optimal_trade_entry",
        "premium_discount",
    ):
        items = [d for d in smc_data if d.get("concept") == concept]
        if not items:
            continue
        latest = max(items, key=lambda d: d.get("index", -1))
        entry = {"direction": latest.get("direction"), "index": latest.get("index"), "count": len(items)}
        if concept == "premium_discount":
            entry["zone"] = latest.get("details", {}).get("current_zone")
        summary[concept] = entry
    return summary


async def diagnose(symbol: str, n_candles: int, min_confidence: float, rr_min: float) -> None:
    candles = generate_candles(symbol, n_candles)
    index_by_id = {id(c): i for i, c in enumerate(candles)}

    detector = SMCDetector()
    configure_detector_for_symbol(detector, symbol)
    strategy = SMCTrendStrategy()
    generator = SignalGenerator(
        min_confidence=min_confidence,
        active_strategy="SMC Trend Following",
        strategies=[strategy],
        validator=SignalValidator(min_risk_reward=rr_min, max_spread=200 if symbol == "XAUUSD" else 30),
    )
    engine = BacktestEngine(initial_balance=Decimal("10000"), risk_per_trade=0.01, symbol=symbol)

    # --- Hooks pour capturer le contexte sans modifier le moteur ---
    last_context: dict[str, Any] = {}
    orig_generate = generator.generate

    async def gen_hook(candles_in, smc_data, master_trend=None, **kw):
        sig = await orig_generate(candles_in, smc_data, master_trend=master_trend, **kw)
        last_context.clear()
        last_context.update({"smc": smc_data, "trend": master_trend, "signal": sig})
        return sig

    generator.generate = gen_hook  # type: ignore[method-assign]

    trade_info: dict[int, dict] = {}
    current_index: list[int] = [-1]

    orig_open = engine._open_trade_from_signal
    orig_close = engine._close_trade
    orig_check = engine._check_open_trades

    def open_hook(signal: Signal, candle: Candle):
        ticket = engine._ticket_counter  # ticket qui sera attribué
        trade_info[ticket] = {"entry_index": index_by_id[id(candle)], "ctx": dict(last_context)}
        return orig_open(signal, candle)

    def close_hook(trade, close_price):
        info = trade_info.get(trade.ticket)
        if info is not None:
            info["close_index"] = current_index[0]
        return orig_close(trade, close_price)

    def check_hook(candle: Candle):
        current_index[0] = index_by_id[id(candle)]
        return orig_check(candle)

    engine._open_trade_from_signal = open_hook  # type: ignore[method-assign]
    engine._close_trade = close_hook  # type: ignore[method-assign]
    engine._check_open_trades = check_hook  # type: ignore[method-assign]

    stats = await engine.run_async(candles, signal_generator=generator, smc_detector=detector)

    print(f"=== {symbol} | candles={n_candles} | conf>={min_confidence} | RR>={rr_min} ===")
    print(
        f"trades={stats.total_trades} WR={stats.win_rate*100:.1f}% PF={stats.profit_factor:.2f} "
        f"maxDD={stats.max_drawdown*100:.1f}% exp={float(stats.expectancy):.2f}"
    )

    # --- Rapport par trade, focalisé sur les SELL ---
    journal = engine.trade_journal
    sells = [e for e in journal if e["direction"] == "SELL"]
    buys = [e for e in journal if e["direction"] == "BUY"]
    print(f"\nBUY: {len(buys)} trades | SELL: {len(sells)} trades")

    print(f"\n--- Détail des {len(sells)} trades SELL ---")
    print(
        f"{'tk':>3} {'bar':>4} {'setup':<20} {'trend':<7} {'concepts':<40} "
        f"{'entry':>9} {'SL':>9} {'TP':>9} {'RR':>5} {'MFE':>5} {'bars':>4} {'R':>6} {'res':<4}"
    )
    for e in sells:
        info = trade_info.get(e["ticket"], {})
        ctx = info.get("ctx", {})
        cs = concepts_summary(ctx.get("smc", []))
        concepts_str = ",".join(
            f"{k.split('_')[0]}:{v['direction'][:4]}@{v['index']}" for k, v in cs.items()
        )
        entry_i = info.get("entry_index", -1)
        close_i = info.get("close_index", entry_i)
        entry, sl = e["entry_price"], e["stop_loss"]
        tp = next((float(t.take_profit) for t in engine.trades if t.ticket == e["ticket"]), 0.0)
        risk = abs(entry - sl) or 1e-9
        planned_rr = (entry - tp) / risk if tp else 0.0  # SELL : favorable = baisse
        mfe = max((entry - float(c.low) for c in candles[entry_i + 1 : close_i + 1]), default=0.0)
        result = "WIN" if e["profit"] > 0 else ("LOSS" if e["profit"] < 0 else "?")
        print(
            f"{e['ticket']:>3} {entry_i:>4} {e['setup_type']:<20} {str(ctx.get('trend')):<7} "
            f"{concepts_str[:40]:<40} {entry:>9.2f} {sl:>9.2f} {tp:>9.2f} {planned_rr:>5.2f} "
            f"{mfe/risk:>5.2f} {max(0, close_i - entry_i):>4} {e['r_multiple']:>6.2f} {result:<4}"
        )

    # --- Caractéristiques communes des SELL perdants ---
    losers = [e for e in sells if e["profit"] <= 0]
    winners = [e for e in sells if e["profit"] > 0]
    print(f"\nSELL gagnants: {len(winners)} | SELL perdants: {len(losers)}")
    if not winners:
        print("-> AUCUN SELL gagnant : pas de comparaison gagnants/perdants possible.")
    print("\nTraits des SELL perdants :")
    trait_counts: dict[str, int] = {}
    mfe_list: list[float] = []
    fast_stops = 0
    for e in losers:
        info = trade_info.get(e["ticket"], {})
        ctx = info.get("ctx", {})
        cs = concepts_summary(ctx.get("smc", []))
        trend = ctx.get("trend")
        if trend != "bearish":
            trait_counts[f"trend={trend}"] = trait_counts.get(f"trend={trend}", 0) + 1
        bos = cs.get("break_of_structure")
        if bos and bos["direction"] != "bearish":
            trait_counts["latest BOS != bearish"] = trait_counts.get("latest BOS != bearish", 0) + 1
        if "change_of_character" in cs:
            trait_counts["CHOCH présent"] = trait_counts.get("CHOCH présent", 0) + 1
        if "liquidity_sweep" in cs:
            trait_counts["sweep présent"] = trait_counts.get("sweep présent", 0) + 1
        entry_i = info.get("entry_index", -1)
        close_i = info.get("close_index", entry_i)
        entry, sl = e["entry_price"], e["stop_loss"]
        risk = abs(entry - sl) or 1e-9
        mfe = max((entry - float(c.low) for c in candles[entry_i + 1 : close_i + 1]), default=0.0)
        mfe_list.append(mfe / risk)
        if close_i - entry_i <= 1:
            fast_stops += 1
    if trait_counts:
        print(f"  {trait_counts}")
    if mfe_list:
        print(f"  MFE max en R: {max(mfe_list):.2f} | MFE médiane: {sorted(mfe_list)[len(mfe_list)//2]:.2f}")
        print(f"  SELL perdants dont le prix n'a JAMAIS atteint 1R en faveur : "
              f"{sum(1 for m in mfe_list if m < 1.0)}/{len(mfe_list)}")
        print(f"  SELL perdants clôturés en <= 1 bougie : {fast_stops}/{len(losers)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic CONTINUATION|SELL")
    parser.add_argument("--symbol", default="XAUUSD", choices=["XAUUSD"])
    parser.add_argument("--candles", type=int, default=600)
    args = parser.parse_args()
    # Config avec validateur (celle du rapport Phase 3F) puis sans.
    await diagnose(args.symbol, args.candles, 0.60, 1.5)
    print()
    await diagnose(args.symbol, args.candles, 0.30, 0.0)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
