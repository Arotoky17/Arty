"""
Test live MT5 - Connexion, ouverture et fermeture d'un trade sur le compte démo.

Ce script :
1. Se connecte à MT5 avec les identifiants du .env
2. Affiche les infos du compte
3. Ouvre un trade de test (0.01 lot EURUSD BUY)
4. Affiche la position ouverte
5. Attend 5 secondes
6. Ferme la position
7. Affiche le résultat

⚠️  Ce script utilise un compte DÉMO uniquement.
"""
import asyncio
import sys
import time

sys.path.insert(0, "src")

import MetaTrader5 as mt5
from arty_trading.config.settings import get_settings
from arty_trading.infrastructure.mt5.connector import MT5Connector


async def main():
    print("=" * 60)
    print("  ARTY - Test Live MT5 (Compte Démo)")
    print("=" * 60)

    # =================================================================
    # 1. Charger la configuration
    # =================================================================
    settings = get_settings()
    print(f"\n📋 Configuration :")
    print(f"   Login    : {settings.mt5.login}")
    print(f"   Server   : {settings.mt5.server}")
    print(f"   Mode     : {settings.trading_mode.value}")
    print(f"   Live     : {settings.is_live_trading_enabled}")

    # =================================================================
    # 2. Se connecter à MT5
    # =================================================================
    print(f"\n🔌 Connexion à MT5...")
    connector = MT5Connector(settings=settings)
    connected = await connector.connect()

    if not connected:
        print("❌ Échec de connexion à MT5 !")
        print("   Vérifiez que :")
        print("   1. MetaTrader 5 Terminal est installé et lancé")
        print("   2. Les identifiants dans .env sont corrects")
        print("   3. Le compte est un compte démo valide")
        return

    print("✅ Connexion réussie !")

    # =================================================================
    # 3. Afficher les infos du compte
    # =================================================================
    account = await connector.get_account_info()
    print(f"\n📊 Compte MT5 :")
    print(f"   Login    : {account.login}")
    print(f"   Server   : {account.server}")
    print(f"   Solde    : {account.balance} USD")
    print(f"   Equity   : {account.equity} USD")
    print(f"   Mode     : {account.mode.value.upper()}")

    if account.mode.value != "demo":
        print("\n⚠️  ATTENTION : Ce n'est pas un compte démo !")
        print("   Le script s'arrête pour sécurité.")
        await connector.disconnect()
        return

    # =================================================================
    # 4. Récupérer le tick EURUSD
    # =================================================================
    symbol = "EURUSD"
    print(f"\n📈 Récupération du tick {symbol}...")
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        print(f"   Bid  : {tick.bid}")
        print(f"   Ask  : {tick.ask}")
        ask_price = tick.ask
    else:
        print("❌ Impossible de récupérer le tick EURUSD")
        await connector.disconnect()
        return

    # =================================================================
    # 5. Détecter le mode de remplissage supporté
    # =================================================================
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is not None:
        filling_mode = symbol_info.filling_mode
        print(f"\n📋 Filling mode supporté : {filling_mode}")
        # filling_mode: 1=FOK, 2=IOC, 3=les deux
        if filling_mode == 1:
            fill_type = mt5.ORDER_FILLING_FOK
        elif filling_mode == 2:
            fill_type = mt5.ORDER_FILLING_IOC
        else:
            fill_type = mt5.ORDER_FILLING_FOK
    else:
        fill_type = mt5.ORDER_FILLING_FOK
    print(f"   Filling type utilisé   : {fill_type}")

    # =================================================================
    # 6. Ouvrir un trade de test (0.01 lot BUY)
    # =================================================================
    print(f"\n🟢 Ouverture d'un trade de test...")
    print(f"   Symbole  : {symbol}")
    print(f"   Direction : BUY")
    print(f"   Volume   : 0.01 lot")
    print(f"   Prix     : {ask_price}")

    # Calculer SL et TP (20 pips SL, 40 pips TP)
    sl_price = ask_price - 0.0020  # 20 pips below
    tp_price = ask_price + 0.0040  # 40 pips above
    print(f"   SL       : {sl_price:.5f}")
    print(f"   TP       : {tp_price:.5f}")

    # Créer la requête d'ordre
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": 0.01,
        "type": mt5.ORDER_TYPE_BUY,
        "price": ask_price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": 20,
        "magic": 234000,
        "comment": "Arty Test Live",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_type,
    }

    # Envoyer l'ordre
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
    # 7. Afficher les positions ouvertes
    # =================================================================
    print(f"\n📋 Positions ouvertes :")
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
        print("   Aucune position ouverte")

    # =================================================================
    # 8. Attendre 5 secondes
    # =================================================================
    print(f"\n⏳ Attente de 5 secondes...")
    for i in range(5, 0, -1):
        print(f"   {i}...", end="\r", flush=True)
        time.sleep(1)
    print("   Terminé !")

    # =================================================================
    # 9. Fermer la position
    # =================================================================
    print(f"\n🔴 Fermeture du trade {ticket}...")

    # Récupérer la position
    position = mt5.positions_get(ticket=ticket)
    if position is None or len(position) == 0:
        print(f"❌ Position {ticket} introuvable (peut-être déjà fermée par SL/TP)")
        await connector.disconnect()
        return

    pos = position[0]
    tick_close = mt5.symbol_info_tick(symbol)
    if pos.type == mt5.POSITION_TYPE_BUY:
        close_price = tick_close.bid
        close_type = mt5.ORDER_TYPE_SELL
    else:
        close_price = tick_close.ask
        close_type = mt5.ORDER_TYPE_BUY

    close_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,
        "price": close_price,
        "deviation": 20,
        "magic": 234000,
        "comment": "Arty Test Close",
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
        print(f"   Profit/Perte      : {pos.profit}")

    # =================================================================
    # 10. Afficher le compte final
    # =================================================================
    account_final = await connector.get_account_info()
    print(f"\n📊 Compte final :")
    print(f"   Solde    : {account_final.balance} USD")
    print(f"   Equity   : {account_final.equity} USD")

    # =================================================================
    # 11. Déconnexion
    # =================================================================
    await connector.disconnect()
    print(f"\n🔌 Déconnecté de MT5.")

    print("\n" + "=" * 60)
    print("  ✅ Test live terminé avec succès !")
    print("=" * 60)
    print("\nLe projet Arty fonctionne correctement avec MT5.")
    print("Le trade a été ouvert et fermé sur le compte démo.")


if __name__ == "__main__":
    asyncio.run(main())