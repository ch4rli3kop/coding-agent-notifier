"""Extract display metadata from coding-agent transcripts.

Claude Code and Codex both hand their hooks a ``transcript_path``. Those
transcripts already carry a session title, the last user request and per-record
timestamps, so the notifier can build a descriptive message by reading what the
agent has written anyway -- no extra summarisation call is needed.

Two on-disk shapes are supported:

* Claude Code ``~/.claude/projects/<slug>/<session>.jsonl`` -- records typed
  ``ai-title`` (the session name), ``last-prompt``, ``user`` and ``assistant``.
* Codex ``~/.codex/sessions/<date>/rollout-*.jsonl`` -- records typed
  ``response_item`` wrapping ``message`` payloads, plus ``session_meta``.

Every helper degrades to ``None`` rather than raising: a notification that is
missing its title is still worth sending.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Long-running sessions produce transcripts of 100MB+, and everything this
# module needs (the current session name, the last request, the last turn's
# timestamps) lives at the end of the file. Read only the tail of a large one so
# an agent's Stop hook never stalls on parsing.
TAIL_BYTES = 4 * 1024 * 1024

# Claude Code appends tool output, slash-command echoes and injected reminders to
# the transcript as `user` records. None of those are something the person typed.
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
NON_PROMPT_PREFIXES = (
    "<task-notification>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<user-prompt-submit-hook>",
    "[Request interrupted",
)


def _clean_user_text(text: str) -> str:
    """Strip injected blocks; return "" when nothing the person typed remains."""
    cleaned = SYSTEM_REMINDER_RE.sub("", text).strip()
    if not cleaned or cleaned.startswith(NON_PROMPT_PREFIXES):
        return ""
    return cleaned


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _claude_text_blocks(record: Dict[str, Any]) -> str:
    """Join the plain-text blocks of a Claude message, skipping tool traffic."""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _codex_text_blocks(payload: Dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _consume_claude(record: Dict[str, Any], state: Dict[str, Any]) -> None:
    record_type = record.get("type")
    timestamp = _parse_timestamp(record.get("timestamp"))

    if record_type == "ai-title":
        title = record.get("aiTitle")
        if isinstance(title, str) and title.strip():
            state["title"] = title.strip()
    elif record_type == "custom-title":
        # A name the person typed themselves; it outranks the generated one.
        title = record.get("customTitle")
        if isinstance(title, str) and title.strip():
            state["custom_title"] = title.strip()
    elif record_type == "last-prompt":
        # Written one or two turns behind the live conversation, so this is only
        # a fallback for when no usable `user` record is in the parsed tail.
        prompt = record.get("lastPrompt")
        if isinstance(prompt, str) and prompt.strip():
            state["stale_prompt"] = prompt.strip()
    elif record_type == "user":
        if record.get("isMeta") or record.get("isSidechain"):
            return
        text = _clean_user_text(_claude_text_blocks(record))
        if text:
            state["prompt"] = text
            if timestamp:
                state["turn_start"] = timestamp
    elif record_type == "assistant":
        if record.get("isSidechain"):
            return
        if timestamp:
            state["turn_end"] = timestamp
        text = _claude_text_blocks(record)
        if text:
            state["result"] = text


def _consume_codex(record: Dict[str, Any], state: Dict[str, Any]) -> None:
    timestamp = _parse_timestamp(record.get("timestamp"))
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return

    if record.get("type") == "session_meta":
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            state.setdefault("cwd", cwd)
        return

    if payload.get("type") != "message":
        return

    role = payload.get("role")
    text = _codex_text_blocks(payload)
    if role == "user":
        cleaned = _clean_user_text(text)
        if cleaned:
            state["prompt"] = cleaned
            if timestamp:
                state["turn_start"] = timestamp
    elif role == "assistant" and text:
        if timestamp:
            state["turn_end"] = timestamp
        state["result"] = text


def read_transcript(transcript_path: str) -> Dict[str, Any]:
    """Return {title, prompt, turn_start, turn_end, cwd} for a transcript file."""
    state: Dict[str, Any] = {}
    path = Path(transcript_path)
    try:
        if not path.is_file():
            return state
        size = path.stat().st_size
    except OSError:
        return state

    try:
        with path.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()  # discard the partial line we landed in
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                # Codex wraps everything in a `payload`; Claude Code does not.
                if isinstance(record.get("payload"), dict):
                    _consume_codex(record, state)
                else:
                    _consume_claude(record, state)
    except OSError:
        return {}

    if not state.get("prompt") and state.get("stale_prompt"):
        state["prompt"] = state["stale_prompt"]

    # The custom title also lives in a sidecar file, which survives when the
    # renaming happened before the slice of transcript we parsed.
    if not state.get("custom_title"):
        sidecar = read_custom_title(transcript_path)
        if sidecar:
            state["custom_title"] = sidecar
    if state.get("custom_title"):
        state["title"] = state["custom_title"]
    return state


def read_custom_title(transcript_path: str) -> Optional[str]:
    """Read `<session-id>/custom-title.json`, written when a session is renamed."""
    try:
        path = Path(transcript_path)
        sidecar = path.with_suffix("") / "custom-title.json"
        if not sidecar.is_file():
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None

    if isinstance(data, dict):
        title = data.get("customTitle")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def last_line(text: Any) -> Optional[str]:
    """Return the last non-empty line of a reply, without list/heading markers."""
    if not isinstance(text, str):
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^(?:[-*+]\s+|#{1,6}\s+|>\s+|\d+\.\s+)", "", stripped)
        if stripped:
            return stripped
    return None


def read_git_branch(repo: str) -> Optional[str]:
    """Read the checked-out branch straight from .git/HEAD (no subprocess)."""
    try:
        git_path = Path(repo) / ".git"
        if git_path.is_file():
            # Worktree or submodule: `.git` is a file pointing at the real dir.
            pointer = git_path.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return None
            git_path = Path(pointer.split(":", 1)[1].strip())
        head = (git_path / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError):
        return None

    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/") :] or None
    if head:
        return head[:7]  # detached HEAD
    return None


def format_duration(start: Optional[datetime], end: Optional[datetime]) -> Optional[str]:
    if not start or not end:
        return None
    total = int((end - start).total_seconds())
    if total < 0:
        return None
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def enrich_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add session_title/last_prompt/duration/branch from the agent transcript.

    Values already present in the payload win: a hook that supplies its own
    title or duration is more authoritative than anything inferred here.
    """
    if not isinstance(payload, dict):
        return payload

    enriched = dict(payload)
    transcript_path = enriched.get("transcript_path")
    state: Dict[str, Any] = {}
    if isinstance(transcript_path, str) and transcript_path:
        state = read_transcript(transcript_path)

    if state.get("title") and not enriched.get("session_title"):
        enriched["session_title"] = state["title"]
    if state.get("prompt") and not enriched.get("last_prompt"):
        enriched["last_prompt"] = state["prompt"]
    if not enriched.get("last_result"):
        result = last_line(state.get("result"))
        if result:
            enriched["last_result"] = result
    if not enriched.get("repo") and not enriched.get("cwd") and state.get("cwd"):
        enriched["repo"] = state["cwd"]

    if not enriched.get("duration"):
        duration = format_duration(state.get("turn_start"), state.get("turn_end"))
        if duration:
            enriched["duration"] = duration

    repo = enriched.get("repo") or enriched.get("cwd") or enriched.get("workspace")
    if isinstance(repo, str) and repo and not enriched.get("branch"):
        branch = read_git_branch(repo)
        if branch:
            enriched["branch"] = branch

    return enriched
