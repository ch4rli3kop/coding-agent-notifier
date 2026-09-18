"""Tests for Codex thread metadata lookups and internal-turn suppression."""

import sqlite3
from pathlib import Path

import pytest

from coding_agent_notifier import codex_state, notifier
from coding_agent_notifier.icons import icon_for
from coding_agent_notifier.notifier import build_message, normalize_payload
from coding_agent_notifier.transcript import format_duration_ms

THREAD_ID = "01a0b4c2-5c07-7fe0-875e-4cb910fbb396"
TURN_ID = "01a0b4c3-0788-75a3-aa17-47dd9277e92f"


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal stand-in for the two Codex databases the notifier reads."""
    state = sqlite3.connect(tmp_path / "state_5.sqlite")
    state.execute(
        "create table threads (id text, name text, title text, cwd text, git_branch text)"
    )
    state.execute(
        "insert into threads values (?, ?, ?, ?, ?)",
        (THREAD_ID, "실험1", "안녕", "/home/user/proj", "main"),
    )
    state.execute(
        "insert into threads values (?, ?, ?, ?, ?)",
        ("unnamed", None, "auto title", "/home/user/proj", None),
    )
    state.commit()
    state.close()

    history = sqlite3.connect(tmp_path / "thread_history_1.sqlite")
    history.execute("create table thread_turns (thread_id text, turn_id text, duration_ms integer)")
    history.execute("insert into thread_turns values (?, ?, ?)", (THREAD_ID, TURN_ID, 1468))
    history.commit()
    history.close()

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def test_read_thread_prefers_the_thread_name(codex_home: Path) -> None:
    assert codex_state.read_thread(THREAD_ID) == {
        "title": "실험1",
        "cwd": "/home/user/proj",
        "branch": "main",
    }


def test_read_thread_falls_back_to_the_generated_title(codex_home: Path) -> None:
    assert codex_state.read_thread("unnamed")["title"] == "auto title"


def test_read_thread_unknown_id(codex_home: Path) -> None:
    assert codex_state.read_thread("nope") == {}


def test_read_thread_without_a_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert codex_state.read_thread(THREAD_ID) == {}


def test_read_turn_duration(codex_home: Path) -> None:
    assert codex_state.read_turn_duration_ms(THREAD_ID, TURN_ID) == 1468
    assert codex_state.read_turn_duration_ms(THREAD_ID, "other") is None
    assert codex_state.read_turn_duration_ms("", "") is None


def test_newest_database_generation_wins(codex_home: Path) -> None:
    """state_10 must beat state_5, which sorting by name alone gets wrong."""
    newer = sqlite3.connect(codex_home / "state_10.sqlite")
    newer.execute(
        "create table threads (id text, name text, title text, cwd text, git_branch text)"
    )
    newer.execute(
        "insert into threads values (?, ?, ?, ?, ?)", (THREAD_ID, "newer", None, None, None)
    )
    newer.commit()
    newer.close()

    assert codex_state.read_thread(THREAD_ID)["title"] == "newer"


def test_format_duration_ms() -> None:
    assert format_duration_ms(1468) == "1s"
    assert format_duration_ms(125_000) == "2m 05s"
    assert format_duration_ms(None) is None
    assert format_duration_ms(-1) is None


def test_internal_title_turn_is_detected() -> None:
    assert codex_state.is_internal_title_turn(
        {
            "input-messages": [
                "Generate a concise, single-line task title of at most 36 characters"
            ],
            "last-assistant-message": '{"title":"인사하기"}',
        }
    )
    # The reply shape alone is enough, in case the prompt wording changes.
    assert codex_state.is_internal_title_turn({"last-assistant-message": '{"title": "x"}'})
    assert not codex_state.is_internal_title_turn(
        {"input-messages": ["안녕"], "last-assistant-message": "안녕하세요!"}
    )
    assert not codex_state.is_internal_title_turn({})


def test_notify_payload_gains_thread_name_and_duration(codex_home: Path) -> None:
    payload = normalize_payload(
        {
            "type": "agent-turn-complete",
            "thread-id": THREAD_ID,
            "turn-id": TURN_ID,
            "cwd": "/home/user/proj",
            "input-messages": ["안녕"],
            "last-assistant-message": "안녕하세요! 무엇을 도와드릴까요?",
        }
    )

    lines = build_message(payload).splitlines()
    assert lines[0] == f"{icon_for(THREAD_ID)}  *실험1*"
    assert lines[1] == "`proj` · main · 1s · Codex"
    assert lines[2] == "\U0001f4ac 안녕"
    assert lines[3] == "↳ 안녕하세요! 무엇을 도와드릴까요?"


def test_main_skips_the_internal_title_turn(
    codex_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setenv("SLACK_USER_ID", "U1")
    sent: list[str] = []
    monkeypatch.setattr(
        notifier.SlackNotifier,
        "send_dm",
        lambda self, user_id, message: sent.append(message),
    )

    exit_code = notifier.slack_main(
        [
            "--payload",
            '{"type":"agent-turn-complete","input-messages":'
            '["Generate a concise, single-line task title"],'
            '"last-assistant-message":"{\\"title\\":\\"x\\"}"}',
        ]
    )

    assert exit_code == 0
    assert sent == []
