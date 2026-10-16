#!/usr/bin/env python3
"""Tell the pet that an AI session wants attention. Standard library only, so any tool can call it.

    python3 bridge/notify.py --source claude --kind needs_input --session website --title "Allow the edit?"
    python3 bridge/notify.py --source local --kind done --session "llama batch" --detail "12 files written"
    python3 bridge/notify.py --source claude --kind cleared --session website

kind: needs_input, done or error show on the box; cleared takes the session's bubble away.
The bridge's address comes from --url, PET_NOTIFY_URL, or ~/.config/desk-pet/bridge.json (written by connect.py on a
machine that posts to the home Mac over Tailscale), else http://127.0.0.1:8765/event. A token for the tailnet
endpoint comes from --token, PET_NOTIFY_TOKEN or that file.
Always exits 0, even on a bad option, and gives up after two seconds, so a hook that calls it never blocks its
tool. Proxy settings in the environment are ignored: the bridge is on this machine.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks"))
import pethook  # noqa: E402  (the shared address and token lookup)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="which AI: claude, codex, local, or any short name")
    ap.add_argument("--kind", required=True, choices=["needs_input", "done", "error", "cleared"])
    ap.add_argument("--session", required=True, help="the session name shown on the box")
    ap.add_argument("--title", default="", help="one line, 80 characters")
    ap.add_argument("--detail", default="", help="one line, 200 characters")
    url, token = pethook.target()
    ap.add_argument("--url", default=url)
    ap.add_argument("--token", default=token, help="bearer token for the tailnet endpoint")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--quiet", action="store_true", help="say nothing even when the bridge is unreachable")
    try:
        a = ap.parse_args()
    except SystemExit:  # argparse exits 2 on a bad option; Claude Code reads 2 from a hook as "block", so do not
        return 0
    body = json.dumps({"v": 1, "source": a.source, "kind": a.kind, "session": a.session,
                       "title": a.title, "detail": a.detail}).encode()
    headers = {"Content-Type": "application/json"}
    if a.token and (a.url == url or a.token != token):  # never send the configured token to a different --url
        headers["Authorization"] = f"Bearer {a.token}"
    req = urllib.request.Request(a.url, data=body, headers=headers, method="POST")
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with local.open(req, timeout=a.timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if not a.quiet:
            print(f"notify: the bridge rejected the event ({exc.code}): "
                  f"{exc.read().decode(errors='replace')[:200]}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as exc:
        if not a.quiet:
            print(f"notify: bridge not reachable at {a.url}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
