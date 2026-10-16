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

```bash
brew install mosquitto
cd bridge
python3 -m venv .venv && .venv/bin/pip install paho-mqtt
cp .env.example .env            # set MQTT_PASS to something random; GITHUB_TOKEN is optional (falls back to `gh auth token`)
mosquitto/setup.sh              # writes mosquitto/pet.conf and the password file from .env
```

Put the same `MQTT_USER` / `MQTT_PASS` and the Mac's LAN IP into `projects/pet/main/secrets.h` (see the `.example`).
The Mac should have a DHCP reservation so the IP in `secrets.h` stays valid.

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
| `pet/notify` | bridge, after every accepted event or ack | yes | the pending list below |
| `pet/ack` | box, when a tap acknowledges a session (milestone 6) | no | `{source, session}` or `{all: true}` |

Retained means the box gets the latest state as soon as it subscribes. The box treats a retained message as a replay:
it renders the numbers but does not re-run the celebrations in `events`.

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
(not JSON, unknown `kind`, missing `source` or `session`, over 4 KB) gets a 400 and a log line; the GitHub loop is
not affected. Every accepted event goes out on `pet/event` (not retained) and the whole pending list on `pet/notify`
(retained: the newest 8 of up to 32 entries, plus `count`), so a box that reboots gets the list back. When the box
acknowledges a tap on `pet/ack` with `{"source":"claude","session":"website"}` (or `{"all":true}`) the bridge drops
the entry and republishes the list. The list lives in memory: a bridge restart publishes an empty list.
`GET http://127.0.0.1:8765/pending` shows what is waiting. Senders with native hooks (Claude Code, Codex) and the
box's rendering are separate units: issues #5 and #6.

## Known limits

- GitHub only breaks down contributions the token can see. Private-repo activity the API calls "restricted" arrives as
  calendar totals only, so it scores as `other` at weight 1. With the `gh` OAuth token that was 623 of 839
  contributions this year. A fine-grained PAT with access to those repos may reveal more; if not, this is a GitHub
  limit and the weights for private PRs and reviews cannot be honoured.
- Calendar days are GitHub's; the detailed window buckets `occurredAt` into `PET_TZ` local days. The two can disagree
  by one day around midnight.
