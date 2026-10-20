#!/usr/bin/env python3
"""The pet's to-do list (#89), from a terminal or an AI session. Standard library only, so any tool can call it.

    python3 bridge/todo.py add "Flash the list screen" --ref 90 --by claude
    python3 bridge/todo.py list            # open items, then the last few done
    python3 bridge/todo.py list --all      # every item the bridge keeps
    python3 bridge/todo.py done 3
    python3 bridge/todo.py undo 3
    python3 bridge/todo.py rm 3

--ref names the issue or PR an item is about (85, #85 or owner/repo#85). The bridge's address is the one notify.py
uses (--url, PET_NOTIFY_URL or ~/.config/desk-pet/bridge.json, else this machine), with /todo in place of /event.
Exits 0 when the bridge took the change, 1 when it said no or could not be reached, 2 for a bad option.
"""
import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks"))
import pethook  # noqa: E402  (the shared address and token lookup)

SHOW_DONE = 5  # done items `list` shows without --all


def todo_url(event_url: str) -> str:
    """http://host:8765/event -> http://host:8765/todo; any other path is replaced the same way."""
    parts = urllib.parse.urlsplit(event_url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/todo", "", ""))


def line(item: dict) -> str:
    ref = f"  {item['ref']}" if item.get("ref") else ""
    said = {"open": "still open", "dropped": "closed without being done"}
    flag = f"  ! ticked, but {item.get('ref')} is {said.get(item['flag'], item['flag'])}" if item.get("flag") else ""
    return f"{item['id']:>4}  [{'x' if item['done'] else ' '}]  {item['text']}{ref}  ({item['by']}){flag}"


def main() -> int:
    url, token = pethook.target()
    common = argparse.ArgumentParser(add_help=False)   # on every subcommand, so they can go after it
    common.add_argument("--url", default=url, help="the bridge's /event address; /todo is derived from it")
    common.add_argument("--token", default=token, help="bearer token for the tailnet endpoint")
    common.add_argument("--timeout", type=float, default=5.0)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="add an item", parents=[common])
    add.add_argument("text", help="one line, 80 characters")
    add.add_argument("--ref", help="the issue or PR it is about: 85, #85 or owner/repo#85")
    add.add_argument("--by", default=os.environ.get("PET_TODO_BY") or "john", help="who added it (default john)")
    ls = sub.add_parser("list", help="show the list", parents=[common])
    ls.add_argument("--all", action="store_true", help="every done item too")
    for name, what in (("done", "tick an item"), ("undo", "untick an item"), ("rm", "remove an item")):
        p = sub.add_parser(name, help=what, parents=[common])
        p.add_argument("id", type=int)
    a = ap.parse_args()

    headers = {"Content-Type": "application/json"}
    if a.token and (a.url == url or a.token != token):  # never send the configured token to a different --url
        headers["Authorization"] = f"Bearer {a.token}"
    elif a.token:
        print(f"todo: not sending the configured token to {a.url}; pass --token for that address", file=sys.stderr)
    if a.cmd == "list":
        body = None
    elif a.cmd == "add":
        body = {"op": "add", "text": a.text, "by": a.by, "ref": a.ref}
    else:
        body = {"op": {"rm": "remove"}.get(a.cmd, a.cmd), "id": a.id, "by": os.environ.get("PET_TODO_BY") or "john"}
    req = urllib.request.Request(todo_url(a.url), data=json.dumps(body).encode() if body else None, headers=headers,
                                 method="POST" if body else "GET")
    try:
        with pethook.opener().open(req, timeout=a.timeout) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            said = json.loads(exc.read()).get("error")
        except (ValueError, AttributeError, OSError):
            said = None
        said = said if isinstance(said, str) else None
        if exc.code == 404 and not said or said and said.startswith("unknown path"):
            said = "this bridge has no to-do list yet (restart it on a version with #89)"
        elif exc.code == 401:
            said = f"{said or exc.reason}: this address needs the tailnet token (--token or PET_NOTIFY_TOKEN)"
        print(f"todo: the bridge said no ({exc.code}): {said or exc.reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        print(f"todo: bridge not reachable at {todo_url(a.url)}: {exc}", file=sys.stderr)
        return 1

    try:
        if a.cmd != "list":
            verb = {"add": "added", "done": "ticked", "undo": "opened again", "rm": "removed"}[a.cmd]
            print(f"{verb}: {line(reply['item'])}")
            return 0
        items = reply["items"]
        open_ = [it for it in items if not it["done"]]
        done = [it for it in items if it["done"]]
        for it in items:
            line(it)
    except (KeyError, TypeError, ValueError):
        print(f"todo: the bridge's reply did not make sense: {json.dumps(reply)[:200]}", file=sys.stderr)
        return 1
    shown = done if a.all else done[:SHOW_DONE]
    for it in open_ + shown:
        print(line(it))
    more = f", {len(done) - len(shown)} more done (--all)" if len(shown) < len(done) else ""
    print(f"{len(open_)} open, {len(done)} done{more}" if items else "the list is empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
