import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from petbrain.events import EventError, Pending, parse_event, MAX_BODY, MAX_PENDING, MAX_TITLE, SHOWN_PENDING
from petbrain.notify_api import Notifier, make_server, TOPIC_EVENT, TOPIC_NOTIFY

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)


def ev(**kw) -> dict:
    base = {"source": "claude", "kind": "needs_input", "session": "website"}
    base.update(kw)
    return base


class ParseTests(unittest.TestCase):
    def test_minimal_event_is_filled_in(self):
        self.assertEqual(parse_event(json.dumps(ev()), NOW),
                         {"v": 1, "ts": "2026-10-16T09:30:00+00:00", "source": "claude", "kind": "needs_input",
                          "session": "website", "title": "", "detail": ""})

    def test_accepts_bytes_and_dicts(self):
        self.assertEqual(parse_event(json.dumps(ev()).encode(), NOW)["session"], "website")
        self.assertEqual(parse_event(ev(), NOW)["session"], "website")

    def test_keeps_a_valid_ts(self):
        self.assertEqual(parse_event(ev(ts="2026-10-16T08:00:00-05:00"), NOW)["ts"], "2026-10-16T08:00:00-05:00")

    def test_rejects_bad_input(self):
        bad = [b"\xff\xfe", "not json", "[1,2]", ev(source=""), ev(session="  "), {"kind": "done", "session": "x"},
               ev(kind="ping"), ev(kind=None), ev(v=2), ev(v=True), ev(ts="yesterday"), ev(ts=12), ev(title=3)]
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(EventError):
                    parse_event(raw, NOW)

    def test_normalises_text(self):
        e = parse_event(ev(source=" Claude ", session="  my\n  project ", title="x" * 500, detail="a\x00b"), NOW)
        self.assertEqual(e["source"], "claude")
        self.assertEqual(e["session"], "my project")
        self.assertEqual(len(e["title"]), MAX_TITLE)
        self.assertEqual(e["detail"], "ab")

    def test_ignores_unknown_fields(self):
        self.assertNotIn("extra", parse_event(ev(extra=1), NOW))


class PendingTests(unittest.TestCase):
    def pend(self, *events) -> Pending:
        p = Pending()
        for e in events:
            p.apply(parse_event(e, NOW))
        return p

    def test_one_entry_per_session_newest_first(self):
        p = self.pend(ev(session="a", title="first"), ev(session="b"), ev(session="a", kind="done", title="second"))
        s = p.summary(NOW)
        self.assertEqual(s["count"], 2)
        self.assertEqual([(e["session"], e["kind"], e["title"]) for e in s["pending"]],
                         [("a", "done", "second"), ("b", "needs_input", "")])

    def test_same_session_name_under_two_sources_stays_separate(self):
        self.assertEqual(len(self.pend(ev(source="claude"), ev(source="codex"))), 2)

    def test_cleared_removes_and_is_a_no_op_otherwise(self):
        p = self.pend(ev(session="a"))
        self.assertFalse(p.apply(parse_event(ev(session="zzz", kind="cleared"), NOW)))
        self.assertEqual(len(p), 1)
        self.assertTrue(p.apply(parse_event(ev(session="a", kind="cleared"), NOW)))
        self.assertEqual(len(p), 0)

    def test_remove_and_clear(self):
        p = self.pend(ev(session="a"), ev(session="b"))
        self.assertTrue(p.remove("Claude", " a "))  # normalised like a posted event
        self.assertFalse(p.remove("claude", "a"))
        self.assertTrue(p.clear())
        self.assertFalse(p.clear())

    def test_summary_is_capped_but_counts_everything_kept(self):
        p = self.pend(*[ev(session=f"s{i}") for i in range(MAX_PENDING + 5)])
        s = p.summary(NOW)
        self.assertEqual(s["count"], MAX_PENDING)
        self.assertEqual(len(s["pending"]), SHOWN_PENDING)
        self.assertEqual(s["pending"][0]["session"], f"s{MAX_PENDING + 4}")
        self.assertEqual(set(s["pending"][0]), {"source", "session", "kind", "title", "ts"})


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.n = Notifier(lambda t, p, r: self.sent.append((t, json.loads(p), r)), lambda: NOW)

    def test_post_publishes_the_event_then_the_pending_list(self):
        self.assertEqual(self.n.post(json.dumps(ev()).encode()), {"ok": True, "pending": 1})
        self.assertEqual([(t, r) for t, _, r in self.sent], [(TOPIC_EVENT, False), (TOPIC_NOTIFY, True)])
        self.assertEqual(self.sent[0][1]["kind"], "needs_input")
        self.assertEqual(self.sent[1][1]["pending"][0]["session"], "website")

    def test_bad_post_publishes_nothing(self):
        with self.assertRaises(EventError):
            self.n.post(b"{}")
        self.assertEqual(self.sent, [])

    def test_ack_drops_one_or_all(self):
        self.n.post(ev())
        self.n.post(ev(source="codex", session="fix"))
        self.sent.clear()
        self.n.ack(b'{"source":"claude","session":"website"}')
        self.assertEqual(self.sent[-1][1]["count"], 1)
        self.n.ack(b'{"session":"nothing"}')  # nothing pending for it: no republish
        self.assertEqual(len(self.sent), 1)
        self.n.ack(b'{"all":true}')
        self.assertEqual(self.sent[-1][1]["count"], 0)
        with self.assertLogs("bridge.notify", level="WARNING"):
            self.n.ack(b"garbage")  # ignored, no exception
        self.assertEqual(len(self.sent), 2)


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sent = []
        cls.server = make_server("127.0.0.1", 0, Notifier(lambda t, p, r: cls.sent.append((t, p, r)), lambda: NOW))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method: str, path: str, body: bytes | None = None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_round_trip(self):
        self.assertEqual(self.call("POST", "/event", json.dumps(ev()).encode()), (200, {"ok": True, "pending": 1}))
        status, pending = self.call("GET", "/pending")
        self.assertEqual((status, pending["count"], pending["pending"][0]["session"]), (200, 1, "website"))

    def test_errors(self):
        self.assertEqual(self.call("POST", "/event", b"nope")[0], 400)
        self.assertEqual(self.call("POST", "/elsewhere", b"{}")[0], 404)
        self.assertEqual(self.call("GET", "/")[0], 404)
        self.assertEqual(self.call("POST", "/event", b"x" * (MAX_BODY + 1))[0], 413)
