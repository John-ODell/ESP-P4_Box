"""The to-do list (#89): kept by the bridge, added to by John and AI sessions, shown and ticked on the box.

An item is {"id": 3, "text": "Flash the box", "done": false, "by": "claude", "ref": "#85", "ts": "...",
"done_ts": null, "done_by": null, "flag": null}. `ref` names an issue or PR ("#85" or "owner/repo#85"), which
todo_refs.py follows on GitHub (#91): it ticks the item when the work is done, and sets `flag` on an item ticked by
hand while GitHub disagrees: "open" (still open) or "dropped" (closed without being done). `gh` is GitHub's state when
the bridge last looked; it is kept in the file, not sent.

    pet/todo        bridge -> box, retained: {"v": 1, "open": 4, "count": 9,
                    "items": [{id, text, done, by, ref, flag?}]}, `flag` ("open" | "dropped") only when set
                    open items first (oldest first), then the most recently done, at most SHOWN and at most
                    BUDGET bytes (the box drops an MQTT message bigger than its 4 KB buffer)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .q20 import clean

TOPIC_TODO = "pet/todo"
TOPIC_TICK = "pet/todo/tick"
MAX_TEXT = 80
MAX_BY = 32
MAX_ITEMS = 50    # kept; past this the oldest done items go, and an add is refused if all 50 are open
SHOWN = 12        # sent to the box: about 2 KB
BUDGET = 3500     # bytes of pet/todo at most: the box's buffer is 4096, topic and header included
# "#85", "85", "PR 85", or "owner/repo#85" with GitHub's own limits on the names (39 and 100 characters)
REF = re.compile(r"^(?:(?:PR|issue)\s*)?(?:([A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100})#|#?)(\d{1,7})$",
                 re.IGNORECASE | re.ASCII)
FLAGS = ("open", "dropped")            # why a hand tick is flagged
GH_STATES = ("open", "done", "dropped")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
EPOCH = datetime.min.replace(tzinfo=timezone.utc)
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
    """One line of printable ASCII (no control characters, which the box cannot draw and JSON sends as 6-byte
    escapes), cut at a word to fit."""
    text = clean(CONTROL.sub(" ", raw), limit) if isinstance(raw, str) else ""
    if not text:
        raise TodoError(f"{what} must be some text")
    return text


def _when(item: dict) -> datetime:
    """When an item was ticked, for ordering: the stored time has its UTC offset, so the hour the clocks go back
    sorts right. Anything unreadable sorts as oldest."""
    try:
        when = datetime.fromisoformat(item["done_ts"])
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return EPOCH


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Todo:
    def __init__(self, publish: Publish, now: Callable[[], datetime], store: Path | None = None) -> None:
        self._publish = publish
        self._now = now
        self._store = store
        self._items: list[dict] = []
        self._next = 1
        self._lock = threading.Lock()
        self.on_hand_tick: Callable[[int], None] | None = None   # set by todo_refs.RefChecker
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
        # A hand-edited file must never stop the bridge: every field is checked, and what does not fit is dropped.
        for it in items:
            try:
                if not _is_int(it["id"]) or it["id"] < 1 or any(k["id"] == it["id"] for k in self._items):
                    raise ValueError("id")
                try:
                    ref = parse_ref(it.get("ref"))
                except TodoError:
                    log.warning("to-do list in %s: item %d's ref %r not kept", self._store, it["id"], it.get("ref"))
                    ref = None
                done = it.get("done") is True
                self._items.append({"id": it["id"], "text": _text(it["text"], MAX_TEXT, "text"), "done": done,
                                    "by": _text(it.get("by") or "?", MAX_BY, "by"), "ref": ref,
                                    "ts": str(it.get("ts", "")),
                                    "done_ts": it.get("done_ts") if done and isinstance(it.get("done_ts"), str) else None,
                                    "done_by": it.get("done_by") if done and isinstance(it.get("done_by"), str) else None,
                                    "flag": it.get("flag") if done and ref and it.get("flag") in FLAGS else None,
                                    "gh": it.get("gh") if ref and it.get("gh") in GH_STATES else None})
            except (KeyError, TypeError, ValueError, AttributeError):
                log.warning("to-do list in %s: an entry not kept: %r", self._store, it)
        while len(self._items) > MAX_ITEMS:   # the oldest done item first, else the oldest item
            done = [it for it in self._items if it["done"]]
            self._items.remove(min(done, key=_when) if done else self._items[0])
        stored = raw.get("next")
        self._next = max([stored if _is_int(stored) else 1] + [it["id"] + 1 for it in self._items])
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
        done = sorted((it for it in self._items if it["done"]), key=_when, reverse=True)
        return open_ + done

    def _summary(self, cap: int | None) -> dict:
        items = self._ordered()[:cap]
        return {"v": 1, "open": sum(not it["done"] for it in self._items), "count": len(self._items),
                "items": [{k: it[k] for k in ("id", "text", "done", "by", "ref", "flag") if k != "flag" or it[k]}
                          for it in items]}

    def _for_box(self) -> str:
        """pet/todo: SHOWN items, fewer if that is over BUDGET bytes (only with long refs and texts full of quotes)."""
        summary = self._summary(SHOWN)
        while True:
            payload = json.dumps(summary, separators=(",", ":"))
            if len(payload) <= BUDGET or not summary["items"]:
                return payload
            summary["items"].pop()

    def _changed(self) -> None:
        """Under the lock: save, then publish what the box shows."""
        self._save()
        self._publish(TOPIC_TODO, self._for_box(), True)

    def _find(self, item_id) -> dict:
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise TodoError("id must be a number")
        for it in self._items:
            if it["id"] == item_id:
                return it
        raise TodoError(f"no item {item_id}")

    def add(self, text, by="john", ref=None) -> dict:
        with self._lock:
            return self._add(text, by, ref)

    def add_once(self, text, by, ref, key: Callable[[str], object] = lambda ref: ref) -> tuple[dict, bool]:
        """Add, unless an open item has the same ref (compared by `key`, so "#85" can match "owner/repo#85").
        Returns the item, new or already there, and whether it was added. A ref `key` gives None for matches nothing."""
        ref = parse_ref(ref)
        want = key(ref) if ref else None
        with self._lock:
            for it in self._items:
                if want is not None and not it["done"] and it["ref"] and key(it["ref"]) == want:
                    return dict(it), False
            return self._add(text, by, ref), True

    def _add(self, text, by, ref) -> dict:
        """add, with the lock held."""
        item = {"id": self._next, "text": _text(text, MAX_TEXT, "text"), "done": False,
                "by": _text(by, MAX_BY, "by"), "ref": parse_ref(ref), "ts": self._now().isoformat(),
                "done_ts": None, "done_by": None, "flag": None, "gh": None}
        if len(self._items) >= MAX_ITEMS:
            done = [it for it in self._items if it["done"]]
            if not done:
                raise TodoError(f"the list holds {MAX_ITEMS} open items; tick or remove some first")
            self._items.remove(min(done, key=_when))
        self._next += 1
        self._items.append(item)
        self._changed()
        log.info("to-do %d added by %s: %s%s", item["id"], item["by"], item["text"],
                 f" ({item['ref']})" if item["ref"] else "")
        return dict(item)

    def set_done(self, item_id, done: bool, by="john") -> dict:
        by = _text(by, MAX_BY, "by")
        with self._lock:
            item = self._find(item_id)
            hand_tick = False
            if item["done"] != done:
                item.update(done=done, done_ts=self._now().isoformat() if done else None,
                            done_by=by if done else None, flag=None)
                self._changed()
                log.info("to-do %d %s by %s", item["id"], "done" if done else "open again", by)
                hand_tick = done and item["ref"] is not None
            result = dict(item)
        if hand_tick and self.on_hand_tick:
            self.on_hand_tick(item_id)   # outside the lock: the check takes the lock again to apply what it finds
        return result

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

    def ref_items(self, ids=None) -> list[tuple[int, str]]:
        """(id, ref) of the items with a ref, or only those in `ids`."""
        with self._lock:
            return [(it["id"], it["ref"]) for it in self._items if it["ref"] and (ids is None or it["id"] in ids)]

    def follow_github(self, states: dict) -> int:
        """Apply GitHub's word, {id: "done" | "open" | "dropped" | None}; publishes once when anything shown changed
        and returns how many items did. The bridge ticks or unticks only when GitHub's state moved since it last
        looked (`gh`), so a hand untick of an item whose issue is closed stays unticked."""
        changed, seen = 0, False
        with self._lock:
            for it in self._items:
                state = states.get(it["id"])
                if state is None or not it["ref"]:
                    continue   # not asked about, or GitHub had no answer: leave it as it is
                moved = state != it["gh"]
                new = {"gh": state}
                if state == "done":
                    new["flag"] = None
                    if not it["done"] and moved:
                        new.update(done=True, done_ts=self._now().isoformat(), done_by="github")
                elif it["done"] and it["done_by"] == "github":   # GitHub ticked it and has since reopened it
                    new.update(done=False, done_ts=None, done_by=None, flag=None)
                else:
                    new["flag"] = state if it["done"] else None   # "open" or "dropped" on a hand tick
                seen = seen or moved
                if any(it[k] != v for k, v in new.items() if k != "gh"):
                    what = (f"flagged: {new['flag']}" if new.get("flag") else
                            "ticked" if new.get("done") else "unticked" if new.get("done") is False else
                            "flag cleared")
                    log.info("to-do %d (%s): GitHub says %s, %s", it["id"], it["ref"], state, what)
                    changed += 1
                it.update(new)
            if changed:
                self._changed()
            elif seen:
                self._save()   # only `gh` moved: nothing to show, but remember it
        return changed

    def summary(self) -> dict:
        """GET /todo: everything, uncapped."""
        with self._lock:
            return self._summary(None)

    def publish(self) -> None:
        """On start: whatever was kept (or the empty list), so the box hears the truth."""
        with self._lock:
            self._changed()
