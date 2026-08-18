"""DEMO TEST - Phase Adaptive Confidence / RR Execution sur compte DEMO MT5.

1. Connexion MT5 (verifie que le compte est bien un DEMO avant tout ordre)
2. Scan EURUSD/XAUUSD (exclusivement) : bougies M5 + tendance H1 + SMC
   (boucle : re-scan toutes les 5 min pendant SCAN_WINDOW_MIN)
3. Signaux via politique adaptative (confiance >= 0.60 si RR >= 2.0)
4. Un ordre marche par signal accepte, SL/TP du signal, risque 1%
5. Surveillance des positions jusqu'a cloture (SL/TP) ou timeout
6. Rapport final : resultat de chaque trade en R

ATTENTION : compte DEMO uniquement - refuse de s'executer sur un compte reel.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import MetaTrader5 as mt5  # noqa: N813

from arty_trading.config.settings import get_settings
from arty_trading.modules.signals.generator import SignalGenerator
from arty_trading.modules.smc.detector import SMCDetector
from paper_test_adaptive import derive_htf_trend, fetch_candles

MAGIC = 234001
# Restriction marches : EURUSD + XAUUSD uniquement (GBPUSD/USDJPY exclus)
ALLOWED_SYMBOLS = {"EURUSD", "XAUUSD"}
SYMBOLS = ["EURUSD", "XAUUSD"]
MONITOR_TIMEOUT_S = 7200  # 2h max par position
POLL_S = 10
MAX_TRADES = 3
SCAN_WINDOW_MIN = 720  # duree totale du scan (minutes) - 12h, arret manuel souhait
SCAN_INTERVAL_MIN = 5  # re-scan toutes les N minutes si aucun signal
# Reserve de marge : on n'utilise jamais plus de 80% de la free margin
MARGIN_SAFETY_FACTOR = 0.80


def ensure_demo() -> None:
    info = mt5.account_info()
    if info is None:
        raise SystemExit("Impossible de lire account_info - MT5 non connecte.")
    if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL:
        raise SystemExit("COMPTE REEL detecte - ce test est reserve au DEMO. Abandon.")
    print(f"Compte #{info.login} | {info.server} | DEMO | balance={info.balance}")


def _normalize_volume(vol: float, vmin: float, vmax: float, step: float) -> float:
    """Normalise un volume : multiple de step, dans [vmin, vmax], sans erreur de float."""
    if step > 0:
        vol = int(vol / step + 1e-9) * step
        decimals = max(0, len(str(step).split(".")[-1])) if "." in str(step) else 0
        vol = round(vol, decimals)
    return min(vol, vmax)


def compute_volume(symbol: str, direction: str, balance: float, risk: float,
                   entry: float, sl: float) -> float:
    """Position sizing contraint par le risque (1%) ET la marge disponible.

    Retourne le volume final (0.0 = trade bloque). Ne modifie jamais entry/SL/TP.
    """
    sym = mt5.symbol_info(symbol)
    sl_distance = abs(entry - sl)
    if sym is None or sl_distance <= 0 or entry <= 0:
        print(f"POSITION SIZING FAILED | {symbol} | reason=INVALID_INPUTS")
        return 0.0

    risk_money = balance * risk
    ticks = sl_distance / sym.trade_tick_size
    loss_per_lot = ticks * sym.trade_tick_value
    if loss_per_lot <= 0:
        print(f"POSITION SIZING FAILED | {symbol} | reason=INVALID_TICK_DATA")
        return 0.0
    risk_volume = risk_money / loss_per_lot

    # --- Contrainte de marge ---
    acc = mt5.account_info()
    if acc is None:
        print(f"POSITION SIZING FAILED | {symbol} | reason=MARGIN_CALCULATION_FAILED")
        return 0.0
    free_margin = float(acc.margin_free)
    usable_margin = free_margin * MARGIN_SAFETY_FACTOR
    otype = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    margin_per_lot = mt5.order_calc_margin(otype, symbol, 1.0, entry)
    if margin_per_lot is None or margin_per_lot <= 0:
        print(f"POSITION SIZING FAILED | {symbol} | reason=MARGIN_CALCULATION_FAILED")
        return 0.0
    margin_max_volume = usable_margin / margin_per_lot

    # La marge ne peut que REDUIRE le volume, jamais l'augmenter
    vol = _normalize_volume(
        min(risk_volume, margin_max_volume, sym.volume_max),
        sym.volume_min, sym.volume_max, sym.volume_step,
    )
    required_margin = vol * margin_per_lot if vol > 0 else margin_per_lot * sym.volume_min

    # --- Cas impossible : meme volume_min depasse la marge utilisable ---
    if vol < sym.volume_min or required_margin > usable_margin:
        print(f"ORDER BLOCKED | {symbol} | {direction.upper()}")
        print("  reason=INSUFFICIENT_MARGIN")
        print(f"  symbol={symbol} direction={direction} "
              f"requested_volume={risk_volume:.2f} final_volume={vol:.2f} "
              f"minimum_volume={sym.volume_min}")
        print(f"  required_margin={required_margin:.2f} free_margin={free_margin:.2f} "
              f"usable_margin={usable_margin:.2f}")
        print(f"  risk_money={risk_money:.2f} risk_percent={risk * 100:.2f}% "
              f"entry={entry} stop_loss={sl}")
        return 0.0

    # --- Risque reel recalcule ---
    real_risk = vol * loss_per_lot
    print(f"POSITION SIZING | {symbol} | {direction.upper()}")
    print(f"  risk_percent={risk * 100:.2f}% risk_money={risk_money:.2f} "
          f"entry={entry} sl={sl} risk_distance={sl_distance}")
    print(f"  risk_based_volume={risk_volume:.2f} "
          f"margin_based_max_volume={margin_max_volume:.2f} final_volume={vol:.2f}")
    print(f"  required_margin={required_margin:.2f} free_margin={free_margin:.2f} "
          f"usable_margin={usable_margin:.2f} margin_safety_factor={MARGIN_SAFETY_FACTOR}")
    print(f"  real_risk={real_risk:.2f} "
          f"({'OK <= cible' if real_risk <= risk_money + 1e-6 else 'DEPASSE (volume_min)'})")
    return vol


def position_size(balance: float, risk: float, sl_distance: float, symbol: str) -> float:
    """Taille en lots via tick_value/tick_size du symbole, risque fixe (legacy, sans marge)."""
    sym = mt5.symbol_info(symbol)
    if sym is None or sl_distance <= 0:
        return 0.0
    risk_amount = balance * risk
    ticks = sl_distance / sym.trade_tick_size
    loss_per_lot = ticks * sym.trade_tick_value
    if loss_per_lot <= 0:
        return 0.0
    lots = risk_amount / loss_per_lot
    lots = max(sym.volume_min, min(lots, sym.volume_max))
    step = sym.volume_step
    if step > 0:
        lots = round(lots // step * step, 2)
    return lots


def fill_type(symbol: str) -> int:
    sym = mt5.symbol_info(symbol)
    if sym and sym.filling_mode == 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_FOK


def send_order(symbol: str, direction: str, lots: float, sl: float, tp: float) -> int | None:
    if symbol not in ALLOWED_SYMBOLS:
        print(f"SYMBOL BLOCKED | {symbol} | not in allowed symbols")
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    if direction == "buy":
        otype, price = mt5.ORDER_TYPE_BUY, tick.ask
    else:
        otype, price = mt5.ORDER_TYPE_SELL, tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": otype,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "Arty Adaptive demo",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_type(symbol),
    }
    res = mt5.order_send(req)
    if res is None:
        print(f"  ERREUR order_send None: {mt5.last_error()}")
        return None
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        if res.retcode == mt5.TRADE_RETCODE_NO_MONEY:
            print(f"ORDER REJECTED | {symbol} | reason=NO_MONEY | volume={lots} | "
                  f"free_margin={mt5.account_info().margin_free if mt5.account_info() else '?'}")
        print(f"  REJET retcode={res.retcode} comment={res.comment}")
        return None
    print(f"ORDER SENT | {symbol} | {('BUY' if otype == mt5.ORDER_TYPE_BUY else 'SELL')} | "
          f"volume={lots}")
    return res.order



async def scan_once(detector: SMCDetector, generator: SignalGenerator,
                    balance: float, risk: float, opened: list) -> bool:
    """Scanne tous les symboles ; ouvre au plus 1 trade par symbole.

    Retourne True si MAX_TRADES atteint.
    """
    for symbol in SYMBOLS:
        assert symbol in ALLOWED_SYMBOLS, f"Symbole non autorise: {symbol}"
        if len(opened) >= MAX_TRADES:
            return True
        if any(t[1] == symbol for t in opened) or mt5.positions_get(symbol=symbol):
            continue
        ltf = fetch_candles(symbol, "M5", 300)
        htf = fetch_candles(symbol, "H1", 100)
        if len(ltf) < 50 or len(htf) < 20:
            continue
        smc_ltf = await detector.detect(ltf, symbol)
        smc_htf = await detector.detect(htf, symbol)
        trend = derive_htf_trend(htf)
        print(f"--- {symbol} | H1 trend={trend} | SMC LTF={len(smc_ltf)} ---")

        signals = await generator.generate_all(
            ltf, smc_ltf, htf_smc_data=smc_htf, htf_trend=trend,
        )
        for sig in signals:
            conf, rr = sig.confidence, sig.risk_reward_ratio
            print(f"  signal | {sig.direction.value} | conf={conf:.2f} | RR={rr:.2f}")
            if conf >= 0.85:
                print("  -> deja eligible legacy (pas un trade 'adaptatif')")
            # Prix reel du marche comme entree pour le sizing (SL/TP du signal intouches)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                print("  -> pas de tick, ignore")
                continue
            entry = float(tick.ask if sig.direction.value == "buy" else tick.bid)
            lots = compute_volume(symbol, sig.direction.value, balance, risk,
                                  entry, float(sig.stop_loss))
            if lots <= 0:
                print("  -> trade bloque (sizing/marge), aucun ordre envoye")
                continue
            risk_amount = balance * risk
            print(f"  ORDRE DEMO | {symbol} {sig.direction.value} {lots} lots | "
                  f"SL={sig.stop_loss} TP={sig.take_profit} | conf={conf:.2f} RR={rr:.2f}")
            ticket = send_order(symbol, sig.direction.value, lots,
                                float(sig.stop_loss), float(sig.take_profit))
            if ticket is not None:
                print(f"  OK ordre envoye (order={ticket})")
                opened.append((ticket, symbol, sig.direction.value,
                               float(sig.stop_loss), float(sig.take_profit),
                               conf, rr, risk_amount))
                break  # 1 trade max par symbole


async def run_demo_test() -> None:
    settings = get_settings()
    connector = None
    try:
        from arty_trading.infrastructure.mt5.connector import MT5Connector

        connector = MT5Connector(settings=settings)
        if not await connector.connect():
            raise SystemExit("Connexion MT5 impossible.")
        ensure_demo()

        detector = SMCDetector()
        generator = SignalGenerator(min_confidence=0.85)

        info = mt5.account_info()
        balance = float(info.balance)
        risk = settings.risk.risk_per_trade
        opened: list[tuple] = []

        # === Phase 1 : scan en boucle jusqu'a trouver des trades ===
        deadline = time.time() + SCAN_WINDOW_MIN * 60
        scan = 0
        while time.time() < deadline and len(opened) < MAX_TRADES:
            scan += 1
            print(f"\n===== SCAN #{scan} | {time.strftime('%H:%M:%S')} | "
                  f"trades ouverts: {len(opened)}/{MAX_TRADES} =====")
            done = await scan_once(detector, generator, balance, risk, opened)
            if done or len(opened) >= MAX_TRADES:
                break
            remaining = int((deadline - time.time()) / 60)
            print(f"  aucun nouveau trade, re-scan dans {SCAN_INTERVAL_MIN} min "
                  f"(il reste ~{remaining} min)")
            time.sleep(SCAN_INTERVAL_MIN * 60)

        if not opened:
            print("\nAucun trade ouvert pendant la fenetre de scan. "
                  "Relancez le script plus tard (les setups dependent du marche).")
            return

        # === Phase 2 : surveillance jusqu'a cloture ===
        print(f"\nSurveillance de {len(opened)} position(s) pendant max "
              f"{MONITOR_TIMEOUT_S // 60} min...")
        pending = {t[0]: t for t in opened}
        results: list[tuple] = []
        start = time.time()
        while pending and time.time() - start < MONITOR_TIMEOUT_S:
            time.sleep(POLL_S)
            for ticket in list(pending):
                pos = mt5.positions_get(ticket=ticket)
                if not pos:
                    _, sym, d, sl, tp, conf, rr, risk_amt = pending[ticket]
                    deals = mt5.history_deals_get(position=ticket)
                    profit = (sum(dd.profit for dd in deals if dd.position_id == ticket)
                              if deals else 0.0)
                    r = profit / risk_amt if risk_amt > 0 else 0.0
                    results.append((sym, d, conf, rr, profit, r))
                    print(f"  CLOTURE {sym} {d} | profit={profit:.2f} USD | "
                          f"{r:+.2f}R | SL={sl} TP={tp}")
                    del pending[ticket]
            if pending:
                open_syms = ", ".join(f"{t[1]}#{t[0]}" for t in pending.values())
                print(f"  ...en cours: {open_syms} ({int(time.time() - start)}s)", flush=True)
        for ticket, t in pending.items():
            print(f"  TIMEOUT, position {t[1]}#{ticket} encore ouverte "
                  "(SL/TP restent actifs cote serveur)")

        # === Rapport final ===
        print("\n" + "=" * 70)
        print("  RESULTATS DEMO - POLITIQUE ADAPTATIVE CONFIANCE/RR")
        print("=" * 70)
        total_r = 0.0
        for sym, d, conf, rr, profit, r in results:
            tag = "ADAPTATIVE" if conf < 0.85 else "LEGACY"
            print(f"  {sym:8s} {d:4s} | conf={conf:.2f} RR={rr:.2f} [{tag}] | "
                  f"{profit:+8.2f} USD | {r:+.2f}R")
            total_r += r
        wins = sum(1 for x in results if x[5] > 0)
        print(f"\n  Trades clotures: {len(results)} | Gagnants: {wins} | Total: {total_r:+.2f}R")
        acct = mt5.account_info()
        if acct:
            print(f"  Solde final: {acct.balance} | Equity: {acct.equity}")
    finally:
        if connector is not None:
            await connector.disconnect()
            print("\nDeconnecte de MT5.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_demo_test())
