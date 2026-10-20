"""The Machines tab (#108): whether the machines the pet depends on are up, and whether this bridge is.

Once a minute, on its own thread, the bridge asks Tailscale (`tailscale status --json`) about the machines in
PET_MACHINES (hostnames, comma separated; by default every peer that is not a phone or tablet and was seen in the last
STALE_DAYS) and publishes:

    pet/machines   bridge -> box, retained: {"v": 1, "at": 1760990000, "up": 1760980000, "poll": 1760989400,
                   "every": 600, "items": [{"n": "wordell", "s": "down", "t": 1760923068}], "err": "..."}

`at` when this was sent (the box says the bridge has gone quiet when it is old), `up` when the bridge started, `poll`
its last good GitHub poll (null before the first) and `every` the poll interval in seconds. Each item: `n` the
hostname, `s` "up", "down" or "unknown" (named in PET_MACHINES but not on the tailnet), `t` when it was last seen
(null while up or never). Machines that are down come first. When Tailscale cannot be asked, `items` is empty and
`err` says why, so the box shows that instead of anything up or down. `status` is injected, so the tests run on
canned answers.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .q20 import clean
from .todo import CONTROL

TOPIC_MACHINES = "pet/machines"
EVERY = 60
SHOWN = 8
MAX_NAME = 32
MOBILE = ("iOS", "android")
STALE_DAYS = 14
APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
log = logging.getLogger("bridge.machines")


def tailscale_status(run=subprocess.run) -> dict:
    cli = shutil.which("tailscale") or APP_CLI
    out = run([cli, "status", "--json"], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout or "failed").strip().splitlines()[0][:80])
    status = json.loads(out.stdout)
    if status.get("BackendState") != "Running":
        raise RuntimeError(f"Tailscale is {status.get('BackendState') or 'not running'}")
    return status


def _when(iso) -> int | None:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(t.timestamp()) if t.year > 2000 else None   # "0001-01-01": never, or connected now


def _name(peer: dict) -> str:
    dns = str(peer.get("DNSName") or "").split(".")[0]
    raw = dns if dns and dns != "localhost" else str(peer.get("HostName") or "")
    return clean(CONTROL.sub(" ", raw), MAX_NAME).lower() if raw else ""


def machines(status: dict, names: list[str] | None = None, now: float | None = None) -> list[dict]:
    """The items for pet/machines from a `tailscale status --json`, down first, then by name."""
    now = time.time() if now is None else now

    def recent(peer: dict) -> bool:
        seen = _when(peer.get("LastSeen"))
        return bool(peer.get("Online")) or (seen is not None and seen >= now - STALE_DAYS * 86400)

    peers = {}
    for peer in (status.get("Peer") or {}).values():
        if isinstance(peer, dict) and _name(peer):
            peers.setdefault(_name(peer), peer)
    wanted = [n.strip().lower() for n in names if n.strip()] if names else \
        [n for n, p in peers.items() if p.get("OS") not in MOBILE and recent(p)]
    out = []
    for name in wanted:
        peer = peers.get(name)
        if peer is None:
            out.append({"n": clean(name, MAX_NAME), "s": "unknown", "t": None})
        else:
            up = bool(peer.get("Online"))
            out.append({"n": name, "s": "up" if up else "down", "t": None if up else _when(peer.get("LastSeen"))})
    out.sort(key=lambda m: ({"down": 0, "unknown": 1, "up": 2}[m["s"]], m["n"]))
    return out[:SHOWN]


class Machines:
    def __init__(self, publish: Callable[[str, str, bool], None], status: Callable[[], dict] = tailscale_status,
                 now: Callable[[], float] = time.time, names: list[str] | None = None, every: int = 600) -> None:
        self._publish = publish
        self._status = status
        self._now = now
        self._names = names
        self._every = every
        self._up = int(now())
        self._poll: int | None = None

    def polled(self) -> None:
        """The poll loop's GitHub cycle worked."""
        self._poll = int(self._now())

    def update(self) -> dict:
        """Ask Tailscale and publish, every time: `at` is the box's sign that the bridge is alive."""
        msg = {"v": 1, "at": int(self._now()), "up": self._up, "poll": self._poll, "every": self._every}
        try:
            msg["items"] = machines(self._status(), self._names, msg["at"])
        except Exception as exc:
            msg["items"], msg["err"] = [], clean(CONTROL.sub(" ", f"Tailscale: {exc}"), 80)
        self._publish(TOPIC_MACHINES, json.dumps(msg, separators=(",", ":")), True)
        return msg

    def start(self) -> None:
        """update() every EVERY seconds on a daemon thread."""
        def run() -> None:
            last = None
            while True:
                try:
                    msg = self.update()
                    summary = msg.get("err") or ", ".join(f"{m['n']} {m['s']}" for m in msg["items"]) or "none"
                    if summary != last:
                        log.info("machines: %s", summary)
                        last = summary
                except Exception as exc:   # the broker gone: try again next minute
                    log.warning("machines not published: %s", exc)
                time.sleep(EVERY)
        threading.Thread(target=run, daemon=True, name="machines").start()
