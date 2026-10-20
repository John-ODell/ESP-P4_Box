"""The contribution radar (#105): the four counts GitHub's profile radar is drawn from, for the box's Radar tab.

For the last 365 days: commits authored, pull requests opened, pull requests reviewed and issues opened, counted with
the REST search API as the github-bullseye skill's bullseye.sh counts them (GraphQL hides private-repo activity even
from the owner; search does not). Search allows 30 calls a minute and the counts move slowly, so the four calls go out
at most once every EVERY seconds, whatever the poll interval.

    pet/radar   bridge -> box, retained: {"v": 1, "since": "2025-10-20", "c": 812, "p": 140, "r": 60, "i": 95}

`c` commits, `p` pull requests, `r` code review, `i` issues. Published only when it changed. `fetch` is injected, so
the tests run on canned answers.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .github_source import find_token, rest

TOPIC_RADAR = "pet/radar"
EVERY = 3600
DAYS = 365
log = logging.getLogger("bridge.radar")

# key -> (search endpoint, query): {u} the login, {d} the first day counted
SEARCHES = {
    "c": ("commits", "author:{u} author-date:>={d}"),
    "p": ("issues", "author:{u} is:pr created:>={d}"),
    "r": ("issues", "reviewed-by:{u} is:pr updated:>={d}"),
    "i": ("issues", "author:{u} is:issue created:>={d}"),
}


def fetch_counts(since: date, token: str | None = None, get=rest) -> dict:
    """{"c": n, "p": n, "r": n, "i": n} since `since`. Raises if any search fails: a partial radar would mislead."""
    token = token or find_token()
    login = get(token, "user")["login"]
    out = {}
    for key, (endpoint, query) in SEARCHES.items():
        q = urllib.parse.quote_plus(query.format(u=login, d=since.isoformat()))
        answer = get(token, f"search/{endpoint}?q={q}&per_page=1")
        n = answer.get("total_count")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise RuntimeError(f"search/{endpoint} gave no count for {key}")
        if answer.get("incomplete_results"):   # GitHub timed out part of the search: the count is too low
            raise RuntimeError(f"search/{endpoint} for {key} came back incomplete")
        out[key] = n
    return out


class Radar:
    def __init__(self, publish: Callable[[str, str, bool], None], fetch: Callable[[date], dict] = fetch_counts,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._publish = publish
        self._fetch = fetch
        self._now = now
        self._last: str | None = None
        self._at: datetime | None = None   # when the counts were last fetched

    def update(self) -> bool:
        """Fetch and publish when EVERY seconds have passed since the last fetch and the counts changed. Raises on a
        GitHub or network failure, which the poll loop logs; the next poll tries again."""
        now = self._now()
        if self._at is not None and 0 <= (now - self._at).total_seconds() < EVERY:   # a clock set back: due now
            return False
        since = (now - timedelta(days=DAYS)).date()
        counts = self._fetch(since)
        self._at = now
        text = json.dumps({"v": 1, "since": since.isoformat(), **{k: counts[k] for k in SEARCHES}},
                          separators=(",", ":"))
        if text == self._last:
            return False
        self._publish(TOPIC_RADAR, text, True)
        self._last = text
        log.info("published %s: %s", TOPIC_RADAR, text)
        return True
