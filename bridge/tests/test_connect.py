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
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.repo = self.home / "Desktop" / "ESP-P4_Box"
        (self.repo / "bridge" / "hooks").mkdir(parents=True)
        src = BRIDGE / "hooks" / "claude-settings.json"
        (self.repo / "bridge" / "hooks" / "claude-settings.json").write_text(src.read_text())
        for name in ("claude_hook.py", "codex_notify.py", "goose_hook.py", "aider_hook.py"):
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
        (self.repo / "bridge" / "hooks" / "claude_hook.py").write_text("")
        self.write(".claude/settings.json", "{}\n")
        code = connect.main(["--connect", "claude", "--yes", "--repo", str(self.repo)], home=self.home,
                            out=self.out.append)
        self.assertEqual(code, 0)
        data = json.loads((self.home / ".claude" / "settings.json").read_text())
        command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn('"$HOME/Desktop/ESP-P4_Box/bridge/hooks/claude_hook.py"', command)


if __name__ == "__main__":
    unittest.main()
