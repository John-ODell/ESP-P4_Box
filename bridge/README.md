# bridge

The pet's brain and its GitHub reader. Runs on the home Mac; the box never holds a GitHub token.

- `petbrain/brain.py`: pure functions. Days in, pet state out. No network, no deps. Unit tested.
- `petbrain/github_source.py`: read-only GraphQL reader that turns the viewer's `contributionsCollection` into `Day`
  records.
- `petbrain/cli.py`: fetch + compute + print, for checking the numbers by eye.
- `petbrain/publish.py`: the bridge loop. Fetch, compute, publish `pet/state` (retained) to Mosquitto.
- `mosquitto/`: broker config template and setup/run scripts.
- `tests/`: `python3 -m unittest discover -s tests -t .`

Milestone 6 adds session notifications from AI tools: the bridge's endpoint is in (see below), the box's bubble
follows. Milestone 7 adds launch-at-login.

## Setup (once)

From the repo root:

```bash
python3 bridge/setup.py
```

It writes `bridge/.env` with a strong MQTT password, keeps that file readable by you only, and, if this Mac is on
Tailscale, offers to let your other devices reach the pet (with a strong secret token). If Tailscale is not installed
it says what it is and where to get it, and leaves that off. It never prints a secret unless you pass
`--show-secrets`, keeps every value you already set, and changes nothing on a second run. It ends with a numbered
list of what is left, run from the repo root. On a new Mac (with Homebrew, https://brew.sh) that is:

```bash
brew install python mosquitto   # the Mac's own python3 is 3.9; the bridge and connect.py need 3.11+
cd bridge && python3 -m venv .venv && .venv/bin/pip install paho-mqtt && cd ..
bridge/mosquitto/setup.sh       # writes mosquitto/pet.conf and the password file from .env
```

then the box's `secrets.h` and flashing (below), and starting the broker and the bridge (Run it).

Put the same `MQTT_USER` / `MQTT_PASS` (`python3 bridge/setup.py --show-secrets`) and the Mac's LAN IP into
`projects/pet/main/secrets.h` (see the `.example`). The Mac should have a DHCP reservation so the IP in `secrets.h`
stays valid. `GITHUB_TOKEN` is optional; without it the bridge uses `gh auth token`.

## Run it

```bash
cd bridge
mosquitto/run.sh                               # the broker, foreground (login item comes in milestone 7)
.venv/bin/python -m petbrain.publish           # fetch + publish every PET_POLL_MIN minutes
.venv/bin/python -m petbrain.publish --once    # one cycle
python3 -m petbrain.cli --days 14              # read-only: print the numbers, no MQTT
```

Poke the box with a made-up state:

```bash
.venv/bin/python -m petbrain.publish --once --set mood=1 --set streak_at_risk=true --no-save
.venv/bin/python -m petbrain.publish --once --set 'events=["level_up"]' --set level=9 --no-save
```

Watch what the box receives:

```bash
set -a; . ./.env; set +a; mosquitto_sub -h localhost -u "$MQTT_USER" -P "$MQTT_PASS" -t 'pet/#' -v
```

The brain needs only the standard library (Python 3.11+); the publisher needs `paho-mqtt` (hence the venv).

## Rules (Flipper dolphin mechanics, GitHub deeds)

| Deed | XP each | Daily cap (XP) |
|---|---|---|
| commit | 1 | 30 |
| pull request | 3 | 10 |
| issue | 3 | 10 |
| code review | 4 | 10 |
| uncategorised (calendar only) | 1 | 30 |

- **Levels**: cumulative XP thresholds 100, 300, 700, 1500, 3000, then doubling (6000, 12000, ...). Level 1 is 0 XP.
- **Mood** 0..10, starts at 5 sixty days back and is replayed forward: each day adds `min(3, day_xp // 10)`; an idle
  day subtracts 1, never more. Today is not penalised until it is over. Labels: 0-2 sad, 3-5 neutral, 6-8 happy,
  9-10 ecstatic.
- **Streak**: consecutive days with any contribution, ending today or, if today is still empty, yesterday.
  `streak_at_risk` turns on from 18:00 local when today is still empty and a streak is alive.
- **Goal**: `today.total >= 20` (configurable). **Big day**: 100 or more.
- **Events** (one-shot, derived against the previously published state): `level_up`, `goal_hit`, `big_day`.

## Topics

| Topic | Who publishes | Retained | Payload |
|---|---|---|---|
| `pet/state` | bridge, every poll | yes | the state below |
| `pet/event` | bridge, from the session-event endpoint below | no | `{v, ts, source, kind, session, title, detail}` |
| `pet/notify` | bridge, after every accepted event or ack | yes | `{v, ts, count, pending: [{source, session, kind, title, ts}]}`, example below |
| `pet/ack` | box, when a tap acknowledges a session (milestone 6) | no | `{source, session}` or `{all: true}` |
| `pet/q20` | box, during a 20 Questions game (#78) | no | `{game, op: start}`, `{game, op: answer, n, a}` or `{game, op: quit}` |
| `pet/q20/ask` | bridge, the model's next turn | no | `{game, n, q}`, `{game, n, guess}` or `{game, error}` |
| `pet/todo` | bridge, on start and after every change to the to-do list (#89) | yes | `{v, open, count, items: [{id, text, done, by, ref, flag?}]}` |
| `pet/todo/tick` | box, when a tap ticks or unticks an item | no | `{id, done}` |

Retained means the box gets the latest state as soon as it subscribes. The box treats a retained message as a replay:
it renders the numbers but does not re-run the celebrations in `events`. Updates over the LAN (#16) add two more
topics, `pet/cmd` (Mac to box, never retained) and `pet/ota` (box to Mac, retained): `projects/pet/README.md`,
"Updating over Wi-Fi". Pet packs over Wi-Fi (#68) use `pet/cmd` too and report on `pet/pack` (box to Mac, retained).

## State message (`pet/state`)

```json
{"v":1,"ts":"2026-10-15T19:51:08-05:00","login":"John-ODell",
 "level":3,"xp":428,"xp_into":128,"xp_next":400,
 "mood":10,"mood_label":"ecstatic",
 "streak":14,"streak_at_risk":false,
 "today":{"total":111,"commits":4,"prs":2,"issues":0,"reviews":0,"other":105,"xp":40},
 "goal":20,"goal_hit":true,"big_day":true,
 "events":[]}
```

Small enough for the box and self-describing enough for a phone client later.

## Session notifications (milestone 6)

Any AI tool on this Mac can tell the pet that a session wants attention. While the bridge runs in loop mode it
listens on `http://127.0.0.1:8765/event` (`PET_HTTP_HOST` and `PET_HTTP_PORT` in `.env`; loopback only unless you
change the host) and forwards each accepted event to the box.

```bash
curl -s -X POST http://127.0.0.1:8765/event -H 'Content-Type: application/json' \
  -d '{"v":1,"source":"claude","kind":"needs_input","session":"website","title":"Allow the edit?"}'
```

From any other tool, or a wrapper around a local model, `bridge/notify.py` does the same with the standard library
only, gives up after two seconds and always exits 0, so a hook that calls it never blocks its tool:

```bash
python3 bridge/notify.py --source local --kind done --session "llama batch" --detail "12 files written"
```

| Field | Required | Meaning |
|---|---|---|
| `v` | no | message version, 1 |
| `source` | yes | which AI: `claude`, `codex`, `local`, or any short name; lower-cased, 32 characters |
| `kind` | yes | `needs_input`, `done` and `error` show on the box; `cleared` takes the session's entry away |
| `session` | yes | the session name shown on the box, 64 characters |
| `title`, `detail` | no | one line each, 80 and 200 characters; longer text is cut |
| `ts` | no | ISO 8601; the bridge fills in the current time when missing |

Rules: one pending entry per `source` plus `session`, a newer event replaces the older one, newest first. A bad body
(not JSON, unknown `kind`, missing `source` or `session`) gets a 400 with the reason, a body over 4 KB gets a 413, a
request carrying a browser `Origin` header gets a 403, and each leaves a log line; the GitHub loop is not affected.
Every accepted event goes out on `pet/event` (not retained) and the whole pending list on `pet/notify` (retained), so
a box that reboots gets the list back:

```json
{"v":1,"ts":"2026-10-16T08:21:45-05:00","count":2,"pending":[
  {"source":"local","session":"llama batch","kind":"done","title":"","ts":"2026-10-16T08:21:45-05:00"},
  {"source":"claude","session":"website","kind":"needs_input","title":"Allow the edit?","ts":"2026-10-16T08:21:45-05:00"}]}
```

`pending` holds the newest 8 of up to 32 entries and `count` says how many are kept. An entry carries `title` but not
`detail`, which only travels on `pet/event`. When the box acknowledges a tap on `pet/ack` with
`{"source":"claude","session":"website"}` (or `{"all":true}`) the bridge drops the entry and republishes the list.
The list is written to `bridge/var/pending.json` (gitignored, next to `last_state.json`) every time it is published and
read back when the bridge starts, each entry through the same checks and caps as a posted event, so a restart publishes
what was still waiting, with each entry's original `ts`. A missing file starts it empty; an unreadable or odd one starts
it empty with a warning in the log; entries that are not what the bridge writes are dropped, with one warning saying
how many. `--once` and `--state-file` runs never start the notifier and touch neither the file nor `pet/notify`.
`GET http://127.0.0.1:8765/pending` shows what is waiting. Senders with native hooks (Claude Code, Codex) and the
box's rendering are separate units: issues #5 and #6.

### From your other machines, over Tailscale

The endpoint above answers this Mac only. To let AI sessions on your other personal machines reach the pet, put this
Mac on Tailscale and run `python3 bridge/setup.py` (answer yes when it offers). It sets these in `.env`:

```bash
PET_TAILNET=on
PET_TAILNET_TOKEN=<32+ random letters, digits, - and _, from Python's secrets module>
```

The bridge then also listens on this Mac's own Tailscale address, which it finds with `tailscale ip -4` (set
`PET_TAILNET_HOST` to override), and keeps retrying until Tailscale is up. It is safe by default:

- It never listens on 0.0.0.0 or the home Wi-Fi address, and it does not start at all without a long token.
- Every request there needs `Authorization: Bearer <token>` and a Tailscale source address; others get 401 or 403
  and a log line.
- Tailscale encrypts the traffic, but only while Tailscale is on at both ends. Use the Mac's MagicDNS name
  (`johns-macbook-pro.<tailnet>.ts.net`), not its 100.x address: with Tailscale off on the sending machine that name
  usually does not resolve, so nothing is sent, where a raw address would go out unencrypted on whatever network it is on.
- The loopback endpoint is unchanged, so hooks on this Mac need no token.

Then, on this Mac, restart the bridge and run:

```bash
python3 bridge/connect.py --share
```

It checks the bridge answers on its Tailscale name and prints one line to paste on each of your other machines (same
Tailscale account, this repo cloned there):

```bash
python3 bridge/connect.py --bridge http://johns-macbook-pro.<tailnet>.ts.net:8765/event --token <the token>
python3 bridge/connect.py      # then connect that machine's AI tools as usual
```

`--bridge` writes `~/.config/desk-pet/bridge.json` owner-only (0600, in a 0700 folder), shows the change with the
token hidden, keeps a private backup, and says whether the bridge accepts that token. Every hook and `notify.py`
reads it from then on; `PET_NOTIFY_URL` and `PET_NOTIFY_TOKEN` still win if set. `connect.py --disconnect bridge`
puts that machine back on its own bridge. Treat the token like a password: paste it only on your own machines.

The box itself keeps using this Mac's broker on the home Wi-Fi: the ESP32 cannot
run Tailscale.

## 20 Questions

The person thinks of something and the pet asks yes-or-no questions on the box (#78). The box sends each tap on
`pet/q20`; the bridge loop keeps the game and asks a model for the next question or a guess (`petbrain/q20.py`), and
sends it back on `pet/q20/ask`. A question and a guess each use one of 20 turns, and turn 20 is always a guess. The box
never holds an API key.

The model is `PET_Q20_CMD` in `.env`: any command that reads the prompt on stdin and prints `{"q": ...}` or `{"guess":
...}`. The default, `claude -p --model haiku --tools '' --setting-sources '' --no-session-persistence`, uses the Claude
Code login already on this computer, so it needs no key. It runs with no tools, and without your user settings, so the
pet's own hooks never turn a question into a waiting session. A turn takes about 5 to 9 seconds. Another command can
stand in, for example `PET_Q20_CMD="ollama run llama3.2"` (not tried yet). Quote the value, so `. ./.env` reads it, and
keep an empty `''` argument off the end of the line: the bridge strips quotes from both ends of a value. A call that
takes longer than `PET_Q20_TIMEOUT` seconds (default 40), fails or gives an unreadable reply twice ends the game with a
short message on the box. A game cannot start while another game's turn is with the model (it gets "busy"), but a game
already running is never refused. Only the bridge that holds the notify endpoint answers, so a second bridge started by
mistake stays quiet.

## To-do list

A short list the box will show and tick (#89; the screen is #90). The bridge keeps it in `var/todo.json`, so it
survives a restart, and publishes it retained on `pet/todo`: open items oldest first, then the most recently done, at
most 12 and at most 3500 bytes, so it always fits the box's 4 KB MQTT buffer. You and AI sessions change it with `todo.py`, which uses the same address and token as `notify.py`:

```bash
python3 todo.py add "Flash the list screen" --ref 90 --by claude
python3 todo.py list            # open items, then the last five done; --all for every one
python3 todo.py done 3          # undo 3 opens it again, rm 3 removes it
```

An item is one line of up to 80 characters, plain ASCII the box can draw. `--ref` names the issue or PR it is about
(`85`, `#85` or `owner/repo#85`), for the box to check later (#91). The bridge keeps 50 items: past that the oldest done
item goes, and an add is refused while all 50 are open. Underneath it is `POST /todo` on the session endpoint with
`{"op": "add", "text", "by", "ref"}` or `{"op": "done" | "undo" | "remove", "id"}`, and `GET /todo` for the whole list.

Items with a ref follow GitHub (#91, `petbrain/todo_refs.py`). Every poll, the bridge asks GitHub about all of them in
one GraphQL call. It ticks an item itself (`done_by` "github") when its issue is closed as completed or its PR merged,
and unticks one it ticked if the issue is reopened. It acts only when GitHub's state has moved since it last looked, so
an item you untick by hand stays unticked until the issue or PR changes again. When you tick an item by hand while
GitHub disagrees, the bridge checks at once and flags it: `flag` is "open", or "dropped" for an issue closed as not
planned or duplicate, or a PR closed without merging. The box shows "! #85 still open" or "! #85 closed, not done" in
place of the ref, and `todo.py list` adds "! ticked, but #85 is still open". The flag goes when GitHub agrees or the
item is unticked. A plain `#85` means the repo in `PET_TODO_REPO` (`owner/repo`), else the GitHub repo this bridge was
cloned from. A ref GitHub cannot find (or this token cannot see) is left alone, with one warning in the log.

## Known limits

- GitHub only breaks down contributions the token can see. Private-repo activity the API calls "restricted" arrives as
  calendar totals only, so it scores as `other` at weight 1. With the `gh` OAuth token that was 623 of 839
  contributions this year. A fine-grained PAT with access to those repos may reveal more; if not, this is a GitHub
  limit and the weights for private PRs and reviews cannot be honoured.
- Calendar days are GitHub's; the detailed window buckets `occurredAt` into `PET_TZ` local days. The two can disagree
  by one day around midnight.

## Hooks for Claude Code and Codex

The hook scripts, the settings snippets and the table of which tools can talk to the pet natively are in
[hooks/README.md](hooks/README.md). Nothing there installs itself: the snippets are pasted by hand into the tool's
own settings, after a look. Replying in a session clears its bubble without a tap for Claude Code and, unverified
(#13), for Codex lifecycle hooks; the Codex `notify` route cannot: see
[Replying in a session clears its bubble](hooks/README.md#replying-in-a-session-clears-its-bubble-issue-8).
