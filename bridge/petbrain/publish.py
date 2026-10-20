"""The bridge loop: fetch GitHub -> brain -> MQTT.

    .venv/bin/python -m petbrain.publish --once          # one state message, then exit
    .venv/bin/python -m petbrain.publish                 # every PET_POLL_MIN minutes, forever
    .venv/bin/python -m petbrain.publish --once --set mood=1 --set streak_at_risk=true   # UI testing
    .venv/bin/python -m petbrain.publish --state-file some.json                          # publish a canned state

`pet/state` is published retained (QoS 1) so the box gets the latest state the
moment it connects. The previous state is kept in bridge/var/last_state.json so
one-shot events survive a restart. Session notifications from AI tools arrive on
the local HTTP endpoint (notify_api.py) and go out on `pet/event` and the retained
`pet/notify`. 20 Questions games from the box arrive on `pet/q20` and are answered on
`pet/q20/ask` (q20.py). The to-do list is changed on the same endpoint (POST /todo), shown
on the retained `pet/todo` and ticked from the box on `pet/todo/tick` (todo.py). The updater feed of
recent PRs, issues and commits goes out retained on `pet/feed` every poll (feed.py), and a tap on an open one on
the box (`pet/feed/add`) puts it on the to-do list.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

from .brain import compute_state
from .github_source import fetch_days, load_dotenv
from . import q20, tailnet
from .todo import TOPIC_TICK, Todo
from .todo_refs import RefChecker, default_repo, resolve
from .feed import TOPIC_ADD as TOPIC_FEED_ADD, Feed
from .notify_api import TOPIC_ACK, Notifier, make_server, serve_in_thread

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "var" / "last_state.json"
PENDING_PATH = ROOT / "var" / "pending.json"  # the sessions still waiting, kept across bridge restarts (#12)
TODO_PATH = ROOT / "var" / "todo.json"        # the to-do list (#89)
TOPIC_STATE = "pet/state"
log = logging.getLogger("bridge")


def config() -> dict:
    load_dotenv(ROOT / ".env")
    return {
        "host": os.environ.get("MQTT_HOST", "localhost"),
        "port": int(os.environ.get("MQTT_PORT", "1883")),
        "user": os.environ.get("MQTT_USER") or None,
        "password": os.environ.get("MQTT_PASS") or None,
        "poll_min": float(os.environ.get("PET_POLL_MIN", "10")),
        "tz": ZoneInfo(os.environ.get("PET_TZ", "America/Chicago")),
        "goal": int(os.environ.get("PET_GOAL", "20")),
        "http_host": os.environ.get("PET_HTTP_HOST", "127.0.0.1"),
        "http_port": int(os.environ.get("PET_HTTP_PORT", "8765")),
        "tailnet": os.environ.get("PET_TAILNET", ""),
        "tailnet_host": os.environ.get("PET_TAILNET_HOST", ""),
        "tailnet_token": os.environ.get("PET_TAILNET_TOKEN", ""),
    }


def load_prev() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_prev(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def connect(cfg: dict, topics: tuple[str, ...] = ()) -> mqtt.Client:
    """Connect and wait for the broker's CONNACK; publishing before it is silently refused by paho.

    `topics` are subscribed inside on_connect, so a reconnect subscribes them again."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pet-bridge-{os.getpid()}")
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"])
    ready = threading.Event()
    result = {}

    def on_connect(c, userdata, flags, reason_code, properties):
        result["rc"] = reason_code
        if not reason_code.is_failure:
            for topic in topics:
                c.subscribe(topic, qos=1)
        ready.set()

    client.on_connect = on_connect
    client.connect(cfg["host"], cfg["port"], keepalive=60)
    client.loop_start()
    if not ready.wait(10):
        raise RuntimeError(f"no CONNACK from {cfg['host']}:{cfg['port']} within 10 s")
    if result["rc"].is_failure:
        raise RuntimeError(f"broker refused connection: {result['rc']}")
    return client


def publish_state(client: mqtt.Client, state: dict) -> None:
    payload = json.dumps(state, separators=(",", ":"))
    info = client.publish(TOPIC_STATE, payload, qos=1, retain=True)
    info.wait_for_publish(timeout=10)
    log.info("published %s (%d bytes): lv%s xp%s mood %s streak %s today %s events %s", TOPIC_STATE, len(payload),
             state.get("level"), state.get("xp"), state.get("mood"), state.get("streak"),
             state.get("today", {}).get("total"), state.get("events"))


def start_notifier(cfg: dict, client: mqtt.Client) -> tuple[Notifier, RefChecker, Feed] | None:
    """Session notifications: pet/ack from the box, the local HTTP endpoint, and the pending list that survived the
    last stop (bridge/var/pending.json) published on start. The to-do list (bridge/var/todo.json) rides on the same
    endpoint and is ticked on pet/todo/tick; items with a ref follow GitHub through the RefChecker, which the poll
    loop runs, as it does the updater feed, whose taps on pet/feed/add add to the same list. None when another bridge
    already serves the endpoint."""
    def publish(topic: str, payload: str, retain: bool) -> None:
        client.publish(topic, payload, qos=1, retain=retain)

    notifier = Notifier(publish, lambda: datetime.now(cfg["tz"]), store=PENDING_PATH)
    todo = Todo(publish, lambda: datetime.now(cfg["tz"]), store=TODO_PATH)
    try:
        server = make_server(cfg["http_host"], cfg["http_port"], notifier, todo=todo)
    except OSError as exc:
        # Most likely another bridge already serves the endpoint: leave its pending list and pet/ack alone.
        log.error("notify endpoint not started on %s:%s (%s); the GitHub loop runs without notifications "
                  "or 20 Questions",
                  cfg["http_host"], cfg["http_port"], exc)
        return None
    client.message_callback_add(TOPIC_ACK, lambda c, userdata, msg: notifier.ack(msg.payload))
    client.message_callback_add(TOPIC_TICK, lambda c, userdata, msg: todo.tick(msg.payload))
    notifier.publish_pending()  # what survived the restart, or the empty list: either way the box hears the truth
    todo.publish()
    serve_in_thread(server)
    log.info("notify endpoint: POST http://%s:%d/event and /todo", cfg["http_host"], cfg["http_port"])
    tailnet.start(cfg, notifier, todo=todo)  # only when PET_TAILNET=on and a long token is set; see petbrain/tailnet.py
    repo = default_repo()
    log.info("to-do refs: plain #n means %s", "/".join(repo) if repo else "nothing (set PET_TODO_REPO)")
    feed = Feed(publish, todo=todo, key=lambda ref: resolve(ref, repo))
    client.message_callback_add(TOPIC_FEED_ADD, lambda c, userdata, msg: feed.add(msg.payload))
    return notifier, RefChecker(todo, repo=repo), feed


def coerce(value: str):
    try:
        return json.loads(value)
    except ValueError:
        return value


def build_state(cfg: dict, overrides: list[str]) -> dict:
    now = datetime.now(cfg["tz"])
    login, days, diag = fetch_days(now=now, tz=cfg["tz"])
    state = compute_state(days, now, login=login, goal=cfg["goal"], prev=load_prev())
    for item in overrides:
        key, _, val = item.partition("=")
        if "." in key:
            outer, inner = key.split(".", 1)
            state.setdefault(outer, {})[inner] = coerce(val)
        else:
            state[key] = coerce(val)
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="publish one state and exit")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="override a state field (e.g. mood=2, today.total=150, streak_at_risk=true)")
    ap.add_argument("--state-file", help="publish this JSON file instead of computing a state")
    ap.add_argument("--no-save", action="store_true", help="do not record this state as the previous state")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config()
    loop_mode = not (a.once or a.state_file)
    client = connect(cfg, topics=(TOPIC_ACK, TOPIC_TICK, TOPIC_FEED_ADD, q20.TOPIC_IN) if loop_mode else ())
    refs = feed = None
    if loop_mode:
        started = start_notifier(cfg, client)
        if started is not None:
            refs, feed = started[1], started[2]
            q20.attach(client)   # like the notifier, left to the bridge already running: two would both answer

    if a.state_file:
        publish_state(client, json.loads(Path(a.state_file).read_text()))
        client.loop_stop()
        client.disconnect()
        return

    while True:
        try:
            state = build_state(cfg, a.set)
            publish_state(client, state)
            if not a.no_save and not a.set:
                save_prev(state)
        except Exception as exc:  # keep the loop alive through GitHub or network hiccups
            log.error("cycle failed: %s", exc)
            if a.once:
                sys.exit(1)
        if refs is not None:
            try:
                refs.check()   # to-do items with a ref follow GitHub (#91)
            except Exception as exc:
                log.warning("to-do refs not checked: %s", exc)
        if feed is not None:
            try:
                feed.update()   # the updater feed on the box's list screen (#92)
            except Exception as exc:
                log.warning("feed not updated: %s", exc)
        if a.once:
            break
        time.sleep(cfg["poll_min"] * 60)

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
