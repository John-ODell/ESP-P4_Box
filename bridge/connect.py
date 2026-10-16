#!/usr/bin/env python3
"""Connect the AI tools on this Mac to the desk pet, the way `goose configure` or `ollama launch` offers integrations.

    python3 bridge/connect.py                      menu: what is found, what is connected; pick to connect or undo
    python3 bridge/connect.py --status             the table only
    python3 bridge/connect.py --connect claude codex [--yes]
    python3 bridge/connect.py --disconnect codex [--yes]
    --repo PATH                                    point the hooks at another checkout (default: this one)

Every change is printed as a diff and asked about first (--yes skips the question, not the diff). The file is copied
to <file>.pet-backup-<time> before it is written and read back afterwards to confirm. Nothing else is touched, and
running a connect twice changes nothing the second time. Python 3.11 or later (tomllib).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRIDGE = os.environ.get("PET_NOTIFY_URL", "http://127.0.0.1:8765/event").rsplit("/", 1)[0]
LATER = [  # tools the pet knows but cannot connect yet: (label, how to tell it is here, note)
    ("Goose", "goose", "connector coming in #31"),
    ("Aider", "aider", "connector coming in #31"),
    ("Ollama", "ollama", "model server: its sessions show through the tool that drives it (Goose, Aider, "
                         "`ollama launch claude`); direct use is #32"),
]


class ConnectError(Exception):
    """A settings file this script will not edit, with the reason; the file is left alone."""


def script_ref(repo: Path, home: Path, name: str) -> str:
    """A hook script as a shell word: "$HOME/..." under the home folder, so the line reads the same on any account."""
    path = repo.resolve() / "bridge" / "hooks" / name
    if any(c in str(path) for c in '"$`\\\n'):
        raise ConnectError(f"{path} has a character a shell would read inside double quotes; move the checkout")
    try:
        return '"$HOME/' + str(path.relative_to(home.resolve())) + '"'
    except ValueError:
        return f'"{path}"'


class Tool:
    key = label = ""

    def __init__(self, home: Path | None = None, repo: Path = REPO):
        self.home = home or Path.home()
        self.repo = repo

    def path(self) -> Path:
        raise NotImplementedError

    def found(self) -> str:
        """Where the tool was seen, or "" when it is not on this Mac."""
        raise NotImplementedError

    def connected_in(self, text: str) -> bool:
        raise NotImplementedError

    def connect_text(self, text: str) -> str:
        raise NotImplementedError

    def disconnect_text(self, text: str) -> str:
        raise NotImplementedError

    def read(self) -> str:
        return self.path().read_text(encoding="utf-8") if self.path().exists() else ""

    def connected(self) -> bool:
        return self.connected_in(self.read())


class ClaudeCode(Tool):
    """Merges the three hook groups of hooks/claude-settings.json into ~/.claude/settings.json."""
    key, label = "claude", "Claude Code"
    MARK = "bridge/hooks/claude_hook.py"

    def path(self) -> Path:
        return self.home / ".claude" / "settings.json"

    def found(self) -> str:
        if self.path().exists():
            return "~/.claude/settings.json"
        return "~/.claude" if (self.home / ".claude").is_dir() or shutil.which("claude") else ""

    def _load(self, text: str) -> dict:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as e:
            raise ConnectError(f"{self.path()} is not valid JSON ({e})") from None
        if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
            raise ConnectError(f"{self.path()} is not a settings object with a hooks object")
        return data

    def _ours(self, hook) -> bool:
        return isinstance(hook, dict) and self.MARK in str(hook.get("command", ""))

    def _wanted(self) -> dict:
        snippet = json.loads((self.repo / "bridge" / "hooks" / "claude-settings.json").read_text(encoding="utf-8"))
        command = f"python3 {script_ref(self.repo, self.home, 'claude_hook.py')} 2>/dev/null || true"
        for groups in snippet["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = command
        return snippet["hooks"]

    def _has(self, groups, event: str) -> bool:
        return isinstance(groups, list) and any(
            isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(self._ours(h) for h in g["hooks"])
            for g in groups)

    def connected_in(self, text: str) -> bool:
        hooks = self._load(text).get("hooks", {})
        return all(self._has(hooks.get(event), event) for event in self._wanted())

    def connect_text(self, text: str) -> str:
        data = self._load(text)
        hooks = data.setdefault("hooks", {})
        for event, groups in self._wanted().items():
            have = hooks.setdefault(event, [])
            if not isinstance(have, list):
                raise ConnectError(f"hooks.{event} in {self.path()} is not a list")
            if not self._has(have, event):
                have.extend(groups)
        return text if self.connected_in(text) else json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def disconnect_text(self, text: str) -> str:
        data = self._load(text)
        hooks = data.get("hooks", {})
        changed = False
        for event in list(hooks):
            if not isinstance(hooks[event], list):
                continue
            kept = []
            for group in hooks[event]:
                inner = group.get("hooks") if isinstance(group, dict) else None
                if isinstance(inner, list) and any(self._ours(h) for h in inner):
                    changed = True
                    inner = [h for h in inner if not self._ours(h)]  # another hook sharing the group stays
                    if not inner:
                        continue
                    group = {**group, "hooks": inner}
                kept.append(group)
            hooks[event] = kept
            if not kept:
                del hooks[event]
        if "hooks" in data and not hooks:
            del data["hooks"]
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n" if changed else text


class CodexApp(Tool):
    """Puts codex_notify.py in front of the `notify` command in ~/.codex/config.toml and keeps the old one after
    --then, so whatever notify ran before (the Codex app sets its own) still runs with the same payload."""
    key, label = "codex", "Codex"
    MARK = "bridge/hooks/codex_notify.py"

    def path(self) -> Path:
        return self.home / ".codex" / "config.toml"

    def found(self) -> str:
        if self.path().exists():
            return "~/.codex/config.toml"
        apps = [a for a in ("/Applications/Codex.app", "/Applications/ChatGPT.app") if Path(a).exists()]
        return apps[0] if apps else ("codex on PATH" if shutil.which("codex") else "")

    def _notify(self, text: str) -> list | None:
        try:
            value = tomllib.loads(text).get("notify")
        except tomllib.TOMLDecodeError as e:
            raise ConnectError(f"{self.path()} is not valid TOML ({e})") from None
        if value is not None and not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
            raise ConnectError(f"notify in {self.path()} is not a list of strings")
        return value

    def connected_in(self, text: str) -> bool:
        value = self._notify(text)
        return bool(value) and any(x.endswith(self.MARK) for x in value[:2])

    def _with_notify(self, text: str, value: list | None) -> str:
        """`text` with its top-level notify line replaced by `value`, added at the top when missing, removed for
        None. Only a one-line `notify = [...]` is edited; anything fancier is refused rather than guessed at."""
        lines = text.splitlines(keepends=True)
        tables = next((i for i, line in enumerate(lines) if re.match(r"\s*\[", line)), len(lines))
        hits = [i for i in range(tables) if re.match(r"\s*notify\s*=", lines[i])]
        if len(hits) > 1 or (hits and not lines[hits[0]].rstrip().endswith("]")):
            raise ConnectError(f"notify in {self.path()} is not a single `notify = [...]` line; edit it by hand")
        new = [] if value is None else ["notify = " + json.dumps(value, ensure_ascii=False) + "\n"]
        if hits:
            lines[hits[0]:hits[0] + 1] = new
        else:
            lines[0:0] = new
        result = "".join(lines)
        if self._notify(result) != value:
            raise ConnectError(f"editing notify in {self.path()} did not give the expected value; left alone")
        return result

    def connect_text(self, text: str) -> str:
        if self.connected_in(text):
            return text
        old = self._notify(text)
        script = str(self.repo / "bridge" / "hooks" / "codex_notify.py")
        return self._with_notify(text, ["python3", script] + (["--then", *old] if old else []))

    def disconnect_text(self, text: str) -> str:
        if not self.connected_in(text):
            return text
        value = self._notify(text)
        rest = value[value.index("--then") + 1:] if "--then" in value else []
        return self._with_notify(text, rest or None)


def tools(home: Path | None = None, repo: Path = REPO) -> list[Tool]:
    return [ClaudeCode(home, repo), CodexApp(home, repo)]


def bridge_state() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(BRIDGE + "/pending", timeout=1) as resp:
            count = json.loads(resp.read() or b"{}").get("count", 0)
        return f"up at {BRIDGE}, {count} waiting"
    except Exception:
        return f"not answering at {BRIDGE}; events are lost until it runs (bridge/README.md)"


def state(tool: Tool) -> str:
    if not tool.found():
        return "not found"
    try:
        return "connected" if tool.connected() else "not connected"
    except ConnectError as e:
        return f"cannot read: {e}"


def show(items: list[Tool], out=print) -> None:
    out("Desk pet: connect your AI tools")
    out(f"Bridge: {bridge_state()}")
    out("")
    for i, tool in enumerate(items, 1):
        out(f"  {i}  {tool.label:<12} {state(tool):<14} {tool.found()}")
    for label, command, note in LATER:
        out(f"     {label:<12} {'found' if shutil.which(command) else 'not found':<14} {note}")
    out("     Anything else (Grok, Muse, a script): post with bridge/notify.py, see bridge/hooks/README.md")
    out("")


def change(tool: Tool, connect: bool, yes: bool, out=print, ask=input) -> bool:
    """Show the edit, ask, back up, write, read back. True when the tool ends up in the asked-for state."""
    path = tool.path()
    old = tool.read()
    try:
        new = tool.connect_text(old) if connect else tool.disconnect_text(old)
    except ConnectError as e:
        out(f"{tool.label}: {e}")
        return False
    verb = "connected" if connect else "disconnected"
    if new == old:
        out(f"{tool.label}: already {verb}, nothing to change.")
        return True
    out("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), str(path), f"{path} (new)")))
    if not yes and ask(f"Write this to {path}? [y/N] ").strip().lower() not in ("y", "yes"):
        out(f"{tool.label}: left alone.")
        return False
    tmp = path.with_name(path.name + ".pet-tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(new, encoding="utf-8")
        if path.exists():
            stamp = f"{path.name}.pet-backup-{datetime.now():%Y%m%d-%H%M%S}"
            backup, n = path.with_name(stamp), 1
            while backup.exists():  # two writes in one second keep both backups
                backup, n = path.with_name(f"{stamp}-{n}"), n + 1
            shutil.copy2(path, backup)
            shutil.copymode(path, tmp)  # config.toml is 0600; keep it that way
            out(f"Backup: {backup}")
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        out(f"{tool.label}: could not write {path} ({e}); it is unchanged.")
        return False
    ok = tool.connected() == connect
    out(f"{tool.label}: {verb}, takes effect in sessions started from now on." if ok else
        f"{tool.label}: wrote {path} but it does not read back as {verb}; the backup above has the old file.")
    return ok


def main(argv=None, home: Path | None = None, out=print, ask=input) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--status", action="store_true", help="print the table and stop")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--connect", nargs="+", metavar="TOOL", help="connect these (claude, codex)")
    action.add_argument("--disconnect", nargs="+", metavar="TOOL", help="remove what --connect added")
    parser.add_argument("--yes", action="store_true", help="write without asking; the diff is still printed")
    parser.add_argument("--repo", type=Path, default=REPO, help="checkout the hooks run from (default: this one)")
    args = parser.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    needed = ("claude_hook.py", "claude-settings.json", "codex_notify.py", "pethook.py")
    if not all((repo / "bridge" / "hooks" / name).is_file() for name in needed):
        out(f"{repo} is not an ESP-P4_Box checkout with bridge/hooks in it.")
        return 2
    items = tools(home, repo)
    show(items, out)
    if args.status:
        return 0
    by_key = {t.key: t for t in items}
    if args.connect or args.disconnect:
        names = args.connect or args.disconnect
        unknown = [n for n in names if n not in by_key]
        if unknown:
            out(f"Unknown tool: {', '.join(unknown)}. Known: {', '.join(by_key)}.")
            return 2
        results = []
        for name in dict.fromkeys(names):
            if args.connect and not by_key[name].found():
                out(f"{by_key[name].label}: not found on this Mac, so nothing to connect.")
                results.append(False)
                continue
            results.append(change(by_key[name], bool(args.connect), args.yes, out, ask))
        return 0 if all(results) else 1
    try:
        picks = ask("Pick by number to connect, or to disconnect one that is connected (e.g. 1 2), "
                    "'all', or Enter to quit: ").lower().split()
    except EOFError:
        return 0
    every = picks == ["all"]
    bad = [p for p in picks if not every and not (p.isdigit() and 0 < int(p) <= len(items))]
    if bad:
        out(f"Not a choice: {' '.join(bad)}. Use the numbers 1 to {len(items)}, or 'all'.")
        return 2
    chosen = items if every else [items[int(p) - 1] for p in dict.fromkeys(picks)]
    results = []
    for tool in chosen:
        if not tool.found():
            out(f"{tool.label}: not found on this Mac, skipped.")
            continue
        results.append(change(tool, every or state(tool) != "connected", args.yes, out, ask))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
