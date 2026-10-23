"""The pet on northbridgelandmark.com (#114): after each poll, the pet's public state goes to the site, whose home
page shows it (John-ODell/website#113).

Only what a visitor may see is sent: the level, XP into it and XP it needs, the mood and its label, the streak and
whether today's goal is met. Never `ts` (its UTC offset gives away the time zone), `today`'s counts, `login`, events,
or anything from the to-do list, feed, radar, CI or machines.

    PET_SITE_TOKEN   the same secret as the site's DESKPET_PET_TOKEN Worker secret, 32+ characters; empty sends nothing
    PET_SITE_URL     default https://northbridgelandmark.com/api/deskpet/pet; https only (http only to this Mac)

The site keeps an unchanged state without rewriting it for an hour, so sending every poll costs nothing. `post` is
injected, so the tests never touch the network.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

PUBLIC = ("level", "xp_into", "xp_next", "mood", "mood_label", "streak", "goal_hit")
DEFAULT_URL = "https://northbridgelandmark.com/api/deskpet/pet"
MIN_TOKEN = 32
log = logging.getLogger("bridge.site")


def public_state(state: dict) -> dict:
    return {k: state[k] for k in PUBLIC}


def safe_url(url: str) -> bool:
    """https anywhere, or plain http to this Mac (wrangler dev), so the token never travels unencrypted."""
    u = urlsplit(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))


def http_post(url: str, token: str, body: bytes, timeout: float = 10) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "user-agent": "ESP-P4_Box bridge",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status
    except urllib.error.HTTPError as err:
        return err.code


class Site:
    def __init__(self, token: str, url: str = DEFAULT_URL, post: Callable[[str, str, bytes], int] = http_post):
        self.token, self.url, self.post = token, url, post

    @classmethod
    def from_env(cls, env=os.environ) -> "Site | None":
        """None (and a log line saying why) unless PET_SITE_TOKEN is long enough and the URL is safe."""
        token = env.get("PET_SITE_TOKEN", "").strip()
        url = env.get("PET_SITE_URL", "").strip() or DEFAULT_URL
        if not token:
            return None
        if len(token) < MIN_TOKEN:
            log.warning("PET_SITE_TOKEN is under %d characters; the pet stays off the website", MIN_TOKEN)
            return None
        if not safe_url(url):
            log.warning("PET_SITE_URL must be https (or http to localhost); the pet stays off the website")
            return None
        return cls(token, url)

    def send(self, state: dict) -> bool:
        body = json.dumps(public_state(state), separators=(",", ":")).encode()
        status = self.post(self.url, self.token, body)
        if status != 200:
            log.warning("the website answered %s to the pet's state", status)
            return False
        return True
