#!/usr/bin/env python3
"""Connect the AI tools on this computer (macOS or Windows) to the desk pet, the way `goose configure` offers its
integrations. On Windows, `py bridge\\connect.py ...` in place of `python3 bridge/connect.py ...`.

    python3 bridge/connect.py                      menu: what is found, what is connected; pick to connect or undo
    python3 bridge/connect.py --status             the table only
    python3 bridge/connect.py --connect claude codex goose aider [--yes]
    python3 bridge/connect.py --disconnect codex [--yes]
    --repo PATH                                    point the hooks at another checkout (default: this one)
    python3 bridge/connect.py --share              on the home Mac: the command to run on your other machines
    python3 bridge/connect.py --bridge URL --token TOKEN   on another machine: post to the home Mac over Tailscale
    python3 bridge/connect.py --disconnect bridge  that machine: back to posting to its own bridge

Every change is printed as a diff and asked about first (--yes skips the question, not the diff). The file is copied
to <file>.pet-backup-<time> before it is written and read back afterwards to confirm. Nothing else is touched, and
running a connect twice changes nothing the second time. Python 3.11 or later (tomllib).
"""
from __future__ import annotations

import argparse
import difflib
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path, PureWindowsPath

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pethook  # noqa: E402  (where this machine's hooks post, and with which token)
from petbrain import tailnet  # noqa: E402  (no MQTT needed: finding this Mac's Tailscale name and token rules)
LATER = [  # tools the pet knows but cannot connect yet: (label, how to tell it is here, note)
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


def on_windows() -> bool:
    return os.name == "nt"


def short_path(path: str) -> str:
    """The 8.3 form of an existing Windows path (no spaces), or the path itself when Windows has none."""
    import ctypes
    windll = getattr(ctypes, "windll", None)  # Windows only
    if " " not in path or windll is None:
        return path
    buf = ctypes.create_unicode_buffer(1024)
    n = windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def checkout(repo: Path) -> str:
    """The checkout's absolute path, as Windows writes it."""
    return str(repo.resolve())


def hook_script(repo: Path, name: str) -> str:
    """A hook script's absolute path on Windows, with backslashes."""
    return str(PureWindowsPath(checkout(repo)) / "bridge" / "hooks" / name)


def hook_command(repo: Path, home: Path, name: str, quotes: bool = True) -> str:
    """The shell line that runs a hook script.

    macOS: python3 "$HOME/...", as in hooks/README.md. Windows (#39): the Python running this script and the hook by
    absolute path, since python3 is often missing there. With `quotes` (Aider, whose `shell=True` is `cmd /c "..."`):
    both quoted, with forward slashes. Without (Goose, whose `cmd /C` goes through Rust's argument quoting and breaks
    on any quote in the line): backslashes, no quotes, and so no spaces; 8.3 names stand in for the Python and the
    checkout folder where Windows has them, and bridge\\hooks\\<name> stays readable so the hook is still ours."""
    if not on_windows():
        return f"python3 {script_ref(repo, home, name)}"
    if quotes:
        words = [PureWindowsPath(sys.executable).as_posix(), PureWindowsPath(hook_script(repo, name)).as_posix()]
        bad = '"%$`!^&|<>\n'
    else:
        base = PureWindowsPath(short_path(checkout(repo)))
        words = [str(PureWindowsPath(short_path(sys.executable))), str(base / "bridge" / "hooks" / name)]
        bad = ' "%&|<>^(),;=\n'
    for word in words:
        if any(c in word for c in bad):
            raise ConnectError(f"{word} has a space or a character the command line would misread; move it to a "
                               "folder without one (or reinstall Python there)")
    return " ".join(f'"{w}"' for w in words) if quotes else " ".join(words)


def mentions(text: str, mark: str) -> bool:
    """True when `text` names the hook in `mark` (bridge/hooks/x.py), with / or \\ between folders."""
    return mark in str(text).replace("\\", "/")


def quiet() -> str:
    """What follows a hook command so a missing Python never shows as an error: sh only; cmd has no `true`."""
    return "" if on_windows() else " 2>/dev/null || true"


class Tool:
    key = label = script = ""  # script: the hook in bridge/hooks/ the tool will run

    def __init__(self, home: Path | None = None, repo: Path = REPO):
        self.home = home or Path.home()
        self.repo = repo

    def path(self) -> Path:
        raise NotImplementedError

    def found(self) -> str:
        """Where the tool was seen, or "" when it is not on this computer."""
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

    def redact(self, text: str) -> str:
        """The text as it may be printed; a file holding a secret hides it."""
        return text


class ClaudeCode(Tool):
    """Merges the three hook groups of hooks/claude-settings.json into ~/.claude/settings.json."""
    key, label, script = "claude", "Claude Code", "claude_hook.py"
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
        if not isinstance(hook, dict):
            return False
        args = hook.get("args") if isinstance(hook.get("args"), list) else []
        return any(mentions(x, self.MARK) for x in [hook.get("command", ""), *args])

    def _snippet(self) -> dict:
        return json.loads((self.repo / "bridge" / "hooks" / "claude-settings.json").read_text(encoding="utf-8"))

    def _wanted(self) -> dict:
        snippet = self._snippet()
        if on_windows():  # exec form: no shell, so neither Git Bash nor PowerShell has to parse a path
            run = {"command": PureWindowsPath(sys.executable).as_posix(),  # forward slashes: nothing to unescape
                   "args": [PureWindowsPath(hook_script(self.repo, "claude_hook.py")).as_posix()]}
        else:
            run = {"command": hook_command(self.repo, self.home, "claude_hook.py") + quiet()}
        for groups in snippet["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    hook.update(run)
        return snippet["hooks"]

    def _has(self, groups, event: str) -> bool:
        return isinstance(groups, list) and any(
            isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(self._ours(h) for h in g["hooks"])
            for g in groups)

    def connected_in(self, text: str) -> bool:
        hooks = self._load(text).get("hooks", {})
        return all(self._has(hooks.get(event), event) for event in self._snippet()["hooks"])

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
    key, label, script = "codex", "Codex", "codex_notify.py"
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
        return bool(value) and any(x.replace("\\", "/").endswith(self.MARK) for x in value[:2])

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
        if on_windows():  # no shell either way
            run = [sys.executable, hook_script(self.repo, "codex_notify.py")]
        else:
            run = ["python3", str(self.repo / "bridge" / "hooks" / "codex_notify.py")]
        return self._with_notify(text, run + (["--then", *old] if old else []))

    def disconnect_text(self, text: str) -> str:
        if not self.connected_in(text):
            return text
        value = self._notify(text)
        rest = value[value.index("--then") + 1:] if "--then" in value else []
        return self._with_notify(text, rest or None)


class LineConfig(Tool):
    """A YAML settings file edited one top-level `key: value` line at a time, the only edit Goose and Aider need, so no
    YAML library is required. Lines added here end with MARK; disconnect drops those and any KEY line naming SCRIPT."""
    MARK = "# desk pet: bridge/connect.py"
    KEY = SCRIPT = ""

    def command(self) -> str:
        raise NotImplementedError

    def extra(self, lines: list[str]) -> list[str]:
        """Other lines to add with the hook line."""
        return []

    @staticmethod
    def hits(lines: list[str], key: str) -> list[int]:
        return [i for i, line in enumerate(lines) if re.match(rf"""(["']?){re.escape(key)}\1\s*:""", line)]

    def found(self) -> str:
        if self.path().exists():
            try:
                return "~/" + self.path().relative_to(self.home).as_posix()
            except ValueError:
                return str(self.path())
        return shutil.which(self.key) or ""

    def connected_in(self, text: str) -> bool:
        lines = text.splitlines(keepends=True)
        return any(mentions(lines[i], self.SCRIPT) for i in self.hits(lines, self.KEY))

    def connect_text(self, text: str) -> str:
        if self.connected_in(text):
            return text
        lines = text.splitlines(keepends=True)
        extra = self.extra(lines)  # may refuse first, with the more useful reason
        hook = self.hits(lines, self.KEY)
        taken = [i for i in hook if not mentions(lines[i], self.SCRIPT)]
        if taken:
            raise ConnectError(f"{self.path()} already sets `{lines[taken[0]].strip()}`; remove it or edit by hand")
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        quoted = "'" + self.command().replace("'", "''") + "'"  # YAML single quotes: only ' itself is escaped
        ours = [] if len(taken) < len(hook) else [f"{self.KEY}: {quoted}  {self.MARK}\n"]  # ours already there
        return "".join(lines + extra + ours)

    def disconnect_text(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        ours = set(i for i in self.hits(lines, self.KEY) if mentions(lines[i], self.SCRIPT))
        kept = [line for i, line in enumerate(lines) if i not in ours and not line.rstrip().endswith(self.MARK)]
        return text if len(kept) == len(lines) else "".join(kept)


class Goose(LineConfig):
    """GOOSE_STATUS_HOOK in Goose's config.yaml runs goose_hook.py (Goose CLI sessions): ~/.config/goose on macOS,
    %APPDATA%\\Block\\goose\\config on Windows."""
    key, label, script = "goose", "Goose", "goose_hook.py"
    KEY, SCRIPT = "GOOSE_STATUS_HOOK", "bridge/hooks/goose_hook.py"

    def path(self) -> Path:
        if on_windows():
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else self.home / "AppData" / "Roaming"
            return base / "Block" / "goose" / "config" / "config.yaml"
        return self.home / ".config" / "goose" / "config.yaml"

    def command(self) -> str:
        return hook_command(self.repo, self.home, "goose_hook.py", quotes=False)  # Goose appends the status word


class Aider(LineConfig):
    """notifications-command in ~/.aider.conf.yml runs aider_hook.py; it needs notifications: true as well."""
    key, label, script = "aider", "Aider", "aider_hook.py"
    KEY, SCRIPT = "notifications-command", "bridge/hooks/aider_hook.py"

    def path(self) -> Path:
        return self.home / ".aider.conf.yml"

    def command(self) -> str:
        return hook_command(self.repo, self.home, "aider_hook.py") + quiet()

    ON = ("true", "yes", "on", "1")  # what Aider's config parser reads as on, quoted or not

    def notifications(self, lines: list[str]):
        """True or False from the file's `notifications` line, None when there is none."""
        at = self.hits(lines, "notifications")
        if not at:
            return None
        value = lines[at[0]].split(":", 1)[1].split("#", 1)[0].strip().strip("'\"").lower()
        return value in self.ON

    def connected_in(self, text: str) -> bool:
        return super().connected_in(text) and self.notifications(text.splitlines(keepends=True)) is True

    def extra(self, lines: list[str]) -> list[str]:
        on = self.notifications(lines)
        if on is None:
            return [f"notifications: true  {self.MARK}\n"]
        if not on:
            line = lines[self.hits(lines, "notifications")[0]].strip()
            raise ConnectError(f"{self.path()} has `{line}`; the hook needs notifications on, "
                               "so change that line first")
        return []


class RemoteBridge(Tool):
    """~/.config/desk-pet/bridge.json on a machine that posts to the home Mac over Tailscale (#37): the bridge's
    tailnet URL and its token, read by every hook through pethook.target(). Written 0600; the token never printed."""
    key, label, script = "bridge", "Home bridge", "pethook.py"
    private = True

    def __init__(self, home: Path | None = None, repo: Path = REPO, url: str = "", token: str = ""):
        super().__init__(home, repo)
        self.url, self.token = url, token

    def path(self) -> Path:
        return self.home / ".config" / "desk-pet" / "bridge.json"

    def found(self) -> str:
        return "~/.config/desk-pet/bridge.json"

    @staticmethod
    def _data(text: str) -> dict:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            return {}  # replaced on connect; the backup keeps it
        return data if isinstance(data, dict) else {}

    def connected_in(self, text: str) -> bool:
        data = self._data(text)
        if not data.get("url"):
            return False
        return not self.url or (data.get("url") == self.url and data.get("token") == self.token)

    def connect_text(self, text: str) -> str:
        if self.connected_in(text):
            return text
        return json.dumps({"url": self.url, "token": self.token}, indent=2) + "\n"

    def disconnect_text(self, text: str) -> str:
        return "{}\n" if self._data(text) else text

    def redact(self, text: str) -> str:
        return re.sub(r'("token"\s*:\s*")(?:[^"\\]|\\.)*(")', r"\1<hidden>\2", text)  # JSON escapes too


TOKEN_RE = re.compile(r"[!-~]{%d,}" % tailnet.MIN_TOKEN)  # what the bridge takes and an HTTP header can carry


def private_host(host: str) -> bool:
    """A Tailscale name or address, or this machine: the only places a token may go over plain http."""
    if host.endswith(".ts.net") or host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or tailnet.in_tailnet(host)


def check_url(url: str) -> str:
    """The bridge URL as stored: http(s), a Tailscale host, a port, and the /event path; ConnectError otherwise."""
    example = "http://your-mac.your-tailnet.ts.net:8765/event"
    parts = urllib.parse.urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        raise ConnectError(f"{url!r} has a port that is not a number from 1 to 65535") from None
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment
            or parts.username is not None or parts.password is not None or port == 0):
        raise ConnectError(f"{url!r} is not a bridge address like {example}")
    if not private_host(parts.hostname.lower().rstrip(".")):
        raise ConnectError(f"{parts.hostname} is not a Tailscale address. Use the home Mac's Tailscale name (like "
                           f"{example}) or its 100.x address; anywhere else the token would travel unencrypted.")
    path = parts.path.rstrip("/")
    if path in ("", "/event"):
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/event", "", ""))
    raise ConnectError(f"{url!r} should end in /event")


def tools(home: Path | None = None, repo: Path = REPO) -> list[Tool]:
    return [ClaudeCode(home, repo), CodexApp(home, repo), Goose(home, repo), Aider(home, repo)]


def probe(url: str, token: str, timeout: float = 2) -> str:
    """How the bridge behind an /event URL answers GET /pending with this token, in a few words."""
    base = url.rsplit("/", 1)[0]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with pethook.opener().open(urllib.request.Request(base + "/pending", headers=headers),
                                   timeout=timeout) as resp:
            count = json.loads(resp.read() or b"{}").get("count", 0)
        return f"up at {base}, {count} waiting"
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 401:
            return f"answering at {base} but refusing this machine's token"
        if 300 <= e.code < 400:
            return f"redirecting {base} elsewhere; not followed, so the token went nowhere else"
        if e.code == 403:
            return f"answering at {base} but refusing this machine's address (not on Tailscale?)"
        return f"answering at {base} with HTTP {e.code}"
    except Exception:
        return f"not answering at {base}; events are lost until it runs and this machine can reach it"


def bridge_state(home: Path | None = None) -> str:
    url, token = pethook.target(os.environ, str((home or Path.home()) / ".config" / "desk-pet" / "bridge.json"))
    return probe(url, token)


def state(tool: Tool) -> str:
    if not tool.found():
        return "not found"
    try:
        return "connected" if tool.connected() else "not connected"
    except ConnectError as e:
        return f"cannot read: {e}"


def show(items: list[Tool], out=print) -> None:
    out("Desk pet: connect your AI tools")
    out(f"Bridge: {bridge_state(items[0].home if items else None)}")
    out("")
    for i, tool in enumerate(items, 1):
        out(f"  {i}  {tool.label:<12} {state(tool):<14} {tool.found()}")
    for label, command, note in LATER:
        out(f"     {label:<12} {'found' if shutil.which(command) else 'not found':<14} {note}")
    out("     Anything else (Grok, Muse, a script): post with bridge/notify.py, see bridge/hooks/README.md")
    out("")


def write_private(path: Path, data: bytes) -> None:
    """Create `path` (replacing any stale copy) as owner-only before a single byte goes in."""
    path.unlink(missing_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)


def change(tool: Tool, connect: bool, yes: bool, out=print, ask=input) -> bool:
    """Show the edit, ask, back up, write, read back. True when the tool ends up in the asked-for state."""
    path = tool.path()
    hook = tool.repo / "bridge" / "hooks" / tool.script
    if connect and not hook.is_file():
        out(f"{tool.label}: {hook} is not there; pull main in that checkout or pass --repo.")
        return False
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
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), str(path), f"{path} (new)")
    out(tool.redact("".join(diff)))
    try:
        answer = "y" if yes else ask(f"Write this to {path}? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        out(f"{tool.label}: left alone.")
        return False
    tmp = path.with_name(path.name + ".pet-tmp")
    try:
        private = getattr(tool, "private", False)
        path.parent.mkdir(parents=True, exist_ok=True)
        if private:  # holds a token: owner only from the first byte, whatever the umask or the old file said
            path.parent.chmod(0o700)  # ~/.config/desk-pet is ours alone
            write_private(tmp, new.encode("utf-8"))
        else:
            tmp.write_text(new, encoding="utf-8")
        if path.exists():
            stamp = f"{path.name}.pet-backup-{datetime.now():%Y%m%d-%H%M%S}"
            backup, n = path.with_name(stamp), 1
            while backup.exists():  # two writes in one second keep both backups
                backup, n = path.with_name(f"{stamp}-{n}"), n + 1
            if private:
                write_private(backup, path.read_bytes())
            else:
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


def read_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")  # as the bridge reads it
    return values


def tailnet_name(run=subprocess.run) -> str | None:
    """This machine's MagicDNS name (johns-macbook-pro.tail1234.ts.net), else its Tailscale IPv4, else None."""
    for cli in tailnet.CLI:
        path = shutil.which(cli) or (cli if cli.startswith("/") and Path(cli).exists() else None)
        if not path:
            continue
        try:
            out = run([path, "status", "--json"], capture_output=True, text=True, timeout=5)
            name = json.loads(out.stdout or "{}").get("Self", {}).get("DNSName", "").rstrip(".")
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            continue
        if name:
            return name
    return tailnet.detect_ip(run)


def share(repo: Path, out=print, name=tailnet_name) -> int:
    """On the home Mac: the one line to run on another machine, with this bridge's tailnet address and token."""
    env = read_env(repo / "bridge" / ".env")
    token = env.get("PET_TAILNET_TOKEN", "")
    if not tailnet.wanted(env.get("PET_TAILNET")) or not TOKEN_RE.fullmatch(token):
        out("Access from your other machines is off on this Mac. Run `python3 bridge/setup.py` to turn it on safely, "
            "or set PET_TAILNET=on and a PET_TAILNET_TOKEN of 32+ random characters (no spaces) in bridge/.env, then "
            "restart the bridge.")
        return 1
    host = name()
    if not host:
        out("Tailscale is not running on this Mac, or it is logged out. Open the Tailscale app, sign in, try again.")
        return 1
    url = f"http://{host}:{env.get('PET_HTTP_PORT', '8765')}/event"
    out(f"This bridge: {probe(url, token)}")
    out("On each of your other machines, with this repo cloned and Tailscale signed in to the same account, run:")
    out("")
    out(f"  python3 bridge/connect.py --bridge {shlex.quote(url)} --token {shlex.quote(token)}")
    out("")
    out("(On Windows, `py` instead of `python3`.) The token is the key to your pet's bridge: paste it only on your own")
    out("machines, never in a chat, an issue or a screenshot. To change it, set a new PET_TAILNET_TOKEN and run this "
        "again.")
    return 0


def main(argv=None, home: Path | None = None, out=print, ask=input) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--status", action="store_true", help="print the table and stop")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--connect", nargs="+", metavar="TOOL", help="connect these (claude, codex, goose, aider)")
    action.add_argument("--disconnect", nargs="+", metavar="TOOL", help="remove what --connect added")
    parser.add_argument("--yes", action="store_true", help="write without asking; the diff is still printed")
    parser.add_argument("--repo", type=Path, default=REPO, help="checkout the hooks run from (default: this one)")
    action.add_argument("--share", action="store_true", help="on the home Mac: print the command for other machines")
    action.add_argument("--bridge", metavar="URL", help="post to the home Mac's bridge at this tailnet URL")
    parser.add_argument("--token", default="", help="the token that goes with --bridge (from --share on the Mac)")
    args = parser.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    needed = ("claude_hook.py", "claude-settings.json", "codex_notify.py", "pethook.py")
    if not all((repo / "bridge" / "hooks" / name).is_file() for name in needed):
        out(f"{repo} is not an ESP-P4_Box checkout with bridge/hooks in it.")
        return 2
    if args.share:
        return share(repo, out)
    if args.bridge:
        try:
            url = check_url(args.bridge)
        except ConnectError as e:
            out(str(e))
            return 2
        if not TOKEN_RE.fullmatch(args.token):
            out(f"--token must be the {tailnet.MIN_TOKEN}+ character token, with no spaces, that "
                "`connect.py --share` printed on the home Mac.")
            return 2
        ok = change(RemoteBridge(home, repo, url, args.token), True, args.yes, out, ask)
        if ok:
            out(f"Bridge: {probe(url, args.token)}")
        return 0 if ok else 1
    items = tools(home, repo)
    show(items, out)
    if args.status:
        return 0
    by_key = {t.key: t for t in items}
    remote = args.disconnect and "bridge" in args.disconnect and RemoteBridge(home, repo)
    if remote:  # back to this machine's own loopback bridge
        by_key["bridge"] = remote
        remote_had_url = remote.connected()
    if args.connect or args.disconnect:
        names = args.connect or args.disconnect
        unknown = [n for n in names if n not in by_key]
        if unknown:
            out(f"Unknown tool: {', '.join(unknown)}. Known: {', '.join(by_key)}.")
            return 2
        results = []
        for name in dict.fromkeys(names):
            if args.connect and not by_key[name].found():
                out(f"{by_key[name].label}: not found on this computer, so nothing to connect.")
                results.append(False)
                continue
            results.append(change(by_key[name], bool(args.connect), args.yes, out, ask))
            if name == "bridge" and remote_had_url and results[-1]:
                out("The old token stays in the owner-only backup above; delete that file if you are done with it.")
        return 0 if all(results) else 1
    try:
        picks = ask("Pick by number to connect, or to disconnect one that is connected (e.g. 1 2), "
                    "'all', or Enter to quit: ").lower().split()
    except EOFError:
        return 0
    every = picks == ["all"]
    bad = [p for p in picks if not every and not (p.isascii() and p.isdecimal() and 0 < int(p) <= len(items))]
    if bad:
        out(f"Not a choice: {' '.join(bad)}. Use the numbers 1 to {len(items)}, or 'all'.")
        return 2
    chosen = items if every else [items[n - 1] for n in dict.fromkeys(int(p) for p in picks)]
    results = []
    for tool in chosen:
        if not tool.found():
            out(f"{tool.label}: not found on this computer, skipped.")
            continue
        results.append(change(tool, every or state(tool) != "connected", args.yes, out, ask))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
