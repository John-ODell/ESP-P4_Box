#!/usr/bin/env python3
"""Aider `notifications-command`: Aider runs it through the shell, with no arguments, in the folder it was started in,
whenever it is waiting for John after the model worked: a reply is ready, or it asks a yes/no question (`ring_bell`
in aider/io.py). Aider waits for it, so it must be quick. In ~/.aider.conf.yml, next to `notifications: true`:

    notifications-command: 'python3 "$HOME/Desktop/ESP-P4_Box/bridge/hooks/aider_hook.py" 2>/dev/null || true'

Posts `done` for the folder. Aider has no hook for a typed prompt, so the bubble stays until the next `done` from
that folder replaces it or a tap clears it. Always exits 0, prints nothing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "aider"


def build_event(cwd: str) -> dict:
    return {"v": 1, "source": SOURCE, "kind": "done", "session": pethook.folder_name(cwd, SOURCE),
            "title": "waiting for you", "detail": ""}


def main() -> int:
    try:
        pethook.post(build_event(os.getcwd()))
    except Exception:  # never fail the caller
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
