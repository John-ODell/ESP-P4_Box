"""The local HTTP endpoint for session events, and the glue that turns an event into MQTT messages.

    POST /event     JSON body (see events.parse_event) -> 200 {"ok": true, "pending": n}
                    or 400 {"ok": false, "error": "..."}; 404 for any other path, 413 over MAX_BODY bytes,
                    403 for a request that carries a browser Origin header
    GET  /pending   the current pending list, the same object that is retained on pet/notify

Notifier keeps the Pending list, publishes each accepted event on pet/event (not retained) and the pending list on
pet/notify (retained), and drops entries when the box acknowledges on pet/ack. The publish function is injected, so
the tests run without a broker.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .events import MAX_BODY, EventError, Pending, parse_event

TOPIC_EVENT = "pet/event"
TOPIC_NOTIFY = "pet/notify"
TOPIC_ACK = "pet/ack"
log = logging.getLogger("bridge.notify")

Publish = Callable[[str, str, bool], None]  # topic, payload, retain


class Notifier:
    def __init__(self, publish: Publish, now: Callable[[], datetime]) -> None:
        self._publish = publish
        self._now = now
        self._pending = Pending()
        self._lock = threading.Lock()

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
        self._publish(TOPIC_NOTIFY, json.dumps(summary, separators=(",", ":")), True)
        return summary


def make_server(host: str, port: int, notifier: Notifier) -> ThreadingHTTPServer:
    """Bind the endpoint (port 0 picks a free port, for tests). Serve it with serve_in_thread()."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "pet-bridge/1"

        def _send(self, status: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.headers.get("Origin"):  # browsers send Origin on cross-site POSTs; curl, urllib and hooks do not
                log.warning("refused browser request from %s (Origin %s)", self.client_address[0],
                            self.headers.get("Origin"))
                self._send(403, {"ok": False, "error": "browser requests are not accepted"})
                return
            if self.path != "/event":
                self._send(404, {"ok": False, "error": "unknown path, POST /event"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send(411, {"ok": False, "error": "Content-Length required"})
                return
            if length < 0 or length > MAX_BODY:
                log.warning("rejected event from %s: body of %s bytes", self.client_address[0], length)
                self._send(413, {"ok": False, "error": f"body must be 0 to {MAX_BODY} bytes"})
                return
            body = self.rfile.read(length)
            try:
                self._send(200, notifier.post(body))
            except EventError as exc:
                log.warning("rejected event from %s: %s", self.client_address[0], exc)
                self._send(400, {"ok": False, "error": str(exc)})
            except Exception:  # a broker hiccup must not take the endpoint down
                log.exception("event from %s not handled", self.client_address[0])
                self._send(500, {"ok": False, "error": "bridge error, see its log"})

        def do_GET(self) -> None:
            if self.path == "/pending":
                self._send(200, notifier.pending())
            else:
                self._send(404, {"ok": False, "error": "unknown path, GET /pending"})

        def log_message(self, fmt, *args) -> None:  # the default writes every request to stderr
            log.debug("http %s " + fmt, self.client_address[0], *args)

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="notify-http", daemon=True)
    thread.start()
    return thread
