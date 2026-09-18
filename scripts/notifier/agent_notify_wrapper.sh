#!/usr/bin/env bash
set -euo pipefail

cleanup_tmp() {
  if [[ "${TMP_PAYLOAD_CREATED:-0}" == "1" && -n "${TMP_PAYLOAD:-}" && -f "$TMP_PAYLOAD" ]]; then
    rm -f "$TMP_PAYLOAD"
  fi
  return 0
}
trap cleanup_tmp EXIT

# Accept payload via file path, inline JSON argument, or stdin.
if [[ -n "${1:-}" && "${1}" != "/dev/stdin" && "${1}" != "-" ]]; then
  candidate="${1}"
  # If the argument looks like inline JSON, materialize it to a temp file.
  if [[ "$candidate" =~ ^[\{\[] ]]; then
    TMP_PAYLOAD="$(mktemp)"
    TMP_PAYLOAD_CREATED="1"
    printf "%s\n" "$candidate" > "$TMP_PAYLOAD"
    src="$TMP_PAYLOAD"
  else
    if [[ ! -r "${candidate}" ]]; then
      # brief retry in case the file is being written
      sleep 0.2
    fi
    if [[ -f "${candidate}" && -r "${candidate}" ]]; then
      src="${candidate}"
    else
      echo "Payload file '${candidate}' not found or not readable, falling back to stdin" >&2
      src="/dev/stdin"
    fi
  fi
else
  src="/dev/stdin"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-"$REPO_ROOT/.env"}"

# Resolve the interpreter that has coding_agent_notifier installed. Hooks run
# outside the shell that activated a virtualenv, so PATH alone is unreliable:
# prefer an explicit NOTIFIER_PYTHON, then the repo-local .venv, then PATH.
resolve_python() {
  if [[ -n "${NOTIFIER_PYTHON:-}" ]]; then
    if [[ -x "${NOTIFIER_PYTHON}" ]] || command -v "${NOTIFIER_PYTHON}" > /dev/null 2>&1; then
      echo "${NOTIFIER_PYTHON}"
      return 0
    fi
    echo "NOTIFIER_PYTHON='${NOTIFIER_PYTHON}' is not executable" >&2
    return 1
  fi

  local candidate
  for candidate in "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/venv/bin/python"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  for candidate in python3 python; do
    if command -v "$candidate" > /dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  echo "No Python interpreter found (set NOTIFIER_PYTHON to an absolute path)" >&2
  return 1
}

if ! PYTHON_BIN="$(resolve_python)"; then
  echo "Notifier failed to send message" >&2
  exit 1
fi

# The notifier CLI only needs an --env-file when one actually exists; the
# recommended setup keeps credentials in the agent's own env config instead.
ENV_FILE_ARGS=()
if [[ -f "$ENV_FILE" ]]; then
  ENV_FILE_ARGS=(--env-file "$ENV_FILE")
fi

# If DEBUG_AGENT_PAYLOAD is set to a filepath, the selected payload will be written there.
# DEBUG_CODEX_PAYLOAD remains supported for older hook setups.
# NOTE: the filter script is passed via -c, not a heredoc. A heredoc would
# become the interpreter's stdin and swallow a payload piped in by the hook.
FILTER_SRC="$(
  cat <<'PY'
import json
import sys
import pathlib
import os

source = sys.argv[1]
debug_path = os.environ.get("DEBUG_AGENT_PAYLOAD") or os.environ.get("DEBUG_CODEX_PAYLOAD")
pwd_env = os.environ.get("PWD")

def read_text():
    if source != "/dev/stdin" and pathlib.Path(source).exists():
        return pathlib.Path(source).read_text(encoding="utf-8", errors="ignore")
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()

def iter_json_objects(text: str):
    cleaned = text.replace("\x00", "").strip()
    if cleaned:
        try:
            yield json.loads(cleaned)
            return
        except json.JSONDecodeError:
            pass

    for line in text.splitlines():
        clean = line.replace("\x00", "").strip()
        if not clean:
            continue
        try:
            yield json.loads(clean)
        except json.JSONDecodeError:
            continue

def is_relevant(obj: dict) -> bool:
    keys = {"status", "state", "title", "event", "task", "summary", "message", "details"}
    return any(k in obj for k in keys)

last_valid = None
last_relevant = None

for obj in iter_json_objects(read_text()):
    last_valid = obj
    if isinstance(obj, dict) and is_relevant(obj):
        last_relevant = obj

chosen = last_relevant or last_valid or {}
if isinstance(chosen, dict):
    if not any(chosen.get(k) for k in ("repo", "cwd", "workspace")):
        fallback_repo = pwd_env
        if fallback_repo:
            chosen["repo"] = fallback_repo
out = json.dumps(chosen)
if debug_path:
    try:
        pathlib.Path(debug_path).write_text(out + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"DEBUG_AGENT_PAYLOAD write failed: {exc}", file=sys.stderr)
sys.stdout.write(out)
PY
)"

filter_and_forward() {
  "$PYTHON_BIN" -c "$FILTER_SRC" "$src"
}

if ! filter_and_forward \
  | "$PYTHON_BIN" "$SCRIPT_DIR/slack_notify.py" ${ENV_FILE_ARGS[@]+"${ENV_FILE_ARGS[@]}"}; then
  echo "Notifier failed to send message" >&2
  exit 1
fi
