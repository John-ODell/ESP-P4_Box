import json
import stat
import unittest
from pathlib import Path
from unittest import mock

from petbrain.notify_api import Notifier, make_server, serve_in_thread
from tests.test_connect import Home, connect
from tests.test_hooks import pethook
from tests.test_tailnet import LOCAL, NOW, TOKEN, quiet

URL = "http://johns-mac.tail1234.ts.net:8765/event"


class CheckUrlTests(unittest.TestCase):
    def test_a_bare_host_and_port_gets_the_event_path(self):
        self.assertEqual(connect.check_url("http://mac.tail1234.ts.net:8765"), "http://mac.tail1234.ts.net:8765/event")
        self.assertEqual(connect.check_url(" http://100.79.52.11:8765/event/ "), "http://100.79.52.11:8765/event")

    def test_other_schemes_paths_and_queries_are_refused(self):
        for bad in ("ftp://mac:8765/event", "mac:8765", "http://mac:8765/pending", "http://mac:8765/event?x=1", ""):
            with self.subTest(bad=bad), self.assertRaises(connect.ConnectError):
                connect.check_url(bad)


class RemoteBridgeTests(Home):
    def config(self):
        return self.home / ".config" / "desk-pet" / "bridge.json"

    def run_main(self, *argv):
        return connect.main([*argv, "--repo", str(self.repo)], home=self.home, out=self.out.append)

    def test_bridge_writes_an_owner_only_file_the_hooks_read_and_never_prints_the_token(self):
        self.assertEqual(self.run_main("--bridge", URL, "--token", TOKEN, "--yes"), 0)
        path = self.config()
        self.assertEqual(json.loads(path.read_text()), {"url": URL, "token": TOKEN})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(pethook.target({}, str(path)), (URL, TOKEN))
        self.assertNotIn(TOKEN, "\n".join(self.out))
        self.assertIn("<hidden>", "\n".join(self.out))

    def test_a_short_token_or_bad_url_writes_nothing(self):
        self.assertEqual(self.run_main("--bridge", URL, "--token", "short", "--yes"), 2)
        self.assertEqual(self.run_main("--bridge", "mac:8765", "--token", TOKEN, "--yes"), 2)
        self.assertFalse(self.config().exists())

    def test_a_second_run_changes_nothing_and_a_new_token_replaces_the_old_with_a_private_backup(self):
        self.run_main("--bridge", URL, "--token", TOKEN, "--yes")
        self.out.clear()
        self.assertEqual(self.run_main("--bridge", URL, "--token", TOKEN, "--yes"), 0)
        self.assertIn("already connected", "\n".join(self.out))
        new = "n" * 40
        self.config().chmod(0o644)  # someone loosened it by hand
        self.assertEqual(self.run_main("--bridge", URL, "--token", new, "--yes"), 0)
        self.assertEqual(json.loads(self.config().read_text())["token"], new)
        self.assertEqual(stat.S_IMODE(self.config().stat().st_mode), 0o600)
        backups = list(self.config().parent.glob("bridge.json.pet-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertNotIn(new, "\n".join(self.out))

    def test_disconnect_bridge_goes_back_to_this_machines_own_bridge(self):
        self.run_main("--bridge", URL, "--token", TOKEN, "--yes")
        self.assertEqual(self.run_main("--disconnect", "bridge", "--yes"), 0)
        self.assertEqual(pethook.target({}, str(self.config())), (pethook.DEFAULT_URL, ""))
        self.assertNotIn(TOKEN, self.config().read_text())

    def test_a_broken_file_is_replaced_and_kept_as_a_backup(self):
        self.config().parent.mkdir(parents=True)
        self.config().write_text("{not json")
        self.assertEqual(self.run_main("--bridge", URL, "--token", TOKEN, "--yes"), 0)
        self.assertEqual(json.loads(self.config().read_text())["url"], URL)


class ShareTests(Home):
    def env(self, text):
        (self.repo / "bridge" / ".env").write_text(text)

    def test_off_or_weak_token_says_how_to_turn_it_on(self):
        self.assertEqual(connect.share(self.repo, self.out.append, name=lambda: "mac.ts.net"), 1)
        self.env("PET_TAILNET=on\nPET_TAILNET_TOKEN=short\n")
        self.assertEqual(connect.share(self.repo, self.out.append, name=lambda: "mac.ts.net"), 1)
        self.assertIn("setup.py", self.out[-1])

    def test_no_tailscale_name_says_to_sign_in(self):
        self.env(f"PET_TAILNET=on\nPET_TAILNET_TOKEN={TOKEN}\n")
        self.assertEqual(connect.share(self.repo, self.out.append, name=lambda: None), 1)
        self.assertIn("Tailscale", self.out[-1])

    def test_prints_the_command_with_this_macs_name_port_and_token(self):
        self.env(f"# comment\nPET_TAILNET=on\nPET_TAILNET_TOKEN={TOKEN}\nPET_HTTP_PORT=9876\n")
        self.assertEqual(connect.share(self.repo, self.out.append, name=lambda: "mac.tail1234.ts.net"), 0)
        line = f"  python3 bridge/connect.py --bridge http://mac.tail1234.ts.net:9876/event --token {TOKEN}"
        self.assertIn(line, self.out)

    def test_name_prefers_magicdns_and_falls_back_to_the_ip(self):
        class Done:
            def __init__(self, stdout):
                self.stdout = stdout

        status = json.dumps({"Self": {"DNSName": "mac.tail1234.ts.net."}})
        with mock.patch.object(connect.shutil, "which", lambda cli: "/bin/tailscale" if cli == "tailscale" else None):
            self.assertEqual(connect.tailnet_name(lambda *a, **k: Done(status)), "mac.tail1234.ts.net")
            with mock.patch.object(connect.tailnet, "detect_ip", lambda run: "100.79.52.11"):
                self.assertEqual(connect.tailnet_name(lambda *a, **k: Done("{}")), "100.79.52.11")


class ProbeTests(unittest.TestCase):
    """probe() against the real notify server, with a token, as the tailnet listener runs it (#37)."""

    def setUp(self):
        quiet(self)
        self.server = make_server("127.0.0.1", 0, Notifier(lambda *a: None, lambda: NOW), token=TOKEN, allow=LOCAL)
        serve_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/event"

    def test_up_refused_and_down_read_differently(self):
        self.assertIn("0 waiting", connect.probe(self.url, TOKEN))
        self.assertIn("refusing this machine's token", connect.probe(self.url, "wrong" * 8))
        self.assertIn("refusing this machine's token", connect.probe(self.url, ""))
        self.assertIn("not answering", connect.probe("http://127.0.0.1:9/event", TOKEN, timeout=0.5))



class BridgeStateTests(unittest.TestCase):
    def test_bridge_state_reads_the_config_file_under_the_given_home(self):
        home, seen = Path("/nowhere/home"), []
        with mock.patch.object(connect, "probe", lambda u, t: seen.append((u, t)) or "ok"), \
                mock.patch.object(connect.pethook, "target", lambda env, cfg: (cfg, "tok")):
            connect.bridge_state(home)
        self.assertEqual(seen, [(str(home / ".config" / "desk-pet" / "bridge.json"), "tok")])


if __name__ == "__main__":
    unittest.main()
