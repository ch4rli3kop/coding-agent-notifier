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
    icons = {icon_for(f"session-{n}") for n in range(len(SESSION_ICONS) * 4)}
    # Collisions are expected; the pool should still be well spread.
    assert len(icons) > len(SESSION_ICONS) * 0.8


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


# Emoji whose default presentation is text: without a U+FE0F variation selector
# they render as monochrome glyphs, which defeats the point of a colour-coded
# pool. This is the subset of ranges the pool draws from.
TEXT_DEFAULT_CODEPOINTS = frozenset(
    list(range(0x1F321, 0x1F32D))
    + [0x1F336, 0x1F37D]
    + list(range(0x1F396, 0x1F398))
    + list(range(0x1F399, 0x1F39C))
    + list(range(0x1F39E, 0x1F3A0))
    + list(range(0x1F3CB, 0x1F3CF))
    + list(range(0x1F3D4, 0x1F3E0))
    + list(range(0x1F3F3, 0x1F3F6))
    + [0x1F3F7, 0x1F43F, 0x1F441, 0x1F4FD]
    + list(range(0x1F549, 0x1F54B))
    + list(range(0x1F56F, 0x1F571))
    + list(range(0x1F573, 0x1F57B))
    + [0x1F587]
    + list(range(0x1F58A, 0x1F58E))
    + [0x1F590, 0x1F5A5, 0x1F5A8, 0x1F5B1, 0x1F5B2, 0x1F5BC]
    + list(range(0x1F5C2, 0x1F5C5))
    + list(range(0x1F5D1, 0x1F5D4))
    + list(range(0x1F5DC, 0x1F5DF))
    + [0x1F5E1, 0x1F5E3, 0x1F5E8, 0x1F5EF, 0x1F5F3, 0x1F5FA, 0x1F6CB]
    + list(range(0x1F6CD, 0x1F6D0))
    + list(range(0x1F6E0, 0x1F6E6))
    + [0x1F6E9, 0x1F6F0, 0x1F6F3]
    + [0x2B50 + 0]  # keep the list explicit; U+2B50 is emoji-default and allowed
)
TEXT_DEFAULT_CODEPOINTS = TEXT_DEFAULT_CODEPOINTS - {0x2B50}


def test_pool_is_large_enough_to_separate_concurrent_sessions() -> None:
    assert len(SESSION_ICONS) >= 100


def test_pool_entries_render_in_colour_without_a_variation_selector() -> None:
    for icon in SESSION_ICONS:
        assert "\ufe0f" not in icon, f"{icon!r} carries a variation selector"
        assert "\u200d" not in icon, f"{icon!r} is a ZWJ sequence"
        assert len(icon) <= 2, f"{icon!r} is more than one code point"
        assert (
            ord(icon[0]) not in TEXT_DEFAULT_CODEPOINTS
        ), f"{icon!r} defaults to a monochrome text glyph"


def test_growing_the_pool_keeps_most_sessions_on_their_icon() -> None:
    """Rendezvous hashing, not modulo: a bigger pool must not reshuffle everything."""
    import hashlib

    def pick(seed: str, pool: tuple) -> str:
        return max(
            pool, key=lambda i: hashlib.sha256(seed.encode() + b"\x00" + i.encode()).digest()
        )

    grown = SESSION_ICONS + ("\U0001f9ed", "\U0001f9f2", "\U0001fa90")
    seeds = [f"session-{n}" for n in range(500)]
    kept = sum(1 for s in seeds if pick(s, SESSION_ICONS) == pick(s, grown))

    # Only seeds that the new icons outscore should move: roughly 3/123 of them.
    assert kept / len(seeds) > 0.95
