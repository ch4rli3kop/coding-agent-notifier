# Coding Agent Notifier

[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/Wangmerlyn/coding-agent-notifier?utm_source=oss&utm_medium=github&utm_campaign=Wangmerlyn%2Fcoding-agent-notifier&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Wangmerlyn/coding-agent-notifier)

Send coding-agent task completion alerts to Slack DMs or Feishu/Lark chats.
Slack uses the Slack Web API. Feishu/Lark uses custom bot incoming webhooks.

For a detailed, step-by-step guide (setup, config, debugging, FAQs), see `docs/guide.md`.
For Feishu/Lark custom bot setup, see `docs/notifier_lark.md`.

For integrations with coding-agent hooks (Codex CLI, Claude Code, Gemini CLI, OpenCode, Copilot CLI, Cursor, and similar tools), see `docs/integrations.md`.
Sample hook config snippets live under `docs/examples/`; the wrapper example is still available for tools without a hook event.
For OpenCode marketplace/npm-style plugin setup, see `docs/opencode_plugin.md`.

## Rename Announcement (2026-05-17)
This project has been renamed from Vibe Coding Slack Notifier to Coding Agent Notifier. Old names including `vibe-coding-slack-notifier`, `codex-slack-notifier`, `codex_slack_notifier`, and `opencode-vibe-coding-slack-notifier` remain compatibility aliases for existing setups: old repo links redirect, and old Python import, wrapper, debug, and env names continue to work. New installs should use `coding-agent-notifier`, the `coding-agent-notifier` Python distribution, the `coding_agent_notifier` import, and `opencode-coding-agent-notifier`.

## Why This Notifier
- Most coding-agent CLIs now expose hooks or plugin events, but local editor notifications still vary by tool and environment.
- CLI-local tricks (terminal bells, desktop notifications) often fail over Remote-SSH because the sound/notification doesn’t propagate, leaving remote users uninformed.
- This project gives those hooks a remote-safe notification target: Slack DMs or Feishu/Lark chats that work independently of where the agent runs.

## Slack quick start
1. **Clone & env**
   - `git clone git@github.com:Wangmerlyn/coding-agent-notifier.git`
   - `cd coding-agent-notifier`
   - Create a Python 3.12+ environment in the repo. Any of these works; a repo-local `.venv`
     is recommended because hooks pick it up automatically:
     ```bash
     uv venv && uv pip install -e '.[dev]'      # uv (fastest)
     python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # stdlib venv
     conda create -n coding_agent_notifier python=3.12 && conda activate coding_agent_notifier && pip install -e '.[dev]'
     ```
   - Python distribution/import names: `coding-agent-notifier` / `coding_agent_notifier`.
   - Optional: `pre-commit install`

2. **Create a Slack app**
   - Go to [api.slack.com/apps](https://api.slack.com/apps) → "Create New App" → "From scratch".
   - Choose a workspace and create a Bot token.
   - Add Bot scopes: `chat:write`, `im:write`, and `users:read` if you need lookups.
   - Install the app to the workspace and copy the **Bot User OAuth Token** (`xoxb-...`).
   - Find your Slack User ID (Profile → ⋯ → Copy member ID).

3. **Configure credentials**
   For agent hooks, the simple setup is to put notifier env vars in your user-level agent config, not in a repo `.env`.

   Codex example:
   ```toml
   # ~/.codex/config.toml
   [shell_environment_policy.set]
   SLACK_BOT_TOKEN = "xoxb-your-token-here"
   SLACK_USER_ID = "U12345678"
   ```

   Claude Code example:
   ```json
   {
     "env": {
       "SLACK_BOT_TOKEN": "xoxb-your-token-here",
       "SLACK_USER_ID": "U12345678"
     }
   }
   ```

   For manual smoke tests, direct shell export is also fine. Repo `.env` is still supported as a fallback for local development, but it is no longer the recommended hook setup.

   This is a simple setup, not secure secret storage. A more secure setup would use an OS keychain, credential helper, or tightly permissioned credential file loaded only by the notifier; this project keeps the default path lightweight.

4. **Send a manual test DM**
   ```bash
   echo '{"status":"success","title":"Agent run","summary":"Finished"}' \
     | python scripts/notifier/slack_notify.py --user-id "$SLACK_USER_ID"
   ```
   - The script opens a DM via `conversations.open` and sends the message via `chat.postMessage`.

5. **Wire up an agent hook**
   Codex hook example (`~/.codex/hooks.json`):
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
   Other agents can point their completion/stop/session-idle hook at the same wrapper. If a tool passes JSON on stdin, via a payload file, or as inline JSON, the wrapper normalizes it before sending Slack. If Codex says the hook needs review, open `/hooks` and approve the command.

   **Interpreter resolution.** Hooks do not inherit the shell that activated your virtualenv, so the wrapper resolves Python itself, in this order:

   1. `$NOTIFIER_PYTHON` (set it to an absolute interpreter path to be explicit)
   2. `<repo>/.venv/bin/python`, then `<repo>/venv/bin/python`
   3. `python3`, then `python` from `PATH`

   With a repo-local `.venv` no extra configuration is needed. With conda or any other
   location, set `NOTIFIER_PYTHON` in the agent's env config, e.g.
   `"NOTIFIER_PYTHON": "/home/you/miniconda3/envs/coding_agent_notifier/bin/python"`.

   **Env file.** The wrapper passes `--env-file <repo>/.env` only when that file exists, so the
   recommended setup (credentials in the agent's own env config) works without a repo `.env`.
   Override the path with `ENV_FILE=/path/to/your.env`.

6. **Run tests (optional)**
   ```bash
   pytest
   # Lint: ruff check .
   ```

## Message format
Slack DMs and Feishu/Lark messages are rendered from the hook payload. When the payload carries a
`transcript_path` (Claude Code and Codex both provide one), the notifier reads the transcript the
agent has already written and builds a three-line message -- no summarisation call is involved:

```
✅  *Session title*
`repo-name` · branch · 2m 45s · Claude Code
💬 the last request you made, clipped to 100 characters
↳ the closing line of the agent's final reply
```

- **The request** is the newest `user` record in the transcript. Claude Code also writes a
  `last-prompt` record, but it lags the conversation by a turn or two, so it is only a fallback.
  Tool results, slash-command echoes, injected reminders and subagent turns are filtered out.
- **The closing line** is the last non-empty line of the agent's final reply.
- **Session title** is the name you gave the session (Claude Code's `custom-title` record, or the
  `custom-title.json` sidecar), falling back to its generated `ai-title` record. Codex transcripts have no
  session name, so the last request is promoted to the headline instead.
- **Duration** covers the final turn (last request to last reply), not the whole session.
- **Branch** is read straight from `.git/HEAD`; no subprocess is spawned.
- The leading emoji reflects `status` when a payload supplies one (success / warning / failure).
- Transcripts of long sessions can exceed 100MB, so only the last 4MB is parsed. A 114MB Codex
  rollout is read in about 20ms.
- Pass `--no-transcript` to disable transcript reading entirely, for example if you would rather
  not have request text leave the machine.

### Codex

Codex has no turn-level hook event: its hook events are `PreToolUse`,
`PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`,
`SubagentStart`, `SubagentStop`, `UserPromptSubmit` and `Interrupt`. A `Stop` entry in
`~/.codex/hooks.json` is simply never dispatched, whatever its trust state. Finished turns are
reported through the `notify` program instead:

```toml
# ~/.codex/config.toml
notify = ["/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh"]
```

Codex passes its `agent-turn-complete` JSON as the first argument, which the wrapper already
accepts. Its kebab-case keys (`input-messages`, `last-assistant-message`, `cwd`) are mapped onto
the fields above, so the request becomes the headline and the reply's closing line follows.

Payloads without a transcript keep the original flat layout (`Status:` / `Duration:` / `Repo:`
lines), so custom hooks and CI scripts are unaffected.

## OpenCode plugin install (official flow)
If you use OpenCode, this repo now exposes an installable plugin package:

```bash
npm install -g opencode-coding-agent-notifier
```

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-coding-agent-notifier"]
}
```

Recommended credentials setup (no shell export needed):

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/agent-notifier.env <<'EOF'
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_USER_ID=U12345678
# Optional Feishu/Lark custom bot target:
# LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here
# or:
# FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
EOF
```

The plugin auto-loads this file. See `docs/opencode_plugin.md` for full setup and advanced options.

## Feishu/Lark custom bot quick start
1. **Create a custom bot**
   - Add a custom bot to the Feishu/Lark chat that should receive notifications.
   - Copy the webhook URL.
   - Leave signature verification disabled for this first notifier version.
   - Official docs: [Lark](https://open.larksuite.com/document/client-docs/bot-v3/add-custom-bot) / [Feishu](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).

2. **Configure the webhook**
   ```bash
   # Lark global endpoint
   export LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here

   # Or Feishu China endpoint
   export FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
   ```
   For agent hooks, prefer user-level config or a user-level env file. Repo `.env` remains a local-development fallback.

   Codex hook env-file option:
   ```bash
   mkdir -p ~/.codex
   install -m 600 /dev/null ~/.codex/coding-agent-notifier.env
   cat > ~/.codex/coding-agent-notifier.env <<'EOF'
   FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
   EOF
   ```

3. **Send a manual test message**
   ```bash
   echo '{"status":"success","title":"Agent run","summary":"Finished"}' \
     | python scripts/notifier/lark_notify.py
   ```

4. **Wire up an agent hook**
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
   Replace `/path/to/python` with the Python 3.12+ interpreter where you installed this package, and replace `/home/you/.codex/coding-agent-notifier.env` with your user-level env file. You can also keep `LARK_WEBHOOK_URL` or `FEISHU_WEBHOOK_URL` in `~/.codex/config.toml` under `[shell_environment_policy.set]`, but `--env-file` makes the hook independent of whether the current Codex process has already loaded that config. Approve the hook from `/hooks` if Codex asks for review.
   Feishu/Lark custom bots send to the chat where the bot is installed, not to a specific user DM.

## Payload expectations
- The notifier builds a message from `title`, `status`, `summary`, `duration`, and `url` when present.
- Missing fields default to a simple message using the inferred agent label, such as `Codex task completed.` or `Claude Code task completed.`

## More details
- `docs/notifier_slack.md` contains expanded setup notes and troubleshooting.
- Example Codex wiring: `docs/examples/codex/hooks.json`, `docs/examples/codex/hooks_lark.json`, and `scripts/notifier/agent_notify_example.sh`.

### Debugging agent hooks (optional)
- Use the wrapper that can read payloads from a file argument, inline JSON, or stdin:
  ```bash
  /path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh
  ```
- To capture the final payload for debugging, set an env var before running Codex:
  ```bash
  export DEBUG_AGENT_PAYLOAD=/path/to/your/agent_payload.json
  ```
  The wrapper will write only the most relevant JSON payload to that path; unset the variable to stop logging.

## Migration and compatibility
- Old repo/path references such as `Wangmerlyn/vibe-coding-slack-notifier`, `git@github.com:Wangmerlyn/vibe-coding-slack-notifier.git`, and `/path/to/vibe-coding-slack-notifier` map to `Wangmerlyn/coding-agent-notifier`, `git@github.com:Wangmerlyn/coding-agent-notifier.git`, and `/path/to/coding-agent-notifier`.
- Use the Python distribution/import names `coding-agent-notifier` and `coding_agent_notifier`. The old `codex_slack_notifier` import remains available only as a compatibility import for existing code.
- New hooks should call `scripts/notifier/agent_notify_wrapper.sh`. `scripts/notifier/codex_notify_wrapper.sh` remains as a compatibility wrapper for old hooks.
- Prefer `DEBUG_AGENT_PAYLOAD`; `DEBUG_CODEX_PAYLOAD` remains a deprecated compatibility alias.
- Prefer env files `~/.codex/coding-agent-notifier.env` and `~/.config/opencode/agent-notifier.env`. Older `~/.codex/vibe-coding-slack-notifier.env` and `~/.config/opencode/slack-notifier.env` paths are compatibility aliases.
- OpenCode docs should use `opencode-coding-agent-notifier` and `OpenCodeAgentNotifierPlugin`. `opencode-vibe-coding-slack-notifier` and `OpenCodeSlackNotifierPlugin` are old compatibility names only.
