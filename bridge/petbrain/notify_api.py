"""The local HTTP endpoint for session events, and the glue that turns an event into MQTT messages.

    POST /event     JSON body (see events.parse_event) -> 200 {"ok": true, "pending": n}
                    or 400 {"ok": false, "error": "..."}; 404 for any other path, 413 over MAX_BODY bytes,
                    403 for a request that carries a browser Origin header
    GET  /pending   the current pending list, the same object that is retained on pet/notify
    POST /todo      a change to the to-do list, GET /todo the whole list (petbrain/todo.py), on a server made with one

A server made with a `token` (the tailnet listener, petbrain/tailnet.py) also answers 403 to a source address
outside `allow` and 401 to a request without `Authorization: Bearer <token>`, before anything else is read.

Notifier keeps the Pending list, publishes each accepted event on pet/event (not retained) and the pending list on
pet/notify (retained), and drops entries when the box acknowledges on pet/ack. With a `store` path it also writes
the list to that file whenever it publishes it and reads it back on start, so a bridge restart does not lose the
sessions that were waiting. The publish function is injected, so the tests run without a broker.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .events import MAX_BODY, EventError, Pending, parse_event
from .todo import Todo, TodoError

TOPIC_EVENT = "pet/event"
TOPIC_NOTIFY = "pet/notify"
TOPIC_ACK = "pet/ack"
log = logging.getLogger("bridge.notify")

Publish = Callable[[str, str, bool], None]  # topic, payload, retain


class Notifier:
    def __init__(self, publish: Publish, now: Callable[[], datetime], store: Path | None = None) -> None:
        self._publish = publish
        self._now = now
        self._pending = Pending()
        self._lock = threading.Lock()
        self._store = store
        if store is not None:
            self._load()

    def _load(self) -> None:
        """What was waiting when the last bridge stopped. Missing: empty. Unreadable or odd: empty, with a warning."""
        try:
            raw = json.loads(self._store.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("pending list in %s not readable (%s); starting empty", self._store, exc)
            return
        entries = raw.get("pending") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            log.warning('pending list in %s is not {"pending": [...]}; starting empty', self._store)
            return
        kept, given = self._pending.restore(entries, self._now())
        if kept < given:
            log.warning("pending list in %s: %d of %d entries not kept (not what the bridge writes)",
                        self._store, given - kept, given)
        if kept:
            log.info("pending list restored from %s: %d waiting", self._store, kept)

    def _save(self) -> None:
        """Called under the lock, right before the publish, so the file is never behind what is on pet/notify."""
        if self._store is None:
            return
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": 1, "pending": self._pending.dump()}))
            tmp.replace(self._store)  # atomic: a crash mid-write leaves the old file, not half a new one
        except OSError as exc:
            log.warning("pending list not saved to %s: %s", self._store, exc)

    def post(self, body) -> dict:
        """Handle one POST /event body. Raises EventError when the event is not usable."""
        event = parse_event(body, self._now())
        with self._lock:
            self._pending.apply(event)
            self._publish(TOPIC_EVENT, json.dumps(event, separators=(",", ":")), False)
            summary = self._publish_pending()
        log.info("event %s %s %r -> %d pending", event["source"], event["kind"], event["session"], summary["count"])
        return {"ok": True, "pending": summary["count"]}

    def ack(self, payload) -> None:
        """pet/ack from the box: {"source": s, "session": name} for one entry, {"all": true} for everything."""
        try:
            obj = json.loads(payload)
            if not isinstance(obj, dict):
                raise ValueError("not a JSON object")
            with self._lock:
                if obj.get("all") is True:
                    changed = self._pending.clear()
                else:
                    changed = self._pending.remove(str(obj.get("source", "")), str(obj.get("session", "")))
                if changed:
                    self._publish_pending()
            log.info("ack %s -> %s", obj, "dropped" if changed else "nothing pending for it")
        except (ValueError, TypeError) as exc:
            log.warning("ignored pet/ack %r: %s", payload, exc)

    def pending(self) -> dict:
        with self._lock:
            return self._pending.summary(self._now())

    def publish_pending(self) -> dict:
        with self._lock:
            return self._publish_pending()

    def _publish_pending(self) -> dict:
        summary = self._pending.summary(self._now())
        self._save()
        self._publish(TOPIC_NOTIFY, json.dumps(summary, separators=(",", ":")), True)
        return summary


def make_server(host: str, port: int, notifier: Notifier, token: str | None = None,
                allow: tuple = (), todo: Todo | None = None) -> ThreadingHTTPServer:
    """Bind the endpoint (port 0 picks a free port, for tests). Serve it with serve_in_thread(). With `token`, every
    request must come from an address in `allow` (ip_network objects) and carry that bearer token. With `todo`, it
    also serves /todo."""
    posts = {"/event": (notifier.post, EventError, "event")}
    if todo is not None:
        posts["/todo"] = (todo.post, TodoError, "to-do change")
    paths = " or ".join(posts)
    expected = f"Bearer {token}".encode() if token else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "pet-bridge/1"
        timeout = 10  # seconds per socket read: a client that connects and says nothing cannot hold a thread forever

        def _admitted(self) -> bool:
            """True on the loopback server; on the tailnet one, only a tailnet address with the right token."""
            if expected is None:
                return True
            try:
                source = ipaddress.ip_address(self.client_address[0].split("%")[0])
            except ValueError:
                source = None
            if source is None or not any(source in net for net in allow):
                log.warning("refused %s %r from %s: not a tailnet address", self.command, self.path[:64],
                            self.client_address[0])
                self._send(403, {"ok": False, "error": "not a tailnet address"})
                return False
            given = self.headers.get("Authorization", "").encode("utf-8", "replace")
            if not hmac.compare_digest(given, expected):
                log.warning("refused %s %r from %s: missing or wrong token", self.command, self.path[:64],
                            self.client_address[0])
                self._send(401, {"ok": False, "error": "missing or wrong token"})
                return False
            return True

        def _send(self, status: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if not self._admitted():
                return
            if self.headers.get("Origin"):  # browsers send Origin on cross-site POSTs; curl, urllib and hooks do not
                log.warning("refused browser request from %s (Origin %r)", self.client_address[0],
                            self.headers.get("Origin"))  # %r: a folded header cannot add a log line
                self._send(403, {"ok": False, "error": "browser requests are not accepted"})
                return
            if self.path not in posts:
                self._send(404, {"ok": False, "error": f"unknown path, POST {paths}"})
                return
            handle, refused, what = posts[self.path]
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send(411, {"ok": False, "error": "Content-Length required"})
                return
            if length < 0 or length > MAX_BODY:
                log.warning("rejected %s from %s: body of %s bytes", what, self.client_address[0], length)
                self._send(413, {"ok": False, "error": f"body must be 0 to {MAX_BODY} bytes"})
                return
            body = self.rfile.read(length)
            try:
                self._send(200, handle(body))
            except refused as exc:
                log.warning("rejected %s from %s: %s", what, self.client_address[0], exc)
                self._send(400, {"ok": False, "error": str(exc)})
            except Exception:  # a broker hiccup must not take the endpoint down
                log.exception("%s from %s not handled", what, self.client_address[0])
                self._send(500, {"ok": False, "error": "bridge error, see its log"})

        def do_GET(self) -> None:
            if not self._admitted():
                return
            if self.path == "/pending":
                self._send(200, notifier.pending())
            elif self.path == "/todo" and todo is not None:
                self._send(200, todo.summary())
            else:
                self._send(404, {"ok": False, "error": "unknown path, GET /pending" + (" or /todo" if todo else "")})

        def log_message(self, fmt, *args) -> None:  # the default writes every request to stderr
            log.debug("http %s " + fmt, self.client_address[0], *args)

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="notify-http", daemon=True)
    thread.start()
    return thread
