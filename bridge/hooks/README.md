# Hooks: how AI tools tell the pet

Everything here posts to the bridge's local endpoint, `POST http://127.0.0.1:8765/event` (the message format and
rules are in [../README.md](../README.md), section "Session notifications"). Nothing in this folder installs
itself. Either paste a snippet into the tool's own settings by hand, or let `../connect.py` do it.

## Connect with one command

```bash
python3 bridge/connect.py
```

It lists the tools the pet knows, whether each is on this Mac and whether it is connected, and whether the bridge is
answering, then asks which to connect (picking a connected one offers to disconnect it). For every file it prints the
diff, asks, copies the file to `<file>.pet-backup-<date>-<time>`, writes it, and reads it back. A second run changes
nothing. `--status` prints the table only; `--connect claude codex --yes` and `--disconnect codex` skip the menu;
`--repo PATH` points the hooks at another checkout, so run it from the checkout the bridge uses.

- Claude Code: the three hook groups of `claude-settings.json` are merged into `~/.claude/settings.json`. Other keys
  and other hooks are kept; disconnecting removes only the hook entries that run `claude_hook.py`.
- Codex: the `notify` route (`codex_notify.py`), because lifecycle hooks need the Codex CLI's `/hooks` trust step and
  are unverified (#13). The `notify` command already in `~/.codex/config.toml` is chained after `--then`, and
  disconnect puts back the same list, written as `notify = ["...", "..."]` (byte for byte when it was written that
  way). Only a one-line `notify = [...]` is edited; anything else is refused.
- Goose: a `GOOSE_STATUS_HOOK` line appended to `~/.config/goose/config.yaml`, running `goose_hook.py`. Aider: a
  `notifications-command` line in `~/.aider.conf.yml`, plus `notifications: true` when the file does not set it.
  Lines added there end with `# desk pet: bridge/connect.py`; disconnecting removes those lines and the hook line,
  also after Goose rewrote the file without the comment. A status hook or `notifications` set to off that is
  already there is refused, not replaced.
- Ollama is a model server, so its sessions reach the pet through the tool that drives it (Goose, Aider, or
  Claude Code started with `ollama launch claude`); direct use is #32.
- Connecting any tool refuses when the checkout named by `--repo` does not have that tool's hook script yet.
- Changes apply to sessions started afterwards. A running Claude Code session picks up new hooks after a look in
  `/hooks`.

| Tool | How it talks to the pet | Reports | Clears itself | Checked on this Mac |
|---|---|---|---|---|
| Claude Code (terminal and desktop app) | native hooks: `claude-settings.json` runs `claude_hook.py` | `needs_input` for permission prompts and questions, `done` when a turn finishes | yes, on the next prompt you type in a session in that folder | live, Claude Code 2.1.261, `claude -p` with `--settings`, nothing global touched |
| Codex, lifecycle hooks | `codex-hooks.json` runs `codex_hook.py` | `needs_input` for approval requests, `done` | yes, next prompt | sample payloads only: no `codex` on PATH; the ChatGPT app bundles `codex-cli 0.162.0-alpha.17.2`, not driven, so whether it runs `hooks.json` is unknown (#13) |
| Codex, the older `notify` setting | `codex-config.snippet.toml` runs `codex_notify.py` | `done` only | no: the bubble goes on the next finished turn or a tap | sample payload only; chains the notify command already in `config.toml` |
| Goose CLI (any model, Ollama included) | `GOOSE_STATUS_HOOK` runs `goose_hook.py` with `thinking` or `waiting` | `done` when it is back at its prompt after a message typed in a normal interactive session | yes, next prompt | `sh -c` runs with sample statuses; no live Goose session (it would need a model) |
| Aider (any model, Ollama included) | `notifications-command` runs `aider_hook.py` | `done` when a reply is ready or it asks a question | no: next `done` or a tap | `sh -c` run without a bridge; no live Aider session |
| Any script, cron job or local model | a wrapper around `../notify.py` | whatever you post, including `error` | your call | live |
| Chat apps without hooks (ChatGPT, Claude.ai in a browser, Cursor chat) | nothing | nothing | - | not possible without a wrapper |

## What the scripts do

- `claude_hook.py` reads Claude Code's hook JSON from stdin. `Notification` with `notification_type`
  `permission_prompt`, `elicitation_dialog`, `elicitation_url_dialog` or `agent_needs_input` becomes `needs_input`
  (title: the notification's title, else its first line); `Stop` becomes `done` (title: first line of the last
  assistant message); `UserPromptSubmit` becomes `cleared`, unless the payload says the prompt came from a `/loop`
  or scheduled wakeup rather than from you (those wakeups still end in a `Stop`, so a looping session posts `done`
  each round). Other notification types send nothing: `idle_prompt` fires a minute
  after every finished turn and would turn each `done` into `needs_input`; `auth_success`, `agent_completed` and
  `quota_*` are noise. The session name is the last component of the project folder (`cwd`), so every session in
  a folder of that name, anywhere, shares one bubble. Claude Code's session title (`--name`, `/rename`) is not
  used: it reaches only the `UserPromptSubmit` payload, not `Stop` or `Notification`, so keying by it would leave
  bubbles that never clear.
- `codex_hook.py` does the same for Codex lifecycle hooks: `PermissionRequest` becomes `needs_input` (title: the
  tool input's description, else "<tool> needs approval"; detail: the command), `Stop` becomes `done`,
  `UserPromptSubmit` becomes `cleared`. The session name is the folder name here too.
- `codex_notify.py` is for Codex's single `notify` command, which only fires on `agent-turn-complete`: it posts
  `done`. Codex appends the payload as the last argument; with `--then CMD ARGS` the script posts its event and
  then starts that command with the same payload appended, without waiting for it, so a notify command that is
  already configured keeps working and can neither hold up nor lose the pet's event.
- `goose_hook.py` takes Goose's status word as its last argument: `thinking` becomes `cleared`, `waiting` becomes
  `done` (title "waiting for you"; Goose passes no text). Goose also says `waiting` once at startup, so `waiting`
  only counts after a `thinking` in the same folder, remembered by a `pet-goose-<hash>` file in the temp folder.
  Goose appends the word to the command text, so the configured line has no `2>/dev/null || true`; Goose throws
  the output away and does not wait for it anyway. The hook belongs to the Goose CLI; the Goose desktop app does
  not run it. Only a message typed in a normal interactive session sends `thinking`: `goose run`, the first turn of
  `goose run -s -t` and plan mode do not, so no `done` follows them. Two Goose sessions in one folder share a
  marker (#36).
- `aider_hook.py` posts `done` for the folder. Aider runs it with no arguments and waits for it when a reply is
  ready and when it asks a question, and only after the model has worked, so not at startup. A question Aider
  answers itself (`--yes-always`, or one you told it not to ask again) still runs it, so a `done` can arrive while
  Aider is still working.
- `pethook.py` is shared: first-line trimming, the folder name, the POST (no proxies, one-second timeout).
- Every script prints nothing on stdout, swallows every error and exits 0, so a stopped bridge can never block or
  slow a session. `PET_NOTIFY_URL` overrides the endpoint for all of them.
- `SessionEnd` is not wired on purpose. In a one-shot `claude -p` run it fires right after `Stop` and would clear the
  `done` bubble before anyone saw it. Bubbles of sessions you closed go away with a tap on the pet.

## Replying in a session clears its bubble (issue #8)

No tap is needed for a session you have answered. The hook posts `cleared` when you send the next prompt, the bridge
drops that session's entry and republishes the retained `pet/notify` list, and the box redraws from that list
(`projects/pet`, issue #6), so once the hooks are installed and the box runs the #6 firmware the bubble should be gone
a moment after Enter. That is the expectation from the path (a `python3` start, one loopback POST, two local publishes,
a redraw on the box's MQTT task), not a measurement: nothing has been timed on the box, and #26 is the UART check
against the 2 s of #8. The key is the folder name, so a prompt in any session in a folder of that name, anywhere (a
second session there, a `claude -p` run), clears that bubble too. A `cleared` for a session that is not pending is a
no-op on the bridge: it logs the event, `Pending.apply` returns False and nothing is inserted
(`bridge/tests/test_events.py`, `test_cleared_removes_and_is_a_no_op_otherwise` covers `Pending.apply`; the endpoint
still answers 200, by `Notifier.post`), the republished list carries the same entries under a fresh timestamp, and
the box redraws the same bubble without a reaction. Per sender:

- Claude Code: yes. `UserPromptSubmit` becomes `cleared`. The script would skip wakeups of `/loop`, schedules and
  polls by their `source` field, because nobody answered anything, but Claude Code 2.1.261 sends no `source` on
  `UserPromptSubmit` (the hooks reference lists `prompt` and `session_title` only; the #14 review saw it live), so
  that guard is dormant: today every prompt clears, wakeups included, and a `/loop` running in a folder named like a
  waiting session empties that bubble each round, until Claude Code sends the field (#27 is about the guard's shape
  when it does). Answering a permission dialog is not a prompt, so a `needs_input` bubble stays until that turn
  ends (#15).
- Codex, lifecycle hooks (`codex-hooks.json`): yes on paper, the same `UserPromptSubmit` to `cleared`, checked against
  the hooks engine in the codex-rs source and sample payloads only. There is no `codex` on PATH on this Mac, but the
  ChatGPT app bundles one at `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex` (`codex-cli
  0.162.0-alpha.17.2`, read with `--version` with HOME and CODEX_HOME pointed at a scratch folder; nothing under
  `~/.codex` touched); it was not driven for this PR, so #13, the live check, can be done on this Mac.
- Codex, the older `notify` setting: no. `notify` fires on `agent-turn-complete` only, so a bubble from this route is
  replaced by `done` at the next finished turn, or goes with a tap.
- Wrappers around `notify.py`: your call. Post `--kind cleared` when your script knows the person has answered.

## Install, by hand

**Claude Code.** Merge the `hooks` block from `claude-settings.json` into `~/.claude/settings.json` (every
project) or a project's `.claude/settings.json` (that project only). The command runs through `sh`, so `$HOME`
expands; change the path if the repo is somewhere else. The hook's `timeout` is 5 s and the script gives up after
1 s on its own. The `|| true` at the end matters: `python3` exits 2 when the script file is missing, and Claude Code
treats a hook's exit code 2 as a blocking error (`UserPromptSubmit` would refuse the prompt), so a moved repo must
not be able to break a session.

**Codex.** Copy `codex-hooks.json` to `~/.codex/hooks.json` (every project) or `<repo>/.codex/hooks.json` (one
project), then run `/hooks` in the Codex CLI to review and trust it: Codex skips a command hook until its exact
text is trusted. The command is written as `"$HOME/..."` like the Claude one; Codex also runs hook commands through
a shell (its hooks engine starts `<shell> -lc`), so it expands; if yours does not, write the absolute path. If
lifecycle hooks do not run in your Codex, the older `notify` route reports finished turns only: see
`codex-config.snippet.toml`. Codex allows one notify command, so chain the one you already have after `--then`.

## Try it without installing anything

With the bridge running (`.venv/bin/python -m petbrain.publish` in `bridge/`):

```bash
claude -p "Reply with exactly the word ok." --settings "$HOME/Desktop/ESP-P4_Box/bridge/hooks/claude-settings.json" < /dev/null
curl -s http://127.0.0.1:8765/pending
```

The bridge log shows `event claude cleared '<folder>'` for the prompt, then `event claude done '<folder>'` for the
finished turn, and `/pending` lists the `done` entry. For Codex without a CLI, feed a payload by hand:

```bash
echo '{"hook_event_name":"PermissionRequest","cwd":"/tmp/demo","tool_name":"shell","tool_input":{"description":"Push the branch"}}' \
  | python3 bridge/hooks/codex_hook.py
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
