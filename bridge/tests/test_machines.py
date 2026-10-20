import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

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

    def test_names_match_hostnames_and_this_mac_and_count_once(self):
        tailnet = dict(TAILNET, Self={"HostName": "Johns-MacBook-Pro", "DNSName": "johns-macbook-pro.tail1234.ts.net.",
                                      "OS": "macOS", "Online": False, "LastSeen": "0001-01-01T00:00:00Z"})
        self.assertEqual(machines(tailnet, ["wordell", "WORDELL", "JohnsDesktop", "johnsdesktop", "johns-macbook-pro"],
                                  now=NOW), [
            {"n": "wordell", "s": "down", "t": NOW - 3600}, {"n": "johns-macbook-pro", "s": "up", "t": None},
            {"n": "johnsdesktop", "s": "up", "t": None}])
        self.assertNotIn("johns-macbook-pro", [m["n"] for m in machines(tailnet, now=NOW)])   # the bridge has its row

    def test_a_machine_once_listed_stays_listed(self):
        later = NOW + STALE_DAYS * 86400
        self.assertEqual([m["n"] for m in machines(TAILNET, now=later)], ["johnsdesktop"])
        self.assertEqual([m["n"] for m in machines(TAILNET, now=later, keep={"wordell"})], ["wordell", "johnsdesktop"])

        clock = [NOW]
        m = Machines(lambda t, p, r: None, lambda: TAILNET, now=lambda: clock[0])
        m.update()
        clock[0] = later
        self.assertEqual([i["n"] for i in m.update()["items"]], ["wordell", "johnsdesktop"])

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

    def test_answers_that_are_not_a_status_say_so_plainly(self):
        def answer(returncode=0, stdout="", stderr=""):
            return lambda args, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        # the app binary run without a login session's environment, as launchd or cron would
        with self.assertRaisesRegex(RuntimeError, "^The Tailscale GUI failed to start$"):
            tailscale_status(answer(stdout="\nThe Tailscale GUI failed to start\n"), cli="tailscale")
        with self.assertRaisesRegex(RuntimeError, "^exit 1$"):
            tailscale_status(answer(1, stderr="\n"), cli="tailscale")
        with self.assertRaisesRegex(RuntimeError, "^no answer$"):
            tailscale_status(answer(), cli="tailscale")

        def hangs(args, **kw):
            raise subprocess.TimeoutExpired(args, 10)
        with self.assertRaisesRegex(RuntimeError, "^no answer in 10 s$"):
            tailscale_status(hangs, cli="tailscale")

        def missing(args, **kw):
            raise FileNotFoundError(2, "No such file", args[0])
        with self.assertRaisesRegex(RuntimeError, "^could not run tailscale$"):
            tailscale_status(missing, cli="/gone/tailscale")

    def test_tailscale_is_found_off_the_path(self):
        seen = []

        def run(args, **kw):
            seen.append(args[0])
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"BackendState": "Running"}))
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("os.path.exists", side_effect=lambda p: p == "/usr/local/bin/tailscale"):
            tailscale_status(run)
        self.assertEqual(seen, ["/usr/local/bin/tailscale"])
        with mock.patch("shutil.which", return_value=None), mock.patch("os.path.exists", return_value=False), \
                self.assertRaisesRegex(RuntimeError, "^not installed$"):
            tailscale_status(run)


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
