#!/usr/bin/env python3
"""Generates the pet's character art and animation manifest for projects/pet.

Everything about the character lives here: body shapes, eyes, mouths, extras,
the animations built from them, and the per-mood idle weights (the Flipper
dolphin's "animation manifest" idea). Output is C code:

    projects/pet/main/sprites.h   enums, frame/anim/manifest types
    projects/pet/main/sprites.c   unique frames as LVGL A8 images + tables

Frames are 48x40 one-bit sprites, drawn 12x on the 720x720 panel by LVGL with
antialiasing off, so one art pixel is a crisp 12x12 block. A8 means the image is
an alpha mask: LVGL fills opaque pixels with the widget's recolor, so the ink
colour is a style decision in the firmware, not baked into the art.

    python3 tools/gen_sprites.py                  # regenerate the C files
    python3 tools/gen_sprites.py --preview p.png  # also write a contact sheet of every unique frame
    python3 tools/gen_sprites.py --ascii          # dump every unique frame as text
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

W, H = 48, 40
REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "projects" / "pet" / "main"


# ----------------------------------------------------------------------------- drawing
class Bitmap:
    def __init__(self):
        self.px = bytearray(W * H)

    def set(self, x, y, v=1):
        if 0 <= x < W and 0 <= y < H:
            self.px[y * W + x] = v

    def get(self, x, y):
        return self.px[y * W + x] if 0 <= x < W and 0 <= y < H else 0

    def ellipse(self, cx, cy, rx, ry, v=1):
        for y in range(H):
            for x in range(W):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 < 1.0:
                    self.set(x, y, v)

    def poly(self, pts, v=1):
        n = len(pts)
        for y in range(H):
            for x in range(W):
                px, py = x + 0.5, y + 0.5
                inside = False
                j = n - 1
                for i in range(n):
                    xi, yi = pts[i]
                    xj, yj = pts[j]
                    if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                        inside = not inside
                    j = i
                if inside:
                    self.set(x, y, v)

    def rect(self, x, y, w, h, v=1):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, v)

    def line(self, x0, y0, x1, y1, v=1):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.set(x0, y0, v)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def arc(self, cx, cy, r, a0, a1, v=1, thick=1):
        a = a0
        while a <= a1:
            rad = math.radians(a)
            for t in range(thick):
                self.set(int(cx + (r - t) * math.cos(rad)), int(cy + (r - t) * math.sin(rad)), v)
            a += 2

    def glyph(self, x, y, rows, v=1):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "#":
                    self.set(x + i, y + j, v)

    def ascii(self):
        return "\n".join("".join("#" if self.px[y * W + x] else "." for x in range(W)) for y in range(H))


# ----------------------------------------------------------------------------- the character
BODIES = {
    "normal": dict(cx=24.5, cy=23.0, rx=17.0, ry=10.5, feat_dy=0),
    "squash": dict(cx=24.5, cy=24.0, rx=18.0, ry=9.5, feat_dy=1),
    "stretch": dict(cx=24.5, cy=22.5, rx=16.0, ry=11.5, feat_dy=-1),
}

Z_SMALL = ["###", ".#.", "###"]
Z_BIG = ["#####", "...#.", "..#..", ".#...", "#####"]
STAR = [".#.", "###", ".#."]


def render(eyes="open", mouth="smile", gaze=0, body="normal", extras=()):
    b = Bitmap()
    p = BODIES[body]
    d = p["feat_dy"]

    # Body, snout, dorsal fin, tail stalk and fluke.
    b.ellipse(p["cx"], p["cy"], p["rx"], p["ry"])
    b.ellipse(41.0, 26.0 + d, 5.5, 3.5)
    b.poly([(31, 14 + d), (29, 10 + d), (25, 6 + d), (19, 4 + d), (17, 5 + d), (20, 8 + d), (21, 11 + d), (22, 14 + d)])
    b.poly([(9, 21 + d), (9, 27 + d), (4, 26 + d), (4, 22 + d)])
    b.poly([(7, 23 + d), (1, 16 + d), (4, 24 + d), (1, 32 + d), (7, 26 + d)])

    # Eyes: dark holes with a light highlight. x0 is the left edge of each hole.
    y0 = 18 + d
    for x0 in (15 + gaze, 25 + gaze):
        if eyes == "open" or eyes == "worried":
            b.rect(x0, y0, 5, 5, 0)
            b.rect(x0, y0, 2, 2, 1)
        elif eyes == "wide":
            b.rect(x0 - 1, y0 - 1, 6, 6, 0)
            b.rect(x0 - 1, y0 - 1, 2, 2, 1)
        elif eyes == "half":
            b.rect(x0, y0 + 2, 5, 3, 0)
            b.set(x0, y0 + 2, 1)
        elif eyes == "closed":
            b.rect(x0, y0 + 3, 5, 1, 0)
        elif eyes == "sparkle":
            b.rect(x0, y0, 5, 5, 0)
            b.glyph(x0 + 1, y0 + 1, STAR, 1)
    if eyes == "worried":
        b.line(14 + gaze, 16 + d, 19 + gaze, 15 + d, 0)
        b.line(25 + gaze, 15 + d, 30 + gaze, 16 + d, 0)

    # Mouth, as cleared pixels.
    cx, cy = 22.5, 25 + d
    if mouth == "smile":
        b.arc(cx, cy, 5, 25, 155, 0)
    elif mouth == "big":
        b.arc(cx, cy, 6, 15, 165, 0, thick=2)
    elif mouth == "flat":
        b.line(19, 28 + d, 26, 28 + d, 0)
    elif mouth == "frown":
        b.arc(cx, 31 + d, 5, 205, 335, 0)
    elif mouth == "o":
        b.rect(21, 26 + d, 3, 3, 0)
        b.set(22, 27 + d, 1)

    # Extras outside the body: sleep letters, sparkles, a raised flipper.
    if "zz1" in extras or "zz2" in extras or "zz3" in extras:
        b.glyph(36, 9, Z_SMALL)
    if "zz2" in extras or "zz3" in extras:
        b.glyph(40, 3, Z_BIG)
    if "zz3" in extras:
        b.rect(34, 13, 2, 1)
    if "spark_a" in extras:
        for sx, sy in ((5, 6), (43, 4), (3, 35)):
            b.glyph(sx, sy, STAR)
    if "spark_b" in extras:
        for sx, sy in ((10, 2), (46, 12), (40, 36)):
            b.glyph(sx, sy, STAR)
    if "point" in extras:
        b.poly([(33, 18 + d), (36, 19 + d), (45, 8), (43, 6)])
    return b


# ----------------------------------------------------------------------------- animations
MOODS = ["sad", "neutral", "happy", "ecstatic", "worried"]
BASE = {
    "sad": dict(eyes="half", mouth="frown"),
    "neutral": dict(eyes="open", mouth="flat"),
    "happy": dict(eyes="open", mouth="smile"),
    "ecstatic": dict(eyes="open", mouth="big"),
    "worried": dict(eyes="worried", mouth="flat"),
}


def F(spec, dx=0, dy=0, ms=500):
    return (spec, dx, dy, ms)


def idle_anims(b):
    """Idle animations for base expression `b`. Each plays once; the player picks the next by weight."""
    def v(**kw):
        s = dict(b)
        s.update(kw)
        return s
    return {
        "breathe": [F(v(), 0, 0, 700), F(v(), 0, 1, 700)],
        "blink": [F(v(eyes="closed"), 0, 0, 120)],
        "look_left": [F(v(gaze=-1), 0, 0, 1300)],
        "look_right": [F(v(gaze=1), 0, 0, 1300)],
        "wiggle": [F(v(), -1, 0, 120), F(v(), 1, 0, 120), F(v(), -1, 0, 120), F(v(), 1, 0, 120), F(v(), 0, 0, 120)],
        "bounce": [F(v(body="stretch"), 0, -1, 90), F(v(body="stretch"), 0, -2, 90), F(v(body="stretch"), 0, -1, 90),
                   F(v(body="squash"), 0, 0, 110), F(v(), 0, 0, 150)],
        "sparkle": [F(v(eyes="sparkle"), 0, 0, 900)],
        "mope": [F(v(gaze=-1), 0, 1, 1400)],
        "sigh": [F(v(eyes="closed"), 0, 0, 700), F(v(eyes="closed"), 0, 1, 700), F(v(), 0, 0, 300)],
        "yawn": [F(v(mouth="o"), 0, 0, 500), F(v(mouth="o", eyes="closed"), 0, 0, 700), F(v(), 0, 0, 200)],
        "glance": [F(v(gaze=-1), 0, 0, 900), F(v(gaze=1), 0, 0, 900), F(v(), 0, 0, 600)],
    }


IDLE_WEIGHTS = {
    "sad": dict(mope=5, sigh=3, blink=2, look_left=1, look_right=1),
    "neutral": dict(breathe=4, blink=3, look_left=2, look_right=2, yawn=1),
    "happy": dict(breathe=3, blink=3, look_left=2, look_right=2, wiggle=2, bounce=1),
    "ecstatic": dict(bounce=3, wiggle=3, sparkle=3, blink=2, breathe=1),
    "worried": dict(glance=5, blink=2, sigh=2, mope=1),
}

CELEB = dict(eyes="wide", mouth="big")
REACTIONS = {
    "celebrate": [F(dict(CELEB, body="stretch"), 0, -1, 90, ), F(dict(CELEB, body="stretch", extras=("spark_a",)), 0, -2, 90),
                  F(dict(CELEB, body="stretch", extras=("spark_b",)), 0, -1, 90), F(dict(CELEB, body="squash", extras=("spark_a",)), 0, 0, 110),
                  F(dict(CELEB, extras=("spark_b",)), 0, 0, 150), F(dict(CELEB, body="stretch", extras=("spark_a",)), 0, -1, 90),
                  F(dict(CELEB, body="stretch", extras=("spark_b",)), 0, -2, 90), F(dict(CELEB, body="stretch", extras=("spark_a",)), 0, -1, 90),
                  F(dict(CELEB, body="squash", extras=("spark_b",)), 0, 0, 110), F(dict(CELEB, extras=("spark_a",)), 0, 0, 200)],
    "notice": [F(dict(eyes="wide", mouth="o", gaze=1, extras=("point",)), 0, 0, 500), F(dict(eyes="wide", mouth="o", gaze=1), 0, 0, 400)],
    "sleep": [F(dict(eyes="closed", mouth="flat", extras=("zz1",)), 0, 0, 700), F(dict(eyes="closed", mouth="flat", extras=("zz2",)), 0, 1, 700),
              F(dict(eyes="closed", mouth="flat", extras=("zz3",)), 0, 0, 700), F(dict(eyes="closed", mouth="flat", extras=("zz2",)), 0, 1, 700)],
}


def build():
    images: list[Bitmap] = []
    index: dict[bytes, int] = {}

    def img_id(spec):
        bm = render(**spec)
        key = bytes(bm.px)
        if key not in index:
            index[key] = len(images)
            images.append(bm)
        return index[key]

    anims = []  # (enum_name, display_name, [(img, dx, dy, ms)])
    base_image = {}
    idle_sets = {}
    for mood in MOODS:
        base_image[mood] = img_id(BASE[mood])
        per = idle_anims(BASE[mood])
        choices = []
        for name, weight in IDLE_WEIGHTS[mood].items():
            enum = f"PET_ANIM_{mood.upper()}_{name.upper()}"
            anims.append((enum, f"{mood}/{name}", [(img_id(s), dx, dy, ms) for (s, dx, dy, ms) in per[name]]))
            choices.append((enum, weight))
        idle_sets[mood] = choices
    for name, frames in REACTIONS.items():
        anims.append((f"PET_ANIM_{name.upper()}", name, [(img_id(s), dx, dy, ms) for (s, dx, dy, ms) in frames]))
    return images, anims, base_image, idle_sets


# ----------------------------------------------------------------------------- output
def write_c(images, anims, base_image, idle_sets):
    h = ["/* Generated by tools/gen_sprites.py. Do not edit; change the script and rerun it. */", "#pragma once",
         "#include <stdint.h>", '#include "lvgl.h"', "",
         f"#define PET_SPRITE_W {W}", f"#define PET_SPRITE_H {H}", "",
         "typedef struct { uint16_t img; int8_t dx; int8_t dy; uint16_t ms; } pet_frame_t;",
         "typedef struct { const char *name; const pet_frame_t *frames; uint16_t count; } pet_anim_t;",
         "typedef struct { uint16_t anim; uint8_t weight; } pet_idle_choice_t;", "",
         "enum { " + ", ".join(f"PET_MOOD_{m.upper()}" for m in MOODS) + ", PET_MOOD_CLASSES };",
         "enum {"]
    h += [f"    {enum}," for enum, _, _ in anims]
    h += ["    PET_ANIM_COUNT", "};", "",
          "extern const lv_image_dsc_t *const pet_images[];", "extern const uint16_t pet_image_count;",
          "extern const pet_anim_t pet_anims[PET_ANIM_COUNT];",
          "extern const uint16_t pet_base_image[PET_MOOD_CLASSES];",
          "extern const pet_idle_choice_t *const pet_idle_sets[PET_MOOD_CLASSES];",
          "extern const uint8_t pet_idle_set_len[PET_MOOD_CLASSES];", ""]
    (OUT_DIR / "sprites.h").write_text("\n".join(h))

    c = ["/* Generated by tools/gen_sprites.py. Do not edit; change the script and rerun it. */", '#include "sprites.h"', ""]
    for i, bm in enumerate(images):
        vals = ",".join("255" if v else "0" for v in bm.px)
        c.append(f"static const uint8_t __attribute__((aligned(4))) pet_img_{i:03d}_map[{W * H}] = {{{vals}}};")
        c.append(f"static const lv_image_dsc_t pet_img_{i:03d} = {{ .header = {{ .magic = LV_IMAGE_HEADER_MAGIC, "
                 f".cf = LV_COLOR_FORMAT_A8, .flags = 0, .w = {W}, .h = {H}, .stride = {W} }}, "
                 f".data_size = {W * H}, .data = pet_img_{i:03d}_map }};")
    c.append("")
    c.append("const lv_image_dsc_t *const pet_images[] = {" + ", ".join(f"&pet_img_{i:03d}" for i in range(len(images))) + "};")
    c.append(f"const uint16_t pet_image_count = {len(images)};")
    c.append("")
    for enum, _, frames in anims:
        body = ", ".join(f"{{{img}, {dx}, {dy}, {ms}}}" for img, dx, dy, ms in frames)
        c.append(f"static const pet_frame_t {enum.lower()}_frames[] = {{{body}}};")
    c.append("")
    c.append("const pet_anim_t pet_anims[PET_ANIM_COUNT] = {")
    for enum, name, frames in anims:
        c.append(f'    [{enum}] = {{ "{name}", {enum.lower()}_frames, {len(frames)} }},')
    c.append("};")
    c.append("")
    c.append("const uint16_t pet_base_image[PET_MOOD_CLASSES] = {" + ", ".join(str(base_image[m]) for m in MOODS) + "};")
    for m in MOODS:
        body = ", ".join(f"{{{enum}, {w}}}" for enum, w in idle_sets[m])
        c.append(f"static const pet_idle_choice_t idle_{m}[] = {{{body}}};")
    c.append("const pet_idle_choice_t *const pet_idle_sets[PET_MOOD_CLASSES] = {" + ", ".join(f"idle_{m}" for m in MOODS) + "};")
    c.append("const uint8_t pet_idle_set_len[PET_MOOD_CLASSES] = {" + ", ".join(str(len(idle_sets[m])) for m in MOODS) + "};")
    c.append("")
    (OUT_DIR / "sprites.c").write_text("\n".join(c))


def write_png(path, images, scale=3, cols=8, gap=4):
    rows = math.ceil(len(images) / cols)
    pw, ph = cols * (W * scale + gap) + gap, rows * (H * scale + gap) + gap
    bg, ink, cell = (11, 15, 20), (230, 240, 245), (24, 32, 40)
    canvas = [bytearray(bg * pw) for _ in range(ph)]
    for i, bm in enumerate(images):
        ox = gap + (i % cols) * (W * scale + gap)
        oy = gap + (i // cols) * (H * scale + gap)
        for y in range(H * scale):
            row = canvas[oy + y]
            for x in range(W * scale):
                col = ink if bm.px[(y // scale) * W + (x // scale)] else cell
                row[(ox + x) * 3:(ox + x) * 3 + 3] = bytes(col)
    raw = b"".join(b"\x00" + bytes(r) for r in canvas)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", pw, ph, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="write a PNG contact sheet of every unique frame")
    ap.add_argument("--ascii", action="store_true", help="print every unique frame as text")
    a = ap.parse_args()
    images, anims, base_image, idle_sets = build()
    write_c(images, anims, base_image, idle_sets)
    total_frames = sum(len(f) for _, _, f in anims)
    print(f"{len(images)} unique frames ({len(images) * W * H / 1024:.0f} KiB), {len(anims)} animations, "
          f"{total_frames} frame slots -> {OUT_DIR / 'sprites.c'}")
    if a.preview:
        write_png(a.preview, images)
        print(f"preview: {a.preview}")
    if a.ascii:
        for i, bm in enumerate(images):
            print(f"--- image {i}")
            print(bm.ascii())


if __name__ == "__main__":
    main()
