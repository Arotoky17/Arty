"""Version freeze and signed-preregistration gate for a forward demo run.

The run manifest captures the code hash, every watched configuration file, the
cost and fill models, the preregistration digest and the start date. Any change
while the run is live invalidates it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arty_trading.config.operational import CONFIG_ROOT, load_config

WATCHED_CONFIGS = (
    "definitions.yaml",
    "execution.yaml",
    "setup1_preregistration.yaml",
    "forward_demo.yaml",
    "market_calendar.yaml",
    "diagnostics.yaml",
    "validation.yaml",
)


class RunInvalidatedError(RuntimeError):
    """Raised when the frozen configuration or code changed mid-run."""


class PreregistrationUnsignedError(RuntimeError):
    """Raised when the forward demo lacks a signed preregistration."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_sha256(root: Path | None = None) -> str:
    """Hash of the executed strategy/execution code tree."""
    root = Path(root or CONFIG_ROOT.parent / "src" / "arty_trading")
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(sha256_file(path).encode())
    return digest.hexdigest()


def _model_sha256(model: Any) -> str:
    return hashlib.sha256(
        json.dumps(asdict(model), sort_keys=True, default=str).encode()
    ).hexdigest()


def cost_model_sha256() -> str:
    from arty_trading.modules.execution.cost_model import CostModel

    return _model_sha256(CostModel(**load_config("execution.yaml")["cost"]))


def fill_model_sha256() -> str:
    from arty_trading.modules.execution.fill_model import FillModel

    return _model_sha256(FillModel(**load_config("execution.yaml")["fill"]))


@dataclass(frozen=True)
class RunFreeze:
    """Immutable fingerprint of everything the run depends on."""

    created_at_utc: str
    start_date: str
    code_sha256: str
    config_sha256: dict[str, str]
    cost_model_sha256: str
    fill_model_sha256: str
    preregistration_sha256: str
    preregistration_signed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    @classmethod
    def capture(cls, preregistration: Path | None = None) -> RunFreeze:
        path = Path(preregistration or Path("preregistration.md"))
        now = datetime.now(UTC)
        return cls(
            created_at_utc=now.isoformat(),
            start_date=now.date().isoformat(),
            code_sha256=code_sha256(),
            config_sha256={name: sha256_file(CONFIG_ROOT / name) for name in WATCHED_CONFIGS},
            cost_model_sha256=cost_model_sha256(),
            fill_model_sha256=fill_model_sha256(),
            preregistration_sha256=sha256_file(path),
            preregistration_signed=is_preregistration_signed(),
        )


def is_preregistration_signed(setup: dict[str, Any] | None = None) -> bool:
    setup = setup or load_config("setup1_preregistration.yaml")
    approval = setup["approval"]
    return bool(
        approval["status"] == "approved"
        and approval["approved_by"]
        and approval["approved_at_utc"]
    )


def assert_forward_preregistration(setup_id: str) -> None:
    """Reuse the backtest launch gate: signed preregistration and frozen config."""
    from arty_trading.validation.preregistration import (
        assert_setup_run_allowed,
        configuration_sha256,
    )

    setup = load_config("setup1_preregistration.yaml")
    if load_config("forward_demo.yaml")["require_signed_preregistration"]:
        if not is_preregistration_signed(setup):
            raise PreregistrationUnsignedError(
                "Forward demo requires a signed preregistration.md (approved status, "
                "approver and UTC signature); the config flag alone is never enough"
            )
    assert_setup_run_allowed(setup_id)
    if setup["approval"]["configuration_sha256"] != configuration_sha256():
        raise PreregistrationUnsignedError("Preregistration configuration digest does not match")


def verify_freeze(freeze: RunFreeze, preregistration: Path | None = None) -> None:
    """Fail closed if anything the run depends on changed since the freeze."""
    current = RunFreeze.capture(preregistration)
    if freeze.code_sha256 != current.code_sha256:
        raise RunInvalidatedError("Strategy/execution code changed during the forward run")
    if freeze.config_sha256 != current.config_sha256:
        changed = sorted(
            name
            for name in set(freeze.config_sha256) | set(current.config_sha256)
            if freeze.config_sha256.get(name) != current.config_sha256.get(name)
        )
        raise RunInvalidatedError(f"Configuration changed during the forward run: {changed}")
    for field in ("cost_model_sha256", "fill_model_sha256", "preregistration_sha256"):
        if getattr(freeze, field) != getattr(current, field):
            raise RunInvalidatedError(f"{field} changed during the forward run")


def write_manifest(freeze: RunFreeze, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(freeze.to_json(), encoding="utf-8")
    return path


def read_manifest(path: Path) -> RunFreeze:
    return RunFreeze(**json.loads(Path(path).read_text(encoding="utf-8")))


def forward_setup_id(setup_id: str | None = None) -> str:
    """Derived id so the demo run is traced without consuming the trial budget."""
    setup_id = setup_id or load_config("setup1_preregistration.yaml")["setup_id"]
    return f"{setup_id}{load_config('forward_demo.yaml')['setup_id_suffix']}"
