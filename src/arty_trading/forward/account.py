"""Strict, technical demo-account verification.

The existing ``TradingAccount.is_demo`` is ``mode != LIVE``: a real account
forced to PAPER would satisfy it. The forward demo therefore refuses to start
unless the broker itself reports a demo account through ``trade_mode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arty_trading.config.operational import load_config

# MetaTrader5 ACCOUNT_TRADE_MODE: 0 demo, 1 contest, 2 real (broker dependent).
BROKER_TRADE_MODE_DEMO = 0


class NotADemoAccountError(RuntimeError):
    """Raised when the broker does not report a demo account."""


@dataclass(frozen=True)
class AccountVerification:
    login: int
    server: str
    broker_trade_mode: int
    broker_account_type: str
    currency: str
    balance: float
    equity: float
    leverage: int
    verified_demo: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "server": self.server,
            "broker_trade_mode": self.broker_trade_mode,
            "broker_account_type": self.broker_account_type,
            "currency": self.currency,
            "balance": self.balance,
            "equity": self.equity,
            "leverage": self.leverage,
            "verified_demo": self.verified_demo,
        }


def _account_type_name(trade_mode: int) -> str:
    return {0: "demo", 1: "contest", 2: "real"}.get(trade_mode, "unknown")


def verify_demo_account(raw_account: Any, cfg: dict[str, Any] | None = None) -> AccountVerification:
    """Verify the demo account from the broker payload, never from a config flag.

    ``raw_account`` is the object returned by ``mt5.account_info()``. The run is
    refused unless ``trade_mode`` is exactly the accepted demo value.
    """
    cfg = cfg if cfg is not None else load_config("forward_demo.yaml")["account"]
    if raw_account is None:
        raise NotADemoAccountError("Broker returned no account information")
    trade_mode = getattr(raw_account, "trade_mode", None)
    if trade_mode is None:
        raise NotADemoAccountError("Broker account payload exposes no trade_mode; refusing")
    try:
        trade_mode = int(trade_mode)
    except (TypeError, ValueError):
        raise NotADemoAccountError(
            f"Broker trade_mode={trade_mode!r} is not an integer; refusing"
        ) from None
    # The broker decides: only the technical demo value MT5 reports (0) is accepted.
    # Configuration only documents the expectation; it can never widen acceptance.
    if trade_mode != BROKER_TRADE_MODE_DEMO:
        raise NotADemoAccountError(
            f"Broker reports trade_mode={trade_mode} "
            f"({_account_type_name(trade_mode)}); forward demo refuses to start"
        )
    return AccountVerification(
        login=int(getattr(raw_account, "login", 0)),
        server=str(getattr(raw_account, "server", "")),
        broker_trade_mode=trade_mode,
        broker_account_type=_account_type_name(trade_mode),
        currency=str(getattr(raw_account, "currency", "USD")),
        balance=float(getattr(raw_account, "balance", 0.0)),
        equity=float(getattr(raw_account, "equity", 0.0)),
        leverage=int(getattr(raw_account, "leverage", 0)),
        verified_demo=True,
    )
