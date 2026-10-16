#!/usr/bin/env python3
"""Claude Code hook: turns Notification, Stop and UserPromptSubmit into pet events.

Claude Code runs this with the hook payload as JSON on stdin (settings snippet: bridge/hooks/claude-settings.json).

    Notification      -> needs_input   (permission prompts, idle prompts, questions; see NEEDS_INPUT_TYPES)
    Stop              -> done          (title: first line of the last assistant message)
    UserPromptSubmit  -> cleared       (John answered in that session, so its bubble goes away)
    anything else     -> nothing is sent (SessionEnd on purpose: a one-shot `claude -p` run would clear its own
                                          `done` before anyone saw it; bubbles of closed sessions go with a tap)

The session name is Claude Code's session title when one is set, otherwise the project folder name (cwd).
Prints nothing on stdout (Claude Code feeds hook stdout back into the session on some events), swallows every
error and always exits 0 within about a second, so a missing bridge can never block or slow a session.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pethook  # noqa: E402

SOURCE = "claude"
# notification_type values that mean "a person has to do something". auth_success, elicitation_complete,
# elicitation_response, agent_completed and the quota_* types are noise for a desk pet.
NEEDS_INPUT_TYPES = {"permission_prompt", "idle_prompt", "elicitation_dialog", "elicitation_url_dialog",
                     "agent_needs_input"}


def session_name(payload: dict) -> str:
    title = payload.get("session_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return pethook.folder_name(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR"), SOURCE)


def build_event(payload: dict):
    """The pet event for one hook payload, or None when nothing should be sent."""
    event = payload.get("hook_event_name")
    if event == "Notification":
        ntype = payload.get("notification_type")
        if ntype and ntype not in NEEDS_INPUT_TYPES:
            return None
        title = (pethook.first_line(payload.get("title"), 80) or pethook.first_line(payload.get("message"), 80)
                 or "needs your input")
        kind, detail = "needs_input", pethook.first_line(payload.get("message"), 200)
    elif event == "Stop":
        kind, title, detail = "done", pethook.first_line(payload.get("last_assistant_message"), 80) or "finished", ""
    elif event == "UserPromptSubmit":
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
