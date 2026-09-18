"""Tests for the systemd install script's generated unit."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install_codex_watch.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


def render(*args: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_unit_has_no_unsubstituted_placeholders() -> None:
    unit = render()

    assert "/path/to/coding-agent-notifier" not in unit
    assert str(REPO_ROOT) in unit


def test_unit_points_at_the_watcher_entrypoint() -> None:
    unit = render()

    assert f"{REPO_ROOT}/scripts/notifier/codex_watch.py" in unit
    assert "WorkingDirectory=" in unit
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit


def test_interval_and_interrupted_options_reach_the_command() -> None:
    unit = render("--interval", "120", "--include-interrupted")

    assert "--interval 120" in unit
    assert "--include-interrupted" in unit


def test_interval_defaults_to_thirty_seconds() -> None:
    assert "--interval 30" in render()


def test_unknown_option_is_rejected() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 2
    assert "Unknown option" in result.stderr


def test_help_exits_cleanly() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0
    assert "--uninstall" in result.stdout
