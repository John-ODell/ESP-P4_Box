import json
import unittest
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from petbrain.radar import EVERY, TOPIC_RADAR, Radar, fetch_counts

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
COUNTS = {"c": 964, "p": 542, "r": 538, "i": 579}


class FetchTests(unittest.TestCase):
    def test_four_searches_for_the_login_since_the_day_given(self):
        asked = []

        def get(token, path):
            asked.append(path)
            if path == "user":
                return {"login": "John-ODell"}
            return {"total_count": len(asked)}
        self.assertEqual(fetch_counts(date(2025, 10, 20), token="t", get=get), {"c": 2, "p": 3, "r": 4, "i": 5})
        queries = [(urlsplit(p).path, parse_qs(urlsplit(p).query)["q"][0]) for p in asked[1:]]
        self.assertEqual(queries, [
            ("search/commits", "author:John-ODell author-date:>=2025-10-20"),
            ("search/issues", "author:John-ODell is:pr created:>=2025-10-20"),
            ("search/issues", "reviewed-by:John-ODell is:pr updated:>=2025-10-20"),
            ("search/issues", "author:John-ODell is:issue created:>=2025-10-20")])

    def test_an_incomplete_search_fails_the_whole_radar(self):
        def get(token, path):
            return {"login": "x"} if path == "user" else {"total_count": 3, "incomplete_results": True}
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            fetch_counts(date(2025, 10, 20), token="t", get=get)

    def test_a_search_without_a_count_fails_the_whole_radar(self):
        def get(token, path):
            return {"login": "x"} if path == "user" else {"message": "API rate limit exceeded"}
        with self.assertRaises(RuntimeError):
            fetch_counts(date(2025, 10, 20), token="t", get=get)


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.asked, self.now = [], [], NOW
        self.counts = dict(COUNTS)

        def fetch(since):
            self.asked.append(since)
            return dict(self.counts)
        self.radar = Radar(lambda t, p, r: self.sent.append((t, json.loads(p), r)), fetch, now=lambda: self.now)

    def test_publishes_retained_with_the_first_day_counted(self):
        self.assertTrue(self.radar.update())
        self.assertEqual(self.sent, [(TOPIC_RADAR, {"v": 1, "since": "2025-10-20", **COUNTS}, True)])
        self.assertEqual(self.asked, [date(2025, 10, 20)])

    def test_asks_github_at_most_once_an_hour_and_publishes_only_a_change(self):
        self.radar.update()
        self.now += timedelta(seconds=EVERY - 60)
        self.counts["c"] += 1
        self.assertFalse(self.radar.update())
        self.assertEqual(len(self.asked), 1)
        self.now += timedelta(seconds=120)
        self.assertTrue(self.radar.update())
        self.now += timedelta(seconds=EVERY)
        self.assertFalse(self.radar.update())   # asked again, nothing new
        self.assertEqual((len(self.asked), len(self.sent)), (3, 2))

    def test_a_clock_set_back_makes_it_due(self):
        self.radar.update()
        self.now -= timedelta(hours=3)
        self.radar.update()
        self.assertEqual(len(self.asked), 2)

    def test_a_failure_raises_and_the_next_poll_tries_again(self):
        def broken(since):
            raise OSError("offline")
        radar = Radar(lambda *a: self.fail("published"), broken, now=lambda: NOW)
        for _ in range(2):
            with self.assertRaises(OSError):
                radar.update()


if __name__ == "__main__":
    unittest.main()
