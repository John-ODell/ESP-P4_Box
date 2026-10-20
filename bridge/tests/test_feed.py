import json
import unittest
from datetime import datetime, timezone

from petbrain.feed import BUDGET, MAX_TEXT, RECENT_DAYS, SHOWN, TOPIC_FEED, Feed, build, fetch_github, payload
from petbrain.todo import Todo
from petbrain.todo_refs import ref_key

NOW = datetime(2026, 10, 20, 1, 30, tzinfo=timezone.utc)
T = int(NOW.timestamp())


def ts(h, m=0, day=20):
    return datetime(2026, 10, day, h, m, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(n, title, created, merged=None, closed=None, repo="ESP-P4_Box"):
    return {"number": n, "title": title, "createdAt": created, "mergedAt": merged, "closedAt": closed or merged,
            "repository": {"name": repo, "owner": {"login": "John-ODell"}}}


def issue(n, title, created, closed=None, reason=None, repo="ESP-P4_Box"):
    return {"number": n, "title": title, "createdAt": created, "closedAt": closed, "stateReason": reason,
            "repository": {"name": repo, "owner": {"login": "John-ODell"}}}


def viewer(prs=(), issues=(), commits=()):
    return {"pullRequests": {"nodes": list(prs)}, "issues": {"nodes": list(issues)},
            "contributionsCollection": {"commitContributionsByRepository": [
                {"repository": {"name": repo}, "contributions": {"nodes": [{"occurredAt": day, "commitCount": n}]}}
                for repo, day, n in commits]}}


class BuildTests(unittest.TestCase):
    def test_each_pr_and_issue_is_one_line_in_its_latest_state(self):
        lines = build(viewer(
            prs=[pr(101, "Follow refs", ts(0, 50), merged=ts(1, 9)), pr(73, "Spike results", ts(1, 11)),
                 pr(60, "Dead end", ts(0, 10), closed=ts(0, 20))],
            issues=[issue(91, "Refs tick", ts(0, 1), closed=ts(1, 9)), issue(102, "Batch", ts(1, 9, ))]), T)
        self.assertEqual([(l["k"], l["x"]) for l in lines], [
            ("pr_open", "#73 Spike results"), ("pr_merged", "#101 Follow refs"), ("issue_closed", "#91 Refs tick"),
            ("issue_open", "#102 Batch"), ("pr_closed", "#60 Dead end")])
        self.assertEqual(lines[1]["t"], int(datetime(2026, 10, 20, 1, 9, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(lines[1]["r"], "ESP-P4_Box")

    def test_issues_closed_as_not_planned_or_duplicate_are_dropped(self):
        lines = build(viewer(issues=[issue(1, "a", ts(0), closed=ts(1), reason="NOT_PLANNED"),
                                     issue(2, "b", ts(0), closed=ts(1), reason="DUPLICATE"),
                                     issue(3, "c", ts(0), closed=ts(1), reason="COMPLETED")]), T)
        self.assertEqual(sorted(l["k"] for l in lines), ["issue_closed", "issue_dropped", "issue_dropped"])

    def test_todays_commits_sort_as_of_now_and_past_days_as_of_their_end(self):
        lines = build(viewer(prs=[pr(5, "late", ts(1, 20)), pr(4, "yesterday evening", ts(20, day=19))],
                             commits=[("ESP-P4_Box", ts(0), 4), ("old", ts(0, day=18), 1), ("none", ts(0), 0)]), T)
        self.assertEqual([l["x"] for l in lines], ["4 commits", "#5 late", "#4 yesterday evening", "1 commit"])
        self.assertEqual(lines[0]["t"], int(datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()))

    def test_state_wins_over_the_times_so_a_reopened_item_is_open(self):
        lines = build(viewer(prs=[dict(pr(7, "reopened", ts(0), closed=ts(1)), state="OPEN")],
                             issues=[dict(issue(8, "reopened", ts(0), closed=ts(1)), state="OPEN"),
                                     dict(issue(9, "shut", ts(0), closed=ts(1)), state="CLOSED")]), T)
        self.assertEqual(sorted(l["k"] for l in lines), ["issue_closed", "issue_open", "pr_open"])

    def test_prs_and_issues_older_than_the_window_are_left_out(self):
        lines = build(viewer(prs=[pr(1, "old", ts(1, day=20 - RECENT_DAYS - 1)), pr(2, "new", ts(1, day=19))],
                             commits=[("r", ts(0, day=18), 2)]), T)
        self.assertEqual([l["x"] for l in lines], ["#2 new", "2 commits"])

    def test_odd_nodes_are_skipped_and_text_is_one_line_of_ascii_cut_to_fit(self):
        long = "“Quoted”\nand\x01 " + "word " * 30
        lines = build({"pullRequests": {"nodes": [None, "x", pr(1, long, ts(1)), pr(2, "no time", "garbage")]},
                       "issues": None, "contributionsCollection": {"commitContributionsByRepository": [
                           None, {"repository": None, "contributions": {"nodes": [{"occurredAt": ts(0),
                                                                                   "commitCount": "3"}]}}]}}, T)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["x"].startswith('#1 "Quoted" and word'))
        self.assertLessEqual(len(lines[0]["x"]), MAX_TEXT)
        self.assertTrue(lines[0]["x"].isascii())
        self.assertEqual(build({}, T), [])


class PayloadTests(unittest.TestCase):
    def test_at_most_shown_lines_and_budget_bytes(self):
        lines = build(viewer(prs=[pr(i, '"' * 200, ts(1, i)) for i in range(SHOWN + 5)]), T)
        text = payload(lines)
        self.assertLessEqual(len(text), BUDGET)
        got = json.loads(text)["items"]
        self.assertGreater(len(got), 0)
        self.assertLessEqual(len(got), SHOWN)
        self.assertEqual(got[0]["x"], lines[0]["x"])   # the newest kept, the oldest cut
        self.assertEqual(json.loads(payload([])), {"v": 1, "items": []})

    def test_prs_and_issues_send_their_number_but_not_the_ref_or_title(self):
        lines = build(viewer(prs=[pr(5, "x", ts(1))], commits=[("r", ts(0), 2)]), T)
        self.assertEqual([l.get("ref") for l in lines], [None, "John-ODell/ESP-P4_Box#5"])   # today's commits first
        self.assertEqual([sorted(l) for l in json.loads(payload(lines))["items"]],
                         [["k", "r", "t", "x"], ["k", "n", "r", "t", "x"]])


class AddTests(unittest.TestCase):
    """A tap on an open PR or issue on the box (#104)."""
    REPO = ("John-ODell", "ESP-P4_Box")

    def setUp(self):
        self.todo = Todo(lambda *a: None, lambda: NOW)
        answer = viewer(prs=[pr(73, "Spike results", ts(1)), pr(70, "Merged one", ts(0), merged=ts(1))],
                        issues=[issue(74, "Run the builder on the Pi", ts(1, 5), repo="flasheide")])
        self.feed = Feed(lambda *a: None, lambda since: answer, now=lambda: NOW, todo=self.todo,
                         key=lambda ref: ref_key(ref, self.REPO))
        self.feed.update()

    def test_an_open_issue_goes_on_the_list_with_its_title_and_full_ref(self):
        item = self.feed.add(b'{"r":"flasheide","n":74}')
        self.assertEqual((item["text"], item["ref"], item["by"], item["done"]),
                         ("Run the builder on the Pi", "John-ODell/flasheide#74", "box", False))

    def test_one_already_open_on_the_list_is_not_added_again_even_as_a_plain_ref(self):
        self.todo.add("Spike", ref="#73")
        self.assertIsNone(self.feed.add(b'{"r":"ESP-P4_Box","n":73}'))
        self.assertIsNotNone(self.feed.add(b'{"r":"flasheide","n":74}'))
        self.assertIsNone(self.feed.add(b'{"r":"flasheide","n":74}'))
        self.assertEqual(len(self.todo.ref_items()), 2)

    def test_owner_and_repo_names_match_in_any_case(self):
        self.todo.add("Spike", ref="john-odell/esp-p4_box#73")
        self.assertIsNone(self.feed.add(b'{"r":"ESP-P4_Box","n":73}'))
        self.assertEqual(len(self.todo.ref_items()), 1)

    def test_a_done_item_with_the_ref_does_not_stop_a_new_one(self):
        old = self.todo.add("Spike", ref="#73")
        self.todo.set_done(old["id"], True)
        self.assertIsNotNone(self.feed.add(b'{"r":"ESP-P4_Box","n":73}'))

    def test_closed_unknown_and_malformed_taps_add_nothing(self):
        for raw in (b'{"r":"ESP-P4_Box","n":70}', b'{"r":"ESP-P4_Box","n":99}', b'{"r":"ESP-P4_Box","n":true}',
                    b'{"r":5,"n":73}', b"[]", b"not json", b'{"n":73}'):
            with self.assertLogs("bridge.feed", "WARNING"):
                self.assertIsNone(self.feed.add(raw), raw)
        self.assertEqual(self.todo.ref_items(), [])

    def test_a_feed_without_a_list_adds_nothing(self):
        feed = Feed(lambda *a: None, lambda since: viewer(prs=[pr(73, "x", ts(1))]), now=lambda: NOW)
        feed.update()
        with self.assertLogs("bridge.feed", "WARNING"):
            self.assertIsNone(feed.add(b'{"r":"ESP-P4_Box","n":73}'))


class MergedTests(unittest.TestCase):
    """The pet celebrates a merge (#106)."""

    def setUp(self):
        self.sent, self.now = [], NOW
        self.answer = viewer(prs=[pr(70, "Old", ts(0), merged=ts(0, 30)), pr(73, "Open", ts(1))])
        self.feed = Feed(lambda t, p, r: self.sent.append((t, json.loads(p), r)), lambda since: self.answer,
                         now=lambda: self.now)

    def merged(self):
        return [(p["items"], r) for t, p, r in self.sent if t == "pet/merged"]

    def test_the_first_poll_celebrates_nothing_already_merged(self):
        self.feed.update()
        self.assertEqual(self.merged(), [])

    def test_a_pr_merged_since_the_last_poll_is_sent_once_not_retained(self):
        self.feed.update()
        self.answer["pullRequests"]["nodes"][1] = pr(73, "Open", ts(1), merged=ts(1, 20))
        self.feed.update()
        self.feed.update()
        self.assertEqual(self.merged(), [([{"r": "ESP-P4_Box", "n": 73, "x": "#73 Open"}], False)])

    def test_an_old_merge_coming_back_into_the_feed_is_not_news(self):
        self.feed.update()
        self.answer["pullRequests"]["nodes"].append(pr(50, "Long ago", ts(0, day=19), merged=ts(1, day=19)))
        self.feed.update()
        self.assertEqual(self.merged(), [])

    def test_a_merge_that_left_the_feed_and_came_back_is_not_sent_again(self):
        self.feed.update()
        self.answer["pullRequests"]["nodes"][1] = pr(73, "Open", ts(1), merged=ts(1, 20))
        self.feed.update()
        gone = self.answer["pullRequests"]["nodes"].pop(1)
        self.feed.update()
        self.answer["pullRequests"]["nodes"].append(gone)
        self.feed.update()
        self.assertEqual(len(self.merged()), 1)


class FeedTests(unittest.TestCase):
    def test_publishes_retained_only_when_the_feed_changed(self):
        sent, asked = [], []
        answer = viewer(prs=[pr(1, "a", ts(1))])

        def fetch(since):
            asked.append(since)
            return answer
        feed = Feed(lambda t, p, r: sent.append((t, json.loads(p), r)), fetch, now=lambda: NOW)
        self.assertTrue(feed.update())
        self.assertFalse(feed.update())
        answer["pullRequests"]["nodes"].append(pr(2, "b", ts(1, 5)))
        self.assertTrue(feed.update())
        self.assertEqual([(t, r, len(p["items"])) for t, p, r in sent], [(TOPIC_FEED, True, 1), (TOPIC_FEED, True, 2)])
        self.assertEqual(asked[0], datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc))

    def test_a_failed_fetch_raises_and_publishes_nothing(self):
        sent = []

        def broken(since):
            raise OSError("offline")
        with self.assertRaises(OSError):
            Feed(lambda *a: sent.append(a), broken, now=lambda: NOW).update()
        self.assertEqual(sent, [])

    def test_fetch_github_sends_since_and_returns_the_viewer(self):
        calls = []

        def post(token, query, variables=None):
            calls.append((token, variables))
            return {"viewer": {"x": 1}}
        self.assertEqual(fetch_github(NOW, token="t", post=post), {"x": 1})
        self.assertEqual(calls, [("t", {"since": "2026-10-20T01:30:00+00:00"})])


if __name__ == "__main__":
    unittest.main()
