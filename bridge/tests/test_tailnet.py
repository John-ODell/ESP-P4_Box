import ipaddress
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from petbrain import tailnet
from petbrain.notify_api import Notifier, make_server, serve_in_thread
from tests.test_hooks import pethook

BRIDGE = Path(__file__).resolve().parent.parent
TOKEN = "t" * 40
LOCAL = (ipaddress.ip_network("127.0.0.0/8"),)  # stands in for the tailnet: tests can only connect from loopback
EVENT = json.dumps({"v": 1, "source": "codex", "kind": "done", "session": "api"}).encode()
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with NO_PROXY.open(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def quiet(test):
    """The refusals these tests provoke are expected; keep them out of the test output."""
    log = logging.getLogger("bridge.notify")
    level = log.level
    log.setLevel(logging.CRITICAL)
    test.addCleanup(log.setLevel, level)


class Served:
    def __init__(self, **kw):
        self.sent = []
        self.server = make_server("127.0.0.1", 0, Notifier(lambda t, p, r: self.sent.append(t), lambda: NOW), **kw)
        serve_in_thread(self.server)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AuthTests(unittest.TestCase):
    def setUp(self):
        quiet(self)
        self.s = Served(token=TOKEN, allow=LOCAL)
        self.addCleanup(self.s.close)

    def test_missing_or_wrong_token_is_401_and_nothing_is_published(self):
        self.assertEqual(call(self.s.url + "/event", EVENT, {"Content-Type": "application/json"}), 401)
        self.assertEqual(call(self.s.url + "/event", EVENT, {"Authorization": "Bearer " + "x" * 40}), 401)
        self.assertEqual(call(self.s.url + "/pending"), 401)
        self.assertEqual(self.s.sent, [])

    def test_the_right_token_works_for_post_and_get(self):
        auth = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
        self.assertEqual(call(self.s.url + "/event", EVENT, auth), 200)
        self.assertEqual(call(self.s.url + "/pending", None, auth), 200)
        self.assertIn("pet/event", self.s.sent)

    def test_an_address_outside_the_allowed_networks_is_403_even_with_the_token(self):
        other = Served(token=TOKEN, allow=tailnet.TAILNET)
        self.addCleanup(other.close)
        self.assertEqual(call(other.url + "/event", EVENT, {"Authorization": "Bearer " + TOKEN}), 403)

    def test_the_loopback_server_still_needs_no_token(self):
        plain = Served()
        self.addCleanup(plain.close)
        self.assertEqual(call(plain.url + "/event", EVENT, {"Content-Type": "application/json"}), 200)


class StartTests(unittest.TestCase):
    CFG = {"tailnet": "on", "tailnet_token": TOKEN, "tailnet_host": "", "http_port": 0}

    def test_off_short_token_or_a_non_tailnet_host_never_listens(self):
        n = Notifier(lambda *a: None, lambda: NOW)
        self.assertIsNone(tailnet.start({**self.CFG, "tailnet": "off"}, n))
        with self.assertLogs("bridge.tailnet", "ERROR"):
            self.assertIsNone(tailnet.start({**self.CFG, "tailnet_token": "short"}, n))
        with self.assertLogs("bridge.tailnet", "ERROR"):
            self.assertIsNone(tailnet.start({**self.CFG, "tailnet_host": "192.168.1.5"}, n))

    def test_waits_for_tailscale_then_binds_only_the_address_it_found_with_the_token(self):
        n = Notifier(lambda *a: None, lambda: NOW)
        answers = iter([None, "100.79.52.11"])
        made, servers = [], []

        def make(host, port, notifier, token=None, allow=(), todo=None):
            made.append((host, token, allow))
            servers.append(make_server("127.0.0.1", 0, notifier, token=token, allow=allow))
            return servers[-1]
        with self.assertLogs("bridge.tailnet", "INFO"):
            thread = tailnet.start(self.CFG, n, detect=lambda: next(answers), sleep=lambda s: None, make=make)
            thread.join(5)
        for server in servers:
            server.shutdown()
            server.server_close()
        self.assertEqual(made, [("100.79.52.11", TOKEN, tailnet.TAILNET)])

    def test_detect_ip_reads_the_cli_and_ignores_anything_else(self):
        class Out:
            def __init__(self, text):
                self.stdout = text
        self.assertEqual(tailnet.detect_ip(lambda *a, **k: Out("100.79.52.11\n")), "100.79.52.11")
        self.assertIsNone(tailnet.detect_ip(lambda *a, **k: Out("192.168.1.9\nlogged out\n")))


class TargetTests(unittest.TestCase):
    def setUp(self):
        quiet(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = os.path.join(tmp.name, "bridge.json")

    def test_env_then_file_then_loopback_and_a_token_only_with_its_url(self):
        Path(self.config).write_text(json.dumps({"url": "http://mac.ts.net:8765/event", "token": TOKEN}))
        self.assertEqual(pethook.target({}, self.config), ("http://mac.ts.net:8765/event", TOKEN))
        self.assertEqual(pethook.target({"PET_NOTIFY_URL": "http://x/event"}, self.config), ("http://x/event", ""))
        Path(self.config).write_text("{not json")
        self.assertEqual(pethook.target({}, self.config), (pethook.DEFAULT_URL, ""))
        os.remove(self.config)
        self.assertEqual(pethook.target({}, self.config), (pethook.DEFAULT_URL, ""))

    def test_post_sends_the_bearer_token(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/event"
            self.assertTrue(pethook.post({"v": 1}, url=url, token=TOKEN))
            self.assertTrue(pethook.post({"v": 1}, url=url))
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen, ["Bearer " + TOKEN, None])

    def test_a_redirect_never_carries_the_token_to_another_host(self):
        seen = []

        class Elsewhere(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        elsewhere = HTTPServer(("127.0.0.1", 0), Elsewhere)
        target = f"http://127.0.0.1:{elsewhere.server_address[1]}/event"

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)  # urllib follows 302 on a POST (as a GET); 307 it never follows
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        redirect = HTTPServer(("127.0.0.1", 0), Redirect)
        for server in (elsewhere, redirect):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{redirect.server_address[1]}/event"
        self.assertFalse(pethook.post({"v": 1}, url=url, token=TOKEN))
        home = Path(self.config).parent
        (home / ".config" / "desk-pet").mkdir(parents=True)
        (home / ".config" / "desk-pet" / "bridge.json").write_text(json.dumps({"url": url, "token": TOKEN}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("PET_NOTIFY")}
        env["HOME"] = str(home)
        args = [sys.executable, str(BRIDGE / "notify.py"), "--source", "local", "--kind", "done", "--session", "s"]
        run = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0)
        self.assertIn("(302)", run.stderr)
        self.assertEqual(seen, [])

    def test_notify_py_exits_0_on_a_url_it_cannot_use(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PET_NOTIFY")}
        args = [sys.executable, str(BRIDGE / "notify.py"), "--source", "local", "--kind", "done", "--session", "s",
                "--url", "http://127.0.0.1:notaport/event"]
        run = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0)
        self.assertIn("not reachable", run.stderr)

    def test_the_handler_times_out_idle_clients(self):
        served = Served(token=TOKEN, allow=LOCAL)
        self.addCleanup(served.close)
        self.assertEqual(served.server.RequestHandlerClass.timeout, 10)

    def test_notify_py_uses_the_file_and_keeps_its_token_from_another_url(self):
        served = Served(token=TOKEN, allow=LOCAL)
        self.addCleanup(served.close)
        home = Path(self.config).parent
        (home / ".config" / "desk-pet").mkdir(parents=True)
        (home / ".config" / "desk-pet" / "bridge.json").write_text(json.dumps({"url": served.url + "/event",
                                                                               "token": TOKEN}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("PET_NOTIFY")}
        env["HOME"] = str(home)
        args = [sys.executable, str(BRIDGE / "notify.py"), "--source", "local", "--kind", "done", "--session", "s"]
        run = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertEqual(served.sent.count("pet/event"), 1)
        other = Served(token=TOKEN, allow=LOCAL)
        self.addCleanup(other.close)
        run = subprocess.run(args + ["--url", other.url + "/event"], env=env, capture_output=True, text=True,
                             timeout=20)
        self.assertIn("(401)", run.stderr)
        self.assertEqual(other.sent, [])


if __name__ == "__main__":
    unittest.main()
