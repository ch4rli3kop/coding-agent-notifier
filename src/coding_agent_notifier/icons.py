"""Give every session a stable icon of its own.

When several agent sessions run at once, the leading emoji is what the eye lands
on first in a Slack list, so it is worth more as an identity than as a status
flag -- especially since Claude Code and Codex hooks report no status at all.

The icon is derived from the session id, so the same session keeps the same icon
for its whole life and across machines. Selection uses rendezvous hashing rather
than `hash % len(pool)`: with the modulo, growing the pool would re-assign every
session's icon, whereas here a session only moves if a newly added icon happens
to outscore its current one.
"""

import hashlib
from typing import Any, Dict, Optional

# Grouped by dominant colour so the pool stays visually varied and easy to
# extend. Entries must render in colour without a variation selector -- see
# tests/notifier/test_icons.py, which rejects text-default code points.
SESSION_ICONS = (
    # red
    "🍎",
    "🍒",
    "🌹",
    "🎈",
    "🧨",
    "🏮",
    "🦞",
    "💥",
    "🚨",
    "🥊",
    # orange
    "🦊",
    "🍊",
    "🎃",
    "🏀",
    "🥕",
    "🔥",
    "🧡",
    "🍁",
    "🍑",
    "🦁",
    # yellow
    "🍋",
    "🌻",
    "⭐",
    "🐤",
    "🧀",
    "🍌",
    "🐝",
    "⚡",
    "💛",
    "🌼",
    # green
    "🐸",
    "🥝",
    "🌵",
    "🍀",
    "🥑",
    "🐢",
    "💚",
    "🥦",
    "🐉",
    "🎾",
    # blue
    "🐬",
    "💎",
    "🧊",
    "🫐",
    "🌊",
    "💙",
    "🦋",
    "🐳",
    "🔵",
    "🌀",
    # purple
    "🍇",
    "🔮",
    "🟣",
    "💜",
    "🦄",
    "👾",
    "🍆",
    "🎆",
    "🔯",
    "🪀",
    # pink
    "🌸",
    "🐷",
    "🦩",
    "💗",
    "🌺",
    "🎀",
    "🍬",
    "🦐",
    "🧁",
    "👛",
    # brown
    "🐻",
    "🍩",
    "🧸",
    "🦉",
    "🌰",
    "🎩",
    "🪵",
    "🥔",
    "🐴",
    "🏈",
    # black, white and grey
    "🐼",
    "🦢",
    "🐧",
    "🎱",
    "⚪",
    "⚫",
    "🦓",
    "🐺",
    "🎹",
    "🖤",
    # multicoloured
    "🌈",
    "🎨",
    "🦜",
    "🎪",
    "🎡",
    "🎠",
    "🪁",
    "🎁",
    "🍭",
    "🧩",
    "🎲",
    "🎯",
    "🚀",
    "🛸",
    "🪐",
    "🌙",
    "🍄",
    "🐙",
    "🦖",
    "🐡",
    "🦈",
    "🐨",
    "🐯",
    "🐰",
    "🐹",
    "🐵",
    "🦥",
    "🦔",
    "🦦",
    "🦚",
)

DEFAULT_ICON = "✅"


def icon_for(seed: Any) -> Optional[str]:
    """Pick this seed's icon, stably.

    Rendezvous hashing: score every icon against the seed and take the highest.
    SHA-256 rather than Python's hash(), which is salted per process.
    """
    if not isinstance(seed, str) or not seed.strip():
        return None

    seed_bytes = seed.strip().encode("utf-8")
    best_icon = SESSION_ICONS[0]
    best_score = b""
    for icon in SESSION_ICONS:
        score = hashlib.sha256(seed_bytes + b"\x00" + icon.encode("utf-8")).digest()
        if score > best_score:
            best_score, best_icon = score, icon
    return best_icon


def session_icon(payload: Dict[str, Any]) -> str:
    """Resolve a payload's icon, preferring the most session-specific id."""
    if not isinstance(payload, dict):
        return DEFAULT_ICON

    explicit = payload.get("icon")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    for key in ("session_id", "thread-id", "session-id", "sessionId", "transcript_path"):
        icon = icon_for(payload.get(key))
        if icon:
            return icon

    # No session identity: fall back to the workspace so at least each repo is
    # recognisable, then to the plain status icon.
    for key in ("repo", "cwd", "workspace"):
        icon = icon_for(payload.get(key))
        if icon:
            return icon

    return DEFAULT_ICON
