"""The bridge loop: fetch GitHub -> brain -> MQTT.

    .venv/bin/python -m petbrain.publish --once          # one state message, then exit
    .venv/bin/python -m petbrain.publish                 # every PET_POLL_MIN minutes, forever
    .venv/bin/python -m petbrain.publish --once --set mood=1 --set streak_at_risk=true   # UI testing
    .venv/bin/python -m petbrain.publish --state-file some.json                          # publish a canned state

`pet/state` is published retained (QoS 1) so the box gets the latest state the
moment it connects. The previous state is kept in bridge/var/last_state.json so
one-shot events survive a restart. Session notifications (`pet/event`) come in
milestone 6.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

from .brain import compute_state
from .github_source import fetch_days, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "var" / "last_state.json"
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
    }


def load_prev() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_prev(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def connect(cfg: dict) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pet-bridge")
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"])
    client.connect(cfg["host"], cfg["port"], keepalive=60)
    client.loop_start()
    return client


def publish_state(client: mqtt.Client, state: dict) -> None:
    payload = json.dumps(state, separators=(",", ":"))
    info = client.publish(TOPIC_STATE, payload, qos=1, retain=True)
    info.wait_for_publish(timeout=10)
    log.info("published %s (%d bytes): lv%s xp%s mood %s streak %s today %s events %s", TOPIC_STATE, len(payload),
             state.get("level"), state.get("xp"), state.get("mood"), state.get("streak"),
             state.get("today", {}).get("total"), state.get("events"))


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
    client = connect(cfg)

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
        if a.once:
            break
        time.sleep(cfg["poll_min"] * 60)

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
