"""Exercise the real baseline filters, including newly introduced violations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.check_baselines import ROOT, check_ruff, fingerprints, mypy_output, ruff_diagnostics


def command(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )


def test_ruff_baseline_exists() -> None:
    path = ROOT / ".ruff-baseline.json"
    assert path.is_file()
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    assert entries


def test_mypy_baseline_exists() -> None:
    path = ROOT / "mypy-baseline.txt"
    assert path.is_file()
    assert ":0: error:" in path.read_text(encoding="utf-8")


def test_ruff_check_passes_with_baseline() -> None:
    result = command("scripts/check_baselines.py", "check", "ruff")
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_311_passes_on_smc_module() -> None:
    result = command("-m", "mypy", "--python-version", "3.11", "src/arty_trading/modules/smc/")
    assert result.returncode == 0, result.stdout + result.stderr


def test_baselines_are_up_to_date() -> None:
    result = command("scripts/check_baselines.py", "fresh", "all")
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_filter_rejects_new_and_duplicate_errors(tmp_path: Path) -> None:
    baseline = tmp_path / "mypy-baseline.txt"
    diagnostic = 'src/example.py:5: error: Name "missing" is not defined  [name-defined]\n'
    result = command(
        "-m", "mypy_baseline", "sync", "--baseline-path", str(baseline), stdin=diagnostic
    )
    assert result.returncode == 0, result.stdout + result.stderr
    accepted = command(
        "-m", "mypy_baseline", "filter", "--baseline-path", str(baseline), stdin=diagnostic
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    for extra in (diagnostic, diagnostic.replace("missing", "another_missing")):
        rejected = command(
            "-m",
            "mypy_baseline",
            "filter",
            "--baseline-path",
            str(baseline),
            stdin=diagnostic + extra,
        )
        assert rejected.returncode != 0


def test_ruff_filter_rejects_new_error_and_stale_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = tmp_path / "regression.py"
    file.write_text("unknown_name\n", encoding="utf-8")
    result = command("-m", "ruff", "check", str(file), "--output-format=json")
    assert result.returncode == 1
    raw: list[dict[str, Any]] = json.loads(result.stdout)
    assert any(entry["code"] == "F821" for entry in raw)
    issue = {
        "file": "regression.py",
        "code": "F821",
        "message": raw[0]["message"],
        "source": "unknown_name",
    }
    baseline = tmp_path / "ruff.json"
    baseline.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_baselines.ruff_diagnostics", lambda: [issue])
    assert check_ruff(baseline) == 1
    with pytest.raises(RuntimeError):
        check_ruff(baseline, sync=True)
    baseline.write_text(json.dumps([issue]), encoding="utf-8")
    assert check_ruff(baseline) == 0
    monkeypatch.setattr("scripts.check_baselines.ruff_diagnostics", lambda: [issue, issue])
    assert check_ruff(baseline) == 1
    monkeypatch.setattr("scripts.check_baselines.ruff_diagnostics", lambda: [])
    assert check_ruff(baseline) == 0
    assert check_ruff(baseline, fresh=True) == 1


def test_ruff_baseline_matches_current_diagnostics() -> None:
    baseline = json.loads((ROOT / ".ruff-baseline.json").read_text(encoding="utf-8"))
    assert fingerprints(ruff_diagnostics()) == fingerprints(baseline)


@pytest.mark.parametrize(
    ("code", "stdout", "stderr"),
    [
        (2, "", "configuration error"),
        (1, "unparseable failure", ""),
        (1, "Found 1 error (errors prevented further checking)", ""),
    ],
)
def test_mypy_tool_failure_is_not_filtered_as_success(
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    stdout: str,
    stderr: str,
) -> None:
    failed = subprocess.CompletedProcess(["mypy"], code, stdout=stdout, stderr=stderr)
    monkeypatch.setattr("scripts.check_baselines.run_tool", lambda *args, **kwargs: failed)
    with pytest.raises(RuntimeError):
        mypy_output()
