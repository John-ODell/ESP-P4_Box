import json
import unittest
from types import SimpleNamespace

from petbrain.machines import STALE_DAYS, TOPIC_MACHINES, Machines, machines, tailscale_status

NOW = 1792500000   # 2026-10-20


def iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status(*peers):
    return {"BackendState": "Running", "Peer": {
        f"k{i}": {"HostName": host, "DNSName": f"{dns}.tail1234.ts.net.", "OS": os_, "Online": online,
                  "LastSeen": seen} for i, (host, dns, os_, online, seen) in enumerate(peers)}}


TAILNET = status(("wordell", "wordell", "linux", False, iso(NOW - 3600)),
                 ("JohnsDesktop", "johnsdesktop", "windows", True, "0001-01-01T00:00:00Z"),
                 ("localhost", "iphone182", "iOS", False, iso(NOW - 60)),
                 ("WG000655", "wg000655", "macOS", False, iso(NOW - (STALE_DAYS + 1) * 86400)))


class MachinesTests(unittest.TestCase):
    def test_by_default_computers_seen_lately_down_first(self):
        self.assertEqual(machines(TAILNET, now=NOW), [
            {"n": "wordell", "s": "down", "t": NOW - 3600}, {"n": "johnsdesktop", "s": "up", "t": None}])

    def test_named_machines_in_any_case_and_unknown_ones(self):
        self.assertEqual(machines(TAILNET, ["Wordell", " wg000655", "pi9"], now=NOW), [
            {"n": "wg000655", "s": "down", "t": NOW - (STALE_DAYS + 1) * 86400},
            {"n": "wordell", "s": "down", "t": NOW - 3600}, {"n": "pi9", "s": "unknown", "t": None}])

    def test_odd_peers_are_skipped(self):
        self.assertEqual(machines({"Peer": {"a": None, "b": {"HostName": ""}}}, now=NOW), [])
        self.assertEqual(machines({}, now=NOW), [])


class StatusTests(unittest.TestCase):
    def test_a_failing_or_stopped_tailscale_raises(self):
        def run(args, **kw):
            return SimpleNamespace(returncode=1, stderr="failed to connect to local tailscaled\n", stdout="")
        with self.assertRaisesRegex(RuntimeError, "failed to connect"):
            tailscale_status(run)

        def stopped(args, **kw):
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"BackendState": "Stopped"}))
        with self.assertRaisesRegex(RuntimeError, "Stopped"):
            tailscale_status(stopped)


class PublishTests(unittest.TestCase):
    def test_every_update_is_published_retained_with_the_bridge_times(self):
        sent, clock = [], [NOW]
        m = Machines(lambda t, p, r: sent.append((t, json.loads(p), r)), lambda: TAILNET, now=lambda: clock[0],
                     every=600)
        m.update()
        clock[0] += 60
        m.polled()
        m.update()
        self.assertEqual([(t, r) for t, p, r in sent], [(TOPIC_MACHINES, True)] * 2)
        first, second = sent[0][1], sent[1][1]
        self.assertEqual((first["at"], first["up"], first["poll"], first["every"]), (NOW, NOW, None, 600))
        self.assertEqual((second["at"], second["poll"]), (NOW + 60, NOW + 60))
        self.assertEqual(len(second["items"]), 2)

    def test_a_check_that_cannot_run_says_so(self):
        sent = []

        def broken():
            raise RuntimeError("Tailscale is Stopped")
        Machines(lambda t, p, r: sent.append(json.loads(p)), broken, now=lambda: NOW).update()
        self.assertEqual((sent[0]["items"], sent[0]["err"]), ([], "Tailscale: Tailscale is Stopped"))


if __name__ == "__main__":
    unittest.main()
