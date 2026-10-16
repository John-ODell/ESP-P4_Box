#!/usr/bin/env python3
"""Codex lifecycle hook: turns PermissionRequest, Stop and UserPromptSubmit into pet events.

Codex runs lifecycle hooks (hooks.json next to config.toml, or an inline [hooks] table) with the payload as JSON
on stdin; snippet: bridge/hooks/codex-hooks.json. Codex only runs a command hook after it has been trusted
(`/hooks` in the Codex CLI).

    PermissionRequest -> needs_input  (title: what the tool wants to do, or the tool's name)
    Stop              -> done         (title: first line of the last assistant message)
    UserPromptSubmit  -> cleared
    anything else     -> nothing is sent (SessionEnd on purpose, as in claude_hook.py)

The session name is the project folder (cwd); Codex sends no session title. Prints nothing on stdout, swallows
every error and always exits 0 within about a second.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "codex"


def build_event(payload: dict):
    """The pet event for one hook payload, or None when nothing should be sent."""
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        title = pethook.first_line(tool_input.get("description"), 80) or f"{tool or 'a tool'} needs approval"
        command = tool_input.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        kind, detail = "needs_input", pethook.first_line(command, 200)
    elif event == "Stop":
        kind, title, detail = "done", pethook.first_line(payload.get("last_assistant_message"), 80) or "finished", ""
    elif event == "UserPromptSubmit":
        kind, title, detail = "cleared", "", ""
    else:
        return None
    return {"v": 1, "source": SOURCE, "kind": kind, "session": pethook.folder_name(payload.get("cwd"), SOURCE),
            "title": title, "detail": detail}


def main() -> int:
    try:
        payload = pethook.read_stdin_json()
        event = build_event(payload) if payload else None
        if event:
            pethook.post(event)
    except Exception:  # a hook must never fail the session it runs in
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
