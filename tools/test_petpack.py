"""Tests for petpack.py (#66): python3 -m unittest discover tools"""
import copy
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import unittest.mock
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import petpack as pp  # noqa: E402

DOLPHIN = pp.REPO / "pets" / "dolphin"


class Dolphin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta, cls.frames = pp.load(DOLPHIN)

    def test_checks_clean(self):
        self.assertEqual(pp.check(self.meta, self.frames), [])

    def test_regenerates_the_committed_firmware_sprites(self):
        # pets/dolphin is the source of the built-in pet: the C in the repo must be exactly what it generates
        with tempfile.TemporaryDirectory() as d:
            pp.write_c(self.meta, self.frames, d)
            for name in ("sprites.h", "sprites.c"):
                self.assertEqual(Path(d, name).read_text(), (pp.C_DIR / name).read_text(),
                                 f"{name} is stale: run python3 tools/petpack.py c pets/dolphin")

    def test_pack_round_trip(self):
        meta, frames = pp.parse(pp.build(self.meta, self.frames))
        expected = {k: v for k, v in self.meta.items() if k != "sheet"}
        self.assertEqual(meta, expected)
        self.assertEqual(frames, self.frames)

    def test_pack_layout(self):
        blob = pp.build(self.meta, self.frames)
        self.assertEqual(blob[:8], b"PETPACK\0")
        fmt, total, crc, jlen = struct.unpack_from("<IIII", blob, 8)
        self.assertEqual((fmt, total), (1, len(blob)))
        self.assertEqual(crc, zlib.crc32(blob[20:]) & 0xFFFFFFFF)
        self.assertEqual(jlen % 4, 0)
        self.assertEqual(len(blob), 24 + jlen + 240 * len(self.frames))

    def test_sheet_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            pp.write_sheet(Path(d, "s.png"), self.frames)
            self.assertEqual(pp.sheet_frames(Path(d, "s.png")), self.frames)


class Png(unittest.TestCase):
    def test_palette_with_transparency(self):
        # 1-bit palette PNG, index 0 transparent, index 1 black: what a pixel editor might save
        w, h = pp.W, pp.H
        rows = b"".join(b"\x00" + bytes([0x80 if y == 0 else 0] + [0] * (w // 8 - 1)) for y in range(h))

        def chunk(tag, body):
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 3, 0, 0, 0))
               + chunk(b"PLTE", bytes([255, 255, 255, 0, 0, 0])) + chunk(b"tRNS", b"\x00")
               + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.png").write_bytes(png)
            frames = pp.sheet_frames(Path(d, "p.png"))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], 1)
        self.assertEqual(sum(frames[0]), 1)

    def test_not_a_grid(self):
        with tempfile.TemporaryDirectory() as d:
            pp.png_write(Path(d, "p.png"), 50, 40, lambda x, y: (0, 0, 0))
            with self.assertRaisesRegex(pp.PetError, "grid"):
                pp.sheet_frames(Path(d, "p.png"))

    def test_light_and_transparent_pixels_are_empty(self):
        self.assertTrue(pp.is_ink((0, 0, 0, 255)))
        self.assertFalse(pp.is_ink((0, 0, 0, 0)))
        self.assertFalse(pp.is_ink((255, 255, 255, 255)))
        self.assertFalse(pp.is_ink((200, 200, 200, 255)))


class Errors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta, cls.frames = pp.load(DOLPHIN)

    def errors(self, change):
        meta = copy.deepcopy(self.meta)
        change(meta)
        return "\n".join(pp.check(meta, self.frames))

    def test_missing_reaction(self):
        self.assertIn("'sleep' is required", self.errors(lambda m: m["animations"].pop("sleep")))

    def test_frame_outside_the_sheet(self):
        def bad(m):
            m["animations"]["notice"][0]["frame"] = 999
        self.assertIn("frame 999 is not in the sheet", self.errors(bad))

    def test_idle_names_an_unknown_animation(self):
        self.assertIn("no animation called 'happy/dance'",
                      self.errors(lambda m: m["moods"]["happy"]["idle"].update({"happy/dance": 2})))

    def test_missing_mood(self):
        self.assertIn("moods.worried is required", self.errors(lambda m: m["moods"].pop("worried")))

    def test_bad_colour(self):
        self.assertIn("colors.ink", self.errors(lambda m: m["colors"].update(ink="white")))

    def test_bad_timing_and_offset(self):
        def bad(m):
            m["animations"]["notice"][0].update(ms=5, dx=20)
        e = self.errors(bad)
        self.assertIn("ms must be", e)
        self.assertIn("dx must be", e)

    def test_pads_need_four_colours(self):
        self.assertIn("play.pads", self.errors(lambda m: m["play"].update(pads=["#000000"])))

    def test_unknown_field(self):
        self.assertIn("sound is not a known field", self.errors(lambda m: m.update(sound="beep")))

    def test_build_refuses_a_bad_pet(self):
        meta = copy.deepcopy(self.meta)
        meta["name"] = ""
        with self.assertRaises(pp.PetError):
            pp.build(meta, self.frames)


class Parse(unittest.TestCase):
    def setUp(self):
        meta, frames = pp.load(DOLPHIN)
        self.blob = pp.build(meta, frames)

    def test_bad_magic(self):
        with self.assertRaisesRegex(pp.PetError, "magic"):
            pp.parse(b"X" + self.blob[1:])

    def test_corrupt_byte(self):
        blob = bytearray(self.blob)
        blob[-1] ^= 1
        with self.assertRaisesRegex(pp.PetError, "checksum"):
            pp.parse(bytes(blob))

    def test_truncated(self):
        with self.assertRaisesRegex(pp.PetError, "bytes"):
            pp.parse(self.blob[:-10])


class CommandLine(unittest.TestCase):
    def test_check_reports_errors_and_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(DOLPHIN, Path(d, "p"))
            meta = json.loads(Path(d, "p", "pet.json").read_text())
            del meta["moods"]["sad"]
            Path(d, "p", "pet.json").write_text(json.dumps(meta))
            with unittest.mock.patch("sys.stderr") as err:
                self.assertEqual(pp.main(["check", str(Path(d, "p"))]), 1)
            self.assertIn("moods.sad is required", "".join(c.args[0] for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
