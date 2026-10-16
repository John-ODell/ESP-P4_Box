"""connect.py on Windows (#39), faked on the Mac: os.name, sys.executable and APPDATA."""
import json
import os
import subprocess
import tomllib
from unittest import mock

from tests.test_connect import Home, connect
from tests.test_hooks import HOOKS, PY, StubBridge

PYTHON = r"C:\Users\John\AppData\Local\Programs\Python\Python313\python.exe"


class WindowsTests(Home):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(connect, "on_windows", lambda: True),
                      mock.patch.object(connect.sys, "executable", PYTHON),
                      mock.patch.dict(os.environ, {"APPDATA": str(self.home / "AppData" / "Roaming")})):
            patch.start()
            self.addCleanup(patch.stop)
        self.hooks = (self.repo / "bridge" / "hooks").resolve().as_posix()

    def test_claude_runs_this_python_and_the_hook_by_quoted_forward_slash_paths(self):
        tool = self.tool(connect.ClaudeCode)
        data = json.loads(tool.connect_text(""))
        cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, f'"C:/Users/John/AppData/Local/Programs/Python/Python313/python.exe" '
                              f'"{self.hooks}/claude_hook.py"')
        self.assertNotIn("/dev/null", cmd)  # cmd has no /dev/null and no `true`
        self.assertTrue(tool.connected_in(json.dumps(data)))
        self.assertFalse(tool.connected_in(tool.disconnect_text(json.dumps(data))))

    def test_a_hook_written_with_backslashes_still_counts_as_ours(self):
        tool = self.tool(connect.ClaudeCode)
        data = json.loads(tool.connect_text(""))
        for groups in data["hooks"].values():
            for hook in groups[0]["hooks"]:
                hook["command"] = r'"C:\py.exe" "C:\Users\John\ESP-P4_Box\bridge\hooks\claude_hook.py"'
        text = json.dumps(data)
        self.assertTrue(tool.connected_in(text))
        self.assertNotIn("hooks", json.loads(tool.disconnect_text(text)))

    def test_goose_lives_under_appdata_and_gets_an_unquoted_backslash_line(self):
        tool = self.tool(connect.Goose)
        self.assertEqual(tool.path(), self.home / "AppData" / "Roaming" / "Block" / "goose" / "config" / "config.yaml")
        line = tool.connect_text("GOOSE_PROVIDER: ollama\n").splitlines()[-1]
        value = line.split(": ", 1)[1].split("  #")[0].strip("'")
        self.assertEqual(value, PYTHON + " " + str(connect.PureWindowsPath(self.hooks + "/goose_hook.py")))
        self.assertNotIn('"', value)  # Goose's cmd /C breaks on quotes (Rust escapes them as \")
        self.assertTrue(tool.connected_in(tool.connect_text("")))
        self.assertEqual(tool.disconnect_text(tool.connect_text("x: 1\n")), "x: 1\n")
        self.write("AppData/Roaming/Block/goose/config/config.yaml", "x: 1\n")
        self.assertEqual(tool.found(), "~/AppData/Roaming/Block/goose/config/config.yaml")

    def test_goose_with_a_space_uses_the_short_name_or_refuses(self):
        tool = self.tool(connect.Goose)
        spaced = r"C:\Program Files\Python313\python.exe"
        with mock.patch.object(connect.sys, "executable", spaced):
            with mock.patch.object(connect, "short_path", lambda p: r"C:\PROGRA~1\PYTHON~1\python.exe" if p == spaced
                                   else p):
                self.assertIn(r"C:\PROGRA~1\PYTHON~1\python.exe ", tool.connect_text(""))
            with mock.patch.object(connect, "short_path", lambda p: p), self.assertRaises(connect.ConnectError):
                tool.connect_text("")

    def test_aider_gets_the_quoted_form_without_the_sh_suffix(self):
        tool = self.tool(connect.Aider)
        text = tool.connect_text("")
        self.assertIn(f'"{self.hooks}/aider_hook.py"', text)
        self.assertNotIn("|| true", text)
        self.assertTrue(tool.connected_in(text))

    def test_codex_runs_this_python_with_no_shell_and_keeps_the_old_notify(self):
        tool = self.tool(connect.CodexApp)
        text = tool.connect_text('notify = ["C:\\\\Tools\\\\notifier.exe", "turn-ended"]\n')
        notify = tomllib.loads(text)["notify"]
        self.assertEqual(notify[0], PYTHON)
        self.assertTrue(notify[1].endswith("bridge/hooks/codex_notify.py"))
        self.assertEqual(notify[2:], ["--then", r"C:\Tools\notifier.exe", "turn-ended"])
        backslashed = 'notify = ["C:\\\\py.exe", "C:\\\\x\\\\bridge\\\\hooks\\\\codex_notify.py"]\n'
        self.assertTrue(tool.connected_in(backslashed))

    def test_a_percent_or_quote_in_a_path_is_refused_not_guessed_at(self):
        with mock.patch.object(connect.sys, "executable", r"C:\Users\100%\python.exe"):
            with self.assertRaises(connect.ConnectError):
                self.tool(connect.ClaudeCode).connect_text("")

    def test_main_connects_on_windows_without_python3_anywhere(self):
        self.write(".claude/settings.json", "{}")
        code = connect.main(["--connect", "claude", "--yes", "--repo", str(self.repo)], home=self.home,
                            out=self.out.append)
        self.assertEqual(code, 0)
        self.assertNotIn("python3", (self.home / ".claude" / "settings.json").read_text())


class HookEncodingTests(Home):
    def test_a_hook_reads_utf8_stdin_whatever_the_console_code_page(self):
        stub = StubBridge()
        self.addCleanup(stub.close)
        env = dict(os.environ, PET_NOTIFY_URL=stub.url, PYTHONIOENCODING="cp1252")  # Windows' default stdin
        payload = json.dumps({"hook_event_name": "Stop", "cwd": "/w/box", "last_assistant_message": "Fertig \u2713 \u00fc"},
                             ensure_ascii=False).encode("utf-8")
        run = subprocess.run([PY, str(HOOKS / "claude_hook.py")], input=payload, env=env, capture_output=True,
                             timeout=20)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(stub.bodies[-1]["title"], "Fertig \u2713 \u00fc")
