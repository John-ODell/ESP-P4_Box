"""To-do items that follow GitHub (#91). An item with a ref ("#85" or "owner/repo#85") is checked against GitHub
every poll and right after a hand tick:

    issue closed as completed,    the bridge ticks the item itself (done_by "github") and clears any flag
    PR merged
    open                          a hand tick is flagged "open"; an item GitHub ticked is unticked again
    issue closed as not planned   a hand tick is flagged "dropped"; an item GitHub ticked is unticked again
    or duplicate, PR closed
    without merging
    not found, no access          left alone, with one warning in the log

The bridge ticks or unticks only when GitHub's state has moved since it last looked, so an item you untick by hand
stays unticked until the issue or PR changes again.

A plain "#85" means PET_TODO_REPO ("owner/repo"), else the GitHub repo this bridge was cloned from. All the refs go to
GitHub in one GraphQL call. `fetch` is injected, so the tests run on canned answers.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .github_source import find_token, graphql
from .todo import Todo

log = logging.getLogger("bridge.todo")
ROOT = Path(__file__).resolve().parent.parent.parent   # the repo this bridge lives in
REPO = re.compile(r"^([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")
REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")

Key = tuple   # (owner, repo, number)
Fetch = Callable[[list], dict]   # [Key] -> {Key: "done" | "open" | "dropped" | None}


def default_repo(environ=os.environ, root: Path = ROOT) -> tuple[str, str] | None:
    """PET_TODO_REPO, else the origin remote of this checkout; None when neither names a GitHub repo."""
    given = environ.get("PET_TODO_REPO", "").strip()
    if given:
        m = REPO.match(given)
        if m:
            return m.group(1), m.group(2)
        log.warning("PET_TODO_REPO=%r is not owner/repo; plain #n refs are not checked", given)
        return None
    try:
        url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = REMOTE.search(url)
    return (m.group(1), m.group(2)) if m else None


def resolve(ref: str, repo: tuple[str, str] | None) -> Key | None:
    """"#85" -> (owner, repo, 85) with the default repo; "o/r#85" -> ("o", "r", 85)."""
    where, _, number = ref.rpartition("#")
    if where:
        owner, _, name = where.partition("/")
        return owner, name, int(number)
    return (repo[0], repo[1], int(number)) if repo else None


def ref_key(ref: str, repo: tuple[str, str] | None) -> Key | None:
    """resolve, for comparing two refs: GitHub's owner and repo names ignore case, so these do too."""
    key = resolve(ref, repo)
    return None if key is None else (key[0].lower(), key[1].lower(), key[2])


def state_of(node: dict | None) -> str | None:
    if not isinstance(node, dict):
        return None
    kind, state = node.get("__typename"), node.get("state")
    if kind == "Issue":
        if state == "CLOSED" and node.get("stateReason") in ("NOT_PLANNED", "DUPLICATE"):
            return "dropped"
        return {"OPEN": "open", "CLOSED": "done"}.get(state)
    if kind == "PullRequest":
        return {"OPEN": "open", "MERGED": "done", "CLOSED": "dropped"}.get(state)
    return None


def fetch_github(keys: list, token: str | None = None, post=graphql) -> dict:
    """One GraphQL call for every ref. A ref GitHub does not know comes back None; the others still count."""
    if not keys:
        return {}
    parts = []
    for i, (owner, name, number) in enumerate(keys):
        parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
                     f"{{ issueOrPullRequest(number: {int(number)}) {{ __typename "
                     f"... on Issue {{ state stateReason }} ... on PullRequest {{ state }} }} }}")
    data = post(token or find_token(), "query { " + " ".join(parts) + " }", partial=True)
    out = {}
    for i, key in enumerate(keys):
        repo = data.get(f"r{i}") if isinstance(data, dict) else None
        out[key] = state_of(repo.get("issueOrPullRequest") if isinstance(repo, dict) else None)
    return out


class RefChecker:
    def __init__(self, todo: Todo, fetch: Fetch = fetch_github, repo: tuple[str, str] | None = None,
                 spawn: Callable[[Callable[[], None]], None] | None = None) -> None:
        self._todo = todo
        self._fetch = fetch
        self._repo = repo
        self._spawn = spawn or (lambda f: threading.Thread(target=f, daemon=True, name="todo-refs").start())
        self._lock = threading.Lock()   # one check at a time: a hand tick during a poll waits its turn
        self._unknown: set = set()      # refs GitHub had no answer for, warned about once
        todo.on_hand_tick = self.ticked

    def check(self, ids=None) -> int:
        """Ask GitHub about the items with a ref (only `ids`, when given) and apply what it says. Returns how many
        items changed; raises on a GitHub or network failure, which the caller logs."""
        with self._lock:
            keys = {}
            for item_id, ref in self._todo.ref_items(ids):
                key = resolve(ref, self._repo)
                if key is None:
                    continue
                keys.setdefault(key, []).append(item_id)
            if not keys:
                return 0
            states = self._fetch(list(keys))
            for key in keys:
                if states.get(key) is None and key not in self._unknown:
                    log.warning("to-do ref %s/%s#%d: GitHub has no issue or PR there, or this token cannot see it; "
                                "left alone", *key)
                    self._unknown.add(key)
                elif states.get(key) is not None:
                    self._unknown.discard(key)
            results = {item_id: states.get(key) for key, item_ids in keys.items() for item_id in item_ids}
            changed = self._todo.follow_github(results)
            if changed:
                log.info("to-do: GitHub changed %d of %d items with a ref", changed, len(results))
            return changed

    def ticked(self, item_id: int) -> None:
        """A hand tick on an item with a ref: check it now, off the caller's thread, so a flag shows in seconds."""
        def run() -> None:
            try:
                self.check([item_id])
            except Exception as exc:   # GitHub down: the next poll tries again
                log.warning("to-do %d: GitHub check after the tick failed: %s", item_id, exc)
        self._spawn(run)
