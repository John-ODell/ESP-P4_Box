"""The pet's brain: deeds -> XP -> level, plus mood, streak, and daily goal.

Mimics the Flipper Zero dolphin's mechanics (weighted deeds, per-category daily
caps, widening level thresholds, a slow-moving mood) with GitHub contributions
as the deeds. Pure functions over a list of `Day` records so it can be unit
tested and ported. Nothing here talks to GitHub or MQTT.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

CATEGORIES = ("commits", "prs", "issues", "reviews")

# XP per deed. Reviews and issues earn extra on purpose.
WEIGHTS = {"commits": 1, "prs": 3, "issues": 3, "reviews": 4, "other": 1}

# Max XP a category can earn in one day, so a 200-commit day does not break the curve.
CAPS_XP = {"commits": 30, "prs": 10, "issues": 10, "reviews": 10, "other": 30}

# Cumulative XP needed to reach level 2, 3, 4, ... Level 1 is 0 XP.
BASE_THRESHOLDS = (100, 300, 700, 1500, 3000)

DEFAULT_GOAL = 20       # contributions per day
BIG_DAY = 100           # contributions per day
MOOD_START = 5
MOOD_MAX = 10
MOOD_IDLE_DROP = 1      # mood never falls more than this in one day
MOOD_GAIN_PER_XP = 10   # +1 mood per 10 XP earned that day...
MOOD_GAIN_CAP = 3       # ...up to +3
MOOD_WINDOW_DAYS = 60
STREAK_RISK_HOUR = 18   # from this local hour on, an empty day puts the streak "at risk"


@dataclass
class Day:
    date: date
    commits: int = 0
    prs: int = 0
    issues: int = 0
    reviews: int = 0
    other: int = 0  # calendar-only contributions with no category breakdown

    @property
    def total(self) -> int:
        return self.commits + self.prs + self.issues + self.reviews + self.other

    def counts(self) -> dict:
        return {"commits": self.commits, "prs": self.prs, "issues": self.issues, "reviews": self.reviews,
                "other": self.other}


def day_xp(day: Day, weights: dict = WEIGHTS, caps: dict = CAPS_XP) -> int:
    """XP a single day is worth after weights and per-category caps."""
    xp = 0
    for cat, n in day.counts().items():
        xp += min(n * weights[cat], caps[cat])
    return xp


def total_xp(days: Iterable[Day]) -> int:
    return sum(day_xp(d) for d in days)


def level_threshold(level: int) -> int:
    """Cumulative XP needed to *reach* `level` (level 1 -> 0). Doubles after the base table."""
    if level <= 1:
        return 0
    idx = level - 2
    if idx < len(BASE_THRESHOLDS):
        return BASE_THRESHOLDS[idx]
    return BASE_THRESHOLDS[-1] * (2 ** (idx - len(BASE_THRESHOLDS) + 1))


def level_for_xp(xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_level, xp_needed_for_next_level)."""
    level = 1
    while xp >= level_threshold(level + 1):
        level += 1
    into = xp - level_threshold(level)
    span = level_threshold(level + 1) - level_threshold(level)
    return level, into, span


def _by_date(days: Sequence[Day]) -> dict:
    return {d.date: d for d in days}


def streak(days: Sequence[Day], today: date) -> int:
    """Consecutive contribution days ending today, or ending yesterday if today is still empty."""
    by = _by_date(days)
    cursor = today
    if (by.get(today) is None or by[today].total == 0):
        cursor = today - timedelta(days=1)
    n = 0
    while cursor in by and by[cursor].total > 0:
        n += 1
        cursor -= timedelta(days=1)
    return n


def streak_at_risk(days: Sequence[Day], now: datetime) -> bool:
    """True in the evening of a day with no contributions yet while a streak is alive."""
    today = now.date()
    by = _by_date(days)
    today_total = by[today].total if today in by else 0
    return today_total == 0 and now.hour >= STREAK_RISK_HOUR and streak(days, today) > 0


def mood(days: Sequence[Day], today: date, window: int = MOOD_WINDOW_DAYS) -> int:
    """Happiness 0..10. Rises with the day's XP, drops by at most 1 on an idle day."""
    by = _by_date(days)
    m = MOOD_START
    for i in range(window, -1, -1):
        d = today - timedelta(days=i)
        day = by.get(d)
        if day is None or day.total == 0:
            if d == today:
                continue  # today is not over; do not punish yet
            m = max(0, m - MOOD_IDLE_DROP)
        else:
            gain = min(MOOD_GAIN_CAP, day_xp(day) // MOOD_GAIN_PER_XP)
            m = min(MOOD_MAX, m + gain)
    return m


def mood_label(m: int) -> str:
    if m <= 2:
        return "sad"
    if m <= 5:
        return "neutral"
    if m <= 8:
        return "happy"
    return "ecstatic"


def compute_state(days: Sequence[Day], now: datetime, *, login: str = "", goal: int = DEFAULT_GOAL,
                  prev: dict | None = None) -> dict:
    """Build the compact, self-describing state the box renders.

    `prev` is the previously published state (or None); it is only used to
    derive one-shot `events` (level_up, goal_hit, big_day) so the box can
    celebrate once.
    """
    today = now.date()
    by = _by_date(days)
    td = by.get(today, Day(today))
    xp = total_xp(days)
    level, into, span = level_for_xp(xp)
    m = mood(days, today)
    st = streak(days, today)
    state = {
        "v": 1,
        "ts": now.isoformat(timespec="seconds"),
        "login": login,
        "level": level,
        "xp": xp,
        "xp_into": into,
        "xp_next": span,
        "mood": m,
        "mood_label": mood_label(m),
        "streak": st,
        "streak_at_risk": streak_at_risk(days, now),
        "today": {"total": td.total, "commits": td.commits, "prs": td.prs, "issues": td.issues,
                  "reviews": td.reviews, "other": td.other, "xp": day_xp(td)},
        "goal": goal,
        "goal_hit": td.total >= goal,
        "big_day": td.total >= BIG_DAY,
        "events": [],
    }
    if prev:
        same_day = str(prev.get("ts", ""))[:10] == today.isoformat()
        if level > int(prev.get("level", level)):
            state["events"].append("level_up")
        if state["goal_hit"] and not (same_day and prev.get("goal_hit")):
            state["events"].append("goal_hit")
        if state["big_day"] and not (same_day and prev.get("big_day")):
            state["events"].append("big_day")
    return state
