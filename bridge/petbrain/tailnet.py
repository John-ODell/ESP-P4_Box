"""The bridge's second listener, for John's other machines on his Tailscale network (tailnet).

Off unless `PET_TAILNET=on` in .env. Safe by default: it never binds 0.0.0.0, it refuses to start without a token of
at least MIN_TOKEN characters, it binds only the Mac's own Tailscale address (found with `tailscale ip -4`, or set
with `PET_TAILNET_HOST`), and every request must come from a tailnet address and carry `Authorization: Bearer
<token>`. Tailscale encrypts the traffic between machines, so plain HTTP on the tailnet is not readable on the way.
The loopback endpoint is untouched, so hooks on the Mac itself need no token.
"""
from __future__ import annotations

import ipaddress
import logging
import shutil
import subprocess
import threading
import time
from typing import Callable

from .notify_api import Notifier, make_server, serve_in_thread

log = logging.getLogger("bridge.tailnet")

TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
MIN_TOKEN = 32
RETRY_S = 30
CLI = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale",
       "/opt/homebrew/bin/tailscale")


def wanted(value: str | None) -> bool:
    return (value or "").strip().lower() in ("on", "1", "true", "yes")


def detect_ip(run: Callable = subprocess.run) -> str | None:
    """This machine's Tailscale IPv4, or None when Tailscale is missing, logged out or not up yet."""
    for cli in CLI:
        path = shutil.which(cli) or (cli if cli.startswith("/") else None)
        if not path:
            continue
        try:
            out = run([path, "ip", "-4"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (out.stdout or "").split():
            if in_tailnet(line):
                return line
    return None


def in_tailnet(address: str) -> bool:
    try:
        return any(ipaddress.ip_address(address) in net for net in TAILNET)
    except ValueError:
        return False


def start(cfg: dict, notifier: Notifier, detect: Callable = detect_ip, sleep: Callable = time.sleep,
          make: Callable = make_server) -> threading.Thread | None:
    """Start the tailnet listener in the background when it is wanted and safe; None when it will not run."""
    if not wanted(cfg.get("tailnet")):
        return None
    token = cfg.get("tailnet_token") or ""
    if len(token) < MIN_TOKEN:
        log.error("tailnet access is on but PET_TAILNET_TOKEN is missing or shorter than %d characters; "
                  "not listening on the tailnet (run bridge/setup.py, or put a long random token in .env)", MIN_TOKEN)
        return None
    fixed = cfg.get("tailnet_host") or None
    if fixed and not in_tailnet(fixed):
        log.error("PET_TAILNET_HOST=%s is not a Tailscale address; not listening on the tailnet", fixed)
        return None

    def run() -> None:
        said = False
        while True:
            host = fixed or detect()
            if host:
                try:
                    server = make(host, cfg["http_port"], notifier, token=token, allow=TAILNET)
                except OSError as exc:
                    if not said:
                        log.warning("tailnet listener not up yet on %s:%s (%s); retrying every %d s",
                                    host, cfg["http_port"], exc, RETRY_S)
                        said = True
                else:
                    serve_in_thread(server)
                    log.info("tailnet endpoint: POST http://%s:%d/event (token required)", host, cfg["http_port"])
                    return
            elif not said:
                log.warning("tailnet access is on but Tailscale has no address yet; retrying every %d s", RETRY_S)
                said = True
            sleep(RETRY_S)

    thread = threading.Thread(target=run, name="tailnet-listener", daemon=True)
    thread.start()
    return thread
