"""Tests for per-session icons."""

from coding_agent_notifier.icons import DEFAULT_ICON, SESSION_ICONS, icon_for, session_icon
from coding_agent_notifier.notifier import build_message


def test_icon_is_stable_for_the_same_seed() -> None:
    assert icon_for("session-a") == icon_for("session-a")
    assert icon_for("  session-a  ") == icon_for("session-a")


def test_icons_are_drawn_from_the_pool() -> None:
    for seed in (f"session-{n}" for n in range(200)):
        assert icon_for(seed) in SESSION_ICONS


def test_the_pool_has_no_duplicates() -> None:
    assert len(set(SESSION_ICONS)) == len(SESSION_ICONS)


def test_different_sessions_usually_differ() -> None:
    icons = {icon_for(f"session-{n}") for n in range(len(SESSION_ICONS))}
    # Hash collisions are expected; the pool should still be well spread.
    assert len(icons) > len(SESSION_ICONS) * 0.5


def test_icon_for_rejects_empty_seeds() -> None:
    assert icon_for("") is None
    assert icon_for("   ") is None
    assert icon_for(None) is None
    assert icon_for(42) is None


def test_session_icon_prefers_the_session_id() -> None:
    payload = {"session_id": "s1", "repo": "/home/user/proj"}

    assert session_icon(payload) == icon_for("s1")


def test_session_icon_falls_back_to_the_workspace() -> None:
    assert session_icon({"repo": "/home/user/proj"}) == icon_for("/home/user/proj")
    assert session_icon({}) == DEFAULT_ICON


def test_explicit_icon_wins() -> None:
    assert session_icon({"icon": "\U0001f680", "session_id": "s1"}) == "\U0001f680"


def test_message_leads_with_the_session_icon() -> None:
    payload = {"session_id": "s1", "session_title": "Work", "repo": "/home/user/proj"}

    assert build_message(payload).splitlines()[0] == f"{icon_for('s1')}  *Work*"


def test_failure_status_is_shown_alongside_the_icon() -> None:
    payload = {
        "session_id": "s1",
        "session_title": "Deploy",
        "status": "failed",
        "repo": "/home/user/proj",
    }

    assert build_message(payload).splitlines()[0] == f"❌ {icon_for('s1')}  *Deploy*"


def test_success_status_does_not_add_a_second_icon() -> None:
    payload = {"session_id": "s1", "session_title": "Deploy", "status": "success"}

    assert build_message(payload).splitlines()[0] == f"{icon_for('s1')}  *Deploy*"
