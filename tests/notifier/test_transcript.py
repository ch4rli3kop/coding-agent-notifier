"""Tests for transcript-derived message enrichment."""

import json
from pathlib import Path

from coding_agent_notifier.notifier import build_message
from coding_agent_notifier.transcript import (
    enrich_payload,
    format_duration,
    last_line,
    read_git_branch,
    read_transcript,
)

CLAUDE_RECORDS = [
    {"type": "ai-title", "aiTitle": "Old title", "sessionId": "s1"},
    {
        "type": "user",
        "timestamp": "2026-09-18T12:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "first request"}]},
    },
    {
        "type": "assistant",
        "timestamp": "2026-09-18T12:00:30.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}]},
    },
    {"type": "ai-title", "aiTitle": "Notifier formatting", "sessionId": "s1"},
    {
        "type": "user",
        "timestamp": "2026-09-18T12:10:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "second request"}]},
    },
    {
        "type": "assistant",
        "timestamp": "2026-09-18T12:12:45.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    },
    {"type": "last-prompt", "lastPrompt": "make the Slack message prettier", "sessionId": "s1"},
]

CODEX_RECORDS = [
    {
        "type": "session_meta",
        "timestamp": "2026-09-18T12:00:00.000Z",
        "payload": {"id": "abc", "cwd": "/home/user/proj"},
    },
    {
        "type": "response_item",
        "timestamp": "2026-09-18T12:01:00.000Z",
        "payload": {"type": "message", "role": "user", "content": [{"text": "run the build"}]},
    },
    {
        "type": "response_item",
        "timestamp": "2026-09-18T12:01:20.000Z",
        "payload": {"type": "reasoning", "content": [{"text": "thinking"}]},
    },
    {
        "type": "response_item",
        "timestamp": "2026-09-18T12:03:20.000Z",
        "payload": {"type": "message", "role": "assistant", "content": [{"text": "build ok"}]},
    },
]


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def test_read_claude_transcript(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_RECORDS)

    state = read_transcript(str(path))

    assert state["title"] == "Notifier formatting"  # last ai-title wins
    # The live `user` record wins over the `last-prompt` record, which Claude
    # Code writes one or two turns behind the conversation.
    assert state["prompt"] == "second request"
    assert state["stale_prompt"] == "make the Slack message prettier"
    assert state["result"] == "done"
    # Duration covers the final turn, not the whole session.
    assert format_duration(state["turn_start"], state["turn_end"]) == "2m 45s"


def test_read_codex_transcript(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "rollout.jsonl", CODEX_RECORDS)

    state = read_transcript(str(path))

    assert state.get("title") is None  # Codex has no session name
    assert state["prompt"] == "run the build"
    assert state["result"] == "build ok"
    assert state["cwd"] == "/home/user/proj"
    assert format_duration(state["turn_start"], state["turn_end"]) == "2m 20s"


def test_read_transcript_tolerates_junk(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('not json\n[1,2,3]\n{"type":"ai-title","aiTitle":"Kept"}\n', encoding="utf-8")

    assert read_transcript(str(path))["title"] == "Kept"


def test_read_transcript_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_transcript(str(tmp_path / "nope.jsonl")) == {}


def test_format_duration_units() -> None:
    from datetime import datetime, timedelta

    start = datetime(2026, 9, 18, 12, 0, 0)
    assert format_duration(start, start + timedelta(seconds=42)) == "42s"
    assert format_duration(start, start + timedelta(minutes=2, seconds=5)) == "2m 05s"
    assert format_duration(start, start + timedelta(hours=1, minutes=3)) == "1h 03m"
    assert format_duration(None, start) is None
    assert format_duration(start, start - timedelta(seconds=1)) is None


def test_read_git_branch(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/feature/pretty-messages\n", encoding="utf-8")

    assert read_git_branch(str(tmp_path)) == "feature/pretty-messages"


def test_read_git_branch_detached(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("a1b2c3d4e5f6\n", encoding="utf-8")

    assert read_git_branch(str(tmp_path)) == "a1b2c3d"


def test_read_git_branch_worktree(tmp_path: Path) -> None:
    real_git = tmp_path / "real.git"
    real_git.mkdir()
    (real_git / "HEAD").write_text("ref: refs/heads/wt\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {real_git}\n", encoding="utf-8")

    assert read_git_branch(str(work)) == "wt"


def test_read_git_branch_without_repo(tmp_path: Path) -> None:
    assert read_git_branch(str(tmp_path)) is None


def test_enrich_payload_does_not_override_explicit_values(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_RECORDS)

    enriched = enrich_payload(
        {
            "transcript_path": str(path),
            "cwd": str(tmp_path),
            "session_title": "explicit",
            "duration": "9m 99s",
        }
    )

    assert enriched["session_title"] == "explicit"
    assert enriched["duration"] == "9m 99s"
    assert enriched["last_prompt"] == "second request"


def test_enrich_payload_without_transcript_is_a_noop() -> None:
    payload = {"status": "ok", "repo": "/tmp/x"}

    assert enrich_payload(payload) == payload


def test_rich_message_layout(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_RECORDS)
    repo = tmp_path / "coding-agent-notifier"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    payload = enrich_payload(
        {
            "hook_event_name": "Stop",
            "transcript_path": str(path),
            "cwd": str(repo),
        }
    )
    lines = build_message(payload).splitlines()

    assert lines[0] == "✅  *Notifier formatting*"
    assert lines[1] == "`coding-agent-notifier` · main · 2m 45s · Codex"
    assert lines[2] == "\U0001f4ac second request"
    assert lines[3] == "\u21b3 done"


def test_rich_message_marks_failure_and_truncates_prompt(tmp_path: Path) -> None:
    payload = {
        "status": "failed",
        "session_title": "Long session",
        "last_prompt": "x" * 400,
        "repo": "/home/user/proj",
    }

    lines = build_message(payload).splitlines()

    assert lines[0].startswith("❌")
    prompt_line = lines[-1]
    assert prompt_line.endswith("…")
    assert len(prompt_line) <= 2 + 100


def test_legacy_payloads_keep_the_flat_layout() -> None:
    """Custom hook payloads without transcript fields must not change shape."""
    message = build_message({"status": "success", "title": "CI", "repo": "/tmp/x"})

    assert message == "CI\nStatus: success\nRepo: /tmp/x"


def test_prompt_becomes_the_headline_without_a_session_title() -> None:
    """Codex transcripts have no session name, so the request is the headline."""
    payload = {"last_prompt": "archive the old reports", "repo": "/home/user/proj"}

    lines = build_message(payload).splitlines()

    assert lines[0] == "✅  *archive the old reports*"
    assert not any(line.startswith("\U0001f4ac") for line in lines)


def test_stale_last_prompt_is_only_a_fallback(tmp_path: Path) -> None:
    """A tail without any `user` record still yields something to show."""
    path = _write_jsonl(
        tmp_path / "tail.jsonl",
        [
            {"type": "last-prompt", "lastPrompt": "older request", "sessionId": "s1"},
            {
                "type": "assistant",
                "timestamp": "2026-09-18T12:00:00.000Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            },
        ],
    )

    assert read_transcript(str(path))["prompt"] == "older request"


def test_injected_user_records_are_not_treated_as_requests(tmp_path: Path) -> None:
    """Tool output, slash-command echoes and subagent turns are not the request."""
    noise = [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "real request"}]},
        },
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]},
        },
        {
            "type": "user",
            "isMeta": True,
            "message": {
                "role": "user",
                "content": "<local-command-caveat>Caveat: ...</local-command-caveat>",
            },
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "<task-notification>done</task-notification>"},
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "<command-name>/compact</command-name>"},
        },
        {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
        {
            "type": "user",
            "isSidechain": True,
            "message": {"role": "user", "content": [{"type": "text", "text": "subagent prompt"}]},
        },
    ]

    assert (
        read_transcript(str(_write_jsonl(tmp_path / "noise.jsonl", noise)))["prompt"]
        == "real request"
    )


def test_system_reminders_are_stripped_from_the_request(tmp_path: Path) -> None:
    text = "do the thing\n<system-reminder>hidden instructions</system-reminder>"
    records = [
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    ]

    assert (
        read_transcript(str(_write_jsonl(tmp_path / "r.jsonl", records)))["prompt"]
        == "do the thing"
    )


def test_reminder_only_record_is_ignored(tmp_path: Path) -> None:
    records = [
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "kept"}]},
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "<system-reminder>x</system-reminder>"}],
            },
        },
    ]

    assert read_transcript(str(_write_jsonl(tmp_path / "r.jsonl", records)))["prompt"] == "kept"


def test_last_line_skips_blanks_and_markers() -> None:
    assert last_line("first\n\n- final point\n\n") == "final point"
    assert last_line("## Heading") == "Heading"
    assert last_line("1. numbered") == "numbered"
    assert last_line("   \n  ") is None
    assert last_line(None) is None


def test_sidechain_replies_do_not_become_the_result(tmp_path: Path) -> None:
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-18T12:00:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "main answer"}]},
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-18T12:00:10.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "subagent answer"}],
            },
        },
    ]

    assert (
        read_transcript(str(_write_jsonl(tmp_path / "s.jsonl", records)))["result"] == "main answer"
    )


def test_custom_title_outranks_generated_title(tmp_path: Path) -> None:
    records = CLAUDE_RECORDS + [
        {"type": "custom-title", "customTitle": "R2U work", "sessionId": "s1"},
    ]

    assert read_transcript(str(_write_jsonl(tmp_path / "s.jsonl", records)))["title"] == "R2U work"


def test_custom_title_sidecar_is_used_when_the_record_is_out_of_range(tmp_path: Path) -> None:
    """Renaming may predate the slice of a long transcript that gets parsed."""
    path = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_RECORDS)
    sidecar_dir = tmp_path / "session"
    sidecar_dir.mkdir()
    (sidecar_dir / "custom-title.json").write_text(
        json.dumps({"customTitle": "Renamed session"}), encoding="utf-8"
    )

    assert read_transcript(str(path))["title"] == "Renamed session"


def test_broken_custom_title_sidecar_is_ignored(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_RECORDS)
    sidecar_dir = tmp_path / "session"
    sidecar_dir.mkdir()
    (sidecar_dir / "custom-title.json").write_text("{not json", encoding="utf-8")

    assert read_transcript(str(path))["title"] == "Notifier formatting"
