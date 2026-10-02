"""Read-only, credential-free MT5 connection diagnostic."""

from typing import Any


def diagnose_mt5(terminal_path: str, client: Any) -> dict[str, Any]:
    initialized = bool(client.initialize(terminal_path, timeout=10000))
    result: dict[str, Any] = {"terminal_path": terminal_path, "initialized": initialized}
    if not initialized:
        result["error"] = list(client.last_error())
        return result
    try:
        info = client.terminal_info()
        result["connected"] = bool(info and info.connected)
        result["maxbars"] = int(info.maxbars) if info else None
        result["error"] = list(client.last_error())
    finally:
        client.shutdown()
    return result
