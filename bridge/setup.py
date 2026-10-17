"""First-time setup for the bridge Mac: a safe bridge/.env, and access from your other devices over Tailscale.

    python3 bridge/setup.py                 asks before turning on access from your other devices
    python3 bridge/setup.py --yes           takes the safe defaults without asking (access on when Tailscale is up)
    python3 bridge/setup.py --no-remote     leaves access from other devices as it is (off on a fresh install)
    python3 bridge/setup.py --show-secrets  also prints the MQTT password, for the box's secrets.h

Run it again any time: it keeps every value already set, fills in only what is missing or unsafe, and says what it
did. Passwords and tokens come from Python's `secrets` module, are never printed unless asked, and stay in
bridge/.env, which it keeps readable by you only (0600). Standard library only, Python 3.9+.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

BRIDGE = Path(__file__).resolve().parent
REPO = BRIDGE.parent
sys.path.insert(0, str(BRIDGE))
from petbrain import tailnet  # noqa: E402  (standard library only: finds this Mac's Tailscale address)

DOWNLOAD = "https://tailscale.com/download"


def get(lines: list[str], key: str) -> str | None:
    """The value of `key` in .env lines as the bridge reads it (load_dotenv: the first line wins, quotes stripped),
    or None when no line sets it."""
    for line in lines:
        name, sep, rest = line.partition("=")
        if sep and not name.strip().startswith("#") and name.strip() == key:
            return rest.strip().strip('"').strip("'")
    return None


def put(lines: list[str], key: str, value: str) -> None:
    """Set `key` on the line the bridge reads (the first), or append it; every other line stays as it was."""
    for i in range(len(lines)):
        name, sep, _ = lines[i].partition("=")
        if sep and not name.strip().startswith("#") and name.strip() == key:
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def installed(which: Callable = shutil.which) -> bool:
    return any(which(cli) or (cli.startswith("/") and Path(cli).exists()) for cli in tailnet.CLI)


def loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, readable by its owner only from the first byte; replace atomically. A symlinked
    .env is written where it points, so the link stays a link."""
    path = path.resolve()
    tmp = path.with_name(path.name + ".setup-tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def setup(env: Path, example: Path, *, yes: bool = False, remote: bool = True, show: bool = False, out=print,
          ask=input, detect: Callable = tailnet.detect_ip, has_tailscale: Callable = installed,
          new_secret: Callable = lambda: secrets.token_urlsafe(32), extra=None,
          version: tuple = sys.version_info[:2]) -> int:
    created = not env.exists()
    if created and not example.exists():
        out(f"{example} is missing; run this from an ESP-P4_Box checkout.")
        return 2
    old = (example if created else env).read_text(encoding="utf-8")
    lines = old.splitlines()
    done, warn = [], []
    checks = extra if extra is not None else default_checks()
    if created:
        done.append("Created bridge/.env from .env.example.")
        if checks["passwd"]:
            warn.append("The broker already has a password from an earlier setup. The new one below replaces it "
                        "once you run bridge/mosquitto/setup.sh, and the box stops connecting until it is flashed "
                        "with the new one.")

    mqtt_new = not get(lines, "MQTT_PASS")
    if mqtt_new:
        put(lines, "MQTT_PASS", new_secret())
        done.append("Made a strong MQTT password, so only your box and this Mac can use the broker.")
    if not get(lines, "MQTT_USER"):
        put(lines, "MQTT_USER", "pet")

    host = get(lines, "PET_HTTP_HOST") or "127.0.0.1"
    if not loopback(host):
        warn.append(f"PET_HTTP_HOST={host} lets other machines post without a token. Unless you meant that, set it "
                    "back to 127.0.0.1 in bridge/.env.")

    ip = detect()
    on = tailnet.wanted(get(lines, "PET_TAILNET"))
    token = get(lines, "PET_TAILNET_TOKEN") or ""
    if on and len(token) < tailnet.MIN_TOKEN:
        put(lines, "PET_TAILNET_TOKEN", new_secret())
        done.append("Replaced a missing or short Tailscale token with a strong one." +
                    (" Run `connect.py --share` again for your other machines: the old token stops working."
                     if token else ""))
    elif not on and remote:
        if ip:
            prompt = ("Let your other devices send AI notifications to the pet from anywhere, over Tailscale? Only "
                      "devices signed in to your Tailscale account can reach it, and each needs a secret token. [Y/n] ")
            try:
                answer = "y" if yes else ask(prompt).strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes"):
                put(lines, "PET_TAILNET", "on")
                if len(token) < tailnet.MIN_TOKEN:
                    put(lines, "PET_TAILNET_TOKEN", new_secret())
                on = True
                done.append("Turned on access from your other devices over Tailscale, with a strong secret token.")
        elif has_tailscale():
            warn.append("Tailscale is installed but not signed in or not running, so access from your other devices "
                        "stays off. Open Tailscale, sign in, and run this again.")
        else:
            warn.append(f"Access from your other devices is off. To get notifications from your other computers "
                        f"anywhere, install Tailscale ({DOWNLOAD}): a free private network between your own devices. "
                        "Nothing is opened to the internet; only devices signed in to your account can reach this "
                        "Mac. Install it here and on the other computers, sign in with the same account, then run "
                        "this again.")

    new = "\n".join(lines) + "\n"
    changed = created or lines != old.splitlines()  # a value changed, so a running bridge must restart
    if created or new != old:
        write_private(env, new)
    elif env.stat().st_mode & 0o077:
        env.chmod(0o600)
        done.append("Made bridge/.env readable by you only; it holds your passwords.")

    out("Desk pet setup")
    for line in done or ["Nothing to change: bridge/.env was already set up."]:
        out(f"  - {line}")
    for line in warn:
        out(f"  ! {line}")
    out("")
    out("Now:")
    out("  Secrets       bridge/.env, readable by you only")
    out(f"  MQTT          user {get(lines, 'MQTT_USER')}, password " +
        (get(lines, "MQTT_PASS") if show else "set (--show-secrets to see it)"))
    out("  GitHub        " + ("token set" if get(lines, "GITHUB_TOKEN") else "no token; uses `gh auth token`"))
    if on:
        where = f"this Mac's Tailscale address {ip}" if ip else "this Mac's Tailscale address, once Tailscale is up"
        out(f"  Other devices on, over Tailscale only, at {where}")
    else:
        out("  Other devices off; only this Mac can send AI notifications")

    steps = []
    oldest = checks.get("python3", version)  # `python3` on PATH is what the next steps run
    if not checks["brew"] and (oldest < (3, 11) or not checks["mosquitto"]):  # the steps below use brew
        steps.append("Install Homebrew, the Mac's package manager: https://brew.sh")
    if oldest < (3, 11):
        found = f"`python3` here is {oldest[0]}.{oldest[1]}" if oldest > (0, 0) else "there is no `python3` here"
        steps.append(f"Install Python 3.11 or later ({found}; the bridge and connect.py need 3.11): "
                     "`brew install python`, then open a new terminal so `python3` is the new one.")
    if not checks["venv"]:
        steps.append("cd bridge && python3 -m venv .venv && .venv/bin/pip install paho-mqtt && cd ..")
    if not checks["mosquitto"]:
        steps.append("brew install mosquitto")
    if mqtt_new or not checks["passwd"]:
        steps.append("bridge/mosquitto/setup.sh     (gives the broker the MQTT password)")
    if mqtt_new or not checks["secrets_h"]:
        steps.append("Give the box its settings: cp projects/pet/main/secrets.h.example projects/pet/main/secrets.h, "
                     "then fill in your 2.4 GHz Wi-Fi, this Mac's home network address, and the MQTT password "
                     "(`python3 bridge/setup.py --show-secrets` shows it), and flash the box (notes/flashing.md).")
    bridge = "cd bridge && .venv/bin/python -m petbrain.publish"
    if created:
        steps.append(f"Start the broker and the bridge: bridge/mosquitto/run.sh, and in a second terminal: {bridge}")
    elif changed:
        steps.append(f"Restart the bridge so it reads the new settings: stop it (Ctrl-C), then {bridge}")
    if on:
        steps.append("python3 bridge/connect.py --share     (prints the one line to run on each of your other machines)")
    steps.append("python3 bridge/connect.py     (connects this Mac's AI tools to the pet)")
    out("")
    out(f"Next, from {REPO}:")
    for n, step in enumerate(steps, 1):
        out(f"  {n}. {step}")
    return 0


def python3_version() -> tuple:
    """The version of `python3` on PATH, which the printed next steps run; (0, 0) when there is none."""
    path = shutil.which("python3")
    if not path:
        return (0, 0)
    try:
        out = subprocess.run([path, "-c", "import sys; print('%d %d' % sys.version_info[:2])"], capture_output=True,
                             text=True, timeout=10).stdout.split()
        return (int(out[0]), int(out[1]))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return (0, 0)


def default_checks() -> dict:
    return {"python3": python3_version(), "venv": (BRIDGE / ".venv").is_dir(), "mosquitto": bool(shutil.which("mosquitto")),
            "passwd": (BRIDGE / "mosquitto" / "passwd").is_file(), "brew": bool(shutil.which("brew")),
            "secrets_h": (REPO / "projects" / "pet" / "main" / "secrets.h").is_file()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="take the safe defaults without asking")
    parser.add_argument("--no-remote", action="store_true", help="do not offer access from other devices")
    parser.add_argument("--show-secrets", action="store_true", help="also print the MQTT password")
    args = parser.parse_args(argv)
    return setup(BRIDGE / ".env", BRIDGE / ".env.example", yes=args.yes, remote=not args.no_remote,
                 show=args.show_secrets)


if __name__ == "__main__":
    sys.exit(main())
