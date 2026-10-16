"""Read-only runner: fetch GitHub contributions, compute the pet state, print it.

    cd bridge && python3 -m petbrain.cli            # pretty state
    cd bridge && python3 -m petbrain.cli --json     # the exact message the box will get
    cd bridge && python3 -m petbrain.cli --days 14  # also print the last N days
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from .brain import compute_state, day_xp
from .github_source import fetch_days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print only the compact JSON state")
    ap.add_argument("--days", type=int, default=0, help="also list the last N days")
    ap.add_argument("--goal", type=int, default=int(os.environ.get("PET_GOAL", "20")))
    a = ap.parse_args()

    tz = ZoneInfo(os.environ.get("PET_TZ", "America/Chicago"))
    now = datetime.now(tz)
    login, days, diag = fetch_days(now=now, tz=tz)
    state = compute_state(days, now, login=login, goal=a.goal)

    if a.json:
        print(json.dumps(state, separators=(",", ":")))
        return

    print(f"{login}  level {state['level']}  xp {state['xp']} ({state['xp_into']}/{state['xp_next']} into level)")
    print(f"mood {state['mood']}/10 {state['mood_label']}   streak {state['streak']} days"
          f"{'  AT RISK' if state['streak_at_risk'] else ''}")
    t = state["today"]
    print(f"today {t['total']} contributions = {t['commits']} commits, {t['prs']} PRs, {t['issues']} issues, "
          f"{t['reviews']} reviews, {t['other']} uncategorised -> {t['xp']} xp   goal {state['goal']} "
          f"{'hit' if state['goal_hit'] else 'not yet'}{'   BIG DAY' if state['big_day'] else ''}")
    print(f"source: {diag['calendar_days']} calendar days, detailed from {diag['window_start']}, "
          f"{diag['pages']} API page(s), {diag['restricted_year']} private contributions this year visible only as "
          f"calendar totals"
          + (f", commit lists truncated for {diag['commit_repos_truncated']}" if diag["commit_repos_truncated"] else ""))
    if a.days:
        print()
        print("date        total  cm  pr  is  rv  other   xp")
        for d in days[-a.days:]:
            print(f"{d.date}  {d.total:5d} {d.commits:3d} {d.prs:3d} {d.issues:3d} {d.reviews:3d} {d.other:6d} "
                  f"{day_xp(d):4d}")
    print()
    print(json.dumps(state, separators=(",", ":")))


if __name__ == "__main__":
    main()
