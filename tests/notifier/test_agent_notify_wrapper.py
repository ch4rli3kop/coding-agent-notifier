"""Regression tests for scripts/notifier/agent_notify_wrapper.sh.

The wrapper is what agent hooks actually execute, so these tests drive the real
script and stub out only the Slack CLI call (via NOTIFIER_PYTHON) to keep the
JSON-normalising step covered end to end.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "notifier" / "agent_notify_wrapper.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


def _make_stub_python(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path, Path]:
    """Build an interpreter shim that records the slack_notify.py invocation."""
    payload_file = tmp_path / "captured_payload.json"
    args_file = tmp_path / "captured_args.txt"
    stub = tmp_path / "stub_python"
    real_python = shutil.which("python3") or shutil.which("python")
    assert real_python is not None
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  *slack_notify.py)\n"
        f'    cat > "{payload_file}"\n'
        "    shift\n"
        f'    printf "%s\\n" "$@" > "{args_file}"\n'
        f"    exit {exit_code}\n"
        "    ;;\n"
        "esac\n"
        f'exec "{real_python}" "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub, payload_file, args_file


def _run_wrapper(
    stub: Path,
    tmp_path: Path,
    *args: str,
    stdin: str = "",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NOTIFIER_PYTHON"] = str(stub)
    env["PWD"] = str(tmp_path)
    # Point at a path that does not exist unless a test creates it.
    env.setdefault("ENV_FILE", str(tmp_path / "missing.env"))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(WRAPPER), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


def test_stdin_payload_exits_zero_on_success(tmp_path: Path) -> None:
    """The stdin path must not inherit a failing status from the EXIT trap."""
    stub, payload_file, _ = _make_stub_python(tmp_path)

    result = _run_wrapper(stub, tmp_path, stdin='{"status":"ok","title":"Agent run"}')

    assert result.returncode == 0, result.stderr
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["title"] == "Agent run"


def test_inline_json_argument_is_forwarded(tmp_path: Path) -> None:
    stub, payload_file, _ = _make_stub_python(tmp_path)

    result = _run_wrapper(stub, tmp_path, '{"status":"ok","title":"Inline"}', stdin="")

    assert result.returncode == 0, result.stderr
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert payload["title"] == "Inline"


def test_payload_file_argument_is_forwarded(tmp_path: Path) -> None:
    stub, payload_file, _ = _make_stub_python(tmp_path)
    source = tmp_path / "payload.json"
    source.write_text('{"status":"ok","title":"From file"}', encoding="utf-8")

    result = _run_wrapper(stub, tmp_path, str(source), stdin="")

    assert result.returncode == 0, result.stderr
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert payload["title"] == "From file"


def test_repo_falls_back_to_pwd(tmp_path: Path) -> None:
    stub, payload_file, _ = _make_stub_python(tmp_path)

    result = _run_wrapper(stub, tmp_path, stdin='{"hook_event_name":"Stop"}')

    assert result.returncode == 0, result.stderr
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert payload["repo"] == str(tmp_path)


def test_jsonl_transcript_picks_last_relevant_object(tmp_path: Path) -> None:
    stub, payload_file, _ = _make_stub_python(tmp_path)
    lines = "\n".join(
        [
            '{"unrelated":"noise"}',
            '{"status":"running","title":"First"}',
            '{"status":"success","title":"Last"}',
            "not json at all",
        ]
    )

    result = _run_wrapper(stub, tmp_path, stdin=lines)

    assert result.returncode == 0, result.stderr
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert payload["title"] == "Last"


def test_missing_env_file_is_not_passed_to_cli(tmp_path: Path) -> None:
    """Hooks default ENV_FILE to a repo .env that often does not exist."""
    stub, _, args_file = _make_stub_python(tmp_path)

    result = _run_wrapper(stub, tmp_path, stdin='{"status":"ok"}')

    assert result.returncode == 0, result.stderr
    assert "--env-file" not in args_file.read_text(encoding="utf-8")


def test_existing_env_file_is_passed_to_cli(tmp_path: Path) -> None:
    stub, _, args_file = _make_stub_python(tmp_path)
    env_file = tmp_path / "present.env"
    env_file.write_text("SLACK_USER_ID=U1\n", encoding="utf-8")

    result = _run_wrapper(
        stub, tmp_path, stdin='{"status":"ok"}', extra_env={"ENV_FILE": str(env_file)}
    )

    assert result.returncode == 0, result.stderr
    assert args_file.read_text(encoding="utf-8").split() == ["--env-file", str(env_file)]


def test_cli_failure_propagates(tmp_path: Path) -> None:
    stub, _, _ = _make_stub_python(tmp_path, exit_code=1)

    result = _run_wrapper(stub, tmp_path, stdin='{"status":"ok"}')

    assert result.returncode == 1
    assert "Notifier failed to send message" in result.stderr


def test_unusable_notifier_python_fails_cleanly(tmp_path: Path) -> None:
    stub, _, _ = _make_stub_python(tmp_path)

    result = _run_wrapper(
        stub,
        tmp_path,
        stdin='{"status":"ok"}',
        extra_env={"NOTIFIER_PYTHON": str(tmp_path / "nope")},
    )

    assert result.returncode == 1
    assert "NOTIFIER_PYTHON" in result.stderr


def test_debug_payload_file_is_written(tmp_path: Path) -> None:
    stub, _, _ = _make_stub_python(tmp_path)
    debug_file = tmp_path / "debug.json"

    result = _run_wrapper(
        stub,
        tmp_path,
        stdin='{"status":"ok","title":"Debug"}',
        extra_env={"DEBUG_AGENT_PAYLOAD": str(debug_file)},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(debug_file.read_text(encoding="utf-8"))["title"] == "Debug"


def test_codex_compat_wrapper_delegates(tmp_path: Path) -> None:
    stub, payload_file, _ = _make_stub_python(tmp_path)
    env = dict(os.environ)
    env["NOTIFIER_PYTHON"] = str(stub)
    env["PWD"] = str(tmp_path)
    env["ENV_FILE"] = str(tmp_path / "missing.env")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "notifier" / "codex_notify_wrapper.sh")],
        input='{"status":"ok","title":"Legacy"}',
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(payload_file.read_text(encoding="utf-8"))["title"] == "Legacy"
