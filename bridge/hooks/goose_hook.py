#!/usr/bin/env python3
"""Goose `GOOSE_STATUS_HOOK`: the Goose CLI runs `<hook> thinking` when a prompt is sent and `<hook> waiting` when it
is back at its prompt (`run_status_hook` in crates/goose-cli/src/session/, aaif-goose/goose), through `sh -c`, in
the session's folder, with stdout and stderr thrown away and without waiting for it. In ~/.config/goose/config.yaml:

    GOOSE_STATUS_HOOK: 'python3 "$HOME/Desktop/ESP-P4_Box/bridge/hooks/goose_hook.py"'

No `2>/dev/null || true` after it: Goose appends the status word to the command text, so anything after the script
would swallow it. `thinking` posts `cleared` and `waiting` posts `done`, keyed by the folder like the other hooks.
Goose also says `waiting` once at startup, before anything was asked, so a `waiting` only counts after a `thinking`
in the same folder, remembered by a marker file in the temp folder. Always exits 0, prints nothing.
"""
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "goose"


def marker(cwd: str) -> str:
    return os.path.join(tempfile.gettempdir(), "pet-goose-" + hashlib.sha1(cwd.encode("utf-8", "replace")).hexdigest())


def build_event(status: str, cwd: str):
    """The pet event for one status call, or None; keeps the marker that tells a real turn from the startup call."""
    session = pethook.folder_name(cwd, SOURCE)
    if status == "thinking":
        with open(marker(cwd), "w"):
            pass
        return {"v": 1, "source": SOURCE, "kind": "cleared", "session": session}
    if status == "waiting":
        try:
            os.remove(marker(cwd))
        except FileNotFoundError:
            return None  # the startup call: nothing was asked yet
        return {"v": 1, "source": SOURCE, "kind": "done", "session": session, "title": "waiting for you", "detail": ""}
    return None


def main(argv: list) -> int:
    try:
        event = build_event(argv[-1] if argv else "", os.getcwd())
        if event:
            pethook.post(event)
    except Exception:  # never fail the caller
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
