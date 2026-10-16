"""Shared bits for the hook scripts in this folder: text trimming, the session name and the POST to the bridge.

Each script puts its own folder on sys.path and imports this, so the three of them stay single-purpose and the
bridge's Python package is not needed on the hook's import path.
"""
import http.client
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("PET_NOTIFY_URL", "http://127.0.0.1:8765/event")
TIMEOUT = 1.0  # seconds; localhost answers in milliseconds, and a hook must never hold a session


def first_line(text, limit: int) -> str:
    """The first non-empty line of `text`, cut to `limit` characters; "" for anything that is not a string."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def folder_name(cwd, fallback: str) -> str:
    """The last path component of `cwd`, or `fallback` when there is none."""
    if isinstance(cwd, str) and cwd.strip():
        name = os.path.basename(os.path.normpath(cwd.strip()))
        if name and name not in (".", "/"):
            return name
    return fallback


def post(event: dict, url: str = URL, timeout: float = TIMEOUT) -> bool:
    """POST one event to the bridge. False, never an exception, when it is not reachable, sick or says no."""
    body = json.dumps(event).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the bridge is on this machine
    try:
        with local.open(req, timeout=timeout) as resp:  # the body is not needed, and reading it would wait on it
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        return False


def read_stdin_json():
    """The hook payload Claude Code or Codex writes on stdin, or None when it is not a JSON object."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return None
    return payload if isinstance(payload, dict) else None
