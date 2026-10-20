import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from petbrain import github_source
from petbrain.todo_refs import RefChecker, default_repo, fetch_github, resolve, state_of
from tests import test_todo
from tests.test_todo import make

REPO = ("John-ODell", "ESP-P4_Box")


class Fake:
    """GitHub as a dict of {(owner, repo, n): state}; records every batch it was asked."""
    def __init__(self, states=None):
        self.states = dict(states or {})
        self.asked = []

    def __call__(self, keys):
        self.asked.append(list(keys))
        return {k: self.states.get(k) for k in keys}


def checker(fake, repo=REPO, store=None):
    todo, sent = make(store)
    return todo, sent, RefChecker(todo, fetch=fake, repo=repo, spawn=lambda f: f())


class ResolveTests(unittest.TestCase):
    def test_plain_and_full_refs(self):
        self.assertEqual(resolve("#85", REPO), ("John-ODell", "ESP-P4_Box", 85))
        self.assertEqual(resolve("a/b#7", REPO), ("a", "b", 7))
        self.assertEqual(resolve("a/b#7", None), ("a", "b", 7))
        self.assertIsNone(resolve("#85", None))

    def test_github_states(self):
        for node, want in (({"__typename": "Issue", "state": "OPEN"}, "open"),
                           ({"__typename": "Issue", "state": "CLOSED"}, "done"),
                           ({"__typename": "PullRequest", "state": "MERGED"}, "done"),
                           ({"__typename": "PullRequest", "state": "CLOSED"}, "dropped"),
                           ({"__typename": "PullRequest", "state": "OPEN"}, "open"),
                           ({"__typename": "Discussion", "state": "OPEN"}, None), (None, None), ("x", None)):
            self.assertEqual(state_of(node), want, node)

    def test_default_repo_from_the_environment_or_the_origin_remote(self):
        self.assertEqual(default_repo({"PET_TODO_REPO": "a/b"}), ("a", "b"))
        with self.assertLogs("bridge.todo", "WARNING"):
            self.assertIsNone(default_repo({"PET_TODO_REPO": "nope"}))
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            self.assertIsNone(default_repo({}, Path(d)))
            for url in ("git@github.com:John-ODell/ESP-P4_Box.git", "https://github.com/John-ODell/ESP-P4_Box",
                        "https://github.com/John-ODell/ESP-P4_Box.git"):
                subprocess.run(["git", "-C", d, "remote", "remove", "origin"], capture_output=True)
                subprocess.run(["git", "-C", d, "remote", "add", "origin", url], check=True)
                self.assertEqual(default_repo({}, Path(d)), REPO, url)
            subprocess.run(["git", "-C", d, "remote", "set-url", "origin", "https://gitlab.com/a/b.git"], check=True)
            self.assertIsNone(default_repo({}, Path(d)))


class FetchTests(unittest.TestCase):
    def test_one_query_for_every_ref_and_a_missing_one_is_none(self):
        calls = []

        def post(token, query, variables=None, partial=False):
            calls.append((token, query, partial))
            return {"r0": {"issueOrPullRequest": {"__typename": "PullRequest", "state": "MERGED"}}, "r1": None}
        keys = [("John-ODell", "ESP-P4_Box", 97), ("a", "gone", 1)]
        self.assertEqual(fetch_github(keys, token="t", post=post), {keys[0]: "done", keys[1]: None})
        token, query, partial = calls[0]
        self.assertTrue(partial)
        self.assertIn('repository(owner: "John-ODell", name: "ESP-P4_Box") { issueOrPullRequest(number: 97)', query)
        self.assertEqual(fetch_github([], token="t", post=post), {})
        self.assertEqual(len(calls), 1)

    def test_graphql_keeps_partial_data_only_when_asked(self):
        reply = {"data": {"r0": None, "r1": {"x": 1}}, "errors": [{"message": "Could not resolve to a Repository"}]}

        def opened(*a, **kw):
            return io.BytesIO(json.dumps(reply).encode())
        with mock.patch.object(github_source.urllib.request, "urlopen", opened):
            self.assertEqual(github_source.graphql("t", "q", partial=True), reply["data"])
            with self.assertRaises(RuntimeError):
                github_source.graphql("t", "q")
            reply["data"] = None
            with self.assertRaises(RuntimeError):
                github_source.graphql("t", "q", partial=True)


class FollowTests(unittest.TestCase):
    def test_a_closed_issue_or_merged_pr_ticks_the_item(self):
        fake = Fake({(*REPO, 85): "done", ("a", "b", 3): "open"})
        todo, sent, refs = checker(fake)
        todo.add("Issue work", ref="#85")
        todo.add("Other repo", ref="a/b#3")
        todo.add("No ref")
        self.assertEqual(refs.check(), 1)
        self.assertEqual(len(fake.asked), 1)   # one GitHub call for everything
        items = {i["id"]: i for i in todo._items}
        self.assertEqual((items[1]["done"], items[1]["done_by"]), (True, "github"))
        self.assertFalse(items[2]["done"])
        self.assertEqual(sent[-1][1]["open"], 2)
        n = len(sent)
        self.assertEqual(refs.check(), 0)   # nothing new: nothing published
        self.assertEqual(len(sent), n)

    def test_a_hand_tick_on_open_work_is_flagged_at_once_until_github_agrees(self):
        fake = Fake({(*REPO, 85): "open"})
        todo, sent, refs = checker(fake)
        todo.add("Fix it", ref="85")
        todo.set_done(1, True, "box")             # the hook checks this item straight away
        self.assertEqual(fake.asked, [[(*REPO, 85)]])
        self.assertEqual(sent[-1][1]["items"][0]["flag"], "#85 still open")
        self.assertEqual(todo.summary()["items"][0]["flag"], "#85 still open")
        fake.states[(*REPO, 85)] = "done"
        self.assertEqual(refs.check(), 1)
        item = todo._items[0]
        self.assertEqual((item["done"], item["done_by"], item["flag"]), (True, "box", None))
        self.assertNotIn("flag", sent[-1][1]["items"][0])

    def test_unticking_clears_the_flag_and_a_closed_unmerged_pr_says_so(self):
        fake = Fake({("o", "r", 4): "dropped"})
        todo, sent, refs = checker(fake)
        todo.add("Land it", ref="o/r#4")
        todo.set_done(1, True)
        self.assertEqual(todo._items[0]["flag"], "r#4 not merged")
        todo.set_done(1, False)
        self.assertIsNone(todo._items[0]["flag"])
        self.assertEqual(refs.check(), 0)   # open and not ticked: nothing to say

    def test_an_item_github_ticked_is_unticked_when_the_issue_reopens(self):
        fake = Fake({(*REPO, 5): "done"})
        todo, sent, refs = checker(fake)
        todo.add("x", ref="#5")
        refs.check()
        fake.states[(*REPO, 5)] = "open"
        self.assertEqual(refs.check(), 1)
        item = todo._items[0]
        self.assertEqual((item["done"], item["done_by"], item["flag"]), (False, None, None))

    def test_unknown_refs_and_plain_refs_without_a_repo_are_left_alone(self):
        fake = Fake({})
        todo, sent, refs = checker(fake, repo=None)
        todo.add("plain", ref="#5")
        todo.add("gone", ref="a/gone#1")
        todo.set_done(2, True)
        self.assertEqual(fake.asked, [[("a", "gone", 1)]])
        self.assertEqual(refs.check(), 0)
        self.assertIsNone(todo._items[1]["flag"])

    def test_two_items_with_the_same_ref_share_one_question(self):
        fake = Fake({(*REPO, 9): "done"})
        todo, _, refs = checker(fake)
        todo.add("a", ref="#9")
        todo.add("b", ref="9")
        self.assertEqual(refs.check(), 2)
        self.assertEqual(fake.asked, [[(*REPO, 9)]])

    def test_a_failed_check_after_a_tick_is_logged_not_raised(self):
        def broken(keys):
            raise OSError("no network")
        todo, sent, refs = checker(broken)
        todo.add("x", ref="#1")
        with self.assertLogs("bridge.todo", "WARNING") as logs:
            self.assertTrue(todo.set_done(1, True)["done"])
        self.assertIn("no network", logs.output[-1])
        with self.assertRaises(OSError):
            refs.check()   # the poll loop logs this one itself

    def test_a_tick_without_a_ref_or_by_github_does_not_ask(self):
        fake = Fake({})
        todo, _, refs = checker(fake)
        todo.add("x")
        todo.set_done(1, True)
        self.assertEqual(fake.asked, [])

    def test_the_flag_survives_a_restart_but_not_on_an_open_item(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "todo.json"
            todo, _, refs = checker(Fake({(*REPO, 1): "open"}), store=path)
            todo.add("a", ref="#1")
            todo.add("b", ref="#2")
            todo.set_done(1, True)
            raw = json.loads(path.read_text())
            raw["items"][1]["flag"] = "#2 still open"   # hand-edited onto an open item
            path.write_text(json.dumps(raw))
            again, _ = make(path)
            self.assertEqual([i.get("flag") for i in again.summary()["items"]], [None, "#1 still open"])


class FlagCliTests(test_todo.Served):
    cli = test_todo.CliTests.cli

    def test_list_shows_the_flag(self):
        refs = RefChecker(self.todo, fetch=Fake({(*REPO, 90): "open"}), repo=REPO, spawn=lambda f: f())
        self.todo.add("Flash it", ref="#90")
        self.todo.set_done(1, True)
        self.assertIsNotNone(refs)
        code, out = self.cli("list")
        self.assertEqual(out.splitlines()[0], "   1  [x]  Flash it  #90  (john)  ! ticked, but #90 still open")


if __name__ == "__main__":
    unittest.main()
