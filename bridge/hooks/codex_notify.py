#!/usr/bin/env python3
"""Codex `notify` command: Codex calls it with one JSON argument when a turn completes.

    notify = ["python3", "/Users/you/Desktop/ESP-P4_Box/bridge/hooks/codex_notify.py"]

Codex allows one notify command. If yours is already taken (on this Mac it points at the Codex app's own
Computer Use client), keep it working by chaining it after `--then`; Codex appends the payload as the last
argument, and this script passes it on unchanged:

    notify = ["python3", ".../codex_notify.py", "--then", "/path/to/OriginalCommand", "its-argument"]

Codex only sends `agent-turn-complete` this way, so only `done` events arrive through notify; approvals and
clearing need the lifecycle hooks (codex_hook.py). Always exits 0, prints nothing.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "codex"
TURN_DONE_TYPES = {"agent-turn-complete", "agent_turn_complete", "turn-ended"}


def _get(payload: dict, *keys):
    for key in keys:  # Codex uses hyphenated keys; be kind to the underscore spelling too
        if key in payload:
            return payload[key]
    return None


def build_event(payload: dict):
    """The pet event for one notify payload, or None when nothing should be sent."""
    if payload.get("type") not in TURN_DONE_TYPES:
        return None
    title = pethook.first_line(_get(payload, "last-assistant-message", "last_assistant_message"), 80) or "finished"
    return {"v": 1, "source": SOURCE, "kind": "done", "session": pethook.folder_name(payload.get("cwd"), SOURCE),
            "title": title, "detail": ""}


def split_args(argv: list):
    """(payload JSON text or None, chained command or []) from the arguments Codex and the config give us."""
    if not argv:
        return None, []
    if argv[0] == "--then":
        if len(argv) <= 2:  # a chain but no payload: nothing to pass on
            return None, argv[1:]
        return argv[-1], argv[1:-1]
    return argv[-1], []


def main(argv: list) -> int:
    try:
        raw, chain = split_args(argv)
        if chain and raw is not None:
            subprocess.run(chain + [raw], check=False, timeout=10, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload = json.loads(raw) if raw else None
        event = build_event(payload) if isinstance(payload, dict) else None
        if event:
            pethook.post(event)
    except Exception:  # never fail the caller
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
