"""
Test live MT5 avec stratégies SMC - Trade basé sur l'analyse Smart Money.

Ce script :
1. Se connecte à MT5 (compte démo)
2. Récupère les bougies H1 EURUSD (200 dernières)
3. Lance le moteur SMC (BOS, CHoCH, FVG, Order Blocks, etc.)
4. Génère un signal avec les stratégies (SMC Trend, Breakout, etc.)
5. Calcule la taille de position selon le solde et le risque (1%)
6. Exécute le trade avec le SL/TP du signal
7. Surveille et ferme la position

⚠️  Ce script utilise un compte DÉMO uniquement.
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, "src")

import MetaTrader5 as mt5
from arty_trading.config.settings import get_settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame, SignalType
from arty_trading.infrastructure.mt5.connector import MT5Connector
from arty_trading.modules.smc.detector import SMCDetector
from arty_trading.modules.signals.generator import SignalGenerator


def get_candles_from_mt5(symbol: str, timeframe: str, count: int = 200) -> list[Candle]:
    """
    Récupère les bougies depuis MT5 et les convertit en objets Candle.

    Args:
        symbol: Symbole (ex: EURUSD)
        timeframe: Timeframe MT5 (ex: mt5.TIMEFRAME_H1)
        count: Nombre de bougies à récupérer

    Returns:
        Liste d'objets Candle (du plus ancien au plus récent)
    """
    # Mapping string -> mt5 timeframe
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)

    # Récupérer les bougies
    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
    if rates is None or len(rates) == 0:
        return []

    # Convertir en objets Candle
    candles = []
    tf_enum = TimeFrame(timeframe)
    for rate in rates:
        try:
            spread_val = int(rate["spread"])
        except (ValueError, KeyError, TypeError):
            spread_val = 0
        candle = Candle(
            symbol=symbol,
            timeframe=tf_enum,
            time=datetime.fromtimestamp(rate["time"], tz=timezone.utc),
            open=Decimal(str(rate["open"])),
            high=Decimal(str(rate["high"])),
            low=Decimal(str(rate["low"])),
            close=Decimal(str(rate["close"])),
            volume=int(rate["tick_volume"]),
            spread=spread_val,
        )
        candles.append(candle)

    return candles


def calculate_position_size(balance: float, risk_per_trade: float, sl_distance: float, symbol: str = "EURUSD") -> float:
    """
    Calcule la taille de position basée sur le solde et le risque.

    Args:
        balance: Solde du compte en USD
        risk_per_trade: Risque par trade (0.01 = 1%)
        sl_distance: Distance du SL en prix (ex: 0.0020 pour 20 pips EURUSD)
        symbol: Symbole pour déterminer la taille du contrat

    Returns:
        Taille de position en lots (arrondie à 0.01)
    """
    # Montant à risquer
    risk_amount = balance * risk_per_trade

    # Taille du contrat (100,000 unités pour EURUSD)
    contract_size = 100_000

    # Valeur du pip pour 1 lot
    # Pour EURUSD: 1 pip = 0.0001, 1 lot = 100,000 units
    # Valeur du pip = 0.0001 * 100,000 = 10 USD par pip
    pip_value_per_lot = sl_distance * contract_size

    if pip_value_per_lot == 0:
        return 0.01

    # Taille de position
    position_size = risk_amount / pip_value_per_lot

    # Arrondir à 0.01 (minimum 0.01)
    position_size = round(position_size, 2)
    if position_size < 0.01:
        position_size = 0.01
    if position_size > 5.0:
        position_size = 5.0  # Cap à 5 lots pour sécurité

    return position_size


async def main():
    print("=" * 60)
    print("  ARTY - Trade SMC Live (Compte Démo)")
    print("=" * 60)

    # =================================================================
    # 1. Charger la configuration
    # =================================================================
    settings = get_settings()
    print(f"\n📋 Configuration :")
    print(f"   Login         : {settings.mt5.login}")
    print(f"   Server        : {settings.mt5.server}")
    print(f"   Mode          : {settings.trading_mode.value}")
    print(f"   Risk/trade    : {settings.risk.risk_per_trade} ({settings.risk.risk_per_trade*100}%)")
    print(f"   Max positions : {settings.risk.max_open_positions}")

    # =================================================================
    # 2. Se connecter à MT5
    # =================================================================
    print(f"\n🔌 Connexion à MT5...")
    connector = MT5Connector(settings=settings)
    connected = await connector.connect()

    if not connected:
        print("❌ Échec de connexion à MT5 !")
        return

    print("✅ Connexion réussie !")

    # =================================================================
    # 3. Afficher les infos du compte
    # =================================================================
    account = await connector.get_account_info()
    balance = float(account.balance)
    print(f"\n📊 Compte MT5 :")
    print(f"   Login    : {account.login}")
    print(f"   Solde    : {balance} USD")
    print(f"   Equity   : {account.equity} USD")
    print(f"   Mode     : {account.mode.value.upper()}")

    if not account.is_demo:
        print("\n⚠️  ATTENTION : Ce n'est pas un compte démo !")
        await connector.disconnect()
        return

    # =================================================================
    # 4. Récupérer les bougies H1 EURUSD
    # =================================================================
    symbol = "EURUSD"
    timeframe = "H1"
    print(f"\n📈 Récupération des bougies {symbol} {timeframe}...")

    candles = get_candles_from_mt5(symbol, timeframe, count=200)
    if not candles:
        print("❌ Impossible de récupérer les bougies")
        await connector.disconnect()
        return

    print(f"✅ {len(candles)} bougies récupérées")
    last_candle = candles[-1]
    print(f"   Dernière bougie : {last_candle.time}")
    print(f"   Open  : {last_candle.open}")
    print(f"   High  : {last_candle.high}")
    print(f"   Low   : {last_candle.low}")
    print(f"   Close : {last_candle.close}")

    # =================================================================
    # 5. Lancer le moteur SMC
    # =================================================================
    print(f"\n🔍 Analyse SMC en cours...")
    smc_detector = SMCDetector()
    smc_detector.enable_all()

    smc_detections = await smc_detector.detect(candles, symbol)
    print(f"✅ {len(smc_detections)} détections SMC trouvées")

    # Afficher les détections par type
    concepts_found = {}
    for d in smc_detections:
        concept = d.get("concept", "unknown")
        if concept not in concepts_found:
            concepts_found[concept] = 0
        concepts_found[concept] += 1

    print(f"\n📋 Concepts SMC détectés :")
    for concept, count in concepts_found.items():
        print(f"   {concept:30s} : {count}")

    # Afficher les dernières détections
    if smc_detections:
        print(f"\n📌 Dernières détections :")
        for d in smc_detections[-5:]:
            print(f"   {d.get('concept', '?'):20s} | {d.get('direction', '?'):10s} | prix={d.get('price', '?')}")

    # =================================================================
    # 6. Générer un signal
    # =================================================================
    print(f"\n🎯 Génération du signal...")
    signal_generator = SignalGenerator(min_confidence=0.3)
    signal_generator.enable_all()

    signal = await signal_generator.generate(candles, smc_detections)

    if signal is None:
        print("❌ Aucun signal généré (aucune stratégie n'a trouvé d'opportunité)")
        print("   Cela peut arriver si le marché n'a pas de setup clair.")
        print("   Réessayez plus tard ou changez de timeframe.")

        # Créer un signal manuel basé sur la tendance
        print("\n🔧 Création d'un signal basé sur la tendance récente...")
        recent_candles = candles[-20:]
        bullish = sum(1 for c in recent_candles if c.is_bullish)
        bearish = len(recent_candles) - bullish

        if bullish > bearish:
            direction = "buy"
            entry = float(last_candle.close)
            sl = entry - 0.0030  # 30 pips SL
            tp = entry + 0.0060  # 60 pips TP (1:2 RR)
            print(f"   Tendance haussière détectée ({bullish}/{len(recent_candles)} bougies haussières)")
        else:
            direction = "sell"
            entry = float(last_candle.close)
            sl = entry + 0.0030  # 30 pips SL
            tp = entry - 0.0060  # 60 pips TP (1:2 RR)
            print(f"   Tendance baissière détectée ({bearish}/{len(recent_candles)} bougies baissières)")

        confidence = 0.5
        strategy_name = "Manual Trend"
        smc_concepts_str = ", ".join(list(concepts_found.keys())[:5])
    else:
        print(f"✅ Signal généré !")
        print(f"   Stratégie    : {signal.strategy_name}")
        print(f"   Direction    : {signal.direction.value}")
        print(f"   Entrée       : {signal.entry_price}")
        print(f"   Stop Loss    : {signal.stop_loss}")
        print(f"   Take Profit  : {signal.take_profit}")
        print(f"   Confiance    : {signal.confidence:.2f}/1.0")
        print(f"   Ratio R/R    : {signal.risk_reward_ratio}")
        print(f"   Justification: {signal.justification[:100]}...")
        print(f"   Concepts SMC : {signal.smc_concepts}")

        direction = signal.direction.value
        entry = float(signal.entry_price)
        sl = float(signal.stop_loss)
        tp = float(signal.take_profit)
        confidence = signal.confidence
        strategy_name = signal.strategy_name
        smc_concepts_str = ", ".join(signal.smc_concepts) if signal.smc_concepts else "N/A"

    # =================================================================
    # 7. Calculer la taille de position
    # =================================================================
    sl_distance = abs(entry - sl)
    position_size = calculate_position_size(
        balance=balance,
        risk_per_trade=settings.risk.risk_per_trade,
        sl_distance=sl_distance,
        symbol=symbol,
    )

    risk_amount = balance * settings.risk.risk_per_trade
    potential_profit = abs(tp - entry) / sl_distance * risk_amount if sl_distance > 0 else 0

    print(f"\n💰 Gestion du risque :")
    print(f"   Solde          : {balance} USD")
    print(f"   Risque/trade   : {settings.risk.risk_per_trade*100}% = {risk_amount:.2f} USD")
    print(f"   SL distance    : {sl_distance:.5f} ({sl_distance/0.0001:.0f} pips)")
    print(f"   TP distance    : {abs(tp-entry):.5f} ({abs(tp-entry)/0.0001:.0f} pips)")
    print(f"   Taille position: {position_size} lots")
    print(f"   Profit potentiel: {potential_profit:.2f} USD")
    print(f"   Ratio R/R      : 1:{abs(tp-entry)/sl_distance:.2f}" if sl_distance > 0 else "   Ratio R/R: N/A")

    # =================================================================
    # 8. Vérifier qu'il n'y a pas déjà une position ouverte
    # =================================================================
    existing_positions = mt5.positions_get(symbol=symbol)
    if existing_positions and len(existing_positions) > 0:
        print(f"\n⚠️  Il y a déjà {len(existing_positions)} position(s) ouverte(s) sur {symbol}")
        print("   Fermeture des positions existantes...")
        for pos in existing_positions:
            tick = mt5.symbol_info_tick(symbol)
            if pos.type == mt5.POSITION_TYPE_BUY:
                close_price = tick.bid
                close_type = mt5.ORDER_TYPE_SELL
            else:
                close_price = tick.ask
                close_type = mt5.ORDER_TYPE_BUY

            # Détecter le filling mode
            symbol_info = mt5.symbol_info(symbol)
            fill_type = mt5.ORDER_FILLING_FOK
            if symbol_info and symbol_info.filling_mode == 2:
                fill_type = mt5.ORDER_FILLING_IOC

            close_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": close_price,
                "deviation": 20,
                "magic": 234000,
                "comment": "Arty Close Existing",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill_type,
            }
            mt5.order_send(close_req)
            print(f"   Position {pos.ticket} fermée")

    # =================================================================
    # 9. Exécuter le trade
    # =================================================================
    print(f"\n🟢 Exécution du trade...")
    print(f"   Symbole    : {symbol}")
    print(f"   Direction  : {direction.upper()}")
    print(f"   Volume     : {position_size} lots")
    print(f"   Entrée     : {entry}")
    print(f"   SL         : {sl}")
    print(f"   TP         : {tp}")
    print(f"   Stratégie  : {strategy_name}")
    print(f"   Confiance  : {confidence:.2f}")

    # Récupérer le filling mode
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is not None:
        filling_mode = symbol_info.filling_mode
        if filling_mode == 1:
            fill_type = mt5.ORDER_FILLING_FOK
        elif filling_mode == 2:
            fill_type = mt5.ORDER_FILLING_IOC
        else:
            fill_type = mt5.ORDER_FILLING_FOK
    else:
        fill_type = mt5.ORDER_FILLING_FOK

    # Récupérer le prix actuel
    tick = mt5.symbol_info_tick(symbol)
    if direction == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        order_price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        order_price = tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position_size,
        "type": order_type,
        "price": order_price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 234000,
        "comment": f"Arty {strategy_name[:20]}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_type,
    }

    result = mt5.order_send(request)

    if result is None:
        print(f"❌ Échec de l'envoi de l'ordre : {mt5.last_error()}")
        await connector.disconnect()
        return

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ Ordre rejeté : retcode={result.retcode}")
        print(f"   Commentaire : {result.comment}")
        await connector.disconnect()
        return

    ticket = result.order
    print(f"✅ Trade ouvert ! Ticket : {ticket}")

    # =================================================================
    # 10. Afficher la position
    # =================================================================
    print(f"\n📋 Position ouverte :")
    positions = mt5.positions_get(symbol=symbol)
    if positions:
        for pos in positions:
            print(f"   Ticket  : {pos.ticket}")
            print(f"   Symbole : {pos.symbol}")
            print(f"   Type    : {'BUY' if pos.type == mt5.POSITION_TYPE_BUY else 'SELL'}")
            print(f"   Volume  : {pos.volume}")
            print(f"   Prix    : {pos.price_open}")
            print(f"   SL      : {pos.sl}")
            print(f"   TP      : {pos.tp}")
            print(f"   Profit  : {pos.profit}")
    else:
        print("   Aucune position (peut-être déjà fermée par SL/TP)")

    # =================================================================
    # 11. Surveiller la position (30 secondes)
    # =================================================================
    print(f"\n⏳ Surveillance de la position (30 secondes)...")
    for i in range(30, 0, -1):
        pos = mt5.positions_get(ticket=ticket)
        if pos is None or len(pos) == 0:
            print(f"\n✅ Position fermée (SL ou TP atteint) !")
            # Vérifier le résultat
            deals = mt5.history_deals_get(ticket=ticket)
            if deals:
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        print(f"   Prix de sortie : {deal.price}")
                        print(f"   Profit/Perte   : {deal.profit}")
            break

        current_pos = pos[0]
        current_tick = mt5.symbol_info_tick(symbol)
        if current_pos.type == mt5.POSITION_TYPE_BUY:
            current_profit = (current_tick.bid - current_pos.price_open) * current_pos.volume * 100_000
        else:
            current_profit = (current_pos.price_open - current_tick.ask) * current_pos.volume * 100_000

        print(f"   {i:2d}s | Profit: {current_pos.profit:.2f} USD | Bid: {current_tick.bid} | Ask: {current_tick.ask}", end="\r", flush=True)
        time.sleep(1)

    # =================================================================
    # 12. Fermer la position si encore ouverte
    # =================================================================
    print("\n")
    pos = mt5.positions_get(ticket=ticket)
    if pos is not None and len(pos) > 0:
        print(f"🔴 Fermeture du trade {ticket}...")
        current_pos = pos[0]
        tick_close = mt5.symbol_info_tick(symbol)

        if current_pos.type == mt5.POSITION_TYPE_BUY:
            close_price = tick_close.bid
            close_type = mt5.ORDER_TYPE_SELL
        else:
            close_price = tick_close.ask
            close_type = mt5.ORDER_TYPE_BUY

        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": current_pos.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 234000,
            "comment": "Arty Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": fill_type,
        }

        close_result = mt5.order_send(close_request)

        if close_result is None:
            print(f"❌ Échec de la fermeture : {mt5.last_error()}")
        elif close_result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Fermeture rejetée : retcode={close_result.retcode}")
            print(f"   Commentaire : {close_result.comment}")
        else:
            print(f"✅ Trade fermé !")
            print(f"   Prix de fermeture : {close_result.price}")
            print(f"   Profit/Perte      : {current_pos.profit:.2f} USD")

    # =================================================================
    # 13. Afficher le compte final
    # =================================================================
    account_final = await connector.get_account_info()
    print(f"\n📊 Compte final :")
    print(f"   Solde    : {account_final.balance} USD")
    print(f"   Equity   : {account_final.equity} USD")

    # =================================================================
    # 14. Déconnexion
    # =================================================================
    await connector.disconnect()
    print(f"\n🔌 Déconnecté de MT5.")

    print("\n" + "=" * 60)
    print("  ✅ Trade SMC terminé !")
    print("=" * 60)
    print(f"\nStratégie utilisée : {strategy_name}")
    print(f"Concepts SMC détectés : {smc_concepts_str}")
    print(f"Confiance du signal : {confidence:.2f}/1.0")
    print(f"Taille de position : {position_size} lots (risque {settings.risk.risk_per_trade*100}%)")


if __name__ == "__main__":
    asyncio.run(main())