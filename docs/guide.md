# Coding Agent Notifier – Full Guide

This guide walks through setup, configuration, usage, debugging, and development for sending coding-agent task notifications to Slack DMs or Feishu/Lark chats.

## What it does
- Opens a DM channel to a target Slack user.
- Posts to a Feishu/Lark chat through a custom bot webhook.
- Posts a concise summary using fields from the agent hook payload (`title`, `status`, `summary`, `duration`, `url`).
- Works with stdin, inline JSON, or a payload file, which covers the common hook styles used by coding-agent CLIs.
- Optional debug capture of the payload used.

## Requirements
- Python 3.12+
- Slack app with bot token (`xoxb-...`) and scopes: `chat:write`, `im:write` (or `conversations:write`). `users:read` is handy if you need to look up IDs.
- Your Slack User ID (Profile → ⋯ → Copy member ID).
- For Feishu/Lark: a custom bot webhook URL from the chat that should receive notifications.

## Install
```bash
git clone git@github.com:Wangmerlyn/coding-agent-notifier.git
cd coding-agent-notifier

# Recommended: a repo-local .venv, which the hook wrapper finds automatically.
uv venv && uv pip install -e '.[dev]'
# or, without uv:
# python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
# or conda: conda create -n coding_agent_notifier python=3.12 && conda activate coding_agent_notifier && pip install -e '.[dev]'

# optional
pre-commit install
```

The hook wrapper does not inherit an activated environment, so it resolves the interpreter
in this order: `$NOTIFIER_PYTHON`, then `<repo>/.venv/bin/python` and `<repo>/venv/bin/python`,
then `python3`/`python` from `PATH`. If your environment lives elsewhere (conda, pyenv), set
`NOTIFIER_PYTHON` to its absolute interpreter path in the agent's env config.

The Python distribution/import names are `coding-agent-notifier` and `coding_agent_notifier`.

## Configure credentials
For agent hooks, the simple setup is to keep notifier values in your user-level agent config. This keeps secrets out of the project tree and avoids having every repo carry a local `.env`.

Codex:
```toml
# ~/.codex/config.toml
[shell_environment_policy.set]
SLACK_BOT_TOKEN = "xoxb-your-token"
SLACK_USER_ID = "U12345678"
LARK_WEBHOOK_URL = "https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here"
# or:
# FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here"
```

Claude Code:
```json
{
  "env": {
    "SLACK_BOT_TOKEN": "xoxb-your-token",
    "SLACK_USER_ID": "U12345678",
    "LARK_WEBHOOK_URL": "https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here"
  }
}
```

Use user-level files (`~/.codex/config.toml` and `~/.claude/settings.json`). Do not put real tokens in project-level `.codex/config.toml`, `.claude/settings.json`, or shell commands checked into a repo.

For one-off manual tests, direct export is still fine:
```bash
export SLACK_BOT_TOKEN=xoxb-your-token
export SLACK_USER_ID=U12345678
export LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here
# or
export FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
```

Repo `.env` remains supported as a local-development fallback:
```bash
cp .env.example .env
edit .env
```

The simple setup is not secure secret storage. A secure setup would use an OS keychain, a credential helper, or a tightly permissioned credential file loaded only by the notifier. Those approaches reduce exposure, but they add complexity, so this guide keeps them as an advanced path rather than the default.

## Wire an agent hook to Slack
Use the portable wrapper so payloads from stdin, inline JSON, or a file all work:
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh"
          }
        ]
      }
    ]
  }
}
```
Save this Codex example as `~/.codex/hooks.json`. If Codex says hooks need review, open `/hooks`, review the command, and enable/trust it.

Options:
- Override env file location for local `.env` fallback: `ENV_FILE=/path/to/.env`.
- Capture the payload used: `DEBUG_AGENT_PAYLOAD=/path/to/agent_payload.json` (unset to disable).

For Claude Code, Gemini CLI, OpenCode, Copilot CLI, Cursor, and other agents, configure the equivalent completion/stop/session-idle hook to run the same wrapper command. See `docs/integrations.md` for examples and fallback wrapper guidance.

## Wire an agent hook to Feishu/Lark
Custom bots send to the chat where the bot is installed, not to a user DM.
For Codex, the most deterministic setup is a user-level env file loaded by the hook command:

```bash
mkdir -p ~/.codex
install -m 600 /dev/null ~/.codex/coding-agent-notifier.env
cat > ~/.codex/coding-agent-notifier.env <<'EOF'
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
EOF
```

Codex hook example (`~/.codex/hooks.json`):
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/python /path/to/coding-agent-notifier/scripts/notifier/lark_notify.py --env-file /home/you/.codex/coding-agent-notifier.env --webhook-url-env FEISHU_WEBHOOK_URL"
          }
        ]
      }
    ]
  }
}
```

Replace `/path/to/python` with the Python 3.12+ interpreter where you installed this package, and replace `/home/you/.codex/coding-agent-notifier.env` with your user-level env file. Keeping `LARK_WEBHOOK_URL` or `FEISHU_WEBHOOK_URL` in `~/.codex/config.toml` under `[shell_environment_policy.set]` also works after Codex has loaded that config, but `--env-file` avoids stale-session environment issues. For full setup and troubleshooting, see `docs/notifier_lark.md`.

Codex's older top-level `notify = [...]` path is different from hooks: recent Codex versions append the payload as a command argument. If you use `notify` anyway, direct Feishu/Lark commands must include `--payload`; new installs should prefer `~/.codex/hooks.json`.

## Manual Slack send (smoke test)
```bash
echo '{"status":"success","title":"Test ping","summary":"Hello"}' \
  | python scripts/notifier/slack_notify.py --user-id "$SLACK_USER_ID"
```

## Manual Feishu/Lark send (smoke test)
```bash
echo '{"status":"success","title":"Test ping","summary":"Hello"}' \
  | python scripts/notifier/lark_notify.py
```

## Wrapper behavior
- Accepts `$1` as a payload file path or falls back to stdin.
- Validates readability; if not readable, waits briefly then falls back to stdin (logs a warning).
- Loads env from `${ENV_FILE:-$REPO_ROOT/.env}` as a fallback if you use an env file.
- Writes the selected payload to `DEBUG_AGENT_PAYLOAD` if set.
- Forwards payload to `slack_notify.py` with `--env-file`.

## Troubleshooting
- `missing_scope`: ensure Slack app has `chat:write` and `im:write`/`conversations:write`; reinstall the app and use the updated token.
- `Missing Slack token/user ID`: set env vars through your agent config, export them in the shell, or ensure `.env` is loaded; wrapper’s `ENV_FILE` can point elsewhere.
- No DM received: set `DEBUG_AGENT_PAYLOAD` and inspect the payload; verify `SLACK_USER_ID` is correct.
- Rate limited: the notifier retries once on HTTP 429/5xx.
- Empty payload: notifier still sends a default message using the inferred agent label.
- Feishu/Lark keyword security: include the configured keyword in the message title or payload.
- Feishu/Lark signing: leave signature verification disabled; this first version does not sign custom bot requests.
- Codex hook not running: open `/hooks` and verify the hook is enabled/trusted. If you script trust state, query the app-server RPC method `hooks/list` for `currentHash` and trust that exact value.
- Manual Feishu/Lark test succeeds but Codex hook sends nothing: make the hook command load a user-level env file with `--env-file`, then re-open Codex or re-approve the modified hook from `/hooks`.
- `unrecognized arguments: {"type":"agent-turn-complete",...}`: a Codex `notify` command is passing inline JSON as argv. Use `~/.codex/hooks.json`, or add `--payload` to the direct Feishu/Lark `notify` command.

## Development
- Format/lint: `pre-commit run --all-files` (uses ruff).
- Tests: `pytest` (mocks Slack API).
- CI: GitHub Actions runs pre-commit on push/PR.

## File map (docs)
- `README.md` – quick start and essential snippets.
- `docs/guide.md` – this detailed guide.
- `docs/notifier_slack.md` – focused setup notes for Slack + coding-agent hooks.
- `docs/notifier_lark.md` – focused setup notes for Feishu/Lark custom bots.
- `docs/examples/codex/hooks.json` – Codex Stop hook example for Slack.
- `docs/examples/codex/hooks_lark.json` – Codex Stop hook example for Feishu/Lark.
- `scripts/notifier/agent_notify_wrapper.sh` – Slack hook entrypoint for common agent payload styles.
- `src/coding_agent_notifier/transcript.py` – reads the agent transcript for the session title, last request and turn duration (see "Message format" in `README.md`; disable with `--no-transcript`).
- `scripts/notifier/slack_notify.py` – CLI entry to the notifier logic.
- `scripts/notifier/lark_notify.py` – CLI entry for Feishu/Lark custom bot webhooks.

## Migration notes
- Use `coding-agent-notifier`, `/path/to/coding-agent-notifier`, `coding_agent_notifier`, `scripts/notifier/agent_notify_wrapper.sh`, `DEBUG_AGENT_PAYLOAD`, `~/.codex/coding-agent-notifier.env`, and `~/.config/opencode/agent-notifier.env` in new docs and hooks.
- Older names remain compatibility aliases only: `vibe-coding-slack-notifier`, `codex_slack_notifier`, `scripts/notifier/codex_notify_wrapper.sh`, `DEBUG_CODEX_PAYLOAD`, `~/.codex/vibe-coding-slack-notifier.env`, and `~/.config/opencode/slack-notifier.env`.
