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
import sys
from pathlib import Path
from typing import Callable

BRIDGE = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE))
from petbrain import tailnet  # noqa: E402  (standard library only: finds this Mac's Tailscale address)

DOWNLOAD = "https://tailscale.com/download"


def get(lines: list[str], key: str) -> str | None:
    """The last value set for `key` in .env lines (comments skipped), or None."""
    value = None
    for line in lines:
        name, sep, rest = line.partition("=")
        if sep and not name.strip().startswith("#") and name.strip() == key:
            value = rest.strip()
    return value


def put(lines: list[str], key: str, value: str) -> None:
    """Set `key` on its last line, or append it; every other line, comments included, stays as it was."""
    for i in reversed(range(len(lines))):
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
    """Write `text` to `path`, readable by its owner only from the first byte; replace atomically."""
    tmp = path.with_name(path.name + ".setup-tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def setup(env: Path, example: Path, *, yes: bool = False, remote: bool = True, show: bool = False, out=print,
          ask=input, detect: Callable = tailnet.detect_ip, has_tailscale: Callable = installed,
          new_secret: Callable = lambda: secrets.token_urlsafe(32), extra=None) -> int:
    created = not env.exists()
    if created and not example.exists():
        out(f"{example} is missing; run this from an ESP-P4_Box checkout.")
        return 2
    old = (example if created else env).read_text(encoding="utf-8")
    lines = old.splitlines()
    done, warn = [], []
    if created:
        done.append("Created bridge/.env from .env.example.")

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
    changed = created or new != old
    if changed:
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
    checks = extra if extra is not None else default_checks()
    if not checks["venv"]:
        steps.append("cd bridge && python3 -m venv .venv && .venv/bin/pip install paho-mqtt && cd ..")
    if not checks["mosquitto"]:
        steps.append("brew install mosquitto")
    if mqtt_new or not checks["passwd"]:
        steps.append("bridge/mosquitto/setup.sh     (gives the broker the MQTT password)")
    if mqtt_new:
        steps.append("Put the MQTT password in projects/pet/main/secrets.h as PET_MQTT_PASS and flash the box "
                     "(`python3 bridge/setup.py --show-secrets` shows it).")
    if changed and not created:
        steps.append("Restart the bridge so it reads the new settings.")
    if on:
        steps.append("python3 bridge/connect.py --share     (prints the one line to run on each of your other machines)")
    steps.append("python3 bridge/connect.py     (connects this Mac's AI tools to the pet)")
    out("")
    out("Next:")
    for n, step in enumerate(steps, 1):
        out(f"  {n}. {step}")
    return 0


def default_checks() -> dict:
    return {"venv": (BRIDGE / ".venv").is_dir(), "mosquitto": bool(shutil.which("mosquitto")),
            "passwd": (BRIDGE / "mosquitto" / "passwd").is_file()}


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
