#!/usr/bin/env python3
"""Claude Code hook: turns Notification, Stop and UserPromptSubmit into pet events.

Claude Code runs this with the hook payload as JSON on stdin (settings snippet: bridge/hooks/claude-settings.json).

    Notification      -> needs_input   (permission prompts and questions; see NEEDS_INPUT_TYPES)
    Stop              -> done          (title: first line of the last assistant message)
    UserPromptSubmit  -> cleared       (John answered in that session, so its bubble goes away)
    anything else     -> nothing is sent (SessionEnd on purpose: a one-shot `claude -p` run would clear its own
                                          `done` before anyone saw it; bubbles of closed sessions go with a tap)

The session name is the last component of the project folder (cwd). Claude Code's session title is not used:
it reaches only the UserPromptSubmit payload, never Stop or Notification, so keying by it would leave bubbles
that never clear. Prints nothing on stdout (Claude Code feeds hook stdout back into the session on some events),
swallows every error and always exits 0 within about a second, so a missing bridge can never block or slow a
session.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "claude"
# notification_type values that mean "a person has to decide something". idle_prompt is left out: it fires about
# a minute after every finished turn and would turn each `done` into `needs_input`. auth_success,
# elicitation_complete, elicitation_response, agent_completed and the quota_* types are noise for a desk pet.
NEEDS_INPUT_TYPES = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
# UserPromptSubmit `source` values that are a person typing (the field is optional; wakeups of /loop, schedules
# and polls carry other values once Claude Code sends it)
TYPED_SOURCES = (None, "user", "sdk")


def session_name(payload: dict) -> str:
    return pethook.folder_name(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR"), SOURCE)


def build_event(payload: dict):
    """The pet event for one hook payload, or None when nothing should be sent."""
    event = payload.get("hook_event_name")
    if event == "Notification":
        ntype = payload.get("notification_type")
        if ntype and ntype not in NEEDS_INPUT_TYPES:
            return None
        kind = "needs_input"
        heading, message = pethook.first_line(payload.get("title"), 80), pethook.first_line(payload.get("message"), 200)
        if heading:
            title, detail = heading, message
        else:  # no separate title: the message is the title, and repeating it as detail says nothing
            title, detail = message[:80] or "needs your input", ""
    elif event == "Stop":
        kind, title, detail = "done", pethook.first_line(payload.get("last_assistant_message"), 80) or "finished", ""
    elif event == "UserPromptSubmit":
        if payload.get("source") not in TYPED_SOURCES:
            return None
        kind, title, detail = "cleared", "", ""
    else:
        return None
    return {"v": 1, "source": SOURCE, "kind": kind, "session": session_name(payload), "title": title,
            "detail": detail}


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
