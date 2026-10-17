import importlib.util
import json
import os
import stat
import tempfile
import tomllib
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("connect", BRIDGE / "connect.py")
connect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connect)

SKY = '/Users/x/.codex/computer-use/Codex Computer Use.app/Contents/MacOS/SkyComputerUseClient'
CODEX_CONFIG = f'notify = ["{SKY}", "turn-ended"]\nmodel = "gpt-5"\n\n[profiles.fast]\nnotify = ["keep-me"]\n'


class Home(unittest.TestCase):
    """A throwaway home folder holding the repo, so script paths come out as "$HOME/..." as on the Mac."""

    def setUp(self):
        real = connect.bridge_state
        connect.bridge_state = lambda: "stubbed"  # no test talks to a bridge that happens to run on this Mac
        self.addCleanup(setattr, connect, "bridge_state", real)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.repo = self.home / "Desktop" / "ESP-P4_Box"
        (self.repo / "bridge" / "hooks").mkdir(parents=True)
        src = BRIDGE / "hooks" / "claude-settings.json"
        (self.repo / "bridge" / "hooks" / "claude-settings.json").write_text(src.read_text())
        for name in ("claude_hook.py", "codex_notify.py", "pethook.py", "goose_hook.py", "aider_hook.py"):
            (self.repo / "bridge" / "hooks" / name).write_text("")
        self.out = []

    def tool(self, cls):
        return cls(self.home, self.repo)

    def write(self, rel, text, mode=0o644):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path


class ClaudeTests(Home):
    def test_empty_settings_get_three_events_pointing_at_this_repo(self):
        tool = self.tool(connect.ClaudeCode)
        data = json.loads(tool.connect_text(""))
        self.assertEqual(sorted(data["hooks"]), ["Notification", "Stop", "UserPromptSubmit"])
        cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, 'python3 "$HOME/Desktop/ESP-P4_Box/bridge/hooks/claude_hook.py" 2>/dev/null || true')
        self.assertTrue(tool.connected_in(json.dumps(data)))

    def test_other_keys_and_hooks_survive_and_a_second_run_changes_nothing(self):
        tool = self.tool(connect.ClaudeCode)
        mine = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
        once = tool.connect_text(json.dumps(mine))
        self.assertEqual(tool.connect_text(once), once)
        data = json.loads(once)
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "say done")
        self.assertEqual(json.loads(tool.disconnect_text(once)), mine)

    def test_disconnect_keeps_a_foreign_hook_that_shares_our_group(self):
        tool = self.tool(connect.ClaudeCode)
        data = json.loads(tool.connect_text(""))
        data["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "say done"})
        left = json.loads(tool.disconnect_text(json.dumps(data)))
        self.assertEqual(left, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}})

    def test_bad_json_is_refused_not_overwritten(self):
        tool = self.tool(connect.ClaudeCode)
        with self.assertRaises(connect.ConnectError):
            tool.connect_text("{not json")
        with self.assertRaises(connect.ConnectError):
            tool.connect_text('{"hooks": []}')

    def test_repo_outside_home_uses_the_absolute_path(self):
        ref = connect.script_ref(Path("/opt/pet"), self.home, "claude_hook.py")
        self.assertEqual(ref, '"/opt/pet/bridge/hooks/claude_hook.py"')


class CodexTests(Home):
    def test_existing_notify_is_chained_after_then_and_restored_exactly(self):
        tool = self.tool(connect.CodexApp)
        on = tool.connect_text(CODEX_CONFIG)
        data = tomllib.loads(on)
        script = str(self.repo / "bridge" / "hooks" / "codex_notify.py")
        self.assertEqual(data["notify"], ["python3", script, "--then", SKY, "turn-ended"])
        self.assertEqual(data["profiles"]["fast"]["notify"], ["keep-me"])
        self.assertTrue(tool.connected_in(on))
        self.assertEqual(tool.connect_text(on), on)
        self.assertEqual(tool.disconnect_text(on), CODEX_CONFIG)

    def test_missing_notify_is_added_above_the_tables_and_removed_again(self):
        tool = self.tool(connect.CodexApp)
        text = 'model = "gpt-5"\n[profiles.fast]\nnotify = ["keep-me"]\n'
        on = tool.connect_text(text)
        self.assertTrue(on.startswith("notify = ["))
        self.assertNotIn("--then", tomllib.loads(on)["notify"])
        self.assertEqual(tool.disconnect_text(on), text)

    def test_a_multi_line_notify_is_refused(self):
        tool = self.tool(connect.CodexApp)
        with self.assertRaises(connect.ConnectError):
            tool.connect_text('notify = [\n  "a",\n]\n')


class ChangeTests(Home):
    def test_writes_a_backup_keeps_the_mode_and_reads_back(self):
        path = self.write(".codex/config.toml", CODEX_CONFIG, 0o600)
        tool = self.tool(connect.CodexApp)
        self.assertTrue(connect.change(tool, True, yes=True, out=self.out.append))
        self.assertTrue(tool.connected())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        backups = list(path.parent.glob("config.toml.pet-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), CODEX_CONFIG)
        self.assertFalse(list(path.parent.glob("*.pet-tmp")))

    def test_answering_no_leaves_the_file_alone(self):
        path = self.write(".claude/settings.json", '{"theme": "dark"}\n')
        tool = self.tool(connect.ClaudeCode)
        self.assertFalse(connect.change(tool, True, yes=False, out=self.out.append, ask=lambda _: "n"))
        self.assertEqual(path.read_text(), '{"theme": "dark"}\n')
        self.assertEqual(os.listdir(path.parent), ["settings.json"])

    def test_already_connected_writes_nothing(self):
        tool = self.tool(connect.ClaudeCode)
        path = self.write(".claude/settings.json", tool.connect_text(""))
        before = path.stat().st_mtime_ns
        self.assertTrue(connect.change(tool, True, yes=True, out=self.out.append))
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertIn("already connected", self.out[-1])

    def test_status_on_an_empty_home_finds_nothing_and_writes_nothing(self):
        items = connect.tools(self.home, self.repo)
        for tool in items:
            tool.found = lambda: ""
        self.assertEqual([connect.state(t) for t in items], ["not found"] * 4)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["Desktop"])

    def test_main_refuses_a_repo_without_the_hooks(self):
        code = connect.main(["--status", "--repo", str(self.home)], home=self.home, out=self.out.append)
        self.assertEqual(code, 2)
        self.assertIn("not an ESP-P4_Box checkout", self.out[-1])

    def test_main_connects_with_yes_and_points_at_the_given_repo(self):
        self.write(".claude/settings.json", "{}\n")
        code = connect.main(["--connect", "claude", "--yes", "--repo", str(self.repo)], home=self.home,
                            out=self.out.append)
        self.assertEqual(code, 0)
        data = json.loads((self.home / ".claude" / "settings.json").read_text())
        command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn('"$HOME/Desktop/ESP-P4_Box/bridge/hooks/claude_hook.py"', command)

    def test_two_writes_in_one_second_keep_both_backups(self):
        path = self.write(".codex/config.toml", CODEX_CONFIG, 0o600)
        tool = self.tool(connect.CodexApp)
        for on in (True, False, True):
            self.assertTrue(connect.change(tool, on, yes=True, out=self.out.append))
        self.assertEqual(len(list(path.parent.glob("config.toml.pet-backup-*"))), 3)

    def test_an_unwritable_folder_is_a_message_not_a_traceback(self):
        path = self.write(".claude/settings.json", "{}\n")
        path.parent.chmod(0o500)
        self.addCleanup(path.parent.chmod, 0o755)
        self.assertFalse(connect.change(self.tool(connect.ClaudeCode), True, yes=True, out=self.out.append))
        self.assertIn("could not write", self.out[-1])
        self.assertEqual(path.read_text(), "{}\n")

    def test_menu_rejects_bad_picks_and_survives_end_of_input(self):
        for answer in ("5", "one"):
            self.out.clear()
            code = connect.main(["--repo", str(self.repo)], home=self.home, out=self.out.append, ask=lambda _: answer)
            self.assertEqual(code, 2)
            self.assertIn("Not a choice", self.out[-1])

        def eof(_):
            raise EOFError
        self.assertEqual(connect.main(["--repo", str(self.repo)], home=self.home, out=self.out.append, ask=eof), 0)

    def test_connect_skips_a_tool_that_is_not_on_this_mac(self):
        real = connect.CodexApp.found
        connect.CodexApp.found = lambda self: ""  # the real one also looks in /Applications
        self.addCleanup(setattr, connect.CodexApp, "found", real)
        code = connect.main(["--connect", "codex", "--yes", "--repo", str(self.repo)], home=self.home,
                            out=self.out.append)
        self.assertEqual(code, 1)
        self.assertFalse((self.home / ".codex").exists())

    def test_a_repo_path_a_shell_would_mangle_is_refused(self):
        with self.assertRaises(connect.ConnectError):
            connect.script_ref(Path('/opt/pet"$(x)'), self.home, "claude_hook.py")

    def test_a_group_with_odd_hooks_does_not_crash_status(self):
        tool = self.tool(connect.ClaudeCode)
        self.assertFalse(tool.connected_in('{"hooks": {"Stop": [{"hooks": 3}]}}'))

    def test_repeated_or_odd_picks_and_end_of_input_at_the_write_prompt(self):
        self.write(".claude/settings.json", "{}\n")
        answers = iter(["01 1"])

        def ask(prompt):
            if prompt.startswith("Write this"):
                raise EOFError
            return next(answers)
        self.assertEqual(connect.main(["--repo", str(self.repo)], home=self.home, out=self.out.append, ask=ask), 1)
        self.assertEqual(sum("left alone" in line for line in self.out), 1)  # one tool, asked once
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(), "{}\n")
        code = connect.main(["--repo", str(self.repo)], home=self.home, out=self.out.append, ask=lambda _: "\u00b2")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
