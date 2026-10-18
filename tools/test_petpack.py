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


def chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def make_png(w, h, ctype, depth, lines, filt=0, extra=b"", interlace=0):
    """A PNG from unfiltered scanlines (bytes each), every row encoded with filter type filt."""
    bpp = max(1, {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype] * depth // 8)
    out, prev = b"", bytes(len(lines[0]))
    for line in lines:
        enc = bytearray()
        for x, v in enumerate(line):
            a = line[x - bpp] if x >= bpp else 0
            b, c = prev[x], prev[x - bpp] if x >= bpp else 0
            pred = [0, a, b, (a + b) // 2, paeth(a, b, c)][filt]
            enc.append((v - pred) & 255)
        out += bytes([filt]) + bytes(enc)
        prev = line
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace))
            + extra + chunk(b"IDAT", zlib.compress(out)) + chunk(b"IEND", b""))


def pattern(x, y):
    """Ink on a diagonal and the frame border, so every filter has varied neighbours to predict from."""
    return (x + 2 * y) % 7 == 0 or x % pp.W == 0 or y % pp.H == 0


class Png(unittest.TestCase):
    W8 = pp.SHEET_W

    def read(self, data):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.png").write_bytes(data)
            return pp.sheet_frames(Path(d, "p.png"))

    def expected(self, h=pp.H):
        return [bytes(int(pattern(c % pp.COLS * pp.W + x, c // pp.COLS * pp.H + y))
                      for y in range(pp.H) for x in range(pp.W)) for c in range(pp.COLS * h // pp.H)]

    def rgb_lines(self, h=pp.H):
        return [bytes(v for x in range(self.W8) for v in ((0, 0, 0) if pattern(x, y) else (255, 255, 255)))
                for y in range(h)]

    def test_every_row_filter(self):
        for filt in range(5):
            with self.subTest(filter=filt):
                self.assertEqual(self.read(make_png(self.W8, pp.H, 2, 8, self.rgb_lines(), filt)), self.expected())

    def test_low_bit_depth_grey(self):
        for depth in (1, 2, 4):
            with self.subTest(depth=depth):
                per, top = 8 // depth, (1 << depth) - 1
                lines = []
                for y in range(pp.H):
                    vals = [0 if pattern(x, y) else top for x in range(self.W8)]
                    lines.append(bytes(sum(v << (8 - depth * (i + 1)) for i, v in enumerate(vals[k:k + per]))
                                       for k in range(0, self.W8, per)))
                self.assertEqual(self.read(make_png(self.W8, pp.H, 0, depth, lines, filt=1)), self.expected())

    def test_palette_with_transparency(self):
        # 1-bit palette PNG, index 0 transparent black, index 1 black: what a pixel editor might save
        lines = [bytes([0x80 if y == 0 else 0] + [0] * (self.W8 // 8 - 1)) for y in range(pp.H)]
        frames = self.read(make_png(self.W8, pp.H, 3, 1, lines,
                                    extra=chunk(b"PLTE", bytes([0, 0, 0, 0, 0, 0])) + chunk(b"tRNS", b"\x00")))
        self.assertEqual(len(frames), pp.COLS)
        self.assertEqual(frames[0][0], 1)
        self.assertEqual(sum(map(sum, frames)), 1)

    def test_transparent_colour_key_for_grey_and_rgb(self):
        # black marked transparent with tRNS: nothing is ink
        black = [bytes(self.W8 * 3)] * pp.H
        self.assertEqual(sum(map(sum, self.read(make_png(self.W8, pp.H, 2, 8, black, extra=chunk(b"tRNS", bytes(6)))))), 0)
        grey = [bytes(self.W8)] * pp.H
        self.assertEqual(sum(map(sum, self.read(make_png(self.W8, pp.H, 0, 8, grey, extra=chunk(b"tRNS", bytes(2)))))), 0)
        self.assertEqual(sum(map(sum, self.read(make_png(self.W8, pp.H, 0, 8, grey)))), self.W8 * pp.H)

    def test_blank_cells_are_kept(self):
        # frame numbers follow the picture, so a blank cell (and blank ones at the end) still counts
        white = bytes([0xFF] * (self.W8 // 8))
        lines = [bytes([0x7F]) + white[1:] if y == pp.H else white for y in range(2 * pp.H)]
        frames = self.read(make_png(self.W8, 2 * pp.H, 0, 1, lines))
        self.assertEqual(len(frames), 2 * pp.COLS)
        self.assertEqual([k for k, f in enumerate(frames) if any(f)], [pp.COLS])

    def test_interlaced_refused(self):
        with self.assertRaisesRegex(pp.PetError, "interlac"):
            self.read(make_png(self.W8, pp.H, 2, 8, self.rgb_lines(), interlace=1))

    def test_sheet_must_be_eight_frames_wide(self):
        for w, h in ((50, 40), (pp.W, pp.H), (self.W8, 50)):
            with self.subTest(size=(w, h)), tempfile.TemporaryDirectory() as d:
                pp.png_write(Path(d, "p.png"), w, h, lambda x, y: (0, 0, 0))
                with self.assertRaisesRegex(pp.PetError, "384 pixels wide"):
                    pp.sheet_frames(Path(d, "p.png"))

    def test_damaged_files_give_readable_errors(self):
        good = make_png(self.W8, pp.H, 2, 8, self.rgb_lines())
        idat = good.index(b"IDAT")
        corrupt = bytearray(good)
        corrupt[idat + 10:idat + 20] = bytes(10)
        short = make_png(self.W8, pp.H, 2, 8, self.rgb_lines(pp.H - 1))
        short = short.replace(struct.pack(">I", pp.H - 1), struct.pack(">I", pp.H), 1)
        bad_filter = (good[:good.index(b"IDAT") - 4]
                      + chunk(b"IDAT", zlib.compress(b"".join(b"\x07" + line for line in self.rgb_lines())))
                      + chunk(b"IEND", b""))
        cases = {"cut off": good[:idat + 30], "damaged PNG image data": bytes(corrupt),
                 "too short": short, "unknown row filter": bad_filter, "not a PNG": b"GIF89a"}
        for why, data in cases.items():
            with self.subTest(why), self.assertRaisesRegex(pp.PetError, why):
                self.read(data)

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

    def test_colour_with_a_trailing_newline(self):
        # re's $ also matches before a final newline; the box refuses "#E6F0F5\n", so check must too
        self.assertIn("colors.ink", self.errors(lambda m: m["colors"].update(ink="#E6F0F5\n")))
        self.assertIn("play.pads", self.errors(lambda m: m["play"].update(pads=["#4FD1C5\n"] * 4)))

    def test_pads_need_four_colours(self):
        self.assertIn("play.pads", self.errors(lambda m: m["play"].update(pads=["#000000"])))

    def test_unknown_field(self):
        self.assertIn("sound is not a known field", self.errors(lambda m: m.update(sound="beep")))

    def test_bools_and_floats_are_not_numbers(self):
        cases = {
            "format": lambda m: m.update(format=1.0),
            "frame": lambda m: m["animations"]["notice"][0].update(frame=True),
            "ms": lambda m: m["animations"]["notice"][0].update(ms=120.0),
            "dx": lambda m: m["animations"]["notice"][0].update(dx=False),
            "rest": lambda m: m["moods"]["happy"].update(rest=10.0),
            "weight": lambda m: m["moods"]["happy"]["idle"].update({"happy/blink": True}),
        }
        for field, change in cases.items():
            with self.subTest(field):
                self.assertNotEqual(self.errors(change), "")

    def test_animation_names(self):
        def add(name):
            return lambda m: m["animations"].update({name: [{"frame": 0}]})
        self.assertIn("animation name", self.errors(add("celebrate\n")))
        self.assertIn("animation name", self.errors(add("a" * 33)))
        self.assertEqual(self.errors(add("a" * 32)), "")

    def test_pet_name(self):
        for bad in ("Dol\ud800", "Dol\nphin", "x" * 25, "\u00e9" * 25, "\U0001F42C" * 13, "\t"):
            with self.subTest(repr(bad)):
                self.assertIn("name must be", self.errors(lambda m: m.update(name=bad)))
        self.assertEqual(self.errors(lambda m: m.update(name="Flipper the 2nd")), "")
        self.assertEqual(self.errors(lambda m: m.update(name="\u00e9" * 24)), "")

    def test_build_refuses_a_bad_pet(self):
        meta = copy.deepcopy(self.meta)
        meta["name"] = ""
        with self.assertRaises(pp.PetError):
            pp.build(meta, self.frames)


class Parse(unittest.TestCase):
    def setUp(self):
        self.meta, self.frames = pp.load(DOLPHIN)
        self.blob = pp.build(self.meta, self.frames)

    def repack(self, js, frames=b""):
        """A pack with this raw metadata and frame data and a correct header and checksum."""
        js += b"\0" * (-len(js) % 4)
        body = struct.pack("<I", len(js)) + js + frames
        total = 20 + len(body)
        return pp.MAGIC + struct.pack("<II", pp.FORMAT, total) + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF) + body

    def test_build_leaves_out_unused_cells_at_the_end(self):
        meta, frames = pp.parse(pp.build(self.meta, self.frames + [bytes(pp.W * pp.H)] * 8))
        self.assertEqual(len(frames), len(self.frames))

    def test_metadata_size_limit(self):
        meta = copy.deepcopy(self.meta)
        for k in range(90):
            meta["animations"][f"long/a{k}"] = [{"frame": 1, "dx": 1, "dy": 1, "ms": 1000}] * 64
        with self.assertRaisesRegex(pp.PetError, "at most 65536"):
            pp.build(meta, self.frames)

    def test_bad_metadata(self):
        frames = b"".join(pp.pack_bits(f) for f in self.frames)
        good = json.loads(self.blob[24:24 + struct.unpack_from("<I", self.blob, 20)[0]].rstrip(b"\0"))
        cases = {
            "not valid JSON": b"{nope",
            "not valid JSON ": b'{"name": "\xff"}',
            "not a JSON object": b"[1, 2]",
            "frames are 32x40": json.dumps(dict(good, frame_w=32)).encode(),
            "frame count": json.dumps(dict(good, frame_count=3)).encode(),
            "moods.sad is required": json.dumps(dict(good, moods={k: v for k, v in good["moods"].items()
                                                                   if k != "sad"})).encode(),
        }
        for why, js in cases.items():
            with self.subTest(why), self.assertRaisesRegex(pp.PetError, why.strip()):
                pp.parse(self.repack(js, frames))

    def test_metadata_length_out_of_range(self):
        for jlen in (3, len(self.blob), pp.MAX_JSON + 4):
            blob = bytearray(self.blob)
            struct.pack_into("<I", blob, 20, jlen)
            struct.pack_into("<I", blob, 16, zlib.crc32(blob[20:]) & 0xFFFFFFFF)
            with self.subTest(jlen), self.assertRaisesRegex(pp.PetError, "metadata length"):
                pp.parse(bytes(blob))

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
