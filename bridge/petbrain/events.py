"""Session events from AI tools: what is a valid event, and what is still waiting.

Any tool posts one small JSON object to the bridge's local HTTP endpoint (see notify_api.py):

    {"v": 1, "source": "claude", "kind": "needs_input", "session": "website", "title": "Allow the edit?"}

`source` names the AI (claude, codex, local, anything short), `session` is the name shown on the box, and `kind`
says why it matters: needs_input, done and error show up on the box; cleared takes the session's entry away again
(sent once the session got its answer). No I/O here, so every rule is unit tested in tests/test_events.py.
"""
from __future__ import annotations

import json
from datetime import datetime

KINDS = ("needs_input", "done", "error", "cleared")
MAX_SOURCE = 32
MAX_SESSION = 64
MAX_TITLE = 80
MAX_DETAIL = 200
MAX_BODY = 4096          # bytes the endpoint accepts in one POST
MAX_PENDING = 32         # entries kept; the oldest fall off beyond this
SHOWN_PENDING = 8        # entries listed in pet/notify; `count` still says how many there are


class EventError(ValueError):
    """The posted event is not usable; str(exc) is safe to send back to the caller."""


def _text(obj: dict, key: str, limit: int, *, required: bool) -> str:
    value = obj.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise EventError(f"{key} must be a string")
    value = "".join(ch for ch in value if ch.isprintable() or ch.isspace())
    value = " ".join(value.split())
    if required and not value:
        raise EventError(f"{key} is required")
    return value[:limit]


def _key(source: str, session: str) -> tuple[str, str]:
    return " ".join(source.split()).lower()[:MAX_SOURCE], " ".join(session.split())[:MAX_SESSION]


def parse_event(raw, now: datetime) -> dict:
    """Validate one posted event (bytes, str or dict) and return it in canonical form."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EventError("body is not UTF-8") from exc
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise EventError("body is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise EventError("body must be a JSON object")
    version = raw.get("v", 1)
    if isinstance(version, bool) or version != 1:
        raise EventError("v must be 1")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise EventError("kind must be one of " + ", ".join(KINDS))
    ts = raw.get("ts")
    if ts is None or ts == "":
        ts = now.isoformat(timespec="seconds")
    else:
        if not isinstance(ts, str):
            raise EventError("ts must be an ISO 8601 string")
        try:
            datetime.fromisoformat(ts)
        except ValueError as exc:
            raise EventError("ts must be an ISO 8601 string") from exc
    return {
        "v": 1,
        "ts": ts,
        "source": _text(raw, "source", MAX_SOURCE, required=True).lower(),
        "kind": kind,
        "session": _text(raw, "session", MAX_SESSION, required=True),
        "title": _text(raw, "title", MAX_TITLE, required=False),
        "detail": _text(raw, "detail", MAX_DETAIL, required=False),
    }


class Pending:
    """What is still waiting for John: newest first, one entry per (source, session)."""

    def __init__(self) -> None:
        self._items: list[dict] = []

    def __len__(self) -> int:
        return len(self._items)

    def _drop(self, source: str, session: str) -> bool:
        before = len(self._items)
        self._items = [e for e in self._items if (e["source"], e["session"]) != (source, session)]
        return len(self._items) != before

    def apply(self, event: dict) -> bool:
        """Fold one parsed event in. Returns True when the list changed."""
        if event["kind"] == "cleared":
            return self._drop(event["source"], event["session"])
        self._drop(event["source"], event["session"])
        self._items.insert(0, {k: event[k] for k in ("source", "session", "kind", "title", "ts")})
        del self._items[MAX_PENDING:]
        return True

    def remove(self, source: str, session: str) -> bool:
        """The box acknowledged this session (a tap)."""
        return self._drop(*_key(source, session))

    def clear(self) -> bool:
        changed = bool(self._items)
        self._items = []
        return changed

    def dump(self) -> list[dict]:
        """Every entry as a plain dict, newest first: what goes to bridge/var/pending.json."""
        return [dict(e) for e in self._items]

    def restore(self, entries) -> int:
        """Replace the list with saved entries that still look like ours; returns how many were kept."""
        kept: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            fields = {k: e.get(k) for k in ("source", "session", "kind", "title", "ts")}
            if not all(isinstance(v, str) for v in fields.values()):
                continue
            if fields["kind"] not in KINDS or fields["kind"] == "cleared":
                continue
            if not fields["source"] or not fields["session"]:
                continue
            key = (fields["source"], fields["session"])
            if key in seen:
                continue
            seen.add(key)
            kept.append(fields)
        self._items = kept[:MAX_PENDING]
        return len(self._items)

    def summary(self, now: datetime) -> dict:
        """The retained pet/notify message: small, newest first, self-describing."""
        return {
            "v": 1,
            "ts": now.isoformat(timespec="seconds"),
            "count": len(self._items),
            "pending": [dict(e) for e in self._items[:SHOWN_PENDING]],
        }
