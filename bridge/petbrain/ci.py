"""The CI tab (#107): the latest checks on each of your recently pushed repos, for the box's list screen.

One GraphQL call per poll: your 12 most recently pushed repos (not archived) and the check suites on the latest commit
of each default branch. A suite that never started (status QUEUED with no run: apps installed on a repo with nothing to
do there, such as a Pages integration without a project) is left out. A commit can carry several suites of one
workflow (a scheduled run, a rerun); only the newest counts. Each workflow that ran or is running is one line:

    pet/ci   bridge -> box, retained: {"v": 1, "items": [{"r": "ESP-P4_Box", "w": "ci", "s": "fail", "t": 1760990000}]}

`r` the repo, `w` the workflow (or the app, for a suite with no workflow: "Cloudflare Workers and Pages"), `s` one of
"fail", "run" (queued with a run, or in progress), "pass" or "other" (skipped, neutral, cancelled, or waiting for an
approval), `t` when the suite last
changed. Failures first, then running, then the rest, newest first within each; at most SHOWN lines and BUDGET bytes.
Published only when it changed. `fetch` is injected, so the tests run on canned answers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Callable

from .github_source import find_token, graphql
from .q20 import clean
from .todo import CONTROL

TOPIC_CI = "pet/ci"
SHOWN = 10
BUDGET = 3000
MAX_NAME = 32
log = logging.getLogger("bridge.ci")

QUERY = """
{ viewer { repositories(first: 12, orderBy: {field: PUSHED_AT, direction: DESC}, ownerAffiliations: OWNER,
                        isArchived: false) {
  nodes { name defaultBranchRef { target { ... on Commit { checkSuites(last: 10) { nodes {
    status conclusion updatedAt app { name } workflowRun { workflow { name } } } } } } } } } } }
"""
FAILED = ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE")   # ACTION_REQUIRED is a run waiting for approval: "other"
ORDER = {"fail": 0, "run": 1, "pass": 2, "other": 3}


def _when(iso) -> int | None:
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _name(text) -> str:
    return clean(CONTROL.sub(" ", text), MAX_NAME) if isinstance(text, str) else ""


def state(suite: dict) -> str | None:
    """"fail", "run", "pass" or "other"; None for a suite that never started."""
    status, conclusion = suite.get("status"), suite.get("conclusion")
    if status == "COMPLETED":
        return "fail" if conclusion in FAILED else "pass" if conclusion == "SUCCESS" else "other"
    if status in ("QUEUED", "REQUESTED", "WAITING", "PENDING") and not suite.get("workflowRun"):
        return None
    return "run"


def build(viewer: dict) -> list[dict]:
    """The CI lines from the query's `viewer`, in the order the box shows them, all of them (payload cuts)."""
    newest = {}   # (repo, name) -> line: GitHub lists suites oldest first, so the last one wins
    for repo in ((viewer.get("repositories") or {}).get("nodes") or []):
        if not isinstance(repo, dict) or not _name(repo.get("name")):
            continue
        target = ((repo.get("defaultBranchRef") or {}).get("target") or {})
        for suite in ((target.get("checkSuites") or {}).get("nodes") or []):
            if not isinstance(suite, dict):
                continue
            s, t = state(suite), _when(suite.get("updatedAt"))
            run = suite.get("workflowRun") or {}
            name = _name((run.get("workflow") or {}).get("name")) or _name((suite.get("app") or {}).get("name"))
            if s is None or t is None or not name:
                continue
            newest[(_name(repo["name"]), name)] = {"r": _name(repo["name"]), "w": name, "s": s, "t": t}
    out = list(newest.values())
    out.sort(key=lambda l: (ORDER[l["s"]], -l["t"]))
    return out


def payload(items: list[dict]) -> str:
    items = items[:SHOWN]
    while True:
        text = json.dumps({"v": 1, "items": items}, separators=(",", ":"))
        if len(text) <= BUDGET or not items:
            return text
        items = items[:-1]


def fetch_github(token: str | None = None, post=graphql) -> dict:
    return post(token or find_token(), QUERY)["viewer"]


class CI:
    def __init__(self, publish: Callable[[str, str, bool], None], fetch: Callable[[], dict] = fetch_github) -> None:
        self._publish = publish
        self._fetch = fetch
        self._last: str | None = None

    def update(self) -> bool:
        """Fetch, and publish when it changed. Raises on a GitHub or network failure, which the poll loop logs; the
        box keeps the last list, which is retained."""
        lines = build(self._fetch())
        text = payload(lines)
        if text == self._last:
            return False
        self._publish(TOPIC_CI, text, True)
        self._last = text
        fails = sum(l["s"] == "fail" for l in lines)
        log.info("published %s (%d lines, %d failing)", TOPIC_CI, len(json.loads(text)["items"]), fails)
        return True
