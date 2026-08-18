"""PAPER TEST — Phase Adaptive Confidence / RR Execution.

Test MT5 (démo/paper, AUCUN ordre envoyé) validant la politique adaptative :
- connexion MT5 via MT5Connector
- bougies M5 (LTF) + H1 (HTF) pour chaque symbole configuré
- détection SMC + génération de signaux (politique adaptative ACTIVE)
- comparaison AVANT/APRÈS au niveau du gate confiance :
    * legacy  : confiance >= 0.85
    * adaptive: 0.60 <= confiance < 0.85 accepté ssi RR >= 2.0
- distribution confiance/RR, rejets par raison, direction des signaux
"""

from __future__ import annotations

import os

os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
os.environ.setdefault("DECISION_DIAGNOSTICS_ENABLED", "true")

import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, "src")

import MetaTrader5 as mt5

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame
from arty_trading.infrastructure.mt5.connector import MT5Connector
from arty_trading.modules.signals.confidence_policy import evaluate_confidence_policy
from arty_trading.modules.signals.generator import SignalGenerator
from arty_trading.modules.smc.detector import SMCDetector

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
TF_MAP = {
    "M5": mt5.TIMEFRAME_M5,
    "H1": mt5.TIMEFRAME_H1,
}


def fetch_candles(symbol: str, timeframe: str, count: int) -> list[Candle]:
    rates = mt5.copy_rates_from_pos(symbol, TF_MAP[timeframe], 0, count)
    if rates is None or len(rates) == 0:
        return []
    tf_enum = TimeFrame(timeframe)
    out: list[Candle] = []
    for rate in rates:
        try:
            spread_val = int(rate["spread"])
        except (ValueError, KeyError, TypeError):
            spread_val = 0
        out.append(Candle(
            symbol=symbol, timeframe=tf_enum,
            time=datetime.fromtimestamp(rate["time"], tz=timezone.utc),
            open=Decimal(str(rate["open"])), high=Decimal(str(rate["high"])),
            low=Decimal(str(rate["low"])), close=Decimal(str(rate["close"])),
            volume=int(rate["tick_volume"]), spread=spread_val,
        ))
    return out


def derive_htf_trend(candles: list[Candle]) -> str:
    """Tendance HTF simple : pente des 20 dernières clôtures."""
    closes = [float(c.close) for c in candles[-20:]]
    if len(closes) < 2:
        return "neutral"
    delta = closes[-1] - closes[0]
    if delta > 0:
        return "bullish"
    if delta < 0:
        return "bearish"
    return "neutral"


async def run_paper_test() -> dict:
    from arty_trading.config.settings import get_settings

    settings = get_settings()
    print(f"Mode trading: {settings.trading_mode.value} | LIVE autorisé: {settings.allow_live_trading}")
    assert not settings.is_live_trading_enabled, "Ce test doit rester en PAPER."

    connector = MT5Connector(settings=settings)
    connected = await connector.connect()
    if not connected:
        print("❌ Impossible de se connecter à MT5 — test annulé.")
        return {}
    account = await connector.get_account_info()
    print(f"✅ MT5 connecté | {account.server} | balance={account.balance} | mode={account.mode.value}")

    detector = SMCDetector()
    generator = SignalGenerator(min_confidence=0.85)  # seuil legacy configuré

    stats = {
        "symbols_scanned": 0,
        "candidates": 0,
        "accepted_legacy": 0,
        "accepted_adaptive": 0,
        "new_opportunities": 0,
        "rejected_low_confidence": 0,
        "rejected_rr_too_low": 0,
    }
    conf_dist: Counter[str] = Counter()
    dir_dist: Counter[str] = Counter()
    rr_values: list[float] = []
    new_trades: list[tuple] = []  # (symbol, direction, confidence, rr)

    for symbol in SYMBOLS:
        ltf = fetch_candles(symbol, "M5", 300)
        htf = fetch_candles(symbol, "H1", 100)
        if len(ltf) < 50 or len(htf) < 20:
            print(f"⚠️  {symbol}: données insuffisantes (LTF={len(ltf)}, HTF={len(htf)})")
            continue
        stats["symbols_scanned"] += 1

        smc_ltf = await detector.detect(ltf, symbol)
        smc_htf = await detector.detect(htf, symbol)
        trend = derive_htf_trend(htf)
        print(f"\n--- {symbol} | M5 x{len(ltf)} | H1 trend={trend} | SMC LTF={len(smc_ltf)} HTF={len(smc_htf)} ---")

        # generate_all retourne tous les signaux franchissant le gate politique.
        signals = await generator.generate_all(
            ltf, smc_ltf, htf_smc_data=smc_htf, htf_trend=trend,
        )
        for sig in signals:
            stats["candidates"] += 1
            conf = sig.confidence
            rr = sig.risk_reward_ratio
            conf_dist[evaluate_confidence_policy(conf, rr).confidence_bucket] += 1
            dir_dist[sig.direction.value] += 1
            rr_values.append(rr)

            legacy_ok = conf >= 0.85
            policy = evaluate_confidence_policy(conf, rr)
            if legacy_ok:
                stats["accepted_legacy"] += 1
                stats["accepted_adaptive"] += 1
            elif policy.allowed:
                stats["accepted_adaptive"] += 1
                stats["new_opportunities"] += 1
                new_trades.append((symbol, sig.direction.value, conf, rr))
                print(f"  🆕 NOUVEAU TRADE | {symbol} {sig.direction.value} | conf={conf:.2f} | RR={rr:.2f} | bucket={policy.confidence_bucket}")
            elif policy.reason == "low_confidence":
                stats["rejected_low_confidence"] += 1
            else:
                stats["rejected_rr_too_low"] += 1
            print(f"  signal | {sig.direction.value} | conf={conf:.2f} | RR={rr:.2f} | legacy={'PASS' if legacy_ok else 'REJECT'}")

    # === RAPPORT AVANT/APRÈS ===
    print("\n" + "=" * 70)
    print("  RAPPORT PAPER TEST — POLITIQUE ADAPTATIVE CONFIANCE/RR")
    print("=" * 70)
    for k, v in stats.items():
        print(f"  {k:26s}: {v}")
    print(f"\n  Distribution confiance (buckets): {dict(conf_dist)}")
    print(f"  Directions: {dict(dir_dist)}")
    if rr_values:
        rr_values.sort()
        print(f"  RR min={rr_values[0]:.2f} | médian={rr_values[len(rr_values)//2]:.2f} | max={rr_values[-1]:.2f}")
    if new_trades:
        print("\n  Nouvelles opportunités (confiance 0.60-0.84 + RR>=2.0) :")
        for t in new_trades:
            print(f"    {t[0]} {t[1]} | conf={t[2]:.2f} | RR={t[3]:.2f}")
    else:
        print("\n  Aucune nouvelle opportunité sur cet échantillon instantané.")
    print("\n  ⚠️  Aucun ordre envoyé — test d'observation uniquement (PAPER).")

    await connector.disconnect()
    print("\n🔌 Déconnecté de MT5.")
    return stats


if __name__ == "__main__":
    asyncio.run(run_paper_test())
