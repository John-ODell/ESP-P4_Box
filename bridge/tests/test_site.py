import json
import unittest

from petbrain.site import DEFAULT_URL, PUBLIC, Site, public_state, safe_url

STATE = {"v": 1, "ts": "2026-10-15T19:51:08-05:00", "login": "John-ODell",
         "level": 3, "xp": 428, "xp_into": 128, "xp_next": 400,
         "mood": 10, "mood_label": "ecstatic", "streak": 14, "streak_at_risk": False,
         "today": {"total": 111, "commits": 4, "prs": 2, "issues": 0, "reviews": 0, "other": 105, "xp": 40},
         "goal": 20, "goal_hit": True, "big_day": True, "events": ["level_up"]}
TOKEN = "k" * 40


class PublicStateTests(unittest.TestCase):
    def test_only_what_a_visitor_may_see(self):
        self.assertEqual(public_state(STATE), {"level": 3, "xp_into": 128, "xp_next": 400, "mood": 10,
                                               "mood_label": "ecstatic", "streak": 14, "goal_hit": True})

    def test_nothing_that_tells_the_time_or_the_day(self):
        sent = json.dumps(public_state(STATE))
        for word in ("ts", "-05:00", "today", "login", "John-ODell", "events", "big_day"):
            self.assertNotIn(word, sent)
        self.assertEqual(set(public_state(STATE)), set(PUBLIC))


class SettingsTests(unittest.TestCase):
    def test_off_without_a_token(self):
        self.assertIsNone(Site.from_env({}))
        self.assertIsNone(Site.from_env({"PET_SITE_TOKEN": "   "}))

    def test_a_short_token_is_refused(self):
        with self.assertLogs("bridge.site", "WARNING") as logs:
            self.assertIsNone(Site.from_env({"PET_SITE_TOKEN": "x" * 31}))
        self.assertIn("under 32", logs.output[0])

    def test_the_default_url_is_the_site(self):
        site = Site.from_env({"PET_SITE_TOKEN": TOKEN})
        self.assertEqual((site.token, site.url), (TOKEN, DEFAULT_URL))

    def test_plain_http_only_to_this_mac(self):
        self.assertTrue(safe_url("https://northbridgelandmark.com/api/deskpet/pet"))
        self.assertTrue(safe_url("http://localhost:8787/api/deskpet/pet"))
        self.assertTrue(safe_url("http://127.0.0.1:8787/api/deskpet/pet"))
        self.assertFalse(safe_url("http://northbridgelandmark.com/api/deskpet/pet"))
        self.assertFalse(safe_url("ftp://localhost/x"))
        with self.assertLogs("bridge.site", "WARNING"):
            self.assertIsNone(Site.from_env({"PET_SITE_TOKEN": TOKEN, "PET_SITE_URL": "http://example.com/api"}))


class SendTests(unittest.TestCase):
    def test_posts_the_public_state_with_the_token(self):
        sent = []
        site = Site(TOKEN, "https://example.test/api/deskpet/pet", post=lambda url, token, body: sent.append((url, token, body)) or 200)
        self.assertTrue(site.send(STATE))
        url, token, body = sent[0]
        self.assertEqual((url, token), ("https://example.test/api/deskpet/pet", TOKEN))
        self.assertEqual(json.loads(body), public_state(STATE))

    def test_a_refusal_is_logged_and_reported(self):
        site = Site(TOKEN, post=lambda url, token, body: 401)
        with self.assertLogs("bridge.site", "WARNING") as logs:
            self.assertFalse(site.send(STATE))
        self.assertIn("401", logs.output[0])


if __name__ == "__main__":
    unittest.main()
