#!/usr/bin/env python3
"""Save what the box's screen shows as a PNG (issue #86).

Serves an upload address on this computer, publishes {"shot": url} on pet/cmd, and waits for the box to POST its
screen: 720x720 RGB565 pixels, LVGL's snapshot of everything the face draws. Writes a PNG with the standard library
only. Broker settings come from bridge/.env; run it with the bridge's venv so paho-mqtt is there:

    bridge/.venv/bin/python tools/screenshot.py                  # pet-20261019-091500.png in this folder
    bridge/.venv/bin/python tools/screenshot.py shots/menu.png   # a name of your own
    bridge/.venv/bin/python tools/screenshot.py --scale 2        # 1440x1440, each pixel doubled

Only the standard library and paho-mqtt, so it should run the same on Windows and Linux (tried on macOS). Exit code
0 when the PNG is written, 1 when the box reported an error, nothing came back, the broker refused or Ctrl-C; 2 for
bad arguments.
"""
from __future__ import annotations

import argparse
import errno
import http.server
import json
import os
import struct
import sys
import threading
import time
import zlib
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bridge"))
sys.path.insert(0, str(ROOT / "tools"))
from petbrain.github_source import load_dotenv  # noqa: E402  the bridge's own .env reader
from ota_push import lan_ip  # noqa: E402  same route-to-the-broker address pick

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt missing: run this with bridge/.venv/bin/python")

TOPIC_CMD = "pet/cmd"
MAX_BYTES = 4 * 1024 * 1024   # a 720x720 RGB565 screen is about 1 MB

# 5- and 6-bit channels widened to 8 bits, rounded to the nearest value (LVGL may differ by 1 here and there).
R5 = bytes((v * 527 + 23) >> 6 for v in range(32))
G6 = bytes((v * 259 + 33) >> 6 for v in range(64))


def rgb565_to_rgb(data: bytes, width: int, height: int, stride: int) -> list[bytes]:
    """Rows of 8-bit RGB from little-endian RGB565 rows `stride` bytes apart."""
    rows = []
    for y in range(height):
        px = array("H")
        px.frombytes(data[y * stride:y * stride + width * 2])
        if sys.byteorder == "big":
            px.byteswap()
        row = bytearray(width * 3)
        row[0::3] = bytes(R5[v >> 11] for v in px)
        row[1::3] = bytes(G6[(v >> 5) & 0x3F] for v in px)
        row[2::3] = bytes(R5[v & 0x1F] for v in px)
        rows.append(bytes(row))
    return rows


def scaled(rows: list[bytes], scale: int) -> list[bytes]:
    """Each pixel repeated `scale` times across and down, so pixel art stays sharp."""
    if scale == 1:
        return rows
    out = []
    for row in rows:
        wide = b"".join(row[i:i + 3] * scale for i in range(0, len(row), 3))
        out.extend([wide] * scale)
    return out


def png(rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    width, height = len(rows[0]) // 3, len(rows)
    raw = b"".join(b"\x00" + r for r in rows)   # filter 0 on every row
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class Upload(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    done = threading.Event()

    def do_POST(self):  # noqa: N802  http.server's name
        if self.path != "/shot":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BYTES:
                raise ValueError(f"{length} bytes is more than a screen")
            body = self.rfile.read(length)
            error = self.headers.get("X-Error")
            if not error:
                missing = [k for k in ("X-Width", "X-Height", "X-Stride") if self.headers.get(k) is None]
                if missing:
                    raise ValueError(f"no {', '.join(missing)} header")
                w, h, stride = (int(self.headers[k]) for k in ("X-Width", "X-Height", "X-Stride"))
                if self.headers.get("X-Format") != "RGB565" or w <= 0 or h <= 0 or stride < 2 * w or \
                        len(body) < stride * (h - 1) + 2 * w:
                    raise ValueError(f"unexpected image: {w}x{h}, stride {stride}, {len(body)} bytes, "
                                     f"format {self.headers.get('X-Format')}")
                Upload.result.update(width=w, height=h, rows=rgb565_to_rgb(body, w, h, stride))
            else:
                Upload.result["error"] = error
        except (KeyError, TypeError, ValueError) as e:
            Upload.result["error"] = f"bad upload: {e}"
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        Upload.done.set()

    def log_message(self, fmt, *args):
        print(f"http: {self.address_string()} {fmt % args}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Save the box's screen as a PNG.")
    ap.add_argument("out", nargs="?", type=Path, help="where to write the PNG (default: pet-<date>-<time>.png here)")
    ap.add_argument("--scale", type=int, default=1, choices=range(1, 5), help="repeat each pixel this many times")
    ap.add_argument("--ip", help="this computer's LAN address to listen on (default: the one that routes to the broker)")
    ap.add_argument("--port", type=int, default=8072, choices=range(1, 65536), metavar="PORT")
    ap.add_argument("--timeout", type=float, default=20, help="seconds to wait for the box")
    a = ap.parse_args()
    out = a.out or Path(time.strftime("pet-%Y%m%d-%H%M%S.png"))

    load_dotenv(ROOT / "bridge" / ".env")
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    ip = a.ip or lan_ip(host)
    url = f"http://{ip}:{a.port}/shot"
    try:
        server = http.server.ThreadingHTTPServer((ip, a.port), Upload)
    except OSError as e:
        hint = " (another screenshot.py running? try --port)" if e.errno == errno.EADDRINUSE else \
            " (check --ip: it must be one of this computer's own addresses)"
        print(f"cannot listen on {ip}:{a.port}: {e.strerror}{hint}")
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"screenshot-{os.getpid()}")
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS") or None)
    try:
        client.connect(host, port, keepalive=30)
        client.loop_start()
        info = client.publish(TOPIC_CMD, json.dumps({"shot": url}), qos=1, retain=False)
        info.wait_for_publish(5)
        if info.rc != mqtt.MQTT_ERR_SUCCESS or not info.is_published():
            print(f"could not publish on {host}:{port} (check MQTT_USER and MQTT_PASS in bridge/.env)")
            return 1
        print(f"asked the box for its screen; waiting up to {a.timeout:.0f} s", flush=True)
        if not Upload.done.wait(a.timeout):
            print("timed out: no screen came back (is the box on Wi-Fi and running a build with screenshots?)")
            return 1
    except (OSError, KeyboardInterrupt) as e:
        print(f"stopped: {e or 'Ctrl-C'}")
        return 1
    finally:
        client.loop_stop()
        client.disconnect()
        server.shutdown()

    if "error" in Upload.result:
        print(f"the box sent no screen: {Upload.result['error']}")
        return 1
    rows = scaled(Upload.result["rows"], a.scale)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png(rows))
    print(f"wrote {out} ({len(rows[0]) // 3}x{len(rows)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
