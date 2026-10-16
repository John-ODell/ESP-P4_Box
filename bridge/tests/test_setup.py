import importlib.util
import itertools
import stat
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pet_setup", BRIDGE / "setup.py")
pet_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pet_setup)

READY = {"venv": True, "mosquitto": True, "passwd": True}


class SetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.env, self.example = self.dir / ".env", self.dir / ".env.example"
        self.example.write_text((BRIDGE / ".env.example").read_text())
        self.out, self.asked = [], []
        counter = itertools.count()
        self.secrets = []

        def secret():
            value = f"secret{next(counter):02d}" + "x" * 40
            self.secrets.append(value)
            return value
        self.secret = secret

    def run_setup(self, ip="100.79.52.11", answer="y", has=True, checks=READY, **kw):
        def ask(prompt):
            self.asked.append(prompt)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        self.out.clear()
        return pet_setup.setup(self.env, self.example, out=self.out.append, ask=ask, detect=lambda: ip,
                               has_tailscale=lambda: has, new_secret=self.secret, extra=checks, **kw)

    def value(self, key):
        return pet_setup.get(self.env.read_text().splitlines(), key)

    def text(self):
        return "\n".join(self.out)

    def test_fresh_install_with_tailscale_is_safe_and_reachable_and_prints_no_secret(self):
        self.assertEqual(self.run_setup(), 0)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        self.assertEqual(self.value("PET_TAILNET"), "on")
        self.assertGreaterEqual(len(self.value("PET_TAILNET_TOKEN")), 32)
        self.assertGreaterEqual(len(self.value("MQTT_PASS")), 32)
        self.assertNotEqual(self.value("MQTT_PASS"), self.value("PET_TAILNET_TOKEN"))
        self.assertEqual(self.value("PET_HTTP_HOST"), "127.0.0.1")
        for s in self.secrets:
            self.assertNotIn(s, self.text())
        self.assertIn("connect.py --share", self.text())
        self.assertIn("100.79.52.11", self.text())
        self.assertIn("# Copy to bridge/.env", self.env.read_text())  # comments kept

    def test_a_second_run_changes_nothing_and_asks_nothing(self):
        self.run_setup()
        before, asked = self.env.read_text(), len(self.asked)
        self.run_setup()
        self.assertEqual(self.env.read_text(), before)
        self.assertEqual(len(self.asked), asked)
        self.assertIn("Nothing to change", self.text())
        self.assertNotIn("Restart the bridge", self.text())

    def test_values_already_set_are_kept(self):
        self.env.write_text("GITHUB_TOKEN=ghp_mine\nMQTT_USER=box\nMQTT_PASS=already-chosen\nPET_GOAL=30\n")
        self.run_setup(answer="n")
        self.assertEqual((self.value("GITHUB_TOKEN"), self.value("MQTT_USER"), self.value("MQTT_PASS"),
                          self.value("PET_GOAL")), ("ghp_mine", "box", "already-chosen", "30"))
        self.assertNotIn("secrets.h", self.text())  # the box's password did not change

    def test_without_tailscale_it_explains_and_leaves_access_off(self):
        self.run_setup(ip=None, has=False)
        self.assertEqual(self.value("PET_TAILNET"), "off")
        self.assertEqual(self.asked, [])
        self.assertIn(pet_setup.DOWNLOAD, self.text())
        self.assertIn("Other devices off", self.text())

    def test_tailscale_signed_out_says_to_sign_in(self):
        self.run_setup(ip=None, has=True)
        self.assertEqual(self.value("PET_TAILNET"), "off")
        self.assertIn("not signed in", self.text())

    def test_no_or_end_of_input_or_no_remote_leaves_access_off(self):
        self.run_setup(answer="n")
        self.assertEqual(self.value("PET_TAILNET"), "off")
        self.assertEqual(self.value("PET_TAILNET_TOKEN"), "")
        self.run_setup(answer=EOFError())
        self.assertEqual(self.value("PET_TAILNET"), "off")
        asked = len(self.asked)
        self.run_setup(remote=False)
        self.assertEqual(len(self.asked), asked)

    def test_yes_turns_it_on_without_asking(self):
        self.run_setup(yes=True)
        self.assertEqual(self.value("PET_TAILNET"), "on")
        self.assertEqual(self.asked, [])

    def test_on_with_a_short_token_gets_a_strong_one_and_a_restart_note(self):
        self.env.write_text("MQTT_PASS=p\nPET_TAILNET=on\nPET_TAILNET_TOKEN=short\n")
        self.env.chmod(0o600)
        self.run_setup()
        self.assertGreaterEqual(len(self.value("PET_TAILNET_TOKEN")), 32)
        self.assertIn("old token stops working", self.text())
        self.assertIn("Restart the bridge", self.text())

    def test_a_readable_env_is_tightened(self):
        self.run_setup()
        self.env.chmod(0o644)
        self.run_setup()
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        self.assertIn("readable by you only", self.text())

    def test_show_secrets_prints_the_mqtt_password_only(self):
        self.run_setup(show=True)
        self.assertIn(self.value("MQTT_PASS"), self.text())
        self.assertNotIn(self.value("PET_TAILNET_TOKEN"), self.text())

    def test_a_non_loopback_http_host_is_flagged(self):
        self.env.write_text("MQTT_PASS=p\nPET_HTTP_HOST=0.0.0.0\n")
        self.run_setup(answer="n")
        self.assertIn("PET_HTTP_HOST=0.0.0.0", self.text())
        self.assertEqual(self.value("PET_HTTP_HOST"), "0.0.0.0")  # flagged, not changed

    def test_missing_pieces_become_numbered_next_steps(self):
        self.run_setup(checks={"venv": False, "mosquitto": False, "passwd": False})
        for step in ("python3 -m venv .venv", "brew install mosquitto", "bridge/mosquitto/setup.sh", "secrets.h"):
            self.assertIn(step, self.text())

    def test_put_and_get_respect_comments_and_the_last_line(self):
        lines = ["# MQTT_PASS=not-this", "MQTT_PASS=a", "MQTT_PASS=b"]
        self.assertEqual(pet_setup.get(lines, "MQTT_PASS"), "b")
        pet_setup.put(lines, "MQTT_PASS", "c")
        self.assertEqual(lines, ["# MQTT_PASS=not-this", "MQTT_PASS=a", "MQTT_PASS=c"])
        pet_setup.put(lines, "NEW", "1")
        self.assertEqual(lines[-1], "NEW=1")


if __name__ == "__main__":
    unittest.main()
