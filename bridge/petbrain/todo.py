"""The to-do list (#89): kept by the bridge, added to by John and AI sessions, shown and ticked on the box.

An item is {"id": 3, "text": "Flash the box", "done": false, "by": "claude", "ref": "#85", "ts": "...",
"done_ts": null, "done_by": null}. `ref` names an issue or PR ("#85" or "owner/repo#85") for #91 to follow.

    pet/todo        bridge -> box, retained: {"v": 1, "open": 4, "count": 9, "items": [{id, text, done, by, ref}]}
                    open items first (oldest first), then the most recently done, at most SHOWN
    pet/todo/tick   box -> bridge, never retained: {"id": 3, "done": true}
    POST /todo      {"op": "add", "text": ..., "by"?: ..., "ref"?: ...} or {"op": "done" | "undo" | "remove", "id": 3}
                    -> 200 {"ok": true, "item": {...}} or 400 {"ok": false, "error": "..."}
    GET  /todo      every item, the same shape as pet/todo without the cap

With a `store` path the list is written to that file before every publish and read back on start, as the pending
sessions are. The publish function is injected, so the tests run without a broker.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from .q20 import clean

TOPIC_TODO = "pet/todo"
TOPIC_TICK = "pet/todo/tick"
MAX_TEXT = 80
MAX_BY = 32
MAX_ITEMS = 50    # kept; past this the oldest done items go, and an add is refused if all 50 are open
SHOWN = 12        # sent to the box: about 2 KB
REF = re.compile(r"^(?:(?:PR|issue)\s*)?(?:([\w.-]+/[\w.-]+))?#?(\d{1,7})$", re.IGNORECASE)
log = logging.getLogger("bridge.todo")

Publish = Callable[[str, str, bool], None]  # topic, payload, retain


class TodoError(ValueError):
    """A request the list cannot take; the message goes back to whoever asked."""


def parse_ref(raw) -> str | None:
    """"#85", "85", "PR #85" or "owner/repo#85" -> "#85" or "owner/repo#85"; None or "" -> None."""
    if raw is None or raw == "":
        return None
    m = REF.match(str(raw).strip())
    if not m or int(m.group(2)) == 0:
        raise TodoError(f"ref must look like #85 or owner/repo#85, not {str(raw)[:40]!r}")
    return f"{m.group(1) or ''}#{int(m.group(2))}"


def _text(raw, limit: int, what: str) -> str:
    if not isinstance(raw, str) or not clean(raw, limit):
        raise TodoError(f"{what} must be some text")
    return clean(raw, limit)


class Todo:
    def __init__(self, publish: Publish, now: Callable[[], datetime], store: Path | None = None) -> None:
        self._publish = publish
        self._now = now
        self._store = store
        self._items: list[dict] = []
        self._next = 1
        self._lock = threading.Lock()
        if store is not None:
            self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._store.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("to-do list in %s not readable (%s); starting empty", self._store, exc)
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            log.warning('to-do list in %s is not {"items": [...]}; starting empty', self._store)
            return
        for it in items:
            try:
                self._items.append({"id": int(it["id"]), "text": _text(it["text"], MAX_TEXT, "text"),
                                    "done": bool(it.get("done")), "by": _text(it.get("by") or "?", MAX_BY, "by"),
                                    "ref": parse_ref(it.get("ref")), "ts": str(it.get("ts", "")),
                                    "done_ts": it.get("done_ts"), "done_by": it.get("done_by")})
            except (KeyError, TypeError, ValueError):
                log.warning("to-do list in %s: an entry not kept: %r", self._store, it)
        self._next = max([int(raw.get("next", 1))] + [it["id"] + 1 for it in self._items])
        log.info("to-do list restored from %s: %d items, %d open", self._store, len(self._items),
                 sum(not it["done"] for it in self._items))

    def _save(self) -> None:
        if self._store is None:
            return
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": 1, "next": self._next, "items": self._items}))
            tmp.replace(self._store)
        except OSError as exc:
            log.warning("to-do list not saved to %s: %s", self._store, exc)

    def _ordered(self) -> list[dict]:
        """Open items oldest first, then done items most recently done first."""
        open_ = [it for it in self._items if not it["done"]]
        done = sorted((it for it in self._items if it["done"]), key=lambda it: it["done_ts"] or "", reverse=True)
        return open_ + done

    def _summary(self, cap: int | None) -> dict:
        items = self._ordered()[:cap]
        return {"v": 1, "open": sum(not it["done"] for it in self._items), "count": len(self._items),
                "items": [{k: it[k] for k in ("id", "text", "done", "by", "ref")} for it in items]}

    def _changed(self) -> None:
        """Under the lock: save, then publish what the box shows."""
        self._save()
        self._publish(TOPIC_TODO, json.dumps(self._summary(SHOWN), separators=(",", ":")), True)

    def _find(self, item_id) -> dict:
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise TodoError("id must be a number")
        for it in self._items:
            if it["id"] == item_id:
                return it
        raise TodoError(f"no item {item_id}")

    def add(self, text, by="john", ref=None) -> dict:
        with self._lock:
            item = {"id": self._next, "text": _text(text, MAX_TEXT, "text"), "done": False,
                    "by": _text(by, MAX_BY, "by"), "ref": parse_ref(ref), "ts": self._now().isoformat(),
                    "done_ts": None, "done_by": None}
            if len(self._items) >= MAX_ITEMS:
                done = [it for it in self._items if it["done"]]
                if not done:
                    raise TodoError(f"the list holds {MAX_ITEMS} open items; tick or remove some first")
                self._items.remove(min(done, key=lambda it: it["done_ts"] or ""))
            self._next += 1
            self._items.append(item)
            self._changed()
            log.info("to-do %d added by %s: %s%s", item["id"], item["by"], item["text"],
                     f" ({item['ref']})" if item["ref"] else "")
            return dict(item)

    def set_done(self, item_id, done: bool, by="john") -> dict:
        with self._lock:
            item = self._find(item_id)
            if item["done"] != done:
                item.update(done=done, done_ts=self._now().isoformat() if done else None,
                            done_by=_text(by, MAX_BY, "by") if done else None)
                self._changed()
                log.info("to-do %d %s by %s", item["id"], "done" if done else "open again", by)
            return dict(item)

    def remove(self, item_id) -> dict:
        with self._lock:
            item = self._find(item_id)
            self._items.remove(item)
            self._changed()
            log.info("to-do %d removed", item["id"])
            return item

    def post(self, body) -> dict:
        """POST /todo."""
        try:
            req = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise TodoError(f"body is not JSON: {exc}") from None
        if not isinstance(req, dict):
            raise TodoError("body must be a JSON object")
        op = req.get("op")
        if op == "add":
            item = self.add(req.get("text"), req.get("by") or "john", req.get("ref"))
        elif op in ("done", "undo"):
            item = self.set_done(req.get("id"), op == "done", req.get("by") or "john")
        elif op == "remove":
            item = self.remove(req.get("id"))
        else:
            raise TodoError('op must be "add", "done", "undo" or "remove"')
        return {"ok": True, "item": item}

    def tick(self, payload) -> None:
        """pet/todo/tick from the box. Anything malformed is logged and dropped."""
        try:
            msg = json.loads(payload)
            if not isinstance(msg, dict) or not isinstance(msg.get("done"), bool):
                raise TodoError('not {"id": n, "done": true|false}')
            self.set_done(msg.get("id"), msg["done"], "box")
        except (ValueError, TypeError) as exc:
            log.warning("ignored pet/todo/tick %r: %s", payload[:120], exc)

    def summary(self) -> dict:
        """GET /todo: everything, uncapped."""
        with self._lock:
            return self._summary(None)

    def publish(self) -> None:
        """On start: whatever was kept (or the empty list), so the box hears the truth."""
        with self._lock:
            self._changed()
