"""Run an explicitly unscored live demo observation. Default execution is simulated."""

import argparse
import asyncio
import hashlib
import html
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from arty_trading.forward.observation import ObservationJournal, ObservationSession
from arty_trading.forward.observation_mt5 import ObservationMT5, infer_timestamp_offset


def render_state(state, output=None):
    text = json.dumps(state, indent=2, default=str, ensure_ascii=False)
    print(text, flush=True)
    if output:
        path = Path(output)
        raw = Path("data/raw").resolve()
        config = Path("config").resolve()
        if (
            path.suffix != ".html"
            or raw in path.resolve().parents
            or config in path.resolve().parents
        ):
            raise ValueError("HTML output requires .html outside data/raw and config")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            '<!doctype html><html lang="fr"><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="5"><title>Arty observation live</title>'
            "<style>body{background:#101820;color:#eee;font:16px monospace;padding:24px}"
            "pre{white-space:pre-wrap}h1{color:#ffcc66}</style>"
            "<h1>PLUMBING ONLY — aucune preuve, aucun trial</h1><pre>"
            + html.escape(text)
            + "</pre></html>",
            encoding="utf-8",
        )
        temporary.replace(path)


async def run(args, api):
    broker = ObservationMT5(
        api,
        send_demo_orders=args.send_demo_orders,
        expected_server=getattr(args, "expected_server", None),
        expected_login=getattr(args, "expected_login", None),
    )
    journal = None
    session = None
    try:
        account = broker.connect()
        print(f"Demo verifiee: {account.server}; capital 10000 USD; plumbing_only", flush=True)
        print("Verification UTC: attente de deux ticks frais distincts (60 s maximum).", flush=True)
        samples = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            tick = broker.tick()
            stamp = getattr(tick, "time_msc", tick.time * 1000) / 1000
            if not samples or stamp > samples[-1][0]:
                samples.append((stamp, datetime.now(UTC)))
            if len(samples) >= 2:
                broker.offset = infer_timestamp_offset(samples)
                break
            await asyncio.sleep(1)
        if broker.offset is None:
            raise RuntimeError("No advancing fresh ticks; UTC clock unverifiable; refused")
        journal = ObservationJournal(args.journal)
        session = ObservationSession(broker, journal)
        session.start()
        session.event(
            "entrypoint",
            file=__file__,
            sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        while True:
            state = await session.poll()
            render_state(state, args.html)
            if not session.kill.may_trade():
                break
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        if session:
            session.stop("user_interrupt")
            render_state(
                {
                    "plumbing_only": True,
                    "halt": "user_interrupt",
                    "action": "Verifier le journal et MT5 pour tout cleanup_failed",
                },
                args.html,
            )
        raise
    except Exception as error:
        if session:
            session.kill.on_feed(None, connected=False)
            session.stop(f"fatal:{type(error).__name__}:{error}")
            render_state(
                {
                    "plumbing_only": True,
                    "halt": str(error),
                    "action": "Consulter observation_events, y compris cleanup_failed",
                },
                args.html,
            )
        raise
    finally:
        if journal:
            journal.close()
        broker.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-demo-orders",
        action="store_true",
        help="Mode B: permit actual pending orders on technically verified DEMO only",
    )
    parser.add_argument("--expected-server", required=True, help="Exact MT5 demo server name")
    parser.add_argument("--expected-login", type=int, required=True, help="Exact MT5 account login")
    parser.add_argument("--journal", default="reports/observation_live/observation.sqlite")
    parser.add_argument("--html", help="Optional local auto-refresh HTML snapshot; no HTTP server")
    args = parser.parse_args()
    # Imported only upon explicit CLI invocation. Tests supply a fake API to run().
    import MetaTrader5 as mt5  # noqa: N813

    try:
        asyncio.run(run(args, mt5))
    except KeyboardInterrupt:
        print("Observation arretee. Verifier le journal et le terminal pour tout cleanup_failed.")
    except Exception as error:
        parser.exit(2, f"Observation refusee/arretee: {error}\n")


if __name__ == "__main__":
    main()
