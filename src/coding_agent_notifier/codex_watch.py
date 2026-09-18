"""Notify about Codex turns that ended in an error.

Codex has no failure hook: `Stop` skips interrupted and aborted turns, `notify`
only ever sends `agent-turn-complete`, and a turn that dies on a usage limit or
a safeguard refusal inside a running session reports nothing at all. But Codex
does record the failure, so this watcher polls its own history database and
sends the notification the agent never does.

It keeps a small state file so a restart does not re-send anything, and starts
from "now" on first run rather than replaying every past failure.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .codex_state import (
    error_message,
    read_failed_turns,
    read_thread,
    read_thread_rollout_path,
)
from .notifier import (
    LarkNotifier,
    NotificationError,
    SlackNotifier,
    _load_default_env_file,
    build_message,
)
from .transcript import enrich_payload, format_duration_ms, last_line

LOG = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 30
# `completed_at` has one-second resolution, so re-read a little of the past and
# let the per-turn record decide what is new.
WATERMARK_LOOKBACK_SECONDS = 5
# A retried rate limit records the same failure several times over.
COALESCE_SECONDS = 600
MAX_REMEMBERED_TURNS = 500


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "coding-agent-notifier" / "codex-watch.json"


def load_state(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash cannot leave a half-written state file.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        LOG.error("Could not write state file %s: %s", path, exc)


def _coalesce_key(turn: Dict[str, Any]) -> str:
    """Same thread, same error: one notification, not one per retry."""
    message = error_message(turn.get("error_json")) or turn.get("status") or ""
    return f"{turn.get('thread_id')}\x00{message[:120]}"


def build_payload(turn: Dict[str, Any]) -> Dict[str, Any]:
    thread_id = str(turn.get("thread_id") or "")
    thread = read_thread(thread_id)

    payload: Dict[str, Any] = {
        "session_id": thread_id,
        "status": turn.get("status") or "failed",
    }
    if thread.get("title"):
        payload["session_title"] = thread["title"]
    if thread.get("cwd"):
        payload["repo"] = thread["cwd"]
    if thread.get("branch"):
        payload["branch"] = thread["branch"]

    duration = format_duration_ms(turn.get("duration_ms"))
    if duration:
        payload["duration"] = duration

    message = error_message(turn.get("error_json"))
    closing = last_line(message) if message else None
    if closing:
        payload["last_result"] = closing

    # The rollout is the transcript, so the request that failed can be shown.
    rollout = read_thread_rollout_path(thread_id)
    if rollout:
        payload["transcript_path"] = rollout

    return enrich_payload(payload)


class Sender:
    """Sends to whichever targets the environment is configured for."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.token = os.environ.get("SLACK_BOT_TOKEN")
        self.user_id = os.environ.get("SLACK_USER_ID")
        self.webhook = os.environ.get("LARK_WEBHOOK_URL") or os.environ.get("FEISHU_WEBHOOK_URL")

    @property
    def configured(self) -> bool:
        return bool(self.dry_run or (self.token and self.user_id) or self.webhook)

    def send(self, message: str) -> None:
        if self.dry_run:
            print(message)
            print("-" * 60)
            return
        if self.token and self.user_id:
            SlackNotifier(self.token).send_dm(self.user_id, message)
        if self.webhook:
            LarkNotifier(self.webhook).send_text(message)


def poll_once(state: Dict[str, Any], sender: Sender, statuses: tuple) -> int:
    """Send a notification per new failed turn. Returns how many were sent."""
    watermark = int(state.get("watermark") or 0)
    notified: List[str] = list(state.get("notified") or [])
    recent: Dict[str, int] = dict(state.get("recent") or {})
    seen = set(notified)
    now = int(time.time())

    sent = 0
    for turn in read_failed_turns(max(0, watermark - WATERMARK_LOOKBACK_SECONDS), statuses):
        turn_id = str(turn.get("turn_id") or "")
        completed_at = int(turn.get("completed_at") or 0)
        if not turn_id or turn_id in seen:
            continue

        key = _coalesce_key(turn)
        last_sent = recent.get(key)
        if last_sent is not None and now - last_sent < COALESCE_SECONDS:
            LOG.info("Coalescing repeat failure for %s", turn.get("thread_id"))
            seen.add(turn_id)
            notified.append(turn_id)
            watermark = max(watermark, completed_at)
            continue

        try:
            sender.send(build_message(build_payload(turn)))
        except NotificationError as exc:
            # Leave the turn unmarked so the next tick retries it.
            LOG.error("Failed to send Codex failure notification: %s", exc)
            break

        sent += 1
        seen.add(turn_id)
        notified.append(turn_id)
        recent[key] = now
        watermark = max(watermark, completed_at)

    state["watermark"] = watermark
    state["notified"] = notified[-MAX_REMEMBERED_TURNS:]
    state["recent"] = {k: v for k, v in recent.items() if now - v < COALESCE_SECONDS * 2}
    return sent


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Codex's history for turns that ended in an error and notify."
    )
    parser.add_argument("--once", action="store_true", help="Poll a single time and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between polls (default: {DEFAULT_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--include-interrupted",
        action="store_true",
        help="Also report turns you interrupted yourself",
    )
    parser.add_argument(
        "--since",
        type=int,
        help="Unix timestamp to start from, instead of the stored watermark",
    )
    parser.add_argument("--state-file", help="Where to keep the watermark (default: XDG state dir)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the messages instead of sending them"
    )
    parser.add_argument("--env-file", help="Path to a .env file with the notifier credentials")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Logging level (default: INFO, which suits a service log)",
    )
    return parser.parse_args(argv)


def watch_main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not _load_default_env_file(args.env_file):
        return 1

    sender = Sender(dry_run=args.dry_run)
    if not sender.configured:
        LOG.error("No Slack or Feishu/Lark credentials found; nothing to notify with")
        return 1

    statuses = ("failed", "interrupted") if args.include_interrupted else ("failed",)
    state_path = Path(args.state_file) if args.state_file else default_state_path()
    state = load_state(state_path)

    if args.since is not None:
        state["watermark"] = args.since
    elif not state.get("watermark"):
        # First run: start from now, or every past failure would arrive at once.
        state["watermark"] = int(time.time())
        LOG.info("First run; watching for failures from now on")

    LOG.info("Watching Codex turns %s every %ss", "/".join(statuses), args.interval)
    try:
        while True:
            sent = poll_once(state, sender, statuses)
            save_state(state_path, state)
            if sent:
                LOG.info("Sent %s failure notification(s)", sent)
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        save_state(state_path, state)
        return 0


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(watch_main())
