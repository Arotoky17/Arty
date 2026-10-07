"""Forward test on a live DEMO account, in complement to the backtest.

One strategy implementation, two execution modes. This package never duplicates
detection logic: it reuses the shared detectors, ``config/definitions.yaml`` and
the setup preregistration, and only adds the operational policy that a demo
account requires (version freeze, account verification, kill switch, journal,
reconciliation, reporting and broker audit).

No order is ever sent by this package without an explicit, signed preregistration
and a validated run manifest.
"""

from __future__ import annotations

__all__ = ["config", "freeze", "account", "kill_switch", "sizing", "journal"]
