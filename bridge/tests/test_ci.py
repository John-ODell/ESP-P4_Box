import json
import unittest

from petbrain.ci import BUDGET, SHOWN, TOPIC_CI, CI, build, fetch_github, payload, state


def suite(status, conclusion=None, at="2026-10-20T12:00:00Z", workflow="ci", app="GitHub Actions"):
    return {"status": status, "conclusion": conclusion, "updatedAt": at, "app": {"name": app},
            "workflowRun": {"workflow": {"name": workflow}} if workflow else None}


def viewer(*repos):
    return {"repositories": {"nodes": [
        {"name": name, "defaultBranchRef": {"target": {"checkSuites": {"nodes": list(suites)}}}}
        for name, suites in repos]}}


class StateTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(state(suite("COMPLETED", "SUCCESS")), "pass")
        for c in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE"):
            self.assertEqual(state(suite("COMPLETED", c)), "fail")
        for c in ("SKIPPED", "NEUTRAL", "CANCELLED", "STALE", "ACTION_REQUIRED", None):
            self.assertEqual(state(suite("COMPLETED", c)), "other")
        self.assertEqual(state(suite("IN_PROGRESS")), "run")
        for s in ("QUEUED", "REQUESTED", "WAITING", "PENDING"):
            self.assertEqual(state(suite(s)), "run")   # with a workflow run: about to start
            self.assertIsNone(state(suite(s, workflow=None, app="Claude")))   # an app with nothing to do


class BuildTests(unittest.TestCase):
    def test_failures_first_then_running_then_newest(self):
        lines = build(viewer(
            ("a", [suite("COMPLETED", "SUCCESS", "2026-10-20T13:00:00Z")]),
            ("b", [suite("COMPLETED", "FAILURE", "2026-10-20T09:00:00Z"), suite("QUEUED", workflow=None)]),
            ("c", [suite("IN_PROGRESS", at="2026-10-20T08:00:00Z")]),
            ("d", [suite("COMPLETED", "SUCCESS", "2026-10-20T14:00:00Z", workflow=None, app="Cloudflare Pages")])))
        self.assertEqual([(l["r"], l["w"], l["s"]) for l in lines], [
            ("b", "ci", "fail"), ("c", "ci", "run"), ("d", "Cloudflare Pages", "pass"), ("a", "ci", "pass")])

    def test_only_the_newest_suite_of_a_workflow_counts(self):
        lines = build(viewer(("a", [suite("COMPLETED", "FAILURE", "2026-10-20T09:00:00Z", workflow="nightly"),
                                    suite("COMPLETED", "SUCCESS", "2026-10-20T10:00:00Z", workflow="nightly"),
                                    suite("COMPLETED", "SUCCESS", "2026-10-20T08:00:00Z", workflow="ci")])))
        self.assertEqual([(l["w"], l["s"]) for l in lines], [("nightly", "pass"), ("ci", "pass")])

    def test_odd_nodes_are_skipped(self):
        v = viewer(("a", [None, "x", suite("COMPLETED", "SUCCESS", at="bad"), suite("COMPLETED", "SUCCESS", app=None,
                                                                                 workflow=None)]))
        v["repositories"]["nodes"] += [None, {"name": None}, {"name": "e", "defaultBranchRef": None}]
        self.assertEqual(build(v), [])
        self.assertEqual(build({}), [])

    def test_payload_is_capped(self):
        lines = [{"r": '"' * 100, "w": '"' * 100, "s": "pass", "t": 1} for _ in range(30)]   # past what build makes
        text = payload(lines)
        self.assertLessEqual(len(text), BUDGET)
        self.assertLessEqual(len(json.loads(text)["items"]), SHOWN)


class CITests(unittest.TestCase):
    def test_publishes_retained_only_on_change(self):
        sent = []
        answer = viewer(("a", [suite("COMPLETED", "SUCCESS")]))
        ci = CI(lambda t, p, r: sent.append((t, r)), lambda: answer)
        self.assertTrue(ci.update())
        self.assertFalse(ci.update())
        answer["repositories"]["nodes"][0]["defaultBranchRef"]["target"]["checkSuites"]["nodes"][0]["conclusion"] = \
            "FAILURE"
        self.assertTrue(ci.update())
        self.assertEqual(sent, [(TOPIC_CI, True), (TOPIC_CI, True)])

    def test_fetch_returns_the_viewer(self):
        self.assertEqual(fetch_github(token="t", post=lambda token, q: {"viewer": {"x": token}}), {"x": "t"})


if __name__ == "__main__":
    unittest.main()
