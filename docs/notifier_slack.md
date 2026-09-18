# Slack DM notifier for coding-agent hooks

Send coding-agent completion events as Slack direct messages using the Slack Web API (no webhooks required).

## Slack app setup
- Create a Slack app (from a manifest or <https://api.slack.com/apps>). Choose a Bot token.
- Add bot scopes: `chat:write`, `im:write`, and `users:read` if you need to look up IDs.
- Install the app to your workspace and grab the **Bot User OAuth Token** (starts with `xoxb-`).
- Find your Slack User ID (profile → ⋯ → Copy member ID) for the DM recipient.

## Environment variables
For agent hooks, the simple setup is user-level agent config. Keep real values out of project-level config and repo `.env` files.

Codex:
```toml
# ~/.codex/config.toml
[shell_environment_policy.set]
SLACK_BOT_TOKEN = "xoxb-123..."
SLACK_USER_ID = "U12345678"
```

Claude Code:
```json
{
  "env": {
    "SLACK_BOT_TOKEN": "xoxb-123...",
    "SLACK_USER_ID": "U12345678"
  }
}
```

For manual tests:
```bash
export SLACK_BOT_TOKEN=xoxb-123...             # required
export SLACK_USER_ID=U12345678                 # optional if passed via --user-id
```
Tokens are read from the environment only; nothing is hard-coded. The notifier also supports `.env` or `--env-file` as local-development fallbacks.

This is a simple setup, not secure secret storage. A secure setup would use an OS keychain, credential helper, or tightly permissioned credential file loaded only by the notifier, but that is intentionally outside the default quick path.

## Script usage
The notifier lives at `scripts/notifier/slack_notify.py` and accepts JSON payloads on stdin or via flags.
```bash
# Send a quick manual notification
echo '{"status":"success","title":"Agent run","summary":"Finished"}' \
  | python scripts/notifier/slack_notify.py --user-id U12345678
```
Flags:
- `--user-id`: Slack User ID (fallback: `SLACK_USER_ID`).
- `--token-env`: Name of the env var holding the bot token (default `SLACK_BOT_TOKEN`).
- `--payload` / `--payload-file`: Provide JSON directly or from a file.
- `--title`: Optional override for the message title.

## Hook integration
Register the wrapper so your coding agent calls it after tasks finish.

Codex has a `Stop` hook, but it needs the hook-trust step and does not fire on an interrupted or
failed turn. `notify` needs no trust, so it is the simpler wiring for finished turns:

```toml
# ~/.codex/config.toml — a top-level key, so keep it above any [table]
notify = ["/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh"]
```

Claude Code and agents with a completion hook point that hook at the same wrapper; see
`docs/integrations.md`. Restart the agent after editing its config.

The hook can pipe JSON to stdin, pass a payload file path, or pass inline JSON. The wrapper normalizes those forms before forwarding to `slack_notify.py`; the notifier formats a concise DM (title, status, duration, summary, link when present).
A concrete example is in `scripts/notifier/agent_notify_example.sh`.

### Optional debugging
- If your agent supplies a payload file instead of stdin, use the wrapper:
  ```bash
  /path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh /path/to/payload.json
  ```
- To capture the selected payload for inspection, set:
  ```bash
  export DEBUG_AGENT_PAYLOAD=/path/to/your/agent_payload.json
  ```
  Unset this variable to stop logging.

> The wrapper can load `.env` from the repo root as a fallback; override with `ENV_FILE=/custom/path/.env` if you use an env file.

## Installing & testing
```
conda activate coding_agent_notifier
pip install -e .[dev]
pytest
```
Ruff linting: `ruff check .`

## Troubleshooting
- Missing token/user ID: set `SLACK_BOT_TOKEN` and `SLACK_USER_ID` or pass `--user-id`.
- Rate limits/HTTP 5xx: the script retries once; errors are logged to stderr and exit with code 1.
- Message content looks sparse: ensure the payload includes keys like `title`, `status`, `summary`, `duration`, `url`.
