"""Forward demo on a live DEMO account. Never sends an order by default.

Subcommands:
  manifest      freeze code/config/cost/fill/preregistration into a run manifest
  verify        re-check the freeze; any change invalidates the run
  preflight     verify the broker demo account and print the go/no-go state
  report        weekly and monthly reports from the SQLite journal
  run           start the demo session; REFUSES without validated approval
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from arty_trading.config.operational import load_config
from arty_trading.forward.account import NotADemoAccountError, verify_demo_account
from arty_trading.forward.freeze import (
    PreregistrationUnsignedError,
    RunFreeze,
    RunInvalidatedError,
    assert_forward_preregistration,
    forward_setup_id,
    is_preregistration_signed,
    read_manifest,
    sha256_file,
    verify_freeze,
    write_manifest,
)
from arty_trading.forward.journal import ForwardJournal
from arty_trading.forward.reporting import build_report, write_report

APPROVAL_FLAG = "--i-approve-forward-demo"


def _manifest_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("forward_demo.yaml")
    return Path(cfg["paths"]["manifest"])


def _raw_account() -> Any:
    """Broker payload. The only place the forward demo touches MetaTrader5."""
    import MetaTrader5 as mt5  # noqa: N813 - broker module convention

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        return mt5.account_info()
    finally:
        mt5.shutdown()


def cmd_manifest(_: argparse.Namespace) -> int:
    freeze = RunFreeze.capture()
    path = write_manifest(freeze, _manifest_path())
    print(json.dumps(freeze.as_dict(), indent=2, sort_keys=True))
    print(f"\nManifest written to {path}")
    print(f"Manifest sha256: {sha256_file(path)}")
    print("No order has been sent. Review it, then validate it explicitly.")
    return 0


def cmd_verify(_: argparse.Namespace) -> int:
    path = _manifest_path()
    if not path.exists():
        print(f"NO MANIFEST at {path}; run 'manifest' first.")
        return 2
    try:
        verify_freeze(read_manifest(path))
    except RunInvalidatedError as error:
        print(f"RUN INVALIDATED: {error}")
        return 3
    print("Freeze intact: code, configs, cost model, fill model and preregistration.")
    return 0


def cmd_preflight(_: argparse.Namespace) -> int:
    cfg = load_config("forward_demo.yaml")
    report: dict[str, Any] = {
        "setup_id": forward_setup_id(),
        "preregistration_signed": is_preregistration_signed(),
        "manifest_exists": _manifest_path().exists(),
        "broker_account": None,
        "may_start": False,
        "blockers": [],
    }
    try:
        assert_forward_preregistration(load_config("setup1_preregistration.yaml")["setup_id"])
    except (PreregistrationUnsignedError, PermissionError) as error:
        report["blockers"].append(f"preregistration: {error}")
    if not report["manifest_exists"]:
        report["blockers"].append("manifest: missing, run 'manifest' first")
    try:
        report["broker_account"] = verify_demo_account(_raw_account(), cfg["account"]).as_dict()
    except NotADemoAccountError as error:
        report["blockers"].append(f"account: {error}")
    except Exception as error:  # noqa: BLE001 - no terminal or broker available
        report["blockers"].append(f"account: broker unavailable ({type(error).__name__})")
    report["may_start"] = not report["blockers"]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["may_start"] else 2


def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config("forward_demo.yaml")
    journal = ForwardJournal(cfg["paths"]["journal"])
    trades = journal.trades(args.run_id)
    signals = journal.signals(args.run_id)
    if not trades and not signals:
        print(f"No journal rows for run {args.run_id}.")
        return 2
    for period in cfg["reporting"]["periods"]:
        report = build_report(trades, signals, period, cfg["reporting"])
        path = write_report(
            report, Path(cfg["paths"]["reports"]), f"forward_{period}_{args.run_id}.json"
        )
        overall = report["overall"]
        print(
            f"[{period}] trades={overall['trades']} "
            f"expectancy_net_r={overall['expectancy_net_r']} "
            f"PF={overall['profit_factor_net']} fill_rate={overall['fill_rate']} "
            f"verdict={overall['verdict']} ({overall['verdict_status']}) -> {path}"
        )
    small = journal.small_account_rows(args.run_id)
    if small:
        print(f"small_account decisions (reported separately): {len(small)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Refuse to start unless the manifest, preregistration and account are valid."""
    cfg = load_config("forward_demo.yaml")
    path = _manifest_path()
    if not path.exists():
        print(f"REFUSED: no manifest at {path}.")
        return 2
    try:
        verify_freeze(read_manifest(path))
        assert_forward_preregistration(load_config("setup1_preregistration.yaml")["setup_id"])
    except (RunInvalidatedError, PreregistrationUnsignedError, PermissionError) as error:
        print(f"REFUSED: {error}")
        return 3
    try:
        verification = verify_demo_account(_raw_account(), cfg["account"])
    except Exception as error:  # noqa: BLE001
        print(f"REFUSED: broker demo verification failed: {error}")
        return 4
    if not args.execute or APPROVAL_FLAG not in sys.argv:
        print(
            "REFUSED (dry-run). No order sent. To start a demo session you must:\n"
            "  1. validate reports/forward_demo/run_manifest.json,\n"
            "  2. sign preregistration.md,\n"
            f"  3. pass {APPROVAL_FLAG} explicitly."
        )
        print(json.dumps(verification.as_dict(), indent=2, sort_keys=True))
        return 0
    print(
        "Forward demo session authorised on a verified demo account; "
        "see docs/forward_demo.md for the live loop wiring."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("manifest", cmd_manifest),
        ("verify", cmd_verify),
        ("preflight", cmd_preflight),
    ):
        sub.add_parser(name).set_defaults(handler=handler)
    report = sub.add_parser("report")
    report.add_argument("--run-id", required=True)
    report.set_defaults(handler=cmd_report)
    run = sub.add_parser("run")
    run.add_argument("--execute", action="store_true", help="Still refuses without approval flag")
    run.set_defaults(handler=cmd_run)
    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
