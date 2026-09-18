import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from coding_agent_notifier import notifier
from coding_agent_notifier.notifier import (
    NotificationError,
    SlackNotificationError,
    SlackNotifier,
    build_message,
    load_payload,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Dict[str, Any] | None = None,
        headers: Dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self) -> Dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: List[FakeResponse]) -> None:
        self.responses = responses
        self.posts: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        headers: dict[str, Any],
        json: dict[str, Any],
        timeout: int,
    ) -> FakeResponse:
        self.posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def test_new_package_is_canonical_and_old_package_reexports_compatibility() -> None:
    import codex_slack_notifier
    import codex_slack_notifier.notifier as legacy_notifier
    import coding_agent_notifier
    import coding_agent_notifier.notifier as canonical_notifier

    assert coding_agent_notifier.SlackNotifier is SlackNotifier
    assert codex_slack_notifier.SlackNotifier is SlackNotifier
    assert legacy_notifier.SlackNotifier is canonical_notifier.SlackNotifier
    assert SlackNotificationError is NotificationError
    assert legacy_notifier.NotificationError is NotificationError
    assert legacy_notifier.SlackNotificationError is NotificationError


def test_slack_main_is_canonical_and_main_is_compatibility_alias() -> None:
    assert notifier.main is notifier.slack_main


def test_agent_notify_wrapper_is_canonical_and_codex_wrapper_is_compatibility() -> None:
    agent_wrapper = Path("scripts/notifier/agent_notify_wrapper.sh")
    codex_wrapper = Path("scripts/notifier/codex_notify_wrapper.sh")

    assert agent_wrapper.exists()
    assert codex_wrapper.exists()

    agent_source = agent_wrapper.read_text(encoding="utf-8")
    codex_source = codex_wrapper.read_text(encoding="utf-8")

    assert "DEBUG_AGENT_PAYLOAD" in agent_source
    assert "DEBUG_CODEX_PAYLOAD" in agent_source
    assert "slack_notify.py" in agent_source
    assert "agent_notify_wrapper.sh" in codex_source
    assert "exec" in codex_source


def test_agent_notify_wrapper_forwards_multiline_json_payload(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_payload = tmp_path / "forwarded.json"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-" || "${1:-}" == "-c" ]]; then
  exec "$REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == */slack_notify.py ]]; then
  cat > "$FAKE_SLACK_PAYLOAD"
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    payload = {
        "title": "Pretty JSON",
        "status": "success",
        "summary": "multi-line payload survived the wrapper",
        "repo": "/tmp/pretty-json-repo",
    }
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "NOTIFIER_PYTHON": str(fake_python),
            "REAL_PYTHON": sys.executable,
            "FAKE_SLACK_PAYLOAD": str(captured_payload),
        }
    )

    result = subprocess.run(
        ["bash", "scripts/notifier/agent_notify_wrapper.sh", json.dumps(payload, indent=2)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(captured_payload.read_text(encoding="utf-8")) == payload


def test_build_message_prefers_payload_fields() -> None:
    payload = {
        "title": "Run notebook",
        "status": "success",
        "duration": "3m 12s",
        "summary": "Notebook finished with no errors",
        "url": "https://example.com/logs/123",
        "repo": "/home/user/project",
    }
    message = build_message(payload)
    assert "Run notebook" in message
    assert "Status: success" in message
    assert "3m 12s" in message
    assert "Notebook finished with no errors" in message
    assert "https://example.com/logs/123" in message
    assert "project" in message


def test_build_message_repo_only_adds_default_headline() -> None:
    message = build_message({"repo": "/path/to/repo"})
    assert message == "Codex task completed at repo /path/to/repo"


def test_build_message_claude_code_stop_payload_uses_claude_label() -> None:
    """Claude Code's Stop hook payload should not be labeled as Codex."""
    payload = {
        "session_id": "abc123",
        "transcript_path": "/home/user/.claude/projects/x.jsonl",
        "cwd": "/path/to/repo",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
    }
    message = build_message(payload)
    assert message == "Claude Code task completed at repo /path/to/repo"


def test_build_message_claude_code_empty_payload_uses_claude_label() -> None:
    """Claude-shaped payload with no useful fields still uses the Claude label."""
    payload = {
        "hook_event_name": "Stop",
        "stop_hook_active": True,
        "transcript_path": "/home/user/.claude/projects/x.jsonl",
    }
    message = build_message(payload)
    assert message == "Claude Code task completed."


def test_build_message_codex_stop_hook_payload_uses_codex_label() -> None:
    """Codex hooks also include hook_event_name; that alone is not a Claude signal."""
    payload = {
        "hook_event_name": "Stop",
        "cwd": "/path/to/repo",
    }
    message = build_message(payload)
    assert message == "Codex task completed at repo /path/to/repo"


def test_build_message_codex_stop_hook_transcript_uses_codex_label() -> None:
    """Codex hooks may include Claude-compatible hook fields but use .codex transcripts."""
    payload = {
        "hook_event_name": "Stop",
        "stop_hook_active": True,
        "transcript_path": "/home/user/.codex/sessions/2026/05/08/session.jsonl",
        "cwd": "/path/to/repo",
    }
    message = build_message(payload)
    assert message == "Codex task completed at repo /path/to/repo"


def test_send_dm_sends_open_then_message(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        FakeResponse(json_data={"ok": True, "channel": {"id": "C123"}}),
        FakeResponse(json_data={"ok": True, "ts": "1.2"}),
    ]
    session = FakeSession(responses)
    notifier = SlackNotifier("xoxb-test-token", session=session)
    monkeypatch.setattr("time.sleep", lambda _: None)

    notifier.send_dm("U999", "hello world")

    assert len(session.posts) == 2
    assert session.posts[0]["json"]["users"] == "U999"
    assert session.posts[1]["json"]["text"] == "hello world"


def test_send_dm_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        FakeResponse(
            status_code=429,
            json_data={"ok": False, "error": "ratelimited"},
            headers={"Retry-After": "0"},
        ),
        FakeResponse(json_data={"ok": True, "channel": {"id": "C123"}}),
        FakeResponse(json_data={"ok": True, "ts": "1.2"}),
    ]
    session = FakeSession(responses)
    notifier = SlackNotifier("xoxb-test-token", session=session)
    monkeypatch.setattr("time.sleep", lambda _: None)

    notifier.send_dm("U999", "rate limited message")

    assert len(session.posts) == 3
    assert session.posts[0]["url"].endswith("conversations.open")
    assert session.posts[1]["url"].endswith("conversations.open")
    assert session.posts[2]["url"].endswith("chat.postMessage")


def test_lark_send_text_posts_text_payload() -> None:
    responses = [FakeResponse(json_data={"code": 0, "msg": "success"})]
    session = FakeSession(responses)
    lark_notifier = notifier.LarkNotifier(
        "https://example.test/open-apis/bot/v2/hook/token", session=session
    )

    lark_notifier.send_text("hello from codex")

    assert len(session.posts) == 1
    assert session.posts[0]["url"] == "https://example.test/open-apis/bot/v2/hook/token"
    assert session.posts[0]["headers"] == {"Content-Type": "application/json; charset=utf-8"}
    assert session.posts[0]["json"] == {
        "msg_type": "text",
        "content": {"text": "hello from codex"},
    }


def test_lark_send_text_rejects_nonzero_code_response() -> None:
    responses = [FakeResponse(json_data={"code": 19001, "msg": "bad webhook"})]
    session = FakeSession(responses)
    lark_notifier = notifier.LarkNotifier("https://example.test/hook", session=session)

    with pytest.raises(NotificationError, match="bad webhook"):
        lark_notifier.send_text("hello")


def test_feishu_send_text_rejects_nonzero_status_code_response() -> None:
    responses = [FakeResponse(json_data={"StatusCode": 19001, "StatusMessage": "bad webhook"})]
    session = FakeSession(responses)
    lark_notifier = notifier.LarkNotifier("https://example.test/hook", session=session)

    with pytest.raises(NotificationError, match="bad webhook"):
        lark_notifier.send_text("hello")


def test_retry_after_parses_float_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[int] = []
    responses = [
        FakeResponse(
            status_code=429,
            json_data={"ok": False, "error": "ratelimited"},
            headers={"Retry-After": "1.6"},
        ),
        FakeResponse(json_data={"ok": True, "channel": {"id": "C123"}}),
        FakeResponse(json_data={"ok": True, "ts": "1.2"}),
    ]
    session = FakeSession(responses)
    notifier = SlackNotifier("xoxb-test-token", session=session)
    monkeypatch.setattr("time.sleep", lambda seconds: sleep_calls.append(seconds))

    notifier.send_dm("U999", "rate limited message")

    assert sleep_calls == [2]
    assert len(session.posts) == 3


def test_load_payload_supports_file(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_data = {"status": "done"}
    payload_path.write_text(json.dumps(payload_data), encoding="utf-8")

    loaded = load_payload(None, str(payload_path))

    assert loaded == payload_data


def test_load_payload_rejects_invalid_json(tmp_path: Path) -> None:
    payload_path = tmp_path / "bad.json"
    payload_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(NotificationError):
        load_payload(None, str(payload_path))


def test_load_payload_rejects_non_object_json() -> None:
    with pytest.raises(NotificationError, match="JSON payload must be an object"):
        load_payload('["status", "done"]', None)


def test_build_message_with_empty_payload_returns_default() -> None:
    """Ensure build_message returns the default message for an empty payload."""
    message = build_message({})
    assert message == "Codex task completed."


def test_env_file_loader_does_not_overwrite_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure _load_env_file does not overwrite existing environment variables."""
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=token-from-file\n", encoding="utf-8")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "token-from-env")

    from coding_agent_notifier import notifier

    notifier._load_env_file(str(env_file))

    assert os.environ["SLACK_BOT_TOKEN"] == "token-from-env"


def test_env_file_loader_sets_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("export SLACK_BOT_TOKEN=test-token\nSLACK_USER_ID=U1\n", encoding="utf-8")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_USER_ID", raising=False)

    notifier._load_env_file(str(env_file))

    assert os.environ["SLACK_BOT_TOKEN"] == "test-token"
    assert os.environ["SLACK_USER_ID"] == "U1"


def test_main_uses_user_id_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=test-token\nSLACK_USER_ID=U123\n", encoding="utf-8")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_USER_ID", raising=False)
    monkeypatch.setenv("REPO_ROOT", "/tmp/repo")

    sent: list[tuple[str, str]] = []

    def fake_send_dm(self: SlackNotifier, user_id: str, message: str) -> None:  # type: ignore[override]
        sent.append((user_id, message))

    monkeypatch.setattr(notifier.SlackNotifier, "send_dm", fake_send_dm)

    exit_code = notifier.slack_main(
        ["--env-file", str(env_file), "--payload", '{"status":"ok","repo":"/tmp/repo"}']
    )

    assert exit_code == 0
    assert sent and sent[0][0] == "U123"


def test_lark_main_uses_webhook_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LARK_WEBHOOK_URL=https://example.test/lark\n", encoding="utf-8")
    monkeypatch.delenv("LARK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)

    sent: list[tuple[str, str]] = []

    def fake_send_text(self: notifier.LarkNotifier, message: str) -> None:
        sent.append((self.webhook_url, message))

    monkeypatch.setattr(notifier.LarkNotifier, "send_text", fake_send_text)

    exit_code = notifier.lark_main(
        ["--env-file", str(env_file), "--payload", '{"status":"ok","title":"Codex run"}']
    )

    assert exit_code == 0
    assert sent == [("https://example.test/lark", "Codex run\nStatus: ok")]


def test_feishu_main_falls_back_to_feishu_webhook_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FEISHU_WEBHOOK_URL=https://example.test/feishu\n", encoding="utf-8")
    monkeypatch.delenv("LARK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)

    sent: list[str] = []

    def fake_send_text(self: notifier.LarkNotifier, message: str) -> None:
        sent.append(self.webhook_url)

    monkeypatch.setattr(notifier.LarkNotifier, "send_text", fake_send_text)

    exit_code = notifier.lark_main(["--env-file", str(env_file), "--payload", '{"status":"ok"}'])

    assert exit_code == 0
    assert sent == ["https://example.test/feishu"]


def test_env_file_loader_strips_surrounding_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quoted values must behave like `set -a; . .env` and the OpenCode plugin."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SLACK_BOT_TOKEN=\"xoxb-quoted\"\nSLACK_USER_ID='U-quoted'\nLARK_WEBHOOK_URL=plain\n",
        encoding="utf-8",
    )
    for key in ("SLACK_BOT_TOKEN", "SLACK_USER_ID", "LARK_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)

    notifier._load_env_file(str(env_file))

    assert os.environ["SLACK_BOT_TOKEN"] == "xoxb-quoted"
    assert os.environ["SLACK_USER_ID"] == "U-quoted"
    assert os.environ["LARK_WEBHOOK_URL"] == "plain"


def test_main_continues_when_env_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hooks pass a default --env-file path that often does not exist."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SLACK_USER_ID", "U999")

    sent: list[tuple[str, str]] = []

    def fake_send_dm(self: SlackNotifier, user_id: str, message: str) -> None:  # type: ignore[override]
        sent.append((user_id, message))

    monkeypatch.setattr(notifier.SlackNotifier, "send_dm", fake_send_dm)

    exit_code = notifier.slack_main(
        ["--env-file", str(tmp_path / "missing.env"), "--payload", '{"status":"ok"}']
    )

    assert exit_code == 0
    assert sent and sent[0][0] == "U999"


def test_main_still_fails_when_env_file_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that exists but cannot be parsed is a real configuration error."""
    env_file = tmp_path / "bad.env"
    env_file.write_bytes(b"SLACK_BOT_TOKEN=\xff\xfe\n")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    exit_code = notifier.slack_main(["--env-file", str(env_file), "--payload", "{}"])

    assert exit_code == 1


def test_detect_agent_label_from_claude_transcript() -> None:
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": "/home/user/.claude/projects/demo/transcript.jsonl",
        "cwd": "/home/user/demo",
    }
    assert build_message(payload).startswith("Claude Code task completed")


def test_positional_json_argument_is_used_as_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex's `notify` appends the payload as an argument, not on stdin."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(
        notifier.SlackNotifier, "send_dm", lambda self, user_id, message: sent.append(message)
    )

    exit_code = notifier.slack_main(
        ['{"session_title":"Positional","repo":"/tmp/proj","last_prompt":"do it"}']
    )

    assert exit_code == 0
    assert "Positional" in sent[0]


def test_positional_path_argument_is_read_as_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"session_title":"From file","repo":"/tmp/proj"}', encoding="utf-8")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(
        notifier.SlackNotifier, "send_dm", lambda self, user_id, message: sent.append(message)
    )

    exit_code = notifier.slack_main([str(payload_file)])

    assert exit_code == 0
    assert "From file" in sent[0]


def test_explicit_payload_flag_beats_the_positional_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(
        notifier.SlackNotifier, "send_dm", lambda self, user_id, message: sent.append(message)
    )

    exit_code = notifier.slack_main(
        ["--payload", '{"session_title":"Flag"}', '{"session_title":"Positional"}']
    )

    assert exit_code == 0
    assert "Flag" in sent[0]
