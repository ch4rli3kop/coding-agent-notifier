"""Look up Codex thread metadata that its `notify` payload leaves out.

Codex reports a finished turn with `thread-id`, `turn-id`, `cwd`,
`input-messages` and `last-assistant-message` -- but not the thread's name or
how long the turn took. Both are in Codex's own SQLite state, so this module
reads them back:

* ``$CODEX_HOME/state_*.sqlite`` -- ``threads.name`` (the thread name shown in
  the UI) and ``threads.title`` (an auto-generated fallback).
* ``$CODEX_HOME/thread_history_*.sqlite`` -- ``thread_turns.duration_ms``.

These are Codex internals rather than a published interface, so every lookup is
best-effort: any failure returns ``None`` and the notification goes out without
the extra detail.
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

# Codex names its databases with a schema generation suffix (state_5.sqlite).
STATE_DB_GLOB = "state_*.sqlite"
HISTORY_DB_GLOB = "thread_history_*.sqlite"
DB_TIMEOUT_SECONDS = 2.0

# The turn Codex runs against itself to name a new thread. It is not work the
# person asked for, so it must not raise a notification.
TITLE_TURN_PROMPT_RE = re.compile(
    r"^generate a (?:concise|short)[^\n]*\btitle\b",
    re.IGNORECASE,
)
TITLE_TURN_REPLY_RE = re.compile(r'^\s*\{\s*"title"\s*:\s*".*"\s*\}\s*$', re.DOTALL)


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _newest_db(pattern: str) -> Optional[Path]:
    try:
        candidates = sorted(codex_home().glob(pattern))
    except OSError:
        return None
    if not candidates:
        return None

    def generation(path: Path) -> int:
        match = re.search(r"_(\d+)\.sqlite$", path.name)
        return int(match.group(1)) if match else -1

    return max(candidates, key=generation)


def _query(db_path: Path, sql: str, params: tuple) -> Optional[sqlite3.Row]:
    try:
        connection = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=DB_TIMEOUT_SECONDS
        )
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def read_thread(thread_id: str) -> Dict[str, Any]:
    """Return {title, cwd, branch} for a Codex thread id."""
    if not thread_id:
        return {}
    db_path = _newest_db(STATE_DB_GLOB)
    if not db_path:
        return {}

    row = _query(
        db_path,
        "select name, title, cwd, git_branch from threads where id = ?",
        (thread_id,),
    )
    if row is None:
        return {}

    result: Dict[str, Any] = {}
    # `name` is the thread name; `title` is derived from the first message and
    # duplicates what the request line already shows, so it is a last resort.
    for key in ("name", "title"):
        value = row[key]
        if isinstance(value, str) and value.strip():
            result["title"] = value.strip()
            break
    for source, key in (("cwd", "cwd"), ("git_branch", "branch")):
        value = row[source]
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def read_turn_duration_ms(thread_id: str, turn_id: str) -> Optional[int]:
    if not thread_id or not turn_id:
        return None
    db_path = _newest_db(HISTORY_DB_GLOB)
    if not db_path:
        return None

    row = _query(
        db_path,
        "select duration_ms from thread_turns where thread_id = ? and turn_id = ?",
        (thread_id, turn_id),
    )
    if row is None:
        return None
    duration = row["duration_ms"]
    return duration if isinstance(duration, int) and duration >= 0 else None


def is_internal_title_turn(payload: Dict[str, Any]) -> bool:
    """True when the turn is Codex naming its own thread, not the person's work."""
    if not isinstance(payload, dict):
        return False

    messages = payload.get("input-messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, str) and TITLE_TURN_PROMPT_RE.match(last.strip()):
            return True

    reply = payload.get("last-assistant-message")
    if isinstance(reply, str) and TITLE_TURN_REPLY_RE.match(reply):
        return True

    return False
