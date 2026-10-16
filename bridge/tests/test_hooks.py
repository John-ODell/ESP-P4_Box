import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
PY = sys.executable


def load(name):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pethook = load("pethook")
claude_hook = load("claude_hook")
codex_hook = load("codex_hook")
codex_notify = load("codex_notify")


class StubBridge:
    """Records every POST body; answers 200. Stands in for the bridge's /event endpoint."""

    def __init__(self):
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                data = b'{"ok": true, "pending": 1}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/event"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RawServer:
    """Accepts one connection and answers `reply`, or nothing for 1.5 s when reply is None: a sick bridge."""

    def __init__(self, reply):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/event"
        self.thread = threading.Thread(target=self._serve, args=(reply,), daemon=True)
        self.thread.start()

    def _serve(self, reply):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(3)
            try:
                conn.recv(4096)
            except OSError:
                return
            if reply is None:
                time.sleep(1.5)
            else:
                conn.sendall(reply)

    def close(self):
        self.thread.join(timeout=5)
        self.sock.close()


class ClaudeHookTests(unittest.TestCase):
    def test_permission_prompt_needs_input(self):
        e = claude_hook.build_event({"hook_event_name": "Notification", "cwd": "/Users/j/Desktop/website",
                                     "notification_type": "permission_prompt", "title": "Permission needed",
                                     "message": "Claude needs your permission to use Bash"})
        self.assertEqual(e, {"v": 1, "source": "claude", "kind": "needs_input", "session": "website",
                             "title": "Permission needed", "detail": "Claude needs your permission to use Bash"})

    def test_notification_without_type_or_title_still_notifies(self):
        e = claude_hook.build_event({"hook_event_name": "Notification", "cwd": "/w", "message": "Waiting\nmore"})
        self.assertEqual((e["kind"], e["title"], e["detail"]), ("needs_input", "Waiting", ""))

    def test_noise_notifications_are_dropped(self):
        for ntype in ("idle_prompt", "auth_success", "agent_completed", "quota_auto_resume_fired",
                      "elicitation_complete"):
            with self.subTest(ntype=ntype):
                self.assertIsNone(claude_hook.build_event({"hook_event_name": "Notification", "cwd": "/w",
                                                           "notification_type": ntype, "message": "x"}))

    def test_stop_is_done_with_the_first_line(self):
        e = claude_hook.build_event({"hook_event_name": "Stop", "cwd": "/w/box", "stop_hook_active": False,
                                     "last_assistant_message": "\nAll tests pass.\nDetails follow."})
        self.assertEqual((e["kind"], e["session"], e["title"]), ("done", "box", "All tests pass."))
        self.assertEqual(claude_hook.build_event({"hook_event_name": "Stop", "cwd": "/w"})["title"], "finished")

    def test_prompt_clears_and_session_end_sends_nothing(self):
        e = claude_hook.build_event({"hook_event_name": "UserPromptSubmit", "cwd": "/w/box", "prompt": "yes"})
        self.assertEqual((e["kind"], e["session"]), ("cleared", "box"))
        for source, expect in (("user", "cleared"), ("sdk", "cleared"), ("loop_wakeup", None),
                               ("schedule_wakeup", None), ("poll_event", None)):
            with self.subTest(source=source):
                e = claude_hook.build_event({"hook_event_name": "UserPromptSubmit", "cwd": "/w/box", "source": source})
                self.assertEqual(e and e["kind"], expect)
        self.assertIsNone(claude_hook.build_event({"hook_event_name": "SessionEnd", "cwd": "/w/box", "reason": "other"}))

    def test_other_events_send_nothing(self):
        self.assertIsNone(claude_hook.build_event({"hook_event_name": "PreToolUse", "cwd": "/w"}))
        self.assertIsNone(claude_hook.build_event({}))

    def test_session_name_is_the_folder_even_when_the_session_has_a_title(self):
        # session_title reaches only UserPromptSubmit, never Stop or Notification, so keying by it would leave
        # bubbles that never clear (found in the review of PR #14)
        self.assertEqual(claude_hook.session_name({"session_title": "backlight fix", "cwd": "/w/box"}), "box")
        self.assertEqual(claude_hook.session_name({"cwd": "/w/box/"}), "box")
        env = os.environ.get("CLAUDE_PROJECT_DIR")
        try:
            os.environ["CLAUDE_PROJECT_DIR"] = "/w/proj"
            self.assertEqual(claude_hook.session_name({}), "proj")
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            self.assertEqual(claude_hook.session_name({}), "claude")
        finally:
            if env is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = env

    def test_title_is_cut_to_eighty_characters(self):
        e = claude_hook.build_event({"hook_event_name": "Stop", "cwd": "/w", "last_assistant_message": "x" * 500})
        self.assertEqual(len(e["title"]), 80)


class CodexHookTests(unittest.TestCase):
    def test_permission_request_needs_input(self):
        e = codex_hook.build_event({"hook_event_name": "PermissionRequest", "cwd": "/w/api", "tool_name": "shell",
                                    "tool_input": {"command": ["git", "push"], "description": "Push the branch"}})
        self.assertEqual(e, {"v": 1, "source": "codex", "kind": "needs_input", "session": "api",
                             "title": "Push the branch", "detail": "git push"})
        e = codex_hook.build_event({"hook_event_name": "PermissionRequest", "cwd": "/w/api", "tool_name": "shell",
                                    "tool_input": {"description": None}})
        self.assertEqual(e["title"], "shell needs approval")

    def test_stop_prompt_end_and_others(self):
        self.assertEqual(codex_hook.build_event({"hook_event_name": "Stop", "cwd": "/w/api",
                                                 "last_assistant_message": "Done."})["title"], "Done.")
        self.assertEqual(codex_hook.build_event({"hook_event_name": "UserPromptSubmit", "cwd": "/w"})["kind"],
                         "cleared")
        self.assertIsNone(codex_hook.build_event({"hook_event_name": "SessionEnd", "cwd": "/w", "reason": "other"}))
        self.assertIsNone(codex_hook.build_event({"hook_event_name": "PreToolUse", "cwd": "/w"}))
        self.assertEqual(codex_hook.build_event({"hook_event_name": "Stop"})["session"], "codex")


class CodexNotifyTests(unittest.TestCase):
    def test_turn_complete_is_done(self):
        for key in ("last-assistant-message", "last_assistant_message"):
            e = codex_notify.build_event({"type": "agent-turn-complete", "turn-id": "1", key: "Renamed.\nok",
                                          "cwd": "/w/api"})
            self.assertEqual(e, {"v": 1, "source": "codex", "kind": "done", "session": "api", "title": "Renamed.",
                                 "detail": ""})
        self.assertEqual(codex_notify.build_event({"type": "agent-turn-complete"})["session"], "codex")
        self.assertIsNone(codex_notify.build_event({"type": "something-else"}))

    def test_split_args(self):
        self.assertEqual(codex_notify.split_args(["{}"]), ("{}", []))
        self.assertEqual(codex_notify.split_args(["--then", "/bin/x", "arg", "{}"]), ("{}", ["/bin/x", "arg"]))
        self.assertEqual(codex_notify.split_args(["--then", "/bin/x"]), (None, ["/bin/x"]))
        self.assertEqual(codex_notify.split_args([]), (None, []))

    def test_chained_command_gets_the_payload_and_the_bridge_gets_the_event(self):
        stub = StubBridge()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "argv.json"
                chain = [PY, "-c", "import json,sys; open(sys.argv[1],'w').write(json.dumps(sys.argv[2:]))", str(out)]
                payload = json.dumps({"type": "agent-turn-complete", "last-assistant-message": "ok", "cwd": "/w/api"})
                env = dict(os.environ, PET_NOTIFY_URL=stub.url)
                run = subprocess.run([PY, str(HOOKS / "codex_notify.py"), "--then", *chain, payload],
                                     env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual((run.returncode, run.stdout), (0, ""))
                for _ in range(200):  # the chain is started after the post and not waited on
                    if out.exists() and out.stat().st_size:
                        break
                    time.sleep(0.05)
                self.assertEqual(json.loads(out.read_text()), [payload])
            self.assertEqual([(b["kind"], b["session"]) for b in stub.bodies], [("done", "api")])
        finally:
            stub.close()

    def test_a_missing_or_slow_chain_never_costs_the_event(self):
        stub = StubBridge()
        try:
            env = dict(os.environ, PET_NOTIFY_URL=stub.url)
            payload = json.dumps({"type": "agent-turn-complete", "last-assistant-message": "ok", "cwd": "/w/api"})
            started = time.monotonic()
            for chain in (["/nonexistent/notify-command", "turn-ended"], ["sleep", "3"]):
                run = subprocess.run([PY, str(HOOKS / "codex_notify.py"), "--then", *chain, payload],
                                     env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""), chain)
            self.assertLess(time.monotonic() - started, 2.5)
            self.assertEqual([b["kind"] for b in stub.bodies], ["done", "done"])
        finally:
            stub.close()


class SnippetTests(unittest.TestCase):
    def test_snippets_parse_and_name_only_the_handled_events(self):
        claude = json.loads((HOOKS / "claude-settings.json").read_text())["hooks"]
        self.assertEqual(set(claude), {"Notification", "Stop", "UserPromptSubmit"})
        self.assertEqual(set(claude["Notification"][0]["matcher"].split("|")), claude_hook.NEEDS_INPUT_TYPES)
        codex = json.loads((HOOKS / "codex-hooks.json").read_text())["hooks"]
        self.assertEqual(set(codex), {"PermissionRequest", "Stop", "UserPromptSubmit"})
        for script, table in (("claude_hook.py", claude), ("codex_hook.py", codex)):
            for entries in table.values():
                for hook in entries[0]["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    self.assertIn(script, hook["command"])
                    self.assertIn('"$HOME/', hook["command"])
                    self.assertTrue(hook["command"].endswith(" 2>/dev/null || true"), hook["command"])
                    self.assertLessEqual(hook["timeout"], 5)
        notify = tomllib.loads((HOOKS / "codex-config.snippet.toml").read_text())["notify"]
        self.assertEqual((notify[0], notify[1].rsplit("/", 1)[-1], notify[2]), ("python3", "codex_notify.py", "--then"))


class PostAndScriptTests(unittest.TestCase):
    def test_post_reports_success_and_failure_without_raising(self):
        stub = StubBridge()
        try:
            self.assertTrue(pethook.post({"v": 1, "source": "t", "kind": "done", "session": "s"}, url=stub.url))
            self.assertEqual(stub.bodies[0]["session"], "s")
        finally:
            stub.close()
        started = time.monotonic()
        self.assertFalse(pethook.post({"v": 1}, url="http://127.0.0.1:1/event"))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_post_survives_a_sick_bridge(self):
        for reply in (b"garbage\r\n\r\n", None):  # a non-HTTP answer, then a bridge that accepts and says nothing
            server = RawServer(reply)
            try:
                started = time.monotonic()
                self.assertFalse(pethook.post({"v": 1}, url=server.url))
                self.assertLess(time.monotonic() - started, 2.0)
            finally:
                server.close()

    def test_claude_script_end_to_end(self):
        stub = StubBridge()
        try:
            env = dict(os.environ, PET_NOTIFY_URL=stub.url)
            payload = json.dumps({"hook_event_name": "Stop", "cwd": "/w/box", "last_assistant_message": "Done"})
            run = subprocess.run([PY, str(HOOKS / "claude_hook.py")], input=payload, env=env,
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))
            self.assertEqual(stub.bodies[-1]["title"], "Done")
            for bad in ("not json", "", "[1]"):
                run = subprocess.run([PY, str(HOOKS / "claude_hook.py")], input=bad, env=env,
                                     capture_output=True, text=True, timeout=20)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))
            self.assertEqual(len(stub.bodies), 1)
        finally:
            stub.close()

    def test_scripts_exit_zero_when_the_bridge_is_down(self):
        env = dict(os.environ, PET_NOTIFY_URL="http://127.0.0.1:1/event")
        for script, stdin in (("claude_hook.py", '{"hook_event_name":"Stop","cwd":"/w"}'),
                              ("codex_hook.py", '{"hook_event_name":"Stop","cwd":"/w"}')):
            run = subprocess.run([PY, str(HOOKS / script)], input=stdin, env=env, capture_output=True, text=True,
                                 timeout=20)
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""), script)
        run = subprocess.run([PY, str(HOOKS / "codex_notify.py"), '{"type":"agent-turn-complete"}'], env=env,
                             capture_output=True, text=True, timeout=20)
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
