# Coding Agent Notifier

[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/Wangmerlyn/coding-agent-notifier?utm_source=oss&utm_medium=github&utm_campaign=Wangmerlyn%2Fcoding-agent-notifier&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Wangmerlyn/coding-agent-notifier)

Tell me when my coding agent is done, wherever I am.

When a long agent task finishes, this sends a Slack DM or a Feishu/Lark message describing what the
session was doing. Slack uses the Slack Web API; Feishu/Lark uses custom bot incoming webhooks.

Terminal bells and desktop notifications do not survive Remote-SSH, tmux or a closed laptop lid, so
an agent running on a remote box finishes unnoticed. Chat does survive all of those, and it is
already open.

**Contents** — [What a notification looks like](#what-a-notification-looks-like) ·
[Install](#install) · [Slack credentials](#slack-credentials) · [Wire up your agent](#wire-up-your-agent)
· [Message format](#message-format) · [Session icons](#session-icons) ·
[Codex failed turns](#codex-failed-turns) · [Feishu/Lark](#feishulark-custom-bot) ·
[Reference](#reference) · [Troubleshooting](#troubleshooting)

## What a notification looks like

```
🫐  R2U 개발
coding-agent-notifier · main · 2m 45s · Claude Code
💬 fix the wrapper so hooks stop failing silently
↳ Tests 95 passed, ruff clean. Pushed as 6de7e05.
```

Nothing there was written by hand or summarised by a model. The session name, the request, the
reply and the duration are all read back from the transcript the agent already wrote to disk. The
leading emoji belongs to that session, so several running at once stay apart at a glance.

## Install

```bash
git clone git@github.com:ch4rli3kop/coding-agent-notifier.git
cd coding-agent-notifier
```

Then create a Python 3.12+ environment. A repo-local `.venv` is recommended because the hook
wrapper finds it on its own:

```bash
uv venv && uv pip install -e '.[dev]'                        # uv, recommended
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'    # stdlib venv
```

Conda works too, but then the interpreter lives outside the repo, so set `NOTIFIER_PYTHON` (see
[Interpreter resolution](#interpreter-resolution)).

Optional: `pre-commit install`.

## Slack credentials

### Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**,
   and pick your workspace.
2. Under **OAuth & Permissions**, add the bot scopes `chat:write` and `im:write` (add `users:read`
   only if you want to look users up by name).
3. **Install to workspace**, then copy the **Bot User OAuth Token** — it starts with `xoxb-`.
4. Copy your own member ID: in Slack, click your avatar → **Profile** → **⋯** → **Copy member ID**.
   It looks like `U12345678`. The notifier DMs this user.

### Put them in a `.env` file

The simplest setup that works for every agent at once. The file lives in the repo and is already
covered by `.gitignore`:

```bash
cd /path/to/coding-agent-notifier
umask 077                       # create it unreadable to other users
cat > .env <<'EOF'
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_USER_ID=U12345678
EOF
chmod 600 .env
```

The hook wrapper passes this file to the CLI automatically whenever it exists, so nothing else
needs configuring. Keep it to one `KEY=VALUE` per line; `export ` prefixes and surrounding quotes
are both accepted, so a line copied from your shell profile works as-is.

Point `ENV_FILE` somewhere else to keep credentials outside the repo:

```bash
ENV_FILE=~/.config/coding-agent-notifier.env
```

`.env.example` lists every variable the notifier reads.

### Or put them in your agent's own config

If you would rather not have a credentials file in the repo, set the variables where your agent
already keeps its environment. The notifier reads them straight from the environment.

```toml
# ~/.codex/config.toml
[shell_environment_policy.set]
SLACK_BOT_TOKEN = "xoxb-your-token-here"
SLACK_USER_ID = "U12345678"
```

```json
// ~/.claude/settings.json
{
  "env": {
    "SLACK_BOT_TOKEN": "xoxb-your-token-here",
    "SLACK_USER_ID": "U12345678"
  }
}
```

**Precedence:** variables already set in the environment always win; an env file only fills in what
is missing. So an agent-level setting overrides the `.env` file, and both override nothing else.

> These are plain files holding a live token. That is a deliberate trade-off for a local developer
> tool, not secure secret storage — an OS keychain or a credential helper would be stronger. Keep
> the file mode at `600`, and revoke the token from the Slack app page if it leaks.

### Send a test DM

```bash
echo '{"status":"success","title":"Agent run","summary":"Finished"}' \
  | .venv/bin/python scripts/notifier/slack_notify.py
```

The script opens a DM with `conversations.open` and posts it with `chat.postMessage`. Add
`--log-level INFO` to see confirmation, and see [Troubleshooting](#troubleshooting) if nothing
arrives.

## Wire up your agent

Every agent points at the same wrapper, which accepts a payload on stdin, as a file path, or as
inline JSON:

```
/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh
```

### Claude Code

Add a `Stop` hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
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

Add `StopFailure` the same way to be told when a turn ends in an error -- a usage limit, an API
error, or a safeguard refusal. `Stop` does not fire in those cases:

```json
"StopFailure": [
  {
    "matcher": "*",
    "hooks": [
      {
        "type": "command",
        "command": "/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh"
      }
    ]
  }
]
```

Restart Claude Code afterwards. `SessionEnd` also works if you would rather be notified once per
session than once per reply.

### Codex

Codex's hook events are `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`,
`PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`,
`SessionStart` and `SessionEnd`. `Stop` fires at a natural stopping point only: the documentation
says it does not run when a turn is interrupted or aborted, and a probe confirmed it stays silent
when a turn dies on a usage limit. **There is no failure event**, so unlike Claude Code a Codex turn
that hits a usage limit or a safeguard refusal sends nothing. (`SessionEnd` does fire when the
process ends, which covers `codex exec` but not a failed turn inside a running session.)

Finished turns can be reported by the `Stop` hook, or without the hook-trust step by the `notify`
program:

```toml
# ~/.codex/config.toml — a top-level key, so keep it above any [table]
notify = ["/path/to/coding-agent-notifier/scripts/notifier/agent_notify_wrapper.sh"]
```

Restart Codex afterwards. `notify` is not subject to hook trust, so there is nothing to approve.

### Codex failed turns

Codex notifies nothing when a turn dies on a usage limit, a rate limit or a safeguard refusal: it
has no failure hook, and `notify` only ever sends `agent-turn-complete`. It does record the failure
though, so a watcher polls its history database and sends what the agent does not:

```bash
./scripts/install_codex_watch.sh
```

That writes a systemd user unit with the absolute paths filled in, starts it, and enables lingering
so it survives logout. `--interval SECONDS`, `--include-interrupted` and `--no-linger` adjust it,
`--dry-run` prints the unit without installing, and `--uninstall` removes it again. Without
systemd, run `scripts/notifier/codex_watch.py --once` from cron instead;
`docs/examples/systemd/` holds the unit for installing by hand.

```
❌ 🧁  *리포트탈고*
`windows_drivers_repo` · main · 13m 46s · Codex
💬 어 진행해줘.
↳ Selected model is at capacity. Please try a different model.
```

- It reports `failed` turns. Add `--include-interrupted` for turns you stopped yourself; those
  record no error text, so they arrive as a `⚠️` with the request alone.
- The first run starts from now rather than replaying every past failure. `--since <unix ts>` walks
  back deliberately.
- A retried rate limit records the same failure several times; repeats of one error in a thread are
  coalesced into one notification for ten minutes.
- A send that fails is retried on the next poll rather than being dropped.
- `--once` polls a single time, for a cron job or a systemd timer instead of a service.
- `--dry-run` prints the messages instead of sending them — the quickest way to see what a watcher
  would have told you:

  ```bash
  .venv/bin/python scripts/notifier/codex_watch.py --once --dry-run --since $(( $(date +%s) - 86400 ))
  ```

State lives in `$XDG_STATE_HOME/coding-agent-notifier/codex-watch.json` (`~/.local/state/…` by
default), holding the watermark and the turns already reported. Logs go to the journal:
`journalctl --user -u coding-agent-notifier-codex-watch -f`.

### OpenCode

OpenCode gets a plugin rather than a hook:

```bash
npm install -g opencode-coding-agent-notifier
```

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-coding-agent-notifier"]
}
```

The plugin loads its own credentials file, so no shell export is needed:

```bash
mkdir -p ~/.config/opencode
install -m 600 /dev/null ~/.config/opencode/agent-notifier.env
cat > ~/.config/opencode/agent-notifier.env <<'EOF'
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_USER_ID=U12345678
# Optional Feishu/Lark target:
# LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here
EOF
```

See `docs/opencode_plugin.md` for the full option list.

### Other agents

Gemini CLI, Cursor, Copilot CLI and similar tools point their completion hook at the same wrapper;
`docs/integrations.md` collects the per-tool wiring, and `docs/examples/` holds ready-made config
snippets. For a tool with no hook at all, wrap the agent command and call the notifier when it
exits — `docs/examples/copilot_wrapper.sh` shows the shape.

### Interpreter resolution

Hooks do not inherit the shell that activated your virtualenv, so the wrapper resolves Python
itself, in order:

1. `$NOTIFIER_PYTHON`
2. `<repo>/.venv/bin/python`, then `<repo>/venv/bin/python`
3. `python3`, then `python` from `PATH`

With a repo-local `.venv` there is nothing to configure. With conda or pyenv, set the absolute path
in your agent's env config:

```json
"NOTIFIER_PYTHON": "/home/you/miniconda3/envs/coding_agent_notifier/bin/python"
```

## Message format

When the payload carries a `transcript_path` — Claude Code and Codex both provide one — the
notifier reads the transcript the agent has already written. No summarisation call is involved:

```
🫐  Session title
repo-name · branch · 2m 45s · Claude Code
💬 the last request you made, clipped to 100 characters
↳ the closing line of the agent's final reply
```

| Line | Where it comes from |
| --- | --- |
| Session title | The name you gave the session (Claude Code's `custom-title` record or its `custom-title.json` sidecar; Codex's `threads.name`), else the generated `ai-title`. With no name at all, the request is promoted to the headline. |
| Repo · branch | The workspace path's last segment, and the branch read straight from `.git/HEAD` — no subprocess. |
| Duration | The final turn only (last request to last reply), so hours of idle time on a long-lived session are not counted. |
| 💬 request | The newest `user` record in the transcript. Tool results, slash-command echoes, `<system-reminder>` blocks and subagent turns are filtered out. Claude Code's `last-prompt` record lags a turn or two behind, so it is only a fallback. |
| ↳ reply | The last non-empty line of the agent's final reply. |

Other behaviour worth knowing:

- Transcripts of long sessions can exceed 100MB, so only the last 4MB is parsed. A real 114MB Codex
  rollout reads in about 20ms.
- A `status` reporting a failure or warning prefixes the session icon (`❌ 🦊`). A successful status
  adds nothing: it is the normal case, and the icon slot is worth more as identity.
- **Failed turns notify too, on Claude Code.** Its `Stop` hook does not fire when a turn ends in an
  error, but an undocumented `StopFailure` hook does, carrying the error as
  `last_assistant_message`. Register it alongside `Stop` and a usage limit, an API error or a
  safeguard refusal arrives as `❌` with the error as the closing line. Codex has no equivalent
  event -- its only notification is `agent-turn-complete` -- so a failed or interrupted Codex turn
  stays silent.
- Payloads with no transcript keep the original flat layout (`Status:` / `Duration:` / `Repo:`
  lines), so custom hooks and CI scripts are unaffected.
- `--no-transcript` turns transcript reading off entirely, for repositories where request text
  should not leave the machine.

### Codex specifics

Codex passes its `agent-turn-complete` JSON as the first argument, which the wrapper accepts. Its
kebab-case keys (`input-messages`, `last-assistant-message`, `cwd`) are mapped onto the fields
above. The payload carries neither the thread name nor the turn duration, so both are read back
from Codex's own SQLite state (`$CODEX_HOME/state_*.sqlite` and `thread_history_*.sqlite`). Those
are internals rather than a published interface, so a failed lookup is ignored and the notification
still goes out.

Codex also runs a turn against itself to name a new thread, which fires `notify` like any other.
Those are recognised — by the prompt, or by a reply that is just `{"title": "..."}` — and send
nothing.

## Session icons

The leading emoji identifies the **session**, not its outcome. Claude Code and Codex hooks report
no status at all, so a fixed check mark carried no information, while the emoji is the first thing
the eye lands on in a Slack list.

The icon is derived from the session or thread id, so one session keeps it for life and across
machines. With no session id the workspace path is used instead, so at least each repository stays
recognisable, and a payload can override the choice with an `icon` field.

Selection uses rendezvous hashing rather than `hash % len(pool)`. With the modulo, the pool size is
part of the mapping and adding an icon would re-assign every session; here a session only moves
when a newly added icon outscores its current one.

The pool holds 120 emoji, grouped by dominant colour. Entries must render in colour without a
variation selector, which a test enforces. Adding to the pool is safe; re-ordering it is not
necessary, since the mapping does not depend on position.

| Colour | Icons |
| --- | --- |
| red | 🍎 🍒 🌹 🎈 🧨 🏮 🦞 💥 🚨 🥊 |
| orange | 🦊 🍊 🎃 🏀 🥕 🔥 🧡 🍁 🍑 🦁 |
| yellow | 🍋 🌻 ⭐ 🐤 🧀 🍌 🐝 ⚡ 💛 🌼 |
| green | 🐸 🥝 🌵 🍀 🥑 🐢 💚 🥦 🐉 🎾 |
| blue | 🐬 💎 🧊 🫐 🌊 💙 🦋 🐳 🔵 🌀 |
| purple | 🍇 🔮 🟣 💜 🦄 👾 🍆 🎆 🔯 🪀 |
| pink | 🌸 🐷 🦩 💗 🌺 🎀 🍬 🦐 🧁 👛 |
| brown | 🐻 🍩 🧸 🦉 🌰 🎩 🪵 🥔 🐴 🏈 |
| black, white, grey | 🐼 🦢 🐧 🎱 ⚪ ⚫ 🦓 🐺 🎹 🖤 |
| multicoloured | 🌈 🎨 🦜 🎪 🎡 🎠 🪁 🎁 🍭 🧩 🎲 🎯 🚀 🛸 🪐 🌙 🍄 🐙 🦖 🐡 🦈 🐨 🐯 🐰 🐹 🐵 🦥 🦔 🦦 🦚 |

## Feishu/Lark custom bot

1. **Create the bot.** Add a custom bot to the chat that should receive notifications and copy its
   webhook URL. Leave signature verification disabled — this version does not sign requests.
   Docs: [Lark](https://open.larksuite.com/document/client-docs/bot-v3/add-custom-bot) ·
   [Feishu](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).

2. **Store the URL** the same way as the Slack credentials, in `.env` or your agent's config:

   ```bash
   LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/your-token-here
   # China endpoint instead:
   FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
   ```

3. **Test it:**

   ```bash
   echo '{"status":"success","title":"Agent run","summary":"Finished"}' \
     | .venv/bin/python scripts/notifier/lark_notify.py
   ```

4. **Wire it up.** `agent_notify_wrapper.sh` is Slack-only, so point hooks at `lark_notify.py`
   directly:

   ```toml
   # ~/.codex/config.toml
   notify = [
     "/path/to/coding-agent-notifier/.venv/bin/python",
     "/path/to/coding-agent-notifier/scripts/notifier/lark_notify.py",
     "--webhook-url-env", "FEISHU_WEBHOOK_URL",
   ]
   ```

   A custom bot posts to the chat it was installed in, not to a user DM. See
   `docs/notifier_lark.md` for the full walkthrough.

## Reference

### Environment variables

| Variable | Purpose |
| --- | --- |
| `SLACK_BOT_TOKEN` | Bot User OAuth token (`xoxb-…`). Required for Slack. |
| `SLACK_USER_ID` | Member ID to DM (`U…`). Required for Slack. |
| `LARK_WEBHOOK_URL` / `FEISHU_WEBHOOK_URL` | Custom bot webhook. Required for Feishu/Lark. |
| `ENV_FILE` | Env file the wrapper passes to the CLI. Default `<repo>/.env`; skipped when absent. |
| `NOTIFIER_PYTHON` | Interpreter the wrapper should use. See [Interpreter resolution](#interpreter-resolution). |
| `DEBUG_AGENT_PAYLOAD` | Path to write the payload the wrapper selected, for debugging. |
| `CODEX_HOME` | Where to find Codex's state databases. Default `~/.codex`. |
| `XDG_STATE_HOME` | Where the Codex watcher keeps its watermark. Default `~/.local/state`. |

### CLI flags

Both `scripts/notifier/slack_notify.py` and `scripts/notifier/lark_notify.py` accept:

| Flag | Meaning |
| --- | --- |
| `--payload '<json>'` / `--payload-file <path>` | Read the payload from an argument or a file instead of stdin. |
| `--env-file <path>` | Load `KEY=VALUE` lines from this file. A missing file warns rather than failing. |
| `--title <text>` | Fallback title when the payload supplies none. |
| `--no-transcript` | Skip reading the agent transcript. |
| `--log-level DEBUG\|INFO\|WARNING\|ERROR\|CRITICAL` | Default `WARNING`, which stays quiet in hooks. |
| `--user-id` (Slack) | Target member ID, overriding `SLACK_USER_ID`. |
| `--webhook-url`, `--webhook-url-env` (Lark) | Webhook URL, or the variable holding it. |

### Payload fields

A custom hook or CI script can supply any of these; each line is omitted when its field is absent:

`title` (or `event`, `task`) · `status` (or `state`) · `summary` (or `message`, `details`) ·
`error`, `error_details`, `last_assistant_message` (StopFailure) ·
`duration` (or `elapsed`, `time`) · `url` (or `link`, `target`) · `repo` (or `cwd`, `workspace`) ·
`icon` · `session_id` · `transcript_path`

## Troubleshooting

**No DM arrives.** Run the wrapper by hand with a payload on stdin and look at stderr:

```bash
echo '{"status":"ok","title":"test"}' | scripts/notifier/agent_notify_wrapper.sh
```

`Missing Slack token …` means the credentials were not found — check the file mode and that the
variable names match. An `invalid_auth` from Slack means the token itself is wrong or revoked.

**The hook never runs.** Confirm the event name exists for your agent: Codex has no `Stop` event,
so use `notify` (above). Restart the agent after editing its config — most read hook config only at
startup.

**Capture what the agent actually sent:**

```bash
export DEBUG_AGENT_PAYLOAD=/tmp/agent_payload.json
```

The wrapper writes the payload it selected to that path. Unset the variable to stop.

**Run the tests:**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

More detail lives in `docs/guide.md` (setup, config, FAQs), `docs/notifier_slack.md`,
`docs/notifier_lark.md` and `docs/integrations.md`.

## Migration and compatibility

This project was renamed from Vibe Coding Slack Notifier to Coding Agent Notifier on 2026-05-17.
Old names remain as aliases, so existing setups keep working:

| Old | Current |
| --- | --- |
| `Wangmerlyn/vibe-coding-slack-notifier` | `Wangmerlyn/coding-agent-notifier` |
| `codex-slack-notifier`, `codex_slack_notifier` | `coding-agent-notifier`, `coding_agent_notifier` |
| `scripts/notifier/codex_notify_wrapper.sh` | `scripts/notifier/agent_notify_wrapper.sh` |
| `DEBUG_CODEX_PAYLOAD` | `DEBUG_AGENT_PAYLOAD` |
| `~/.codex/vibe-coding-slack-notifier.env` | `~/.codex/coding-agent-notifier.env` |
| `~/.config/opencode/slack-notifier.env` | `~/.config/opencode/agent-notifier.env` |
| `opencode-vibe-coding-slack-notifier`, `OpenCodeSlackNotifierPlugin` | `opencode-coding-agent-notifier`, `OpenCodeAgentNotifierPlugin` |

New installs should use the current names throughout.
