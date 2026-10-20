"""Read-only GitHub contribution source for the pet brain.

Builds a list of `Day` records from the viewer's `contributionsCollection`:

- the contribution calendar for the last year (daily totals, feeds XP history and streak),
- a detailed window (default 90 days) where commits, PRs, issues, and reviews are
  bucketed per local day from their `occurredAt` timestamps.

Calendar days outside the window, and any calendar count the breakdown does not
explain inside it, land in `Day.other` (weight 1, capped like commits).

Known limit: GitHub only breaks down contributions the token can see. Private
repo activity that the API reports as "restricted" shows up in the calendar
totals but not in the per-category lists, so it is scored as `other`.

Token: `GITHUB_TOKEN` in the environment or a `.env` next to `bridge/`, else
`gh auth token` as a convenience for local runs. The token never leaves this
process and is never logged.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .brain import Day

API = "https://api.github.com/graphql"

CALENDAR_QUERY = """
query { viewer { login contributionsCollection {
  restrictedContributionsCount
  contributionCalendar { weeks { contributionDays { date contributionCount } } } } } }
"""

WINDOW_QUERY = """
query($from: DateTime!, $to: DateTime!) {
  viewer { contributionsCollection(from: $from, to: $to) {
    commitContributionsByRepository(maxRepositories: 100) {
      repository { nameWithOwner }
      contributions(first: 100) { totalCount nodes { occurredAt commitCount } }
    }
    pullRequestContributions(first: 100) { pageInfo { hasNextPage endCursor } nodes { occurredAt } }
    issueContributions(first: 100) { pageInfo { hasNextPage endCursor } nodes { occurredAt } }
    pullRequestReviewContributions(first: 100) { pageInfo { hasNextPage endCursor } nodes { occurredAt } }
  } }
}
"""

# One category at a time, for the rare case a window holds more than 100 of it.
PAGE_QUERY = """
query($from: DateTime!, $to: DateTime!, $after: String) {
  viewer { contributionsCollection(from: $from, to: $to) {
    %s(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { occurredAt } }
  } }
}
"""

CATEGORY_FIELDS = (("pullRequestContributions", "prs"), ("issueContributions", "issues"),
                   ("pullRequestReviewContributions", "reviews"))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def find_token() -> str:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise RuntimeError("no GitHub token: set GITHUB_TOKEN in bridge/.env or log in with `gh auth login`")


def graphql(token: str, query: str, variables: dict | None = None, partial: bool = False) -> dict:
    """The query's data. An error raises, unless `partial` and GitHub still sent data (a batch where one part was
    not found, say): then the data, with that part None."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "esp-p4-pet-bridge",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if "errors" in data and not (partial and isinstance(data.get("data"), dict)):
        raise RuntimeError(f"GitHub GraphQL error: {data['errors'][0].get('message', data['errors'])}")
    return data["data"]


def _local_day(iso: str, tz: ZoneInfo) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).date()


def fetch_days(token: str | None = None, *, now: datetime | None = None, tz: ZoneInfo | None = None,
               window_days: int = 90) -> tuple[str, list[Day], dict]:
    """Returns (login, days sorted by date, diagnostics)."""
    token = token or find_token()
    tz = tz or ZoneInfo(os.environ.get("PET_TZ", "America/Chicago"))
    now = now or datetime.now(tz)

    cal = graphql(token, CALENDAR_QUERY)["viewer"]
    login = cal["login"]
    calendar = {}
    for week in cal["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for d in week["contributionDays"]:
            calendar[date.fromisoformat(d["date"])] = d["contributionCount"]

    start = (now - timedelta(days=window_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    frm = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    to = (now + timedelta(days=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    counts: dict = defaultdict(lambda: {"commits": 0, "prs": 0, "issues": 0, "reviews": 0})
    diag = {"commit_repos_truncated": [], "pages": 1,
            "restricted_year": cal["contributionsCollection"]["restrictedContributionsCount"]}

    coll = graphql(token, WINDOW_QUERY, {"from": frm, "to": to})["viewer"]["contributionsCollection"]
    for repo in coll["commitContributionsByRepository"]:
        c = repo["contributions"]
        # nodes are per repo per day (totalCount counts commits), so 100 nodes only overflow past 100 days
        if len(c["nodes"]) >= 100:
            diag["commit_repos_truncated"].append(repo["repository"]["nameWithOwner"])
        for n in c["nodes"]:
            counts[_local_day(n["occurredAt"], tz)]["commits"] += n["commitCount"]
    for field, cat in CATEGORY_FIELDS:
        block = coll[field]
        while True:
            for n in block["nodes"]:
                counts[_local_day(n["occurredAt"], tz)][cat] += 1
            if not block["pageInfo"]["hasNextPage"]:
                break
            diag["pages"] += 1
            block = graphql(token, PAGE_QUERY % field, {"from": frm, "to": to,
                                                         "after": block["pageInfo"]["endCursor"]})
            block = block["viewer"]["contributionsCollection"][field]

    window_start = start.date()
    days = {}
    all_dates = set(calendar) | set(counts)
    for d in sorted(all_dates):
        day = Day(d)
        if d >= window_start:
            c = counts.get(d, {"commits": 0, "prs": 0, "issues": 0, "reviews": 0})
            day.commits, day.prs, day.issues, day.reviews = c["commits"], c["prs"], c["issues"], c["reviews"]
            explained = day.commits + day.prs + day.issues + day.reviews
            day.other = max(0, calendar.get(d, 0) - explained)
        else:
            day.other = calendar.get(d, 0)
        days[d] = day
    diag["window_start"] = window_start.isoformat()
    diag["calendar_days"] = len(calendar)
    return login, [days[d] for d in sorted(days)], diag
