"""Tests for Claude Code's StopFailure hook, which reports a turn that errored."""

from coding_agent_notifier.icons import icon_for
from coding_agent_notifier.notifier import build_message, normalize_payload

BASE = {
    "session_id": "s1",
    "cwd": "/home/user/proj",
    "hook_event_name": "StopFailure",
    "prompt_id": "p1",
}


def test_stop_failure_is_marked_as_a_failure() -> None:
    payload = normalize_payload(
        {**BASE, "error": "unknown", "last_assistant_message": "API Error: 400 bad request"}
    )

    assert payload["status"] == "failed"
    assert payload["last_result"] == "API Error: 400 bad request"

    lines = build_message({**payload, "session_title": "Work"}).splitlines()
    assert lines[0] == f"❌ {icon_for('s1')}  *Work*"
    assert lines[-1] == "↳ API Error: 400 bad request"


def test_unknown_error_string_is_not_used_as_the_closing_line() -> None:
    """`error` is frequently the literal "unknown"; prefer anything else."""
    payload = normalize_payload(
        {**BASE, "error": "unknown", "error_details": "usage limit reached"}
    )

    assert payload["last_result"] == "usage limit reached"


def test_error_field_is_used_when_it_is_the_only_detail() -> None:
    payload = normalize_payload({**BASE, "error": "overloaded_error"})

    assert payload["last_result"] == "overloaded_error"


def test_stop_failure_without_any_detail_still_reports_failure() -> None:
    payload = normalize_payload({**BASE, "error": "unknown"})

    assert payload["status"] == "failed"
    assert "last_result" not in payload
    assert build_message({**payload, "session_title": "Work"}).splitlines()[0].startswith("❌")


def test_an_explicit_status_is_not_overwritten() -> None:
    payload = normalize_payload({**BASE, "status": "aborted", "error": "x"})

    assert payload["status"] == "aborted"


def test_a_normal_stop_payload_is_untouched() -> None:
    payload = {"session_id": "s1", "hook_event_name": "Stop", "cwd": "/home/user/proj"}

    assert normalize_payload(payload) == payload
