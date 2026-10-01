import subprocess
import sys


def test_python_module_entrypoint_help_works() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "arty_trading", "--help"],
        capture_output=True,
        text=True,
        cwd=".",
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "arty" in result.stdout.lower()
