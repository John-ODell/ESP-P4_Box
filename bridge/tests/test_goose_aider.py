import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_connect import Home, connect
from tests.test_hooks import HOOKS, StubBridge

PY = sys.executable


def load(name):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


goose_hook = load("goose_hook")
aider_hook = load("aider_hook")


class GooseHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = os.path.join(tmp.name, "website")
        self.addCleanup(lambda: os.path.exists(goose_hook.marker(self.cwd)) and os.remove(goose_hook.marker(self.cwd)))

    def test_startup_waiting_sends_nothing_then_a_turn_clears_and_finishes(self):
        self.assertIsNone(goose_hook.build_event("waiting", self.cwd))
        self.assertEqual(goose_hook.build_event("thinking", self.cwd),
                         {"v": 1, "source": "goose", "kind": "cleared", "session": "website"})
        self.assertEqual(goose_hook.build_event("waiting", self.cwd)["kind"], "done")
        self.assertIsNone(goose_hook.build_event("waiting", self.cwd))  # one done per turn
        self.assertIsNone(goose_hook.build_event("something-new", self.cwd))

    def test_run_like_goose_does_through_sh_with_the_status_appended(self):
        stub = StubBridge()
        try:
            env = dict(os.environ, PET_NOTIFY_URL=stub.url)
            os.makedirs(self.cwd)
            for status in ("waiting", "thinking", "waiting"):
                run = subprocess.run(["sh", "-c", f'{PY} "{HOOKS / "goose_hook.py"}" {status}'], cwd=self.cwd, env=env,
                                     capture_output=True, text=True, timeout=20)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))
            self.assertEqual([(b["kind"], b["session"]) for b in stub.bodies],
                             [("cleared", "website"), ("done", "website")])
        finally:
            stub.close()


class AiderHookTests(unittest.TestCase):
    def test_posts_done_for_the_folder_and_exits_quietly_without_a_bridge(self):
        self.assertEqual(aider_hook.build_event("/w/api"),
                         {"v": 1, "source": "aider", "kind": "done", "session": "api", "title": "waiting for you",
                          "detail": ""})
        env = dict(os.environ, PET_NOTIFY_URL="http://127.0.0.1:9/event")
        run = subprocess.run(["sh", "-c", f'{PY} "{HOOKS / "aider_hook.py"}" 2>/dev/null || true'], env=env,
                             capture_output=True, text=True, timeout=20)
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))


class GooseConfigTests(Home):
    CONFIG = "GOOSE_TELEMETRY_ENABLED: false\nextensions:\n  developer:\n    enabled: true"  # no final newline

    def test_hook_line_is_appended_without_a_suffix_and_removed_again(self):
        tool = self.tool(connect.Goose)
        on = tool.connect_text(self.CONFIG)
        last = on.splitlines()[-1]
        self.assertEqual(last, "GOOSE_STATUS_HOOK: 'python3 \"$HOME/Desktop/ESP-P4_Box/bridge/hooks/goose_hook.py\"'"
                               "  # desk pet: bridge/connect.py")
        self.assertTrue(tool.connected_in(on))
        self.assertEqual(tool.connect_text(on), on)
        self.assertEqual(tool.disconnect_text(on), self.CONFIG + "\n")

    def test_someone_elses_status_hook_is_not_replaced(self):
        with self.assertRaises(connect.ConnectError):
            self.tool(connect.Goose).connect_text("GOOSE_STATUS_HOOK: say\n")

    def test_disconnect_finds_the_line_after_goose_rewrote_the_file_without_comments(self):
        tool = self.tool(connect.Goose)
        rewritten = "GOOSE_STATUS_HOOK: python3 \"$HOME/Desktop/ESP-P4_Box/bridge/hooks/goose_hook.py\"\nx: 1\n"
        self.assertEqual(tool.disconnect_text(rewritten), "x: 1\n")


class AiderConfigTests(Home):
    def test_new_file_gets_notifications_on_and_the_command(self):
        tool = self.tool(connect.Aider)
        on = tool.connect_text("")
        self.assertEqual(on, "notifications: true  # desk pet: bridge/connect.py\n"
                             "notifications-command: 'python3 \"$HOME/Desktop/ESP-P4_Box/bridge/hooks/aider_hook.py\""
                             " 2>/dev/null || true'  # desk pet: bridge/connect.py\n")
        self.assertEqual(tool.disconnect_text(on), "")

    def test_an_existing_true_is_kept_and_a_false_is_refused(self):
        tool = self.tool(connect.Aider)
        on = tool.connect_text("model: sonnet\nnotifications: yes\n")
        self.assertEqual(on.count("notifications:"), 1)
        self.assertEqual(tool.disconnect_text(on), "model: sonnet\nnotifications: yes\n")
        with self.assertRaises(connect.ConnectError):
            tool.connect_text("notifications: false\n")

    def test_connect_refuses_when_the_checkout_lacks_the_script(self):
        (self.repo / "bridge" / "hooks" / "aider_hook.py").unlink()
        self.assertFalse(connect.change(self.tool(connect.Aider), True, yes=True, out=self.out.append))
        self.assertFalse((self.home / ".aider.conf.yml").exists())
        self.assertIn("is not there", self.out[-1])

    def test_quoted_on_values_count_and_off_means_not_connected(self):
        tool = self.tool(connect.Aider)
        for value in ('"true"', "'yes'", "1", "True"):
            on = tool.connect_text(f"notifications: {value}\n")
            self.assertEqual(on.count("notifications:"), 1, value)
            self.assertTrue(tool.connected_in(on), value)
        off = tool.connect_text("").replace("notifications: true", "notifications: false")
        self.assertFalse(tool.connected_in(off))
        with self.assertRaisesRegex(connect.ConnectError, "notifications on"):
            tool.connect_text(off)


class QuotedKeyTests(Home):
    def test_a_hand_quoted_goose_key_is_seen_and_not_doubled(self):
        tool = self.tool(connect.Goose)
        with self.assertRaises(connect.ConnectError):
            tool.connect_text('"GOOSE_STATUS_HOOK": say\n')
        ours = "'GOOSE_STATUS_HOOK': python3 \"$HOME/Desktop/ESP-P4_Box/bridge/hooks/goose_hook.py\"\nx: 1\n"
        self.assertTrue(tool.connected_in(ours))
        self.assertEqual(tool.disconnect_text(ours), "x: 1\n")


if __name__ == "__main__":
    unittest.main()
