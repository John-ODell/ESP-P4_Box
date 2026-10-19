import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from petbrain import todo as todo_mod
from petbrain.notify_api import Notifier, make_server, serve_in_thread
from petbrain.todo import BUDGET, MAX_ITEMS, SHOWN, TOPIC_TODO, Todo, TodoError, parse_ref
from tests.test_tailnet import LOCAL, NO_PROXY, TOKEN, call, quiet

BRIDGE = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def make(store=None):
    sent = []
    return Todo(lambda topic, payload, retain: sent.append((topic, json.loads(payload), retain)), Clock(), store), sent


class RefTests(unittest.TestCase):
    def test_issue_and_pr_numbers_in_the_shapes_people_type(self):
        for raw, want in (("85", "#85"), ("#85", "#85"), (85, "#85"), ("PR #85", "#85"), ("issue 7", "#7"),
                          ("John-ODell/ESP-P4_Box#85", "John-ODell/ESP-P4_Box#85"), (" #085 ", "#85"),
                          (None, None), ("", None)):
            self.assertEqual(parse_ref(raw), want, raw)

    def test_anything_else_is_refused(self):
        for raw in ("#0", "abc", "#85 and #86", "owner#85", "https://github.com/a/b/pull/85", "#12345678",
                    "owner/repo85", "\u00f3wner/rep\u00f6#5", "#\u0665", "a" * 40 + "/repo#5", "o/" + "r" * 101 + "#5"):
            with self.assertRaises(TodoError, msg=raw):
                parse_ref(raw)


class ListTests(unittest.TestCase):
    def test_add_tick_untick_remove_publish_the_list_retained(self):
        t, sent = make()
        a = t.add("Flash the box", "claude", "#90")
        t.add("  Write   the\nREADME  ", "john")
        self.assertEqual((a["id"], a["text"], a["by"], a["ref"], a["done"]), (1, "Flash the box", "claude", "#90", False))
        t.set_done(1, True, "box")
        topic, last, retain = sent[-1]
        self.assertEqual((topic, retain), (TOPIC_TODO, True))
        self.assertEqual(last["open"], 1)
        self.assertEqual([(i["id"], i["text"], i["done"]) for i in last["items"]],
                         [(2, "Write the README", False), (1, "Flash the box", True)])
        self.assertEqual(set(last["items"][0]), {"id", "text", "done", "by", "ref"})
        t.set_done(1, False)
        self.assertEqual(sent[-1][1]["open"], 2)
        t.remove(2)
        self.assertEqual([i["id"] for i in sent[-1][1]["items"]], [1])
        self.assertEqual(len(sent), 5)

    def test_ticking_what_is_already_ticked_publishes_nothing(self):
        t, sent = make()
        t.add("x")
        t.set_done(1, True)
        n = len(sent)
        self.assertTrue(t.set_done(1, True)["done"])
        self.assertEqual(len(sent), n)

    def test_bad_input_is_a_todo_error(self):
        t, sent = make()
        for args in (("",), ("   ",), (None,), (5,), ("x", ""), ("x", "john", "nope")):
            with self.assertRaises(TodoError, msg=args):
                t.add(*args)
        for bad in (99, "1", True, None):
            with self.assertRaises(TodoError, msg=bad):
                t.set_done(bad, True)
        self.assertEqual(sent, [])

    def test_text_is_plain_ascii_and_cut_to_fit(self):
        t, _ = make()
        item = t.add("“Ship it” — " + "word " * 30)
        self.assertTrue(item["text"].startswith('"Ship it" - word'))
        self.assertLessEqual(len(item["text"]), todo_mod.MAX_TEXT)
        self.assertTrue(item["text"].endswith("..."))

    def test_the_box_gets_open_items_oldest_first_then_the_latest_done_and_at_most_shown(self):
        t, sent = make()
        for i in range(SHOWN + 3):
            t.add(f"item {i + 1}")
        for i in (2, 5, 4):
            t.set_done(i, True)
        items = sent[-1][1]["items"]
        self.assertEqual(len(items), SHOWN)
        self.assertEqual(sent[-1][1]["count"], SHOWN + 3)
        ids = [i["id"] for i in items]
        self.assertEqual(ids[:3], [1, 3, 6])
        self.assertNotIn(2, ids)   # open items fill the screen first
        full = t.summary()["items"]
        self.assertEqual([i["id"] for i in full[-3:]], [4, 5, 2])   # done, most recently done first

    def test_control_characters_never_reach_the_box(self):
        t, sent = make()
        item = t.add("tab\there\x01bell\x07\x7fend", "cl\naude")
        self.assertEqual((item["text"], item["by"]), ("tab here bell end", "cl aude"))
        self.assertNotIn("\\u", json.dumps(sent[-1][1]))
        t.set_done(1, True, "evil\nINFO forged line")
        self.assertEqual(t._items[0]["done_by"], "evil INFO forged line")

    def test_pet_todo_stays_under_the_box_buffer_whatever_the_items_hold(self):
        t, sent = make()
        ref = "a" * 39 + "/" + "r" * 100 + "#1234567"
        for _ in range(SHOWN + 2):
            t.add('"' * 200, "\\" * 40, ref)
        topic, payload, retain = sent[-1]
        self.assertLessEqual(len(json.dumps(payload, separators=(",", ":"))), BUDGET)
        self.assertGreater(len(payload["items"]), 0)
        self.assertLess(len(payload["items"]), SHOWN)
        self.assertEqual(payload["count"], SHOWN + 2)

    def test_a_full_list_drops_the_oldest_done_item_or_refuses_when_all_are_open(self):
        t, _ = make()
        for i in range(MAX_ITEMS):
            t.add(f"item {i}")
        with self.assertRaises(TodoError):
            t.add("one too many")
        t.set_done(7, True)
        t.set_done(3, True)
        new = t.add("fits now")
        ids = [i["id"] for i in t.summary()["items"]]
        self.assertEqual(len(ids), MAX_ITEMS)
        self.assertNotIn(7, ids)
        self.assertIn(3, ids)
        self.assertEqual(new["id"], MAX_ITEMS + 1)   # a refused add used no number


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "var" / "todo.json"

    def test_the_list_and_its_numbering_survive_a_restart(self):
        t, _ = make(self.path)
        t.add("a", ref="5")
        t.add("b")
        t.set_done(1, True)
        t.remove(2)
        again, sent = make(self.path)
        self.assertEqual(again.summary()["items"],
                         [{"id": 1, "text": "a", "done": True, "by": "john", "ref": "#5"}])
        self.assertEqual(again.add("c")["id"], 3)   # 2 is not handed out again
        again2, sent2 = make(self.path)
        again2.publish()
        self.assertEqual(sent2[0][1]["count"], 2)

    def test_a_broken_file_starts_empty_and_odd_entries_are_dropped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        with self.assertLogs("bridge.todo", "WARNING"):
            t, _ = make(self.path)
        self.assertEqual(t.summary()["count"], 0)
        self.path.write_text(json.dumps({"v": 1, "next": 4, "items": [
            {"id": 1, "text": "kept", "done": False, "by": "john", "ref": None},
            {"id": 2, "text": ""}, {"text": "no id"}, "junk", {"id": 3, "text": "bad ref", "ref": "nope"}]}))
        with self.assertLogs("bridge.todo", "WARNING") as logs:
            t, _ = make(self.path)
        self.assertEqual([(i["text"], i["ref"]) for i in t.summary()["items"]], [("kept", None), ("bad ref", None)])
        self.assertEqual(sum("not kept" in m for m in logs.output), 4)   # three entries and one ref
        self.assertEqual(t.add("next")["id"], 4)

    def test_wrong_types_in_a_hand_edited_file_never_stop_the_bridge(self):
        self.path.parent.mkdir(parents=True)
        for nxt in ("abc", None, [], 2.5, True, {}):
            self.path.write_text(json.dumps({"next": nxt, "items": [
                {"id": 1, "text": "a", "done": True, "done_ts": 5},
                {"id": 2, "text": "b", "done": True, "done_ts": ["x"], "done_by": 7},
                {"id": 3, "text": "c", "done": "false", "done_ts": "2026-10-19T09:00:00+00:00"},
                {"id": 3, "text": "duplicate"}, {"id": True, "text": "bool id"}, {"id": -1, "text": "neg"},
                {"id": 4, "text": "bad ref kept, ref dropped", "ref": "nope"}]}))
            with self.assertLogs("bridge.todo", "WARNING"):
                t, sent = make(self.path)
            t.publish()
            self.assertEqual([(i["id"], i["done"], i["ref"]) for i in t.summary()["items"]],
                             [(3, False, None), (4, False, None), (1, True, None), (2, True, None)], nxt)
            self.assertIsNone(t._items[1]["done_by"])
            self.assertEqual(t.add("next")["id"], 5)
            self.path.unlink()

    def test_a_file_over_the_limit_is_trimmed_oldest_done_first(self):
        self.path.parent.mkdir(parents=True)
        items = [{"id": i, "text": f"t{i}", "done": i in (10, 20), "done_ts": f"2026-10-{i:02d}T09:00:00+00:00"}
                 for i in range(1, MAX_ITEMS + 4)]
        self.path.write_text(json.dumps({"next": 99, "items": items}))
        t, _ = make(self.path)
        ids = [i["id"] for i in t.summary()["items"]]
        self.assertEqual(len(ids), MAX_ITEMS)
        self.assertNotIn(10, ids)
        self.assertNotIn(20, ids)
        self.assertNotIn(1, ids)

    def test_done_order_follows_the_real_moment_across_a_clock_change(self):
        t, _ = make()
        for i in range(2):
            t.add(f"t{i}")
        t.set_done(1, True)
        t.set_done(2, True)
        # 1:30 am EDT is earlier than 1:10 am EST the same night
        t._items[0]["done_ts"] = "2026-11-01T01:30:00-04:00"
        t._items[1]["done_ts"] = "2026-11-01T01:10:00-05:00"
        self.assertEqual([i["id"] for i in t.summary()["items"]], [2, 1])


class TickTests(unittest.TestCase):
    def test_the_box_ticks_and_unticks_by_id(self):
        t, sent = make()
        t.add("x")
        t.tick(b'{"id":1,"done":true}')
        self.assertEqual(t._items[0]["done_by"], "box")
        t.tick(b'{"id":1,"done":false}')
        self.assertFalse(sent[-1][1]["items"][0]["done"])

    def test_anything_malformed_is_logged_and_dropped(self):
        t, sent = make()
        t.add("x")
        n = len(sent)
        for bad in (b"nope", b"[]", b'{"id":1}', b'{"id":1,"done":1}', b'{"id":"1","done":true}',
                    b'{"id":true,"done":true}', b'{"id":9,"done":true}', b"\xff"):
            with self.assertLogs("bridge.todo", "WARNING"):
                t.tick(bad)
        self.assertEqual(len(sent), n)


class Served(unittest.TestCase):
    """A loopback endpoint with a list behind it."""
    def setUp(self):
        quiet(self)
        self.todo, self.sent = make()
        self.server = make_server("127.0.0.1", 0, Notifier(lambda *a: None, Clock()), todo=self.todo)
        serve_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, obj):
        return call(self.url + "/todo", json.dumps(obj).encode(), {"Content-Type": "application/json"})


class HttpTests(Served):

    def test_post_changes_and_get_lists(self):
        self.assertEqual(self.post({"op": "add", "text": "Flash it", "ref": "#90", "by": "claude"}), 200)
        self.assertEqual(self.post({"op": "done", "id": 1}), 200)
        with NO_PROXY.open(self.url + "/todo", timeout=5) as resp:
            got = json.loads(resp.read())
        self.assertEqual(got["items"], [{"id": 1, "text": "Flash it", "done": True, "by": "claude", "ref": "#90"}])
        self.assertEqual(self.post({"op": "remove", "id": 1}), 200)
        self.assertEqual(self.todo.summary()["count"], 0)

    def test_bad_requests_are_400_and_change_nothing(self):
        for body in ({"op": "add"}, {"op": "add", "text": "x", "ref": "nope"}, {"op": "done", "id": 5},
                     {"op": "sing"}, [], "add"):
            self.assertEqual(self.post(body), 400, body)
        self.assertEqual(call(self.url + "/todo", b"{oops", {}), 400)
        self.assertEqual(self.sent, [])

    def test_browser_requests_and_big_bodies_are_refused_as_on_event(self):
        self.assertEqual(call(self.url + "/todo", b'{"op":"add","text":"x"}', {"Origin": "https://evil.example"}), 403)
        self.assertEqual(call(self.url + "/todo", b"x" * 5000, {}), 413)
        self.assertEqual(self.sent, [])

    def test_the_tailnet_server_needs_the_token_for_todo_too(self):
        guarded = make_server("127.0.0.1", 0, Notifier(lambda *a: None, Clock()), token=TOKEN, allow=LOCAL,
                              todo=self.todo)
        serve_in_thread(guarded)
        url = f"http://127.0.0.1:{guarded.server_address[1]}/todo"
        try:
            self.assertEqual(call(url, b'{"op":"add","text":"x"}', {}), 401)
            self.assertEqual(call(url), 401)
            self.assertEqual(self.sent, [])
            self.assertEqual(call(url, b'{"op":"add","text":"x"}', {"Authorization": f"Bearer {TOKEN}"}), 200)
            self.assertEqual(call(url, headers={"Authorization": f"Bearer {TOKEN}"}), 200)
        finally:
            guarded.shutdown()
            guarded.server_close()

    def test_a_server_without_a_list_has_no_todo_path(self):
        bare = make_server("127.0.0.1", 0, Notifier(lambda *a: None, Clock()))
        serve_in_thread(bare)
        url = f"http://127.0.0.1:{bare.server_address[1]}/todo"
        try:
            self.assertEqual(call(url, b'{"op":"add","text":"x"}', {}), 404)
            self.assertEqual(call(url), 404)
        finally:
            bare.shutdown()
            bare.server_close()


class CliTests(Served):
    def cli(self, *args, url=None, token=""):
        env = {**os.environ, "PET_NOTIFY_URL": url or self.url + "/event", "PET_NOTIFY_TOKEN": token}
        env.pop("PET_TODO_BY", None)
        done = subprocess.run([sys.executable, str(BRIDGE / "todo.py"), *args], capture_output=True, text=True,
                              env=env, timeout=20)
        return done.returncode, done.stdout + done.stderr

    def test_add_list_done_undo_rm(self):
        self.assertEqual(self.cli("add", "Flash the list screen", "--ref", "90", "--by", "claude")[0], 0)
        self.assertEqual(self.cli("add", "Second")[0], 0)
        code, out = self.cli("done", "1")
        self.assertEqual((code, out.strip()), (0, "ticked:    1  [x]  Flash the list screen  #90  (claude)"))
        code, out = self.cli("list")
        self.assertEqual(out.splitlines(), ["   2  [ ]  Second  (john)",
                                            "   1  [x]  Flash the list screen  #90  (claude)", "1 open, 1 done"])
        self.assertEqual(self.cli("undo", "1")[0], 0)
        self.assertEqual(self.cli("rm", "2")[0], 0)
        self.assertEqual([i["id"] for i in self.todo.summary()["items"]], [1])

    def test_options_work_after_the_subcommand(self):
        code, out = self.cli("add", "x", "--timeout", "3", "--url", self.url + "/event", url="http://127.0.0.1:9/event")
        self.assertEqual(code, 0, out)

    def test_the_configured_token_goes_only_to_its_own_address(self):
        seen = []

        class Echo(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                body = b'{"v":1,"open":0,"count":0,"items":[]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        echo = HTTPServer(("127.0.0.1", 0), Echo)
        threading.Thread(target=echo.serve_forever, daemon=True).start()
        other = f"http://127.0.0.1:{echo.server_address[1]}/event"
        try:
            self.assertEqual(self.cli("list", url=other, token=TOKEN)[0], 0)
            code, out = self.cli("list", "--url", other, url=self.url + "/event", token=TOKEN)
            self.assertIn("not sending the configured token", out)
            self.assertEqual(self.cli("list", "--url", other, "--token", "u" * 40, url=self.url + "/event",
                                      token=TOKEN)[0], 0)
        finally:
            echo.shutdown()
            echo.server_close()
        self.assertEqual(seen, [f"Bearer {TOKEN}", None, "Bearer " + "u" * 40])

    def test_a_reply_that_makes_no_sense_is_exit_1_not_a_traceback(self):
        for body, status in ((b'[1]', 200), (b'{"ok":true}', 200), (b'{"error":5}', 400), (b"<html>", 500)):
            class Odd(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                do_POST = do_GET

                def log_message(self, *a):
                    pass
            odd = HTTPServer(("127.0.0.1", 0), Odd)
            threading.Thread(target=odd.serve_forever, daemon=True).start()
            try:
                for args in (("list",), ("done", "1")):
                    code, out = self.cli(*args, url=f"http://127.0.0.1:{odd.server_address[1]}/event")
                    self.assertEqual(code, 1, (body, args, out))
                    self.assertNotIn("Traceback", out)
            finally:
                odd.shutdown()
                odd.server_close()

    def test_list_hides_old_done_items_without_all(self):
        for i in range(8):
            self.todo.add(f"t{i}")
            self.todo.set_done(i + 1, True)
        self.assertEqual(self.cli("list")[1].splitlines()[-1], "0 open, 8 done, 3 more done (--all)")
        self.assertEqual(len(self.cli("list", "--all")[1].splitlines()), 9)

    def test_refusals_and_an_unreachable_bridge_exit_1_with_the_reason(self):
        code, out = self.cli("done", "42")
        self.assertEqual(code, 1)
        self.assertIn("no item 42", out)
        code, out = self.cli("list", url="http://127.0.0.1:9/event")
        self.assertEqual(code, 1)
        self.assertIn("not reachable", out)
        code, out = self.cli("nonsense")
        self.assertEqual(code, 2)

    def test_an_old_bridge_without_the_list_is_named(self):
        bare = make_server("127.0.0.1", 0, Notifier(lambda *a: None, Clock()))
        serve_in_thread(bare)
        try:
            code, out = self.cli("list", url=f"http://127.0.0.1:{bare.server_address[1]}/event")
        finally:
            bare.shutdown()
            bare.server_close()
        self.assertEqual(code, 1)
        self.assertIn("no to-do list yet", out)


if __name__ == "__main__":
    unittest.main()
