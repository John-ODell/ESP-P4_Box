#!/usr/bin/env python3
"""Push a pet build to the box over Wi-Fi (issue #16).

Serves projects/pet/build/pet.bin from this Mac over plain HTTP, publishes {"ota": url} on pet/cmd, and prints what
the box reports on pet/ota until it runs the new image or gives up. Broker settings come from bridge/.env, like the
bridge's own; run it with the bridge's venv so paho-mqtt is there:

    bridge/.venv/bin/python tools/ota_push.py                       # right after idf.py -C projects/pet build
    bridge/.venv/bin/python tools/ota_push.py --ip 10.0.0.20 --port 8070 --timeout 300

The box takes the command only live (a retained pet/cmd is ignored), only over http://, and only for a pet image.
Exit code 0 when the box reports the new version running and confirmed, 1 otherwise (failed, rolled back, timeout,
broker refused, stopped with Ctrl-C).
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bridge"))
from petbrain.github_source import load_dotenv  # noqa: E402  the bridge's own .env reader

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt missing: run this with bridge/.venv/bin/python")

TOPIC_CMD = "pet/cmd"
TOPIC_OTA = "pet/ota"
APP_DESC_OFFSET = 0x20  # esp_app_desc_t: after the 24-byte image header and the first 8-byte segment header
APP_DESC_MAGIC = 0xABCD5432


def image_info(data: bytes) -> dict:
    """Version, project and build stamp from the image's esp_app_desc_t: what the box logs at boot."""
    if len(data) < APP_DESC_OFFSET + 128 or struct.unpack_from("<I", data, APP_DESC_OFFSET)[0] != APP_DESC_MAGIC:
        sys.exit("not an ESP-IDF app image (no app descriptor at 0x20)")

    def text(off: int, n: int) -> str:
        return data[off:off + n].split(b"\0", 1)[0].decode(errors="replace")

    base = APP_DESC_OFFSET + 16  # past magic, secure_version and reserv1
    return {"version": text(base, 32), "project": text(base + 32, 32), "time": text(base + 64, 16),
            "date": text(base + 80, 16)}


def lan_ip(broker: str) -> str:
    """This Mac's address on the box's network: the interface that routes to the broker (or to the default route)."""
    target = broker if broker not in ("localhost", "127.0.0.1", "::1") else "10.255.255.255"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))  # a UDP connect sends nothing; it only picks the route
        return s.getsockname()[0]
    finally:
        s.close()


class OneFile(http.server.BaseHTTPRequestHandler):
    data = b""

    def do_GET(self):  # noqa: N802  http.server's name
        if self.path != "/pet.bin":
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
    ap = argparse.ArgumentParser(description="Push a pet build to the box over Wi-Fi.")
    ap.add_argument("--bin", type=Path, default=ROOT / "projects" / "pet" / "build" / "pet.bin")
    ap.add_argument("--ip", help="this Mac's LAN address to serve from (default: the one that routes to the broker)")
    ap.add_argument("--port", type=int, default=8070)
    ap.add_argument("--timeout", type=float, default=300, help="seconds to wait for the box to run the new image")
    a = ap.parse_args()

    load_dotenv(ROOT / "bridge" / ".env")
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    data = a.bin.read_bytes()
    info = image_info(data)
    if info["project"] != "pet":
        sys.exit(f"{a.bin} is a {info['project']!r} image, not pet")
    ip = a.ip or lan_ip(host)
    url = f"http://{ip}:{a.port}/pet.bin"

    OneFile.data = data
    server = http.server.ThreadingHTTPServer((ip, a.port), OneFile)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving {a.bin} ({len(data)} bytes, pet {info['version']} built {info['date']} {info['time']}) at {url}")

    done = threading.Event()
    connected = threading.Event()
    refused = threading.Event()
    seen: dict = {"sent": False, "rebooting": False, "ok": False, "before": None}

    def on_message(client, userdata, msg):
        try:
            st = json.loads(msg.payload)
        except ValueError:
            print(f"{TOPIC_OTA}: unreadable: {msg.payload[:80]!r}")
            return
        if msg.retain and not seen["sent"]:  # what the broker remembered from before this push
            seen["before"] = st.get("version")
            print(f"box before the push: {st}")
            return
        print("box: " + ", ".join(f"{k} {v}" for k, v in st.items()), flush=True)
        state = st.get("state")
        if state == "rebooting":
            seen["rebooting"] = True
        elif state == "failed":
            done.set()
        elif state == "running" and st.get("rolled_back") == info["version"]:
            print(f"the box rolled {info['version']} back: it never confirmed, so it never reached MQTT after the reboot")
            done.set()
        elif state == "running" and st.get("confirmed") and st.get("version") == info["version"]:
            if seen["rebooting"] or seen["before"] != info["version"]:
                seen["ok"] = True
                done.set()

    def on_connect(client, userdata, flags, rc, props):
        if rc.is_failure:
            print(f"broker {host}:{port} refused the connection: {rc} (check MQTT_USER and MQTT_PASS in bridge/.env)")
            refused.set()
            return
        client.subscribe(TOPIC_OTA, qos=1)
        connected.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ota-push-{os.getpid()}")
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS") or None)
    client.on_message = on_message
    client.on_connect = on_connect
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        if not connected.wait(10):
            if not refused.is_set():
                print(f"broker {host}:{port} not reachable")
            return 1
        time.sleep(1)  # let the retained pet/ota in first, so it is not mistaken for a reply
        client.publish(TOPIC_CMD, json.dumps({"ota": url}), qos=1, retain=False).wait_for_publish(5)
        seen["sent"] = True
        print(f"told the box on {TOPIC_CMD}; waiting up to {a.timeout:.0f} s", flush=True)
        if not done.wait(a.timeout):
            print("timed out: the box never reported the new image running (check the UART if it is on the cable)")
        return 0 if seen["ok"] else 1
    except KeyboardInterrupt:
        print("stopped; the box may still be updating, watch pet/ota or the UART")
        return 1
    finally:
        client.loop_stop()
        client.disconnect()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
