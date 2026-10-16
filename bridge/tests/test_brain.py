import unittest
from datetime import date, datetime, timedelta

from petbrain.brain import (Day, day_xp, total_xp, level_threshold, level_for_xp, streak, streak_at_risk,
                            mood, mood_label, compute_state, MOOD_START)

T = date(2026, 10, 15)


def run(days_ago_to_day: dict) -> list:
    return [Day(T - timedelta(days=k), **v) for k, v in days_ago_to_day.items()]


class XpTests(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(day_xp(Day(T, commits=1)), 1)
        self.assertEqual(day_xp(Day(T, prs=1)), 3)
        self.assertEqual(day_xp(Day(T, issues=1)), 3)
        self.assertEqual(day_xp(Day(T, reviews=1)), 4)
        self.assertEqual(day_xp(Day(T, commits=2, prs=1, issues=1, reviews=1)), 2 + 3 + 3 + 4)

    def test_caps(self):
        self.assertEqual(day_xp(Day(T, commits=200)), 30)
        self.assertEqual(day_xp(Day(T, prs=10)), 10)
        self.assertEqual(day_xp(Day(T, issues=10)), 10)
        self.assertEqual(day_xp(Day(T, reviews=10)), 10)
        self.assertEqual(day_xp(Day(T, other=500)), 30)
        self.assertEqual(day_xp(Day(T, commits=999, prs=99, issues=99, reviews=99)), 60)

    def test_total(self):
        self.assertEqual(total_xp(run({0: {"commits": 5}, 1: {"reviews": 1}})), 9)


class LevelTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual([level_threshold(l) for l in range(1, 10)], [0, 100, 300, 700, 1500, 3000, 6000, 12000, 24000])

    def test_level_for_xp(self):
        self.assertEqual(level_for_xp(0), (1, 0, 100))
        self.assertEqual(level_for_xp(99), (1, 99, 100))
        self.assertEqual(level_for_xp(100), (2, 0, 200))
        self.assertEqual(level_for_xp(812), (4, 112, 800))
        self.assertEqual(level_for_xp(3000), (6, 0, 3000))


class StreakTests(unittest.TestCase):
    def test_counts_through_today(self):
        self.assertEqual(streak(run({0: {"commits": 1}, 1: {"commits": 1}, 2: {"prs": 1}}), T), 3)

    def test_gap_breaks(self):
        self.assertEqual(streak(run({0: {"commits": 1}, 2: {"commits": 1}}), T), 1)

    def test_empty_today_keeps_yesterdays_streak(self):
        self.assertEqual(streak(run({1: {"commits": 1}, 2: {"commits": 1}}), T), 2)

    def test_zero_when_nothing(self):
        self.assertEqual(streak([], T), 0)

    def test_at_risk_only_in_evening_with_live_streak(self):
        days = run({1: {"commits": 1}})
        self.assertFalse(streak_at_risk(days, datetime(2026, 10, 15, 9)))
        self.assertTrue(streak_at_risk(days, datetime(2026, 10, 15, 19)))
        self.assertFalse(streak_at_risk(run({0: {"commits": 1}}), datetime(2026, 10, 15, 23)))
        self.assertFalse(streak_at_risk([], datetime(2026, 10, 15, 23)))


class MoodTests(unittest.TestCase):
    def test_idle_decays_one_per_day_to_floor(self):
        self.assertEqual(mood([], T), 0)
        busy = run({k: {"commits": 20} for k in range(1, 70)})
        self.assertEqual(mood(busy, T), 10)
        # Same history but the last three days before today idle: 10 - 3.
        gap = run({k: {"commits": 20} for k in range(4, 70)})
        self.assertEqual(mood(gap, T), 7)

    def test_long_idle_then_one_day_then_idle(self):
        # 60 idle days drive mood to 0; one 20 XP day lifts it to 2; six idle days bring it back to 0.
        self.assertEqual(mood(run({7: {"commits": 20}}), T), 0)
        # Within a short history the start value shows: 3 idle days from 5 -> 2, then +2 today... except
        # today's gain counts: 2 + 2 = 4.
        self.assertEqual(mood(run({4: {"commits": 20}, 0: {"commits": 20}}), T, window=4), MOOD_START + 2 - 3 + 2)

    def test_today_empty_does_not_punish_yet(self):
        busy = run({k: {"commits": 20} for k in range(1, 70)})
        self.assertEqual(mood(busy, T), 10)

    def test_gain_capped(self):
        # 60 idle days before today drive mood to the floor; today's 40 XP adds at most +3.
        self.assertEqual(mood(run({0: {"commits": 500, "reviews": 50}}), T), 3)

    def test_labels(self):
        self.assertEqual([mood_label(m) for m in (0, 2, 3, 5, 6, 8, 9, 10)],
                         ["sad", "sad", "neutral", "neutral", "happy", "happy", "ecstatic", "ecstatic"])


class StateTests(unittest.TestCase):
    def test_state_shape_and_goal(self):
        days = run({0: {"commits": 98, "prs": 3, "issues": 1, "reviews": 2}, 1: {"commits": 5}})
        s = compute_state(days, datetime(2026, 10, 15, 20, 0), login="x")
        self.assertEqual(s["v"], 1)
        self.assertEqual(s["today"]["total"], 104)
        self.assertEqual(s["today"]["xp"], 30 + 9 + 3 + 8)
        self.assertTrue(s["goal_hit"])
        self.assertTrue(s["big_day"])
        self.assertEqual(s["streak"], 2)
        self.assertFalse(s["streak_at_risk"])
        self.assertEqual(s["events"], [])
        self.assertEqual(s["level"], 1)

    def test_events_fire_once(self):
        days = run({0: {"commits": 150}})
        now = datetime(2026, 10, 15, 20, 0)
        prev = {"ts": "2026-10-15T19:50:00", "level": 1, "goal_hit": False, "big_day": False}
        s = compute_state(days, now, prev=prev)
        self.assertEqual(s["events"], ["goal_hit", "big_day"])
        s2 = compute_state(days, now, prev=s)
        self.assertEqual(s2["events"], [])

    def test_level_up_event(self):
        days = run({k: {"commits": 30} for k in range(0, 4)})  # 120 XP -> level 2
        s = compute_state(days, datetime(2026, 10, 15, 12), prev={"ts": "2026-10-15T11:50:00", "level": 1,
                                                                   "goal_hit": True, "big_day": False})
        self.assertEqual(s["level"], 2)
        self.assertIn("level_up", s["events"])
        self.assertNotIn("goal_hit", s["events"])

    def test_new_day_refires_goal(self):
        days = run({0: {"commits": 25}})
        s = compute_state(days, datetime(2026, 10, 15, 12), prev={"ts": "2026-10-14T23:50:00", "level": 1,
                                                                   "goal_hit": True, "big_day": False})
        self.assertEqual(s["events"], ["goal_hit"])


if __name__ == "__main__":
    unittest.main()
