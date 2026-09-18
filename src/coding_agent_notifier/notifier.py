import argparse
import json
import math
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests import Response, Session

from .codex_state import is_internal_title_turn, read_thread, read_turn_duration_ms
from .transcript import enrich_payload, format_duration_ms, last_line

SLACK_API_BASE = "https://slack.com/api"
DEFAULT_TIMEOUT_SECONDS = 10

LOG = logging.getLogger(__name__)


class NotificationError(Exception):
    """Raised when a notification provider rejects a request."""


SlackNotificationError = NotificationError


class SlackNotifier:
    """Minimal Slack Web API client for DM notifications."""

    def __init__(
        self,
        token: str,
        session: Optional[Session] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.token = token
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{SLACK_API_BASE}/{endpoint.lstrip('/')}"
        attempts = 0
        max_attempts = 2

        while True:
            attempts += 1
            try:
                response = self.session.post(
                    url, headers=self._headers(), json=payload, timeout=self.timeout_seconds
                )
            except requests.RequestException as exc:  # pragma: no cover - requests raises rarely
                raise NotificationError(f"Request to Slack failed: {exc}") from exc

            if response.status_code == 429 and attempts < max_attempts:
                retry_header = response.headers.get("Retry-After")
                retry_after = 1
                if retry_header:
                    try:
                        retry_val = float(retry_header)
                        retry_after = max(1, int(math.ceil(retry_val)))
                    except (ValueError, TypeError):
                        retry_after = 1
                LOG.warning(
                    "Slack rate limited request to %s, retrying in %s seconds",
                    endpoint,
                    retry_after,
                )
                time.sleep(retry_after)
                continue

            if response.status_code >= 500 and attempts < max_attempts:
                LOG.warning(
                    "Slack returned %s for %s, retrying once", response.status_code, endpoint
                )
                time.sleep(1)
                continue

            break

        self._raise_for_response(response)
        data = self._parse_json(response)
        if not data.get("ok"):
            raise NotificationError(f"Slack API error: {data.get('error', 'unknown_error')}")
        return data

    def _raise_for_response(self, response: Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise NotificationError(f"HTTP error from Slack: {response.status_code}") from exc

    @staticmethod
    def _parse_json(response: Response) -> Dict[str, Any]:
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise NotificationError("Invalid JSON received from Slack") from exc

    def open_dm_channel(self, user_id: str) -> str:
        payload = {"users": user_id}
        data = self._post("conversations.open", payload)
        channel = data.get("channel", {})
        channel_id = channel.get("id")
        if not channel_id:
            raise NotificationError("Slack did not return a channel ID for the DM")
        return channel_id

    def post_message(self, channel_id: str, text: str) -> None:
        payload = {"channel": channel_id, "text": text}
        self._post("chat.postMessage", payload)

    def send_dm(self, user_id: str, text: str) -> None:
        channel_id = self.open_dm_channel(user_id)
        self.post_message(channel_id, text)


class LarkNotifier:
    """Minimal Feishu/Lark custom bot webhook client."""

    def __init__(
        self,
        webhook_url: str,
        session: Optional[Session] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.webhook_url = webhook_url
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _headers() -> Dict[str, str]:
        return {"Content-Type": "application/json; charset=utf-8"}

    def send_text(self, text: str) -> None:
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            response = self.session.post(
                self.webhook_url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:  # pragma: no cover - requests raises rarely
            raise NotificationError(f"Request to Feishu/Lark failed: {exc}") from exc

        if response.status_code >= 400:
            raise NotificationError(f"HTTP error from Feishu/Lark: {response.status_code}")

        data = self._parse_json(response)
        self._raise_for_api_error(data)

    @staticmethod
    def _parse_json(response: Response) -> Dict[str, Any]:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            text = getattr(response, "text", "")
            if not text.strip():
                return {}
            raise NotificationError("Invalid JSON received from Feishu/Lark") from exc

    @staticmethod
    def _raise_for_api_error(data: Dict[str, Any]) -> None:
        code = data.get("code")
        if code not in (None, 0):
            message = data.get("msg") or data.get("message") or "unknown_error"
            raise NotificationError(f"Feishu/Lark API error: {message}")

        status_code = data.get("StatusCode")
        if status_code not in (None, 0):
            message = data.get("StatusMessage") or data.get("message") or "unknown_error"
            raise NotificationError(f"Feishu/Lark API error: {message}")


def _detect_agent_label(payload: Dict[str, Any]) -> str:
    """Infer a human-readable agent label from the payload shape.

    Codex's hooks and Claude Code's hooks both use Claude-compatible field names
    such as ``hook_event_name`` and ``transcript_path``. The transcript location
    is the stable signal: Claude Code stores hook transcripts under ``.claude``.
    """
    if not isinstance(payload, dict):
        return "Codex"
    transcript_path = str(payload.get("transcript_path") or "").replace("\\", "/")
    if "/.claude/" in f"/{transcript_path.strip('/')}/":
        return "Claude Code"
    return "Codex"


TITLE_MAX_CHARS = 80
PROMPT_MAX_CHARS = 100
RESULT_MAX_CHARS = 100
SUMMARY_MAX_CHARS = 200


def _one_line(text: Any, limit: int) -> str:
    """Collapse whitespace and clip to `limit` characters."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


def _status_emoji(status: Any) -> str:
    lowered = str(status or "").lower()
    if any(word in lowered for word in ("fail", "error", "abort", "cancel", "denied", "reject")):
        return "\u274c"
    if any(word in lowered for word in ("warn", "partial", "timeout", "skip")):
        return "\u26a0\ufe0f"
    return "\u2705"


def _build_rich_message(payload: Dict[str, Any], default_title: Optional[str], agent: str) -> str:
    """Render the compact three-line format used for agent session transcripts."""
    last_prompt = payload.get("last_prompt")
    title = (
        payload.get("title")
        or payload.get("event")
        or payload.get("task")
        or payload.get("session_title")
        or default_title
    )
    # Codex transcripts carry no session name. Promote the request to the
    # headline rather than printing a generic line above the agent label.
    prompt_is_title = False
    if not title and last_prompt:
        title = _one_line(last_prompt, TITLE_MAX_CHARS)
        prompt_is_title = True
    if not title:
        title = f"{agent} task completed"
    repo = payload.get("repo") or payload.get("cwd") or payload.get("workspace")
    branch = payload.get("branch")
    duration = payload.get("duration") or payload.get("elapsed") or payload.get("time")
    summary = payload.get("summary") or payload.get("message") or payload.get("details")
    url = payload.get("url") or payload.get("link") or payload.get("target")

    lines = [f"{_status_emoji(payload.get('status') or payload.get('state'))}  *{title}*"]

    context = []
    if repo:
        context.append(f"`{Path(str(repo)).name or repo}`")
    if branch:
        context.append(str(branch))
    if duration:
        context.append(str(duration))
    context.append(agent)
    lines.append(" \u00b7 ".join(context))

    if last_prompt and not prompt_is_title:
        lines.append(f"\U0001f4ac {_one_line(last_prompt, PROMPT_MAX_CHARS)}")
    last_result = payload.get("last_result")
    if last_result:
        lines.append(f"\u21b3 {_one_line(last_result, RESULT_MAX_CHARS)}")
    if summary:
        lines.append(_one_line(summary, SUMMARY_MAX_CHARS))
    if url:
        lines.append(f"\U0001f517 {url}")

    return "\n".join(lines)


def build_message(payload: Dict[str, Any], default_title: Optional[str] = None) -> str:
    """Create a concise message from a coding-agent notification payload."""
    status = payload.get("status") or payload.get("state")
    title = payload.get("title") or payload.get("event") or payload.get("task") or default_title
    summary = payload.get("summary") or payload.get("message") or payload.get("details")
    duration = payload.get("duration") or payload.get("elapsed") or payload.get("time")
    url = payload.get("url") or payload.get("link") or payload.get("target")
    repo = payload.get("repo") or payload.get("cwd") or payload.get("workspace")
    agent = _detect_agent_label(payload)

    # Transcript-derived fields mean we know the session name and/or the request,
    # which is worth a richer layout than the flat key/value list below.
    if payload.get("session_title") or payload.get("last_prompt"):
        return _build_rich_message(payload, default_title, agent)

    lines = []

    # If we only have repo, return a single-line, humane message.
    if repo and not any([title, status, duration, summary, url]):
        return f"{agent} task completed at repo {repo}"

    if title:
        lines.append(str(title))
    if status:
        lines.append(f"Status: {status}")
    if duration:
        lines.append(f"Duration: {duration}")
    if summary:
        lines.append(str(summary))
    if url:
        lines.append(f"Details: {url}")
    if repo:
        lines.append(f"Repo: {repo}")

    if not lines:
        return f"{agent} task completed."

    return "\n".join(lines)


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map an agent's own notification shape onto the fields build_message reads.

    Codex has no turn-level hook event; it reports finished turns through the
    `notify` program in ~/.codex/config.toml, whose JSON argument uses
    kebab-case keys of its own (`agent-turn-complete`).
    """
    if not isinstance(payload, dict) or payload.get("type") != "agent-turn-complete":
        return payload

    normalized = dict(payload)

    messages = payload.get("input-messages")
    if isinstance(messages, list) and messages and not normalized.get("last_prompt"):
        last = messages[-1]
        if isinstance(last, str) and last.strip():
            normalized["last_prompt"] = last.strip()

    reply = payload.get("last-assistant-message")
    if isinstance(reply, str) and not normalized.get("last_result"):
        closing = last_line(reply)
        if closing:
            normalized["last_result"] = closing

    # The thread name and the turn duration are not in the payload; Codex keeps
    # both in its own state database.
    thread = read_thread(str(payload.get("thread-id") or ""))
    if thread.get("title") and not normalized.get("session_title"):
        normalized["session_title"] = thread["title"]
    if thread.get("branch") and not normalized.get("branch"):
        normalized["branch"] = thread["branch"]
    if thread.get("cwd") and not normalized.get("cwd") and not normalized.get("repo"):
        normalized["cwd"] = thread["cwd"]

    if not normalized.get("duration"):
        duration = format_duration_ms(
            read_turn_duration_ms(
                str(payload.get("thread-id") or ""), str(payload.get("turn-id") or "")
            )
        )
        if duration:
            normalized["duration"] = duration

    return normalized


def load_payload(payload_arg: Optional[str], payload_file: Optional[str]) -> Dict[str, Any]:
    """Load JSON payload from CLI args or stdin."""
    raw = None
    if payload_arg:
        raw = payload_arg
    elif payload_file:
        try:
            with open(payload_file, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise NotificationError(f"Could not read payload file: {exc}") from exc
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()

    if not raw:
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotificationError(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise NotificationError("JSON payload must be an object")
    return normalize_payload(payload)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send coding-agent notifications to Slack DM.")
    parser.add_argument(
        "--user-id",
        help="Slack User ID to DM (or set SLACK_USER_ID)",
        default=os.environ.get("SLACK_USER_ID"),
    )
    parser.add_argument(
        "--env-file",
        help="Path to a .env file with SLACK_BOT_TOKEN/SLACK_USER_ID values",
    )
    parser.add_argument(
        "--token-env",
        help="Environment variable that holds the Slack Bot Token",
        default="SLACK_BOT_TOKEN",
    )
    parser.add_argument(
        "--payload",
        help="Raw JSON payload string",
    )
    parser.add_argument(
        "--payload-file",
        help="Path to file containing JSON payload",
    )
    parser.add_argument(
        "--title",
        help="Override title for the Slack message",
    )
    parser.add_argument(
        "--no-transcript",
        action="store_true",
        help="Do not read the agent transcript for a session title, request and duration",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Logging level (default: WARNING). INFO is noisy for agent hooks.",
    )
    return parser.parse_args(argv)


def _parse_lark_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send coding-agent notifications to a Feishu/Lark custom bot webhook."
    )
    parser.add_argument(
        "--webhook-url",
        help="Feishu/Lark custom bot webhook URL",
    )
    parser.add_argument(
        "--webhook-url-env",
        help="Environment variable that holds the Feishu/Lark webhook URL",
        default="LARK_WEBHOOK_URL",
    )
    parser.add_argument(
        "--env-file",
        help="Path to a .env file with LARK_WEBHOOK_URL/FEISHU_WEBHOOK_URL values",
    )
    parser.add_argument(
        "--payload",
        help="Raw JSON payload string",
    )
    parser.add_argument(
        "--payload-file",
        help="Path to file containing JSON payload",
    )
    parser.add_argument(
        "--title",
        help="Override title for the Feishu/Lark message",
    )
    parser.add_argument(
        "--no-transcript",
        action="store_true",
        help="Do not read the agent transcript for a session title, request and duration",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Logging level (default: WARNING). INFO is noisy for agent hooks.",
    )
    return parser.parse_args(argv)


class EnvFileNotFound(NotificationError):
    """Raised when an env file path does not exist."""


def _strip_quotes(value: str) -> str:
    """Drop one layer of matching surrounding quotes, like `set -a; . .env` does."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_env_file(env_file: str) -> None:
    """Load simple KEY=VALUE pairs (optionally prefixed with 'export ') into os.environ."""
    env_path = Path(env_file)
    if not env_path.exists():
        raise EnvFileNotFound(f".env file not found: {env_file}")

    try:
        content = env_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NotificationError(f"Could not read env file {env_file}: {exc}") from exc

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), _strip_quotes(value.strip())
        if key and value and key not in os.environ:
            os.environ[key] = value


def _load_default_env_file(env_file: Optional[str]) -> bool:
    env_file_to_load = env_file
    if not env_file_to_load and Path(".env").exists():
        env_file_to_load = ".env"

    if not env_file_to_load:
        return True

    try:
        _load_env_file(env_file_to_load)
    except EnvFileNotFound as exc:
        # A missing env file is not fatal: credentials are usually supplied by
        # the agent's own env config (hooks pass a default --env-file path that
        # may simply not exist). Missing credentials are reported separately.
        LOG.warning("Skipping env file %s: %s", env_file_to_load, exc)
    except NotificationError as exc:
        LOG.error("Failed to load env file %s: %s", env_file_to_load, exc)
        return False
    return True


def slack_main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    level = getattr(logging, args.log_level.upper(), logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    if not _load_default_env_file(args.env_file):
        return 1

    user_id = args.user_id or os.environ.get("SLACK_USER_ID")

    token = os.environ.get(args.token_env)
    if not token:
        LOG.error("Missing Slack token in environment variable %s", args.token_env)
        return 1

    if not user_id:
        LOG.error("Missing Slack user ID (set --user-id or SLACK_USER_ID)")
        return 1

    try:
        payload = load_payload(args.payload, args.payload_file)
        if is_internal_title_turn(payload):
            LOG.info("Skipping Codex internal thread-title turn")
            return 0
        if not args.no_transcript:
            payload = enrich_payload(payload)
        message = build_message(payload, args.title)
        notifier = SlackNotifier(token)
        notifier.send_dm(user_id, message)
    except NotificationError as exc:
        LOG.error("Failed to send Slack notification: %s", exc)
        return 1

    LOG.info("Slack notification sent to %s", user_id)
    return 0


def _resolve_lark_webhook_url(args: argparse.Namespace) -> Optional[str]:
    if args.webhook_url:
        return args.webhook_url

    webhook_url = os.environ.get(args.webhook_url_env)
    if webhook_url:
        return webhook_url

    if args.webhook_url_env == "LARK_WEBHOOK_URL":
        return os.environ.get("FEISHU_WEBHOOK_URL")

    return None


def lark_main(argv: Optional[list[str]] = None) -> int:
    args = _parse_lark_args(argv)
    level = getattr(logging, args.log_level.upper(), logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    if not _load_default_env_file(args.env_file):
        return 1

    webhook_url = _resolve_lark_webhook_url(args)
    if not webhook_url:
        if args.webhook_url_env == "LARK_WEBHOOK_URL":
            LOG.error(
                "Missing Feishu/Lark webhook URL (set LARK_WEBHOOK_URL or FEISHU_WEBHOOK_URL)"
            )
        else:
            LOG.error(
                "Missing Feishu/Lark webhook URL in environment variable %s", args.webhook_url_env
            )
        return 1

    try:
        payload = load_payload(args.payload, args.payload_file)
        if is_internal_title_turn(payload):
            LOG.info("Skipping Codex internal thread-title turn")
            return 0
        if not args.no_transcript:
            payload = enrich_payload(payload)
        message = build_message(payload, args.title)
        notifier = LarkNotifier(webhook_url)
        notifier.send_text(message)
    except NotificationError as exc:
        LOG.error("Failed to send Feishu/Lark notification: %s", exc)
        return 1

    LOG.info("Feishu/Lark notification sent")
    return 0


main = slack_main


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(slack_main())
