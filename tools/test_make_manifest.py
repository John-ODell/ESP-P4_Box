"""Tests for make_manifest.py (#51): python3 -m unittest discover tools"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import make_manifest as mm  # noqa: E402

CFG = {"IDF_TARGET": "esp32p4", "ESP_REV_MIN_FULL": 100, "ESP_REV_MAX_FULL": 199, "ESPTOOLPY_FLASHSIZE": "32MB"}
FLASH_ARGS = """--flash_mode dio --flash_freq 80m --flash_size 32MB
0x2000 bootloader/bootloader.bin
0x20000 pet.bin
0x8000 partition_table/partition-table.bin
0xf000 ota_data_initial.bin
"""


class FlashArgs(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "flash_args")
            with open(p, "w") as f:
                f.write(text)
            return mm.read_flash_args(p)

    def test_options_and_parts_sorted_by_offset(self):
        options, parts = self.read(FLASH_ARGS)
        self.assertEqual(options, ["--flash_mode", "dio", "--flash_freq", "80m", "--flash_size", "32MB"])
        self.assertEqual([o for o, _ in parts], [0x2000, 0x8000, 0xF000, 0x20000])
        self.assertEqual(parts[0][1], "bootloader/bootloader.bin")

    def test_no_images_is_an_error(self):
        with self.assertRaises(ValueError):
            self.read("--flash_mode dio\n")

    def test_odd_line_is_an_error(self):
        with self.assertRaises(ValueError):
            self.read("0x2000 a.bin extra\n")


class Manifest(unittest.TestCase):
    data = b"\xe9" + bytes(4095)

    def test_fields_match_the_flasher_contract(self):
        m = mm.manifest("1.2.3", "desk-pet-esp32p4-rev1-v1.2.3.bin", self.data, 0x2000, CFG)
        self.assertEqual(m["v"], 1)
        self.assertEqual(m["chip"], "ESP32-P4")
        self.assertEqual((m["chip_rev_min"], m["chip_rev_max"]), (100, 199))
        self.assertEqual(m["flash_size"], "32MB")
        self.assertEqual((m["baud"], m["console_baud"]), (921600, 115200))
        self.assertEqual(m["parts"], [{
            "path": "desk-pet-esp32p4-rev1-v1.2.3.bin",
            "offset": 0x2000,
            "size": 4096,
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }])
        self.assertEqual(m["notes"], "https://github.com/John-ODell/ESP-P4_Box/releases/tag/pet-v1.2.3")
        json.dumps(m)  # serialisable as written

    def test_refuses_a_rev3_range(self):
        with self.assertRaises(ValueError):
            mm.manifest("1.0.0", "x.bin", self.data, 0x2000, dict(CFG, ESP_REV_MAX_FULL=399))

    def test_refuses_another_target(self):
        with self.assertRaises(ValueError):
            mm.manifest("1.0.0", "x.bin", self.data, 0x2000, dict(CFG, IDF_TARGET="esp32s3"))


class Main(unittest.TestCase):
    def build_dir(self, d):
        b = os.path.join(d, "build")
        os.makedirs(os.path.join(b, "config"))
        with open(os.path.join(b, "flash_args"), "w") as f:
            f.write(FLASH_ARGS)
        with open(os.path.join(b, "config", "sdkconfig.json"), "w") as f:
            json.dump(CFG, f)
        return b

    def test_writes_image_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            b, out = self.build_dir(d), os.path.join(d, "dist")
            calls = []

            def fake_merge(build, options, parts, offset, out_path, python=None):
                calls.append((build, options, parts, offset))
                with open(out_path, "wb") as f:
                    f.write(b"\xe9" + b"\xff" * 99)

            with mock.patch.object(mm, "merge", fake_merge), mock.patch("sys.stdout"):
                mm.main(["--build", b, "--version", "1.0.0", "--out", out])
            self.assertEqual(calls[0][3], 0x2000)
            with open(os.path.join(out, "manifest.json")) as f:
                m = json.load(f)
            self.assertEqual(m["parts"][0]["path"], "desk-pet-esp32p4-rev1-v1.0.0.bin")
            self.assertEqual(m["parts"][0]["size"], 100)
            self.assertTrue(os.path.exists(os.path.join(out, "desk-pet-esp32p4-rev1-v1.0.0.bin")))

    def test_refuses_an_image_without_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            b, out = self.build_dir(d), os.path.join(d, "dist")

            def fake_merge(build, options, parts, offset, out_path, python=None):
                with open(out_path, "wb") as f:
                    f.write(b"\xff" * 100)

            with mock.patch.object(mm, "merge", fake_merge), self.assertRaises(SystemExit):
                mm.main(["--build", b, "--version", "1.0.0", "--out", out])

    def test_refuses_a_bad_version(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            mm.main(["--build", "b", "--version", "v1.0", "--out", "o"])


if __name__ == "__main__":
    unittest.main()
