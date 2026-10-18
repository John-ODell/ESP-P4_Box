#!/usr/bin/env python3
"""Send a pet pack to the box over Wi-Fi (issue #68).

Builds the pack if given a pet folder (or checks a ready .petpack), serves it from this computer over plain HTTP,
publishes {"pack": url, "sha256": hex} on pet/cmd, and prints what the box reports on pet/pack until it reboots
drawing the new pet or gives up. The box checks the download's SHA-256 and every check its boot loader makes before
it overwrites the pack it has, so a bad pack leaves the current pet in place. Broker settings come from bridge/.env;
run it with the bridge's venv so paho-mqtt is there:

    bridge/.venv/bin/python tools/pack_push.py pets/dolphin
    bridge/.venv/bin/python tools/pack_push.py ember.petpack --ip 10.0.0.20 --port 8071 --timeout 120

Works the same on macOS, Windows and Linux. Exit code 0 when the box reports it is drawing the new pack, 1 otherwise
(the box refused it, timeout, broker refused, stopped with Ctrl-C).
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bridge"))
sys.path.insert(0, str(ROOT / "tools"))
from petbrain.github_source import load_dotenv  # noqa: E402  the bridge's own .env reader
import petpack  # noqa: E402
from ota_push import lan_ip  # noqa: E402  same route-to-the-broker address pick

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt missing: run this with bridge/.venv/bin/python")

TOPIC_CMD = "pet/cmd"
TOPIC_PACK = "pet/pack"


def pack_bytes(path: Path) -> tuple[bytes, str]:
    """The .petpack bytes and the pet's name: built from a folder, or a file read and checked like the box would."""
    try:
        if path.is_dir():
            meta, frames = petpack.load(path)
            blob = petpack.build(meta, frames)
        else:
            blob = path.read_bytes()
            meta, _ = petpack.parse(blob)
    except (petpack.PetError, OSError) as e:
        sys.exit(f"{path}: {e}")
    return blob, meta["name"]


class OneFile(http.server.BaseHTTPRequestHandler):
    data = b""

    def do_GET(self):  # noqa: N802  http.server's name
        if self.path != "/pet.petpack":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.data)))
        self.end_headers()
        try:
            self.wfile.write(self.data)
        except (BrokenPipeError, ConnectionResetError):
            print("http: the box dropped the connection", flush=True)

    def log_message(self, fmt, *args):
        print(f"http: {self.address_string()} {fmt % args}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a pet pack to the box over Wi-Fi.")
    ap.add_argument("pack", type=Path, help="a pet folder (pets/<name>) or a .petpack file")
    ap.add_argument("--ip", help="this computer's LAN address to serve from (default: the one that routes to the "
                                 "broker)")
    ap.add_argument("--port", type=int, default=8071)
    ap.add_argument("--timeout", type=float, default=120, help="seconds to wait for the box to draw the new pet")
    a = ap.parse_args()

    blob, name = pack_bytes(a.pack)
    crc = "%08x" % struct.unpack_from("<I", blob, 16)[0]
    sha = hashlib.sha256(blob).hexdigest()

    load_dotenv(ROOT / "bridge" / ".env")
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    ip = a.ip or lan_ip(host)
    url = f"http://{ip}:{a.port}/pet.petpack"

    OneFile.data = blob
    server = http.server.ThreadingHTTPServer((ip, a.port), OneFile)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving {name} ({len(blob)} bytes, crc {crc}) at {url}")

    done = threading.Event()
    answered = threading.Event()  # the broker took the connection or refused it
    seen = {"sent": False, "rebooting": False, "ok": False, "refused": False, "before": None}

    def on_message(client, userdata, msg):
        try:
            st = json.loads(msg.payload)
        except ValueError:
            print(f"{TOPIC_PACK}: unreadable: {msg.payload[:80]!r}")
            return
        if msg.retain:  # what the broker remembered from before this push; live reports to a subscriber are not retained
            seen["before"] = st.get("crc")
            print(f"box before the push: {st}")
            return
        print("box: " + ", ".join(f"{k} {v}" for k, v in st.items()), flush=True)
        state = st.get("state")
        if state == "rebooting":
            seen["rebooting"] = True
        elif state == "failed":
            done.set()
        elif state == "running" and (seen["rebooting"] or (st.get("crc") == crc and seen["before"] != crc)):
            # The box repeats its running report on every reconnect, so only one after this push's reboot counts,
            # or one already drawing the new pack when it was not before (the rebooting report can be missed).
            seen["ok"] = st.get("crc") == crc
            if not seen["ok"]:
                print(f"the box came back drawing {st.get('pet')} (crc {st.get('crc')}), not {name} ({crc})")
            done.set()

    def on_connect(client, userdata, flags, rc, props):
        if rc.is_failure:
            if not seen["refused"]:
                print(f"broker {host}:{port} refused the connection: {rc} "
                      "(check MQTT_USER and MQTT_PASS in bridge/.env)")
            seen["refused"] = True
            answered.set()
            return
        client.subscribe(TOPIC_PACK, qos=1)
        answered.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pack-push-{os.getpid()}")
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS") or None)
    client.on_message = on_message
    client.on_connect = on_connect
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        if not answered.wait(10):
            print(f"broker {host}:{port} not reachable")
            return 1
        if seen["refused"]:
            return 1
        time.sleep(1)  # let the retained pet/pack in first, so the pet before the push is known
        client.publish(TOPIC_CMD, json.dumps({"pack": url, "sha256": sha}), qos=1, retain=False).wait_for_publish(5)
        seen["sent"] = True
        print(f"told the box on {TOPIC_CMD}; waiting up to {a.timeout:.0f} s", flush=True)
        if not done.wait(a.timeout):
            print("timed out: the box never reported the new pet (check the UART if it is on the cable)")
        if seen["ok"]:
            print(f"the box is drawing {name}")
        return 0 if seen["ok"] else 1
    except KeyboardInterrupt:
        print("stopped; the box may still be working, watch pet/pack or the UART")
        return 1
    finally:
        client.loop_stop()
        client.disconnect()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
