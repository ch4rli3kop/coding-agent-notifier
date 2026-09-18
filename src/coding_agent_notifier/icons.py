"""Give every session a stable icon of its own.

When several agent sessions run at once, the leading emoji is what the eye
lands on first in a Slack list, so it is worth more as an identity than as a
status flag -- especially since Claude Code and Codex hooks report no status at
all. The icon is derived from the session id, so the same session keeps the same
icon for its whole life and across machines.
"""

import hashlib
from typing import Any, Dict, Optional

# Visually distinct emoji that render consistently and need no variation
# selector. Order is part of the mapping: inserting into the middle would
# re-assign icons to existing sessions, so new entries go at the end.
SESSION_ICONS = (
    "\U0001f98a",  # fox
    "\U0001f43c",  # panda
    "\U0001f428",  # koala
    "\U0001f42f",  # tiger
    "\U0001f981",  # lion
    "\U0001f438",  # frog
    "\U0001f419",  # octopus
    "\U0001f98b",  # butterfly
    "\U0001f422",  # turtle
    "\U0001f989",  # owl
    "\U0001f41d",  # bee
    "\U0001f42c",  # dolphin
    "\U0001f9a9",  # flamingo
    "\U0001f99c",  # parrot
    "\U0001f433",  # whale
    "\U0001f988",  # shark
    "\U0001f40a",  # crocodile
    "\U0001f996",  # t-rex
    "\U0001f427",  # penguin
    "\U0001f430",  # rabbit
    "\U0001f34e",  # apple
    "\U0001f34a",  # tangerine
    "\U0001f34b",  # lemon
    "\U0001f347",  # grapes
    "\U0001f353",  # strawberry
    "\U0001f351",  # peach
    "\U0001f34d",  # pineapple
    "\U0001f95d",  # kiwi
    "\U0001f951",  # avocado
    "\U0001f33d",  # corn
    "\U0001f344",  # mushroom
    "\U0001f335",  # cactus
    "\U0001f33b",  # sunflower
    "\U0001f341",  # maple leaf
    "\U0001f30a",  # wave
    "\U0001f525",  # fire
    "\U0001f308",  # rainbow
    "\U0001f319",  # crescent moon
    "\U0001f388",  # balloon
    "\U0001f3a8",  # artist palette
    "\U0001f3b8",  # guitar
    "\U0001f3ba",  # trumpet
    "\U0001f680",  # rocket
    "\U0001f6f8",  # flying saucer
    "\U0001f9ed",  # compass
    "\U0001f52e",  # crystal ball
    "\U0001f48e",  # gem
    "\U0001f9e9",  # puzzle piece
    "\U0001f3b2",  # game die
    "\U0001fa81",  # kite
    "\U0001f6f9",  # skateboard
    "\U0001f3c0",  # basketball
    "\U0001f3af",  # bullseye
    "\U0001f3aa",  # circus tent
    "\U0001f9ff",  # nazar amulet
    "\U0001f9ca",  # ice cube
)

DEFAULT_ICON = "✅"


def icon_for(seed: Any) -> Optional[str]:
    """Pick this seed's icon, stably. Python's hash() is salted, so use SHA-256."""
    if not isinstance(seed, str) or not seed.strip():
        return None
    digest = hashlib.sha256(seed.strip().encode("utf-8")).digest()
    return SESSION_ICONS[int.from_bytes(digest[:8], "big") % len(SESSION_ICONS)]


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
