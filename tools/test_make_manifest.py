"""Tests for make_manifest.py (#51): python3 -m unittest discover tools"""
import hashlib
import json
import os
import struct
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


def entry(ptype, subtype, offset, size, label):
    return struct.pack("<HBBII16sI", 0x50AA, ptype, subtype, offset, size, label.encode(), 0)


# The layout of projects/pet/partitions.csv: nvs, otadata, phy_init, ota_0, ota_1, storage.
TABLE = (entry(1, 2, 0x9000, 0x6000, "nvs") + entry(1, 0, 0xF000, 0x2000, "otadata")
         + entry(1, 1, 0x11000, 0x1000, "phy_init") + entry(0, 0x10, 0x20000, 0x400000, "ota_0")
         + b"\xeb\xeb" + b"\xff" * 30 + b"\xff" * 32)
IMAGE = b"\xe9" + bytes(255)


def parts(over=None):
    p = {0x2000: ("bootloader", IMAGE), 0x8000: ("partition-table", TABLE),
         0xF000: ("ota-data-initial", b"\xff" * 0x2000), 0x20000: ("app", IMAGE)}
    p.update(over or {})
    return [(off, name, data) for off, (name, data) in sorted(p.items())]


class FlashArgs(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "flash_args")
            with open(p, "w") as f:
                f.write(text)
            return mm.read_flash_args(p)

    def test_parts_sorted_by_offset_options_skipped(self):
        got = self.read(FLASH_ARGS)
        self.assertEqual([o for o, _ in got], [0x2000, 0x8000, 0xF000, 0x20000])
        self.assertEqual(got[0][1], "bootloader/bootloader.bin")

    def test_no_images_is_an_error(self):
        with self.assertRaises(ValueError):
            self.read("--flash_mode dio\n")

    def test_odd_line_is_an_error(self):
        with self.assertRaises(ValueError):
            self.read("0x2000 a.bin extra\n")


class Names(unittest.TestCase):
    def test_part_names(self):
        self.assertEqual(mm.part_name("bootloader/bootloader.bin", "pet"), "bootloader")
        self.assertEqual(mm.part_name("partition_table/partition-table.bin", "pet"), "partition-table")
        self.assertEqual(mm.part_name("ota_data_initial.bin", "pet"), "ota-data-initial")
        self.assertEqual(mm.part_name("pet.bin", "pet"), "app")
        self.assertEqual(mm.asset_name("1.0.0", "app"), "desk-pet-esp32p4-rev1-v1.0.0-app.bin")


class CheckParts(unittest.TestCase):
    def test_the_real_layout_passes(self):
        mm.check_parts(parts())

    def test_nvs_found_in_the_table(self):
        self.assertEqual(mm.nvs_ranges(TABLE), [(0x9000, 0xF000)])

    def test_a_part_over_nvs_is_refused(self):
        # what a merged image does: one part from 0x2000 straight through to the app
        with self.assertRaisesRegex(ValueError, "NVS"):
            mm.check_parts(parts({0x8000: ("partition-table", TABLE + b"\xff" * 0x2000)}))

    def test_overlap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            mm.check_parts(parts({0x2000: ("bootloader", b"\xe9" + bytes(0x6100))}))

    def test_missing_header_is_refused(self):
        with self.assertRaisesRegex(ValueError, "0xE9"):
            mm.check_parts(parts({0x20000: ("app", b"\xff" * 16)}))

    def test_no_partition_table_is_refused(self):
        with self.assertRaisesRegex(ValueError, "partition table"):
            mm.check_parts([(0x2000, "bootloader", IMAGE)])


class Manifest(unittest.TestCase):
    def test_fields_match_the_flasher_contract(self):
        assets = [(0x2000, "b.bin", IMAGE), (0x20000, "a.bin", IMAGE + b"x")]
        m = mm.manifest("1.2.3", assets, CFG)
        self.assertEqual(m["v"], 1)
        self.assertEqual(m["chip"], "ESP32-P4")
        self.assertEqual((m["chip_rev_min"], m["chip_rev_max"]), (100, 199))
        self.assertEqual(m["flash_size"], "32MB")
        self.assertEqual((m["baud"], m["console_baud"]), (921600, 115200))
        self.assertEqual(m["parts"][1], {"path": "a.bin", "offset": 0x20000, "size": 257,
                                         "sha256": hashlib.sha256(IMAGE + b"x").hexdigest()})
        self.assertEqual(m["notes"], "https://github.com/John-ODell/ESP-P4_Box/releases/tag/pet-v1.2.3")
        json.dumps(m)

    def test_refuses_a_rev3_range(self):
        with self.assertRaises(ValueError):
            mm.manifest("1.0.0", [], dict(CFG, ESP_REV_MAX_FULL=399))

    def test_refuses_another_target(self):
        with self.assertRaises(ValueError):
            mm.manifest("1.0.0", [], dict(CFG, IDF_TARGET="esp32s3"))


class Main(unittest.TestCase):
    def build_dir(self, d, app=IMAGE):
        b = os.path.join(d, "build")
        for sub in ("config", "bootloader", "partition_table"):
            os.makedirs(os.path.join(b, sub))
        files = {"bootloader/bootloader.bin": IMAGE, "partition_table/partition-table.bin": TABLE,
                 "ota_data_initial.bin": b"\xff" * 0x2000, "pet.bin": app}
        for name, data in files.items():
            with open(os.path.join(b, name), "wb") as f:
                f.write(data)
        with open(os.path.join(b, "flash_args"), "w") as f:
            f.write(FLASH_ARGS)
        with open(os.path.join(b, "config", "sdkconfig.json"), "w") as f:
            json.dump(CFG, f)
        return b

    def test_writes_parts_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            b, out = self.build_dir(d), os.path.join(d, "dist")
            with mock.patch("sys.stdout"):
                mm.main(["--build", b, "--version", "1.0.0", "--out", out])
            with open(os.path.join(out, "manifest.json")) as f:
                m = json.load(f)
            self.assertEqual([(p["offset"], p["path"]) for p in m["parts"]], [
                (0x2000, "desk-pet-esp32p4-rev1-v1.0.0-bootloader.bin"),
                (0x8000, "desk-pet-esp32p4-rev1-v1.0.0-partition-table.bin"),
                (0xF000, "desk-pet-esp32p4-rev1-v1.0.0-ota-data-initial.bin"),
                (0x20000, "desk-pet-esp32p4-rev1-v1.0.0-app.bin"),
            ])
            for p in m["parts"]:
                with open(os.path.join(out, p["path"]), "rb") as f:
                    self.assertEqual(hashlib.sha256(f.read()).hexdigest(), p["sha256"])

    def test_refuses_an_app_without_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            b, out = self.build_dir(d, app=b"\xff" * 64), os.path.join(d, "dist")
            with self.assertRaises(ValueError):
                mm.main(["--build", b, "--version", "1.0.0", "--out", out])
            self.assertFalse(os.path.exists(os.path.join(out, "manifest.json")))

    def test_refuses_a_bad_version(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            mm.main(["--build", "b", "--version", "v1.0", "--out", "o"])


if __name__ == "__main__":
    unittest.main()
