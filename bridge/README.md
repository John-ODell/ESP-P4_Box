# bridge

The pet's brain and its GitHub reader. Runs on the home Mac; the box never holds a GitHub token.

- `petbrain/brain.py`: pure functions. Days in, pet state out. No network, no deps. Unit tested.
- `petbrain/github_source.py`: read-only GraphQL reader that turns the viewer's `contributionsCollection` into `Day`
  records.
- `petbrain/cli.py`: fetch + compute + print, for checking the numbers by eye.
- `tests/`: `python3 -m unittest discover -s tests -t .`

Later milestones add the MQTT publisher, the HTTP endpoint for Claude Code / Codex session events, and launch-at-login.

## Run it

```bash
cd bridge
cp .env.example .env            # optional: put a GITHUB_TOKEN in it; otherwise `gh auth token` is used
python3 -m petbrain.cli --days 14
```

Needs Python 3.11+ (stdlib only so far).

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

## Known limits

- GitHub only breaks down contributions the token can see. Private-repo activity the API calls "restricted" arrives as
  calendar totals only, so it scores as `other` at weight 1. With the `gh` OAuth token that was 623 of 839
  contributions this year. A fine-grained PAT with access to those repos may reveal more; if not, this is a GitHub
  limit and the weights for private PRs and reviews cannot be honoured.
- Calendar days are GitHub's; the detailed window buckets `occurredAt` into `PET_TZ` local days. The two can disagree
  by one day around midnight.
