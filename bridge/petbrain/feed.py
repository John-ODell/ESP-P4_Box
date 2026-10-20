"""The updater feed (#92): the latest things that happened on GitHub, newest first, for the box's list screen.

One GraphQL call per poll: your recent pull requests and issues (with titles, private repos included) and your commit
counts per repo per day. Each PR or issue is one line, in its latest state; each repo's commits one line per day,
sorted as of the end of that day (today's: as of now, so they stay near the top):

    pr_open, pr_merged, pr_closed         "#101 Follow to-do refs on GitHub"   (closed: without merging)
    issue_open, issue_closed,             "#91 Follow to-do refs"              (dropped: closed as not planned or
    issue_dropped                                                                duplicate)
    commits                               "3 commits"                          (one line per repo per day)

    pet/feed   bridge -> box, retained: {"v": 1, "items": [{"t": 1760922613, "k": "pr_merged", "r": "ESP-P4_Box",
               "x": "#101 Follow to-do refs on GitHub"}]}

`t` is Unix time (for commits, the start of that day); `r` the repo without its owner. At most SHOWN items and BUDGET
bytes, so it always fits the box's 4 KB MQTT buffer. It is published only when it changed. `fetch` is injected, so the
tests run on canned answers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from .github_source import find_token, graphql
from .q20 import clean
from .todo import CONTROL

TOPIC_FEED = "pet/feed"
SHOWN = 10
BUDGET = 3000
MAX_TEXT = 70
MAX_REPO = 32
COMMIT_DAYS = 3   # commit counts from this many days back
log = logging.getLogger("bridge.feed")

QUERY = """
query($since: DateTime!) { viewer {
  pullRequests(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { number title createdAt mergedAt closedAt state repository { name } } }
  issues(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { number title createdAt closedAt state stateReason repository { name } } }
  contributionsCollection(from: $since) { commitContributionsByRepository(maxRepositories: 10) {
    repository { name } contributions(first: 10) { nodes { occurredAt commitCount } } } }
} }
"""


def _when(iso) -> int | None:
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _line(text: str, limit: int) -> str:
    return clean(CONTROL.sub(" ", text), limit)


def build(viewer: dict, now: int) -> list[dict]:
    """The feed lines from the query's `viewer`, newest first, all of them (the caller cuts). `now` is Unix time."""
    out = []   # (sort time, line)

    def add(t, kind, repo, text, sort=None):
        t = _when(t)
        if t is not None:
            out.append((t if sort is None else sort(t),
                        {"t": t, "k": kind, "r": _line(repo or "?", MAX_REPO), "x": _line(text, MAX_TEXT)}))

    for pr in ((viewer.get("pullRequests") or {}).get("nodes") or []):
        if not isinstance(pr, dict):
            continue
        repo, text = (pr.get("repository") or {}).get("name"), f"#{pr.get('number')} {pr.get('title') or ''}"
        if pr.get("mergedAt"):
            add(pr["mergedAt"], "pr_merged", repo, text)
        elif pr.get("closedAt"):
            add(pr["closedAt"], "pr_closed", repo, text)
        else:
            add(pr.get("createdAt"), "pr_open", repo, text)
    for issue in ((viewer.get("issues") or {}).get("nodes") or []):
        if not isinstance(issue, dict):
            continue
        repo, text = (issue.get("repository") or {}).get("name"), f"#{issue.get('number')} {issue.get('title') or ''}"
        if issue.get("closedAt"):
            dropped = issue.get("stateReason") in ("NOT_PLANNED", "DUPLICATE")
            add(issue["closedAt"], "issue_dropped" if dropped else "issue_closed", repo, text)
        else:
            add(issue.get("createdAt"), "issue_open", repo, text)
    by_repo = ((viewer.get("contributionsCollection") or {}).get("commitContributionsByRepository") or [])
    for entry in by_repo:
        if not isinstance(entry, dict):
            continue
        repo = (entry.get("repository") or {}).get("name")
        for day in ((entry.get("contributions") or {}).get("nodes") or []):
            n = day.get("commitCount") if isinstance(day, dict) else None
            if isinstance(n, int) and n > 0:
                add(day.get("occurredAt"), "commits", repo, f"{n} commit{'s' if n != 1 else ''}",
                    sort=lambda t: min(now, t + 86399))   # the day's count, as of its end (or now)
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [line for _, line in out]


def payload(items: list[dict]) -> str:
    """pet/feed: the newest SHOWN lines, fewer if that is over BUDGET bytes."""
    items = items[:SHOWN]
    while True:
        text = json.dumps({"v": 1, "items": items}, separators=(",", ":"))
        if len(text) <= BUDGET or not items:
            return text
        items = items[:-1]


def fetch_github(since: datetime, token: str | None = None, post=graphql) -> dict:
    return post(token or find_token(), QUERY, {"since": since.isoformat()})["viewer"]


class Feed:
    def __init__(self, publish: Callable[[str, str, bool], None], fetch: Callable[[datetime], dict] = fetch_github,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._publish = publish
        self._fetch = fetch
        self._now = now
        self._last: str | None = None

    def update(self) -> bool:
        """Fetch, and publish when the feed changed. Raises on a GitHub or network failure, which the poll loop logs;
        the box keeps the last feed, which is retained."""
        now = self._now()
        text = payload(build(self._fetch(now - timedelta(days=COMMIT_DAYS)), int(now.timestamp())))
        if text == self._last:
            return False
        self._publish(TOPIC_FEED, text, True)
        self._last = text
        log.info("published %s (%d bytes, %d lines)", TOPIC_FEED, len(text), len(json.loads(text)["items"]))
        return True
