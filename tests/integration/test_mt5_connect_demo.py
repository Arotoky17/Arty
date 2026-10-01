"""Test de connexion MT5 au compte démo (avec timeout)."""
import sys
import threading
import time

import MetaTrader5 as mt5

PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
TIMEOUT = 30  # secondes


def _run():
    ok = mt5.initialize(PATH)
    if not ok:
        print("initialize: FAILED", mt5.last_error())
        return
    print("initialize: OK")
    # Laisser le terminal se connecter au serveur
    deadline = time.time() + 20
    while time.time() < deadline:
        info = mt5.terminal_info()
        if info is not None and info.connected:
            break
        time.sleep(1)
    info = mt5.terminal_info()
    acc = mt5.account_info()
    print("terminal connected:", info.connected if info else None)
    if acc is not None:
        print("login:", acc.login)
        print("server:", acc.server)
        print("trade_mode (0=demo,1=real):", acc.trade_mode)
        print("balance:", acc.balance)
        print("equity:", acc.equity)
    else:
        print("account_info: None", mt5.last_error())
    mt5.shutdown()


if __name__ == "__main__":
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=TIMEOUT)
    if t.is_alive():
        print("RESULT: TIMEOUT (terminal/serveur trop lent)")
        try:
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(2)
    else:
        print("RESULT: DONE")