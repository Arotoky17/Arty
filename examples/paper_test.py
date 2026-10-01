"""PAPER TEST PHASE 2.1 — Validation du fix retest_still_valid.

Injecte des données SMC synthétiques réalistes pour valider le Final Gate
sur EURUSD (BUY) et XAUUSD (SELL) en mode PAPER.
"""

from __future__ import annotations

import os

os.environ["TRADING_MODE"] = "paper"
os.environ["ALLOW_LIVE_TRADING"] = "false"
os.environ["DECISION_DIAGNOSTICS_ENABLED"] = "true"
os.environ["DEFAULT_TIMEFRAME"] = "H1"

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arty_trading.application.execution_guards import final_gate_before_execution
from arty_trading.config.settings import get_settings
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.decision.market_context import MarketContext
from arty_trading.utils.helpers import retest_still_valid_detailed

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("paper_test")


def gen_ltf_candles(symbol: str, n: int = 96, base: float = 1.0800) -> list[Candle]:
    """Generate LTF M5 candles ending with a retest of a wide FVG zone."""
    candles = []
    t0 = datetime(2024, 6, 1, 8, 0, tzinfo=UTC)
    b = Decimal(str(base))

    for i in range(n):
        t = t0 + timedelta(minutes=i * 5)
        o = b + Decimal(str((i % 7 - 3) * 0.0002))
        c = o + Decimal(str((i % 3 - 1) * 0.0001))
        h = max(o, c) + Decimal("0.0005")
        low = min(o, c) - Decimal("0.0005")
        candles.append(Candle(
            symbol=symbol, timeframe=TimeFrame.M5, time=t,
            open=o, high=h, low=low, close=c, volume=500, spread=5,
        ))
    return candles


def gen_htf_candles(symbol: str, n: int = 50, trend: str = "bullish") -> list[Candle]:
    """Generate HTF H1 candles with clear trend."""
    candles = []
    t0 = datetime(2024, 6, 1, 8, 0, tzinfo=UTC)
    for i in range(n):
        t = t0 + timedelta(hours=i)
        if trend == "bullish":
            o = Decimal("1.0800") + Decimal(str(i * 0.002))
            c = o + Decimal("0.0015")
        else:
            o = Decimal("1.0850") - Decimal(str(i * 0.002))
            c = o - Decimal("0.0015")
        h = max(o, c) + Decimal("0.0010")
        low = min(o, c) - Decimal("0.0010")
        candles.append(Candle(
            symbol=symbol, timeframe=TimeFrame.H1, time=t,
            open=o, high=h, low=low, close=c, volume=500, spread=10,
        ))
    return candles


def gen_xau_ltf_candles(n: int = 96) -> list[Candle]:
    """Generate XAUUSD M5 candles ending with a bearish retest."""
    candles = []
    t0 = datetime(2024, 6, 1, 8, 0, tzinfo=UTC)
    for i in range(n):
        t = t0 + timedelta(minutes=i * 5)
        o = Decimal("2350.00") + Decimal(str((i % 7 - 3) * 0.10))
        c = o + Decimal(str((i % 3 - 1) * 0.05))
        h = max(o, c) + Decimal("0.10")
        low = min(o, c) - Decimal("0.10")
        candles.append(Candle(
            symbol="XAUUSD", timeframe=TimeFrame.M5, time=t,
            open=o, high=h, low=low, close=c, volume=500, spread=5,
        ))
    # Last candle: bearish, at zone boundary
    candles[-1] = Candle(
        symbol="XAUUSD", timeframe=TimeFrame.M5,
        time=t0 + timedelta(minutes=(n - 1) * 5),
        open=Decimal("2345.05"), high=Decimal("2345.10"),
        low=Decimal("2344.80"), close=Decimal("2344.95"),
        volume=500, spread=5,
    )
    return candles


async def run_paper_test():
    settings = get_settings()
    logger.info("=== PAPER TEST PHASE 2.1 ===")
    logger.info("Mode: %s | Symbols: %s", settings.trading_mode.value, settings.symbols_list)

    profile_usd = settings.get_instrument_profile("EURUSD")
    profile_xau = settings.get_instrument_profile("XAUUSD")
    logger.info("EURUSD profile: atr_mult=%.1f max_age=%d",
                profile_usd.retest_atr_mult, profile_usd.max_zone_age_bars)
    logger.info("XAUUSD profile: atr_mult=%.1f max_age=%d",
                profile_xau.retest_atr_mult, profile_xau.max_zone_age_bars)

    stats = {
        "candles_analyzed": 0,
        "opportunities_seen": 0,
        "signals_generated": 0,
        "rejected": 0,
        "approved": 0,
        "final_gate_passed": 0,
        "final_gate_rejected": 0,
        "orders_submitted": 0,
        "orders_executed": 0,
        "retest_blocked": 0,
    }

    # =================================================================
    # EURUSD BUY — wide FVG (50 pips), price at zone boundary
    # =================================================================
    logger.info("\n--- EURUSD BUY (bullish trend, wide FVG) ---")

    gap_bottom = Decimal("1.0810")
    gap_top = Decimal("1.0860")  # 50-pip gap
    fvg_index = 90

    ltf = gen_ltf_candles("EURUSD", 96)
    # Force last candle: bullish, inside zone (close between gap_bottom and gap_top)
    ltf[-1] = Candle(
        symbol="EURUSD", timeframe=TimeFrame.M5,
        time=ltf[-1].time,
        open=Decimal("1.0805"), high=Decimal("1.0825"),
        low=Decimal("1.0800"), close=Decimal("1.0815"),  # bullish, inside zone
        volume=500, spread=5,
    )

    # Synthetic SMC: wide bullish FVG
    eurusd_smc = [{
        "concept": "fair_value_gap", "direction": "bullish",
        "price": float(gap_bottom), "index": fvg_index,
        "details": {
            "gap_top": float(gap_top), "gap_bottom": float(gap_bottom),
            "gap_size": float(gap_top - gap_bottom),
            "mitigation_count": 0, "filled": False,
        },
    }]
    # Also add a BOS detection
    eurusd_smc.append({
        "concept": "break_of_structure", "direction": "bullish",
        "price": 1.0860, "index": 92, "details": {"strength": 3},
    })

    htf_eurusd = gen_htf_candles("EURUSD", 50, "bullish")

    context = MarketContext(
        symbol="EURUSD", timestamp=ltf[-1].time,
        master_trend="bullish", regime="trend", spread=5,
        ltf_candles=ltf, ltf_smc_data=eurusd_smc,
        htf_smc_data=[], atr=Decimal("0.0009"),
    )

    stats["candles_analyzed"] += len(ltf) + len(htf_eurusd)
    stats["opportunities_seen"] += 1

    signal = Signal(
        symbol="EURUSD", signal_type=SignalType.BUY, direction=Direction.BUY,
        entry_price=Decimal("1.0815"), stop_loss=Decimal("1.0800"),
        take_profit=Decimal("1.0845"), confidence=1.0,
        strategy_name="SMC Trend Following", timeframe=TimeFrame.M5,
        justification="Bullish FVG retest + BOS + CHoCH", smc_concepts=["FVG", "BOS"],
    )
    stats["signals_generated"] += 1

    # Retest diagnostic
    diag = retest_still_valid_detailed(
        candles=context.ltf_candles, smc_data=context.ltf_smc_data,
        direction="bullish",
        max_age_bars=profile_usd.max_zone_age_bars,
        max_distance_atr_mult=profile_usd.retest_atr_mult,
        symbol="EURUSD",
    )
    logger.info("Retest diagnostic (EURUSD BUY):")
    logger.info("  zone_type=%s zone_id=%s zone_age_bars=%s", diag.zone_type, diag.zone_id, diag.zone_age_bars)
    logger.info("  ATR=%.6f distance_to_zone=%s max_distance=%.6f", diag.atr, diag.distance_to_zone, diag.max_distance)
    logger.info("  retest_detected=%s retest_confirmed=%s retest_still_valid=%s",
                diag.retest_detected, diag.retest_confirmed, diag.valid)
    logger.info("  max_zone_age_bars=%d retest_atr_mult=%.1f", diag.max_zone_age_bars, diag.retest_atr_mult)
    logger.info("  reason=%s last_candle_time=%s", diag.reason, diag.last_candle_time)

    passed = await final_gate_before_execution("EURUSD", signal, context, settings)

    if passed:
        stats["final_gate_passed"] += 1
        stats["approved"] += 1
        stats["orders_submitted"] += 1
        stats["orders_executed"] += 1
        logger.info("FINAL GATE: PASS | EURUSD BUY")
    else:
        stats["final_gate_rejected"] += 1
        stats["rejected"] += 1
        stats["retest_blocked"] += 1
        logger.info("FINAL GATE: REJECT | EURUSD BUY | reason=%s", diag.reason)

    # =================================================================
    # XAUUSD SELL — wide FVG (50 pips), price at zone boundary
    # =================================================================
    logger.info("\n--- XAUUSD SELL (bearish trend, wide FVG) ---")

    x_gap_top = Decimal("2350.00")
    x_gap_bottom = Decimal("2345.00")  # 50-pip gap
    x_fvg_index = 90

    ltf_x = gen_xau_ltf_candles(96)
    # Synthetic SMC: wide bearish FVG
    xau_smc = [{
        "concept": "fair_value_gap", "direction": "bearish",
        "price": float(x_gap_top), "index": x_fvg_index,
        "details": {
            "gap_top": float(x_gap_top), "gap_bottom": float(x_gap_bottom),
            "gap_size": float(x_gap_top - x_gap_bottom),
            "mitigation_count": 0, "filled": False,
        },
    }]

    x_context = MarketContext(
        symbol="XAUUSD", timestamp=ltf_x[-1].time,
        master_trend="bearish", regime="trend", spread=10,
        ltf_candles=ltf_x, ltf_smc_data=xau_smc,
        htf_smc_data=[], atr=Decimal("1.20"),
    )

    stats["candles_analyzed"] += len(ltf_x) + len(htf_eurusd)
    stats["opportunities_seen"] += 1

    x_signal = Signal(
        symbol="XAUUSD", signal_type=SignalType.SELL, direction=Direction.SELL,
        entry_price=Decimal("2344.95"), stop_loss=Decimal("2355.00"),
        take_profit=Decimal("2315.00"), confidence=1.0,
        strategy_name="SMC Trend Following", timeframe=TimeFrame.M5,
        justification="Bearish FVG retest + BOS", smc_concepts=["FVG", "BOS"],
    )
    stats["signals_generated"] += 1

    diag_x = retest_still_valid_detailed(
        candles=x_context.ltf_candles, smc_data=x_context.ltf_smc_data,
        direction="bearish",
        max_age_bars=profile_xau.max_zone_age_bars,
        max_distance_atr_mult=profile_xau.retest_atr_mult,
        symbol="XAUUSD",
    )
    logger.info("Retest diagnostic (XAUUSD SELL):")
    logger.info("  zone_type=%s zone_age_bars=%s ATR=%.6f", diag_x.zone_type, diag_x.zone_age_bars, diag_x.atr)
    logger.info("  distance_to_zone=%s max_distance=%.6f", diag_x.distance_to_zone, diag_x.max_distance)
    logger.info("  retest_detected=%s retest_confirmed=%s retest_still_valid=%s",
                diag_x.retest_detected, diag_x.retest_confirmed, diag_x.valid)
    logger.info("  reason=%s last_candle_time=%s", diag_x.reason, diag_x.last_candle_time)

    x_passed = await final_gate_before_execution("XAUUSD", x_signal, x_context, settings)

    if x_passed:
        stats["final_gate_passed"] += 1
        stats["approved"] += 1
        stats["orders_submitted"] += 1
        stats["orders_executed"] += 1
        logger.info("FINAL GATE: PASS | XAUUSD SELL")
    else:
        stats["final_gate_rejected"] += 1
        stats["rejected"] += 1
        stats["retest_blocked"] += 1
        logger.info("FINAL GATE: REJECT | XAUUSD SELL | reason=%s", diag_x.reason)

    # === RESULTS ===
    logger.info("\n=== PAPER TEST RESULTS ===")
    for k, v in stats.items():
        logger.info("  %s: %d", k, v)

    logger.info("\n=== FINAL GATE SUMMARY ===")
    logger.info("Signals reaching FINAL_GATE: %d", stats["signals_generated"])
    logger.info("Final Gate PASS: %d | REJECT: %d | Retest blocked: %d",
                stats["final_gate_passed"], stats["final_gate_rejected"], stats["retest_blocked"])
    logger.info("Orders submitted: %d | executed: %d",
                stats["orders_submitted"], stats["orders_executed"])

    return stats


if __name__ == "__main__":
    asyncio.run(run_paper_test())
