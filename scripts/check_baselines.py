"""Reject new lint/type errors; explicitly synchronize resolved baseline entries."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def run_tool(arguments: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *arguments],
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def ruff_diagnostics() -> list[dict[str, str]]:
    result = run_tool(["ruff", "check", ".", "--output-format=json", "--no-cache"])
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr or result.stdout)
    diagnostics: list[dict[str, str]] = []
    for entry in json.loads(result.stdout):
        path = Path(entry["filename"])
        lines = path.read_text(encoding="utf-8").splitlines()
        start = entry["location"]["row"] - 1
        end = entry["end_location"]["row"]
        diagnostics.append(
            {
                "file": path.relative_to(ROOT).as_posix(),
                "code": entry["code"],
                "message": entry["message"],
                "source": "\n".join(lines[start:end]),
            }
        )
    return sorted(diagnostics, key=lambda entry: json.dumps(entry, sort_keys=True))


def fingerprints(entries: list[dict[str, str]]) -> Counter[str]:
    """Count duplicates; an additional identical violation is still a regression."""
    return Counter(json.dumps(entry, sort_keys=True) for entry in entries)


def check_ruff(baseline: Path, *, fresh: bool = False, sync: bool = False) -> int:
    current = ruff_diagnostics()
    if not baseline.exists() and not sync:
        raise RuntimeError(f"Missing baseline: {baseline}")
    previous = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else []
    added = fingerprints(current) - fingerprints(previous)
    removed = fingerprints(previous) - fingerprints(current)
    if sync:
        # New errors cannot silently be frozen during routine maintenance.
        if baseline.exists() and added:
            raise RuntimeError("Resolve new Ruff errors before synchronizing the baseline")
        baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Ruff baseline synchronized: {len(current)} diagnostics")
        return 0
    for entry, count in added.items():
        issue = json.loads(entry)
        print(f"NEW Ruff {issue['file']}: {issue['code']} {issue['message']} (x{count})")
    print(
        f"Ruff: {sum(added.values())} new, {sum(removed.values())} resolved, {len(current)} total"
    )
    if fresh and removed:
        print("Ruff baseline is stale; run scripts/check_baselines.py sync ruff")
    return int(bool(added or (fresh and removed)))


def mypy_output() -> str:
    result = run_tool(
        [
            "mypy",
            "src",
            "--python-version=3.11",
            "--no-pretty",
            "--no-error-summary",
            "--show-error-codes",
            "--no-color-output",
            "--cache-dir=.mypy_cache/baseline",
        ]
    )
    if result.returncode not in (0, 1) or "errors prevented further checking" in result.stdout:
        raise RuntimeError(result.stderr or result.stdout or "mypy failed without diagnostics")
    if result.stderr:
        raise RuntimeError(result.stderr)
    if result.returncode == 1 and not re.search(
        r"^.+:\d+(?::\d+)?: error: .+\s+\[[\w-]+\]$", result.stdout, re.MULTILINE
    ):
        raise RuntimeError("mypy failed without diagnostics")
    return result.stdout.replace("\\", "/")


def baseline_command(command: str, baseline: Path, output: str, *, fresh: bool = False) -> int:
    arguments = ["mypy_baseline", command, "--baseline-path", str(baseline), "--no-colors"]
    if fresh:
        # CLI flag only enables allow_unsynced; a temporary config can disable it.
        cache = ROOT / ".quality-cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            config = Path(directory) / "pyproject.toml"
            config.write_text(
                "[tool.mypy-baseline]\nallow_unsynced = false\nsort_baseline = true\n",
                encoding="utf-8",
            )
            result = run_tool([*arguments, "--config", str(config)], stdin=output)
    else:
        result = run_tool(arguments, stdin=output)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return int(result.returncode != 0)


def check_mypy(baseline: Path, *, fresh: bool = False, sync: bool = False) -> int:
    if not baseline.exists() and not sync:
        raise RuntimeError(f"Missing baseline: {baseline}")
    output = mypy_output()
    if sync:
        if baseline.exists() and baseline_command("filter", baseline, output):
            raise RuntimeError("Resolve new mypy errors before synchronizing the baseline")
        return baseline_command("sync", baseline, output)
    return baseline_command("filter", baseline, output, fresh=fresh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "fresh", "sync"))
    parser.add_argument("tool", choices=("ruff", "mypy", "all"), default="all", nargs="?")
    args = parser.parse_args()
    configuration: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    baselines = configuration["tool"]["arty-baselines"]
    status = 0
    for name, checker in (("ruff", check_ruff), ("mypy", check_mypy)):
        if args.tool in (name, "all"):
            status |= checker(
                ROOT / baselines[name], fresh=args.action == "fresh", sync=args.action == "sync"
            )
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Baseline check failed: {exc}", file=sys.stderr)
        sys.exit(2)
