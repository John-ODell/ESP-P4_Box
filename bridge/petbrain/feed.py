"""The updater feed (#92): the latest things that happened on GitHub, newest first, for the box's list screen.

One GraphQL call per poll: your recent pull requests and issues (with titles, private repos included) and your commit
counts per repo per day. Each PR or issue changed in the last RECENT_DAYS is one line, in its latest state (from its
`state`, so a reopened one is open again); each repo's commits one line per day, sorted as of the end of that day
(today's: as of now, so they stay near the top). Commit days are GitHub's, which need not start at your midnight, and
the counts are GitHub's contributions: commits on default branches, so a squash merge is one:

    pr_open, pr_merged, pr_closed         "#101 Follow to-do refs on GitHub"   (closed: without merging)
    issue_open, issue_closed,             "#91 Follow to-do refs"              (dropped: closed as not planned or
    issue_dropped                                                                duplicate)
    commits                               "3 commits"                          (one line per repo per day)

    pet/feed       bridge -> box, retained: {"v": 1, "items": [{"t": 1760922613, "k": "pr_merged", "r": "ESP-P4_Box",
                   "x": "#101 Follow to-do refs on GitHub", "n": 101}]}
    pet/feed/add   box -> bridge, never retained: {"r": "ESP-P4_Box", "n": 74}, a tap on an open PR or issue (#104)

`t` is Unix time (for commits, the start of that day); `r` the repo without its owner; `n` the PR or issue number. At
most SHOWN items and BUDGET bytes, so it always fits the box's 4 KB MQTT buffer. It is published only when it changed.

A tap on an open PR or issue on the box puts it on the to-do list, titled as on GitHub and with its "owner/repo#n" ref,
so todo_refs.py ticks it when it closes; one already open on the list is not added again. `fetch` is injected, so the
tests run on canned answers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from .github_source import find_token, graphql
from .q20 import clean
from .todo import CONTROL, Todo, TodoError

TOPIC_FEED = "pet/feed"
TOPIC_ADD = "pet/feed/add"
OPEN_KINDS = ("pr_open", "issue_open")   # the lines a tap can put on the to-do list
SENT = ("t", "k", "r", "x", "n")          # what the box gets of each line
SHOWN = 10
BUDGET = 3000
MAX_TEXT = 70
MAX_REPO = 32
COMMIT_DAYS = 3   # commit counts from this many days back
RECENT_DAYS = 7   # PRs and issues whose latest change is older than this are left out
log = logging.getLogger("bridge.feed")

QUERY = """
query($since: DateTime!) { viewer {
  pullRequests(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { number title createdAt mergedAt closedAt state repository { name owner { login } } } }
  issues(first: 15, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { number title createdAt closedAt state stateReason repository { name owner { login } } } }
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
    """The feed lines from the query's `viewer`, newest first, all of them (the caller cuts). `now` is Unix time.
    A PR or issue line also has its number `n`, and, kept here and not sent, its `ref` ("owner/repo#n", when GitHub
    gave the owner) and `title`."""
    out = []   # (sort time, line)

    def add(t, kind, repo, text, sort=None, node=None):
        t = _when(t)
        if t is None or (sort is None and t < now - RECENT_DAYS * 86400):
            return
        line = {"t": t, "k": kind, "r": _line(repo or "?", MAX_REPO), "x": _line(text, MAX_TEXT)}
        if node is not None:
            n, where = node.get("number"), node.get("repository") or {}
            owner = (where.get("owner") or {}).get("login")
            if isinstance(n, int) and n > 0:
                line["n"] = n
                line["ref"] = f"{owner}/{repo}#{n}" if isinstance(owner, str) and isinstance(repo, str) else None
                line["title"] = node.get("title") or text
        out.append((t if sort is None else sort(t), line))

    for pr in ((viewer.get("pullRequests") or {}).get("nodes") or []):
        if not isinstance(pr, dict):
            continue
        repo, text = (pr.get("repository") or {}).get("name"), f"#{pr.get('number')} {pr.get('title') or ''}"
        state = pr.get("state")   # without one, the times say it
        if state == "MERGED" or (state is None and pr.get("mergedAt")):
            add(pr.get("mergedAt"), "pr_merged", repo, text, node=pr)
        elif state == "CLOSED" or (state is None and pr.get("closedAt")):
            add(pr.get("closedAt"), "pr_closed", repo, text, node=pr)
        else:
            add(pr.get("createdAt"), "pr_open", repo, text, node=pr)
    for issue in ((viewer.get("issues") or {}).get("nodes") or []):
        if not isinstance(issue, dict):
            continue
        repo, text = (issue.get("repository") or {}).get("name"), f"#{issue.get('number')} {issue.get('title') or ''}"
        state = issue.get("state")
        if state == "CLOSED" or (state is None and issue.get("closedAt")):
            dropped = issue.get("stateReason") in ("NOT_PLANNED", "DUPLICATE")
            add(issue.get("closedAt"), "issue_dropped" if dropped else "issue_closed", repo, text, node=issue)
        else:
            add(issue.get("createdAt"), "issue_open", repo, text, node=issue)
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
    items = [{k: line[k] for k in SENT if k in line} for line in items[:SHOWN]]
    while True:
        text = json.dumps({"v": 1, "items": items}, separators=(",", ":"))
        if len(text) <= BUDGET or not items:
            return text
        items = items[:-1]


def fetch_github(since: datetime, token: str | None = None, post=graphql) -> dict:
    return post(token or find_token(), QUERY, {"since": since.isoformat()})["viewer"]


class Feed:
    def __init__(self, publish: Callable[[str, str, bool], None], fetch: Callable[[datetime], dict] = fetch_github,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), todo: Todo | None = None,
                 key: Callable[[str], object] = lambda ref: ref) -> None:
        """`todo` is the list a tap adds to; `key` makes two refs comparable ("#85" and "owner/repo#85")."""
        self._publish = publish
        self._fetch = fetch
        self._now = now
        self._todo = todo
        self._key = key
        self._last: str | None = None
        self._lines: list[dict] = []   # the last build, all of it: a tap can name a line the box was sent

    def update(self) -> bool:
        """Fetch, and publish when the feed changed. Raises on a GitHub or network failure, which the poll loop logs;
        the box keeps the last feed, which is retained."""
        now = self._now()
        lines = build(self._fetch(now - timedelta(days=COMMIT_DAYS)), int(now.timestamp()))
        self._lines = lines
        text = payload(lines)
        if text == self._last:
            return False
        self._publish(TOPIC_FEED, text, True)
        self._last = text
        log.info("published %s (%d bytes, %d lines)", TOPIC_FEED, len(text), len(json.loads(text)["items"]))
        return True

    def add(self, payload) -> dict | None:
        """pet/feed/add from the box: put that open PR or issue on the to-do list. Returns the item added, or None
        when it was already open on the list or the message is not understood (logged either way)."""
        try:
            msg = json.loads(payload)
            repo, n = (msg.get("r"), msg.get("n")) if isinstance(msg, dict) else (None, None)
            if not isinstance(repo, str) or not isinstance(n, int) or isinstance(n, bool):
                raise ValueError('not {"r": repo, "n": number}')
            line = next((l for l in self._lines if l["k"] in OPEN_KINDS and l["r"] == repo and l.get("n") == n), None)
            if line is None or not line.get("ref"):
                raise ValueError(f"{repo}#{n} is not an open PR or issue in the feed")
            if self._todo is None:
                raise ValueError("no to-do list on this bridge")
            item, added = self._todo.add_once(line["title"], "box", line["ref"], key=self._key)
        except (ValueError, TypeError, TodoError) as exc:
            log.warning("ignored %s %r: %s", TOPIC_ADD, payload[:120], exc)
            return None
        if not added:
            log.info("%s: %s is already to-do %d", TOPIC_ADD, line["ref"], item["id"])
            self._todo.publish()   # the box waits for a pet/todo with it, to say it is on the list
            return None
        return item
