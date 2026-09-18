"""Tests for the Codex failed-turn watcher."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from coding_agent_notifier import codex_watch
from coding_agent_notifier.icons import icon_for
from coding_agent_notifier.notifier import NotificationError, build_message

THREAD = "01a0b4c2-5c07-7fe0-875e-4cb910fbb396"


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = sqlite3.connect(tmp_path / "state_5.sqlite")
    state.execute(
        "create table threads (id text, name text, title text, cwd text, git_branch text,"
        " rollout_path text)"
    )
    state.execute(
        "insert into threads values (?,?,?,?,?,?)",
        (THREAD, "실험1", "안녕", "/home/user/proj", "main", str(tmp_path / "rollout.jsonl")),
    )
    state.commit()
    state.close()

    history = sqlite3.connect(tmp_path / "thread_history_1.sqlite")
    history.execute(
        "create table thread_turns (thread_id text, turn_id text, status text,"
        " completed_at integer, duration_ms integer, error_json text)"
    )
    history.commit()
    history.close()

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def add_turn(
    home: Path,
    turn_id: str,
    *,
    status: str = "failed",
    completed_at: int = 1_000,
    duration_ms: int = 2_000,
    message: str | None = "You've hit your usage limit.",
) -> None:
    con = sqlite3.connect(home / "thread_history_1.sqlite")
    con.execute(
        "insert into thread_turns values (?,?,?,?,?,?)",
        (
            THREAD,
            turn_id,
            status,
            completed_at,
            duration_ms,
            json.dumps({"message": message}) if message else None,
        ),
    )
    con.commit()
    con.close()


class RecordingSender(codex_watch.Sender):
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.dry_run = False

    @property
    def configured(self) -> bool:
        return True

    def send(self, message: str) -> None:
        if self.fail:
            raise NotificationError("Slack API error: invalid_auth")
        self.sent.append(message)


def test_payload_carries_thread_name_error_and_duration(codex_home: Path) -> None:
    add_turn(codex_home, "t1")

    payload = codex_watch.build_payload(
        {
            "thread_id": THREAD,
            "turn_id": "t1",
            "status": "failed",
            "duration_ms": 125_000,
            "error_json": json.dumps({"message": "Selected model is at capacity."}),
        }
    )

    assert payload["session_id"] == THREAD
    assert payload["session_title"] == "실험1"
    assert payload["status"] == "failed"
    assert payload["duration"] == "2m 05s"
    assert payload["last_result"] == "Selected model is at capacity."

    lines = build_message(payload).splitlines()
    assert lines[0] == f"❌ {icon_for(THREAD)}  *실험1*"
    assert lines[1] == "`proj` · main · 2m 05s · Codex"


def test_each_failure_notifies_once(codex_home: Path) -> None:
    add_turn(codex_home, "t1", completed_at=1_000)
    sender = RecordingSender()
    state = {"watermark": 0}

    assert codex_watch.poll_once(state, sender, ("failed",)) == 1
    assert codex_watch.poll_once(state, sender, ("failed",)) == 0
    assert len(sender.sent) == 1
    assert state["watermark"] == 1_000
    assert state["notified"] == ["t1"]


def test_repeat_of_the_same_error_is_coalesced(codex_home: Path) -> None:
    """A retried rate limit records the same failure several times."""
    add_turn(codex_home, "t1", completed_at=1_000)
    add_turn(codex_home, "t2", completed_at=1_010)
    sender = RecordingSender()
    state = {"watermark": 0}

    codex_watch.poll_once(state, sender, ("failed",))

    assert len(sender.sent) == 1
    assert set(state["notified"]) == {"t1", "t2"}  # both accounted for, one sent


def test_a_different_error_is_not_coalesced(codex_home: Path) -> None:
    add_turn(codex_home, "t1", completed_at=1_000, message="usage limit")
    add_turn(codex_home, "t2", completed_at=1_010, message="model at capacity")
    sender = RecordingSender()

    codex_watch.poll_once({"watermark": 0}, sender, ("failed",))

    assert len(sender.sent) == 2


def test_a_send_failure_is_retried_on_the_next_tick(codex_home: Path) -> None:
    add_turn(codex_home, "t1", completed_at=1_000)
    failing = RecordingSender(fail=True)
    state = {"watermark": 0}

    assert codex_watch.poll_once(state, failing, ("failed",)) == 0
    assert state.get("notified") == []

    working = RecordingSender()
    assert codex_watch.poll_once(state, working, ("failed",)) == 1


def test_interrupted_turns_are_skipped_unless_asked_for(codex_home: Path) -> None:
    add_turn(codex_home, "t1", status="interrupted", completed_at=1_000, message=None)
    sender = RecordingSender()

    assert codex_watch.poll_once({"watermark": 0}, sender, ("failed",)) == 0
    assert codex_watch.poll_once({"watermark": 0}, sender, ("failed", "interrupted")) == 1
    assert sender.sent[0].startswith("⚠")  # a warning, not a failure


def test_turns_before_the_watermark_are_ignored(codex_home: Path) -> None:
    add_turn(codex_home, "old", completed_at=1_000)
    sender = RecordingSender()

    assert codex_watch.poll_once({"watermark": 5_000}, sender, ("failed",)) == 0


def test_state_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    codex_watch.save_state(path, {"watermark": 42, "notified": ["a"]})

    assert codex_watch.load_state(path)["watermark"] == 42


def test_unreadable_state_file_starts_clean(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")

    assert codex_watch.load_state(path) == {}


def test_first_run_starts_from_now_not_from_history(
    codex_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise every past failure would arrive at once."""
    add_turn(codex_home, "ancient", completed_at=1_000)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "t")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(codex_watch.Sender, "send", lambda self, message: sent.append(message))

    state_file = tmp_path / "state.json"
    assert codex_watch.watch_main(["--once", "--state-file", str(state_file)]) == 0

    assert sent == []
    assert codex_watch.load_state(state_file)["watermark"] >= int(time.time()) - 5


def test_since_overrides_the_watermark(
    codex_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_turn(codex_home, "t1", completed_at=1_000)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "t")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(codex_watch.Sender, "send", lambda self, message: sent.append(message))

    exit_code = codex_watch.watch_main(
        ["--once", "--since", "0", "--state-file", str(tmp_path / "s.json")]
    )

    assert exit_code == 0
    assert len(sent) == 1


def test_missing_credentials_is_an_error(
    codex_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("SLACK_BOT_TOKEN", "SLACK_USER_ID", "LARK_WEBHOOK_URL", "FEISHU_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    assert codex_watch.watch_main(["--once", "--state-file", str(tmp_path / "s.json")]) == 1
