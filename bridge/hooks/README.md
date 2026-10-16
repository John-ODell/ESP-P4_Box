# Hooks: how AI tools tell the pet

Everything here posts to the bridge's local endpoint, `POST http://127.0.0.1:8765/event` (the message format and
rules are in [../README.md](../README.md), section "Session notifications"). Nothing in this folder installs
itself: the snippets are pasted by hand into the tool's own settings, after a look at what they run.

| Tool | How it talks to the pet | Reports | Clears itself | Checked on this Mac |
|---|---|---|---|---|
| Claude Code (terminal and desktop app) | native hooks: `claude-settings.json` runs `claude_hook.py` | `needs_input` for permission and idle prompts and questions, `done` when a turn finishes | yes, when you send the next prompt in that session | live, Claude Code 2.1.261, `claude -p` with `--settings`, nothing global touched |
| Codex, lifecycle hooks | `codex-hooks.json` runs `codex_hook.py` | `needs_input` for approval requests, `done` | yes, next prompt | sample payloads only: no Codex CLI here, and whether the Codex inside the ChatGPT app runs `hooks.json` is unknown |
| Codex, the older `notify` setting | `codex-config.snippet.toml` runs `codex_notify.py` | `done` only | no | sample payload only; chains the notify command already in `config.toml` |
| Any script, cron job or local model | a wrapper around `../notify.py` | whatever you post, including `error` | your call | live |
| Chat apps without hooks (ChatGPT, Claude.ai in a browser, Cursor chat) | nothing | nothing | - | not possible without a wrapper |

## What the scripts do

- `claude_hook.py` reads Claude Code's hook JSON from stdin. `Notification` with `notification_type`
  `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog` or `agent_needs_input` becomes
  `needs_input` (title: the notification's title or first line); `Stop` becomes `done` (title: first line of the
  last assistant message); `UserPromptSubmit` becomes `cleared`. Other notification types (`auth_success`,
  `agent_completed`, `quota_*`) and other events send nothing. The session name is the session title when one is
  set (`--name`, `/rename`), otherwise the project folder; two untitled sessions in one folder share one bubble.
- `codex_hook.py` does the same for Codex lifecycle hooks: `PermissionRequest` becomes `needs_input` (title: the
  tool input's description, else "<tool> needs approval"; detail: the command), `Stop` becomes `done`,
  `UserPromptSubmit` becomes `cleared`. Codex sends no session title, so the folder name is used.
- `codex_notify.py` is for Codex's single `notify` command, which only fires on `agent-turn-complete`: it posts
  `done`. Codex appends the payload as the last argument; with `--then CMD ARGS` the script first runs that command
  with the same payload appended, so a notify command that is already configured keeps working.
- `pethook.py` is shared: first-line trimming, the folder name, the POST (no proxies, one-second timeout).
- Every script prints nothing on stdout, swallows every error and exits 0, so a stopped bridge can never block or
  slow a session. `PET_NOTIFY_URL` overrides the endpoint for all of them.
- `SessionEnd` is not wired on purpose. In a one-shot `claude -p` run it fires right after `Stop` and would clear the
  `done` bubble before anyone saw it. Bubbles of sessions you closed go away with a tap on the pet.

## Install, by hand

**Claude Code.** Merge the `hooks` block from `claude-settings.json` into `~/.claude/settings.json` (every
project) or a project's `.claude/settings.json` (that project only). The command runs through `sh`, so `$HOME`
expands; change the path if the repo is somewhere else. The hook's `timeout` is 5 s and the script gives up after
1 s on its own.

**Codex.** Copy `codex-hooks.json` to `~/.codex/hooks.json` (every project) or `<repo>/.codex/hooks.json` (one
project), then run `/hooks` in the Codex CLI to review and trust it: Codex skips a command hook until its exact
text is trusted. If your Codex does not expand `~` in the command, write the absolute path. If lifecycle hooks do
not run in your Codex, the older `notify` route reports finished turns only: see `codex-config.snippet.toml`.
Codex allows one notify command, so chain the one you already have after `--then`.

## Try it without installing anything

With the bridge running (`.venv/bin/python -m petbrain.publish` in `bridge/`):

```bash
claude -p "Reply with exactly the word ok." --settings "$HOME/Desktop/ESP-P4_Box/bridge/hooks/claude-settings.json" < /dev/null
curl -s http://127.0.0.1:8765/pending
```

The bridge log shows `event claude cleared '<folder>'` for the prompt, then `event claude done '<folder>'` for the
finished turn, and `/pending` lists the `done` entry. For Codex without a CLI, feed a payload by hand:

```bash
echo '{"hook_event_name":"PermissionRequest","cwd":"/tmp/demo","tool_name":"shell","tool_input":{"description":"Push the branch"}}' | python3 bridge/hooks/codex_hook.py
python3 bridge/hooks/codex_notify.py '{"type":"agent-turn-complete","last-assistant-message":"Rename complete.","cwd":"/tmp/demo"}'
```

## A wrapper for anything else

```bash
long_job; rc=$?
python3 "$HOME/Desktop/ESP-P4_Box/bridge/notify.py" --source local --kind "$([ "$rc" -eq 0 ] && echo done || echo error)" \
  --session "nightly build" --title "exit $rc"
```

`notify.py` takes `--kind needs_input`, `done`, `error` or `cleared`, exits 0 whatever happens and gives up after
two seconds.
