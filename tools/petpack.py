#!/usr/bin/env python3
"""Check, preview and pack a pet folder for the desk pet box (#66).

A pet is a folder, for example pets/dolphin/:

    pet.json     name, colours, animations, per-mood rest frame and idle weights, play looks
    frames.png   every frame as 48x40 pixel art in a grid, 8 frames per row, no gaps

In frames.png a pixel is ink when it is opaque and dark (black on white or on transparency is the
easy way to draw); everything else is empty. The firmware recolours ink with the pack's colours.

    python3 tools/petpack.py check   pets/dolphin                 # readable errors, or OK
    python3 tools/petpack.py preview pets/dolphin -o sheet.png    # contact sheet in the pet's colours
    python3 tools/petpack.py build   pets/dolphin -o dolphin.petpack
    python3 tools/petpack.py c       pets/dolphin                 # regenerate the firmware's built-in sprites.c/h

The .petpack layout is in notes/pet-packs.md. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

W, H = 48, 40
COLS = 8
FORMAT = 1
MAGIC = b"PETPACK\0"
MOODS = ["sad", "neutral", "happy", "ecstatic", "worried"]
REACTIONS = ["celebrate", "notice", "sleep"]
COLOR_KEYS = ["ink", "sleep_ink", "background", "panel", "dim", "accent", "warn", "good"]
MAX_FRAMES, MAX_ANIMS, MAX_ANIM_FRAMES = 1024, 128, 64
MAX_JSON = 64 * 1024     # bytes of metadata in a pack, padding included
MAX_ANIM_NAME = 32
MAX_NAME_BYTES = 48      # the pet's display name, UTF-8
SHEET_W = COLS * W       # sheets are exactly 8 frames wide, so frame numbers never shift
MAX_SHEET_ROWS = MAX_FRAMES // COLS
REPO = Path(__file__).resolve().parent.parent
C_DIR = REPO / "projects" / "pet" / "main"
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
NAME_RE = re.compile(r"[a-z0-9_]+(/[a-z0-9_]+)?")  # used with fullmatch


class PetError(ValueError):
    """A pet folder or pack that cannot be used; the message says what to fix."""


# ----------------------------------------------------------------------------- PNG
def png_read(path):
    """(width, height, rows) where rows[y][x] is (r, g, b, a). Non-interlaced PNG, any colour type, 8-bit, or 1/2/4-bit
    grey or palette: what pixel editors write."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PetError(f"{path}: not a PNG")
    pos, idat, plte, trns, hdr = 8, b"", None, None, None
    while pos < len(data):
        if pos + 8 > len(data):
            raise PetError(f"{path}: damaged PNG (cut off inside a chunk header)")
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if len(body) != n:
            raise PetError(f"{path}: damaged PNG (cut off inside {tag.decode('latin-1')})")
        pos += 12 + n
        if tag == b"IHDR":
            if n != 13:
                raise PetError(f"{path}: damaged PNG (bad IHDR)")
            hdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if not hdr:
        raise PetError(f"{path}: no IHDR")
    w, h, depth, ctype, _, _, interlace = hdr
    if interlace:
        raise PetError(f"{path}: interlaced PNGs are not supported; save without interlacing")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None or (depth != 8 and not (ctype in (0, 3) and depth in (1, 2, 4))):
        raise PetError(f"{path}: unsupported PNG (colour type {ctype}, {depth}-bit)")
    if w > SHEET_W or h > MAX_SHEET_ROWS * H:
        raise PetError(f"{path}: {w}x{h} is larger than a sheet can be ({SHEET_W}x{MAX_SHEET_ROWS * H})")
    try:
        raw = zlib.decompress(idat)
    except zlib.error as e:
        raise PetError(f"{path}: damaged PNG image data ({e})")
    bpp = max(1, channels * depth // 8)
    stride = (w * channels * depth + 7) // 8
    if len(raw) < h * (1 + stride):
        raise PetError(f"{path}: damaged PNG (image data too short)")
    gray_key = trns[0] << 8 | trns[1] if ctype == 0 and trns and len(trns) >= 2 else None
    rgb_key = struct.unpack(">HHH", trns[:6]) if ctype == 2 and trns and len(trns) >= 6 else None
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        ftype, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ftype > 4:
            raise PetError(f"{path}: damaged PNG (unknown row filter {ftype})")
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 255
            elif ftype == 2:
                line[x] = (line[x] + b) & 255
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if depth < 8:
            per = 8 // depth
            vals = [(line[x // per] >> (8 - depth * (x % per + 1))) & ((1 << depth) - 1) for x in range(w)]
        else:
            vals = list(line)
        row = []
        for x in range(w):
            if ctype == 0:
                v = vals[x] * 255 // ((1 << depth) - 1)
                row.append((v, v, v, 0 if vals[x] == gray_key else 255))
            elif ctype == 2:
                px = (vals[3 * x], vals[3 * x + 1], vals[3 * x + 2])
                row.append(px + (0 if px == rgb_key else 255,))
            elif ctype == 3:
                k = vals[x]
                if plte is None or k >= len(plte):
                    raise PetError(f"{path}: palette index out of range")
                alpha = trns[k] if trns and k < len(trns) else 255
                row.append(plte[k] + (alpha,))
            elif ctype == 4:
                row.append((vals[2 * x], vals[2 * x], vals[2 * x], vals[2 * x + 1]))
            else:
                row.append(tuple(vals[4 * x:4 * x + 4]))
        rows.append(row)
    return w, h, rows


def png_write(path, w, h, pixel):
    """RGB PNG; pixel(x, y) -> (r, g, b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def is_ink(px):
    r, g, b, a = px
    return a >= 128 and (r + g + b) < 3 * 128


def sheet_frames(path):
    """Frames from a sheet, each a bytes of W*H 0/1, every cell in reading order (frame k is row k // 8, column
    k % 8). Blank cells are kept so frame numbers match the picture; build() leaves out unused ones at the end."""
    w, h, rows = png_read(path)
    if w != SHEET_W or h % H or h == 0:
        raise PetError(f"{path}: {w}x{h}; a sheet is {SHEET_W} pixels wide ({COLS} frames of {W}x{H}) "
                       f"and a multiple of {H} high")
    frames = []
    for fy in range(h // H):
        for fx in range(w // W):
            frames.append(bytes(1 if is_ink(rows[fy * H + y][fx * W + x]) else 0 for y in range(H) for x in range(W)))
    return frames


def write_sheet(path, frames, ink=(0, 0, 0), paper=(255, 255, 255), scale=1, gap=0, cell=None):
    rows = max(1, math.ceil(len(frames) / COLS))
    fw, fh = W * scale + gap, H * scale + gap
    pw, ph = COLS * fw + gap, rows * fh + gap
    cell = cell or paper

    def pixel(x, y):
        cx, cy = (x - gap) // fw, (y - gap) // fh
        ix, iy = (x - gap) % fw, (y - gap) % fh
        k = cy * COLS + cx
        if x < gap or y < gap or ix >= W * scale or iy >= H * scale or k >= len(frames):
            return paper
        return ink if frames[k][(iy // scale) * W + ix // scale] else cell

    png_write(path, pw, ph, pixel)


# ----------------------------------------------------------------------------- pet
def load(folder):
    """(meta, frames) from a pet folder. meta is pet.json as parsed; frames a list of W*H 0/1 bytes."""
    folder = Path(folder)
    try:
        meta = json.loads((folder / "pet.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PetError(f"{folder}: no pet.json")
    except json.JSONDecodeError as e:
        raise PetError(f"{folder / 'pet.json'}: {e}")
    sheet = meta.get("sheet", "frames.png")
    if not isinstance(sheet, str) or "/" in sheet or "\\" in sheet:
        raise PetError("sheet must be a file name in the pet folder")
    frames = sheet_frames(folder / sheet)
    return meta, frames


def is_int(v):
    """A JSON whole number: Python's bool is an int, and 1.0 is not one."""
    return type(v) is int


def frame_list(entries, where, nframes, errors):
    if not isinstance(entries, list) or not entries:
        errors.append(f"{where}: needs at least one frame")
        return
    if len(entries) > MAX_ANIM_FRAMES:
        errors.append(f"{where}: {len(entries)} frames, at most {MAX_ANIM_FRAMES}")
    for i, f in enumerate(entries):
        at = f"{where} frame {i}"
        if not isinstance(f, dict) or set(f) - {"frame", "dx", "dy", "ms"}:
            errors.append(f"{at}: must be {{\"frame\": n}} with optional dx, dy, ms")
            continue
        k = f.get("frame")
        if not is_int(k) or not 0 <= k < nframes:
            errors.append(f"{at}: frame {k!r} is not in the sheet (0 to {nframes - 1})")
        for key, lo, hi in (("dx", -8, 8), ("dy", -8, 8), ("ms", 20, 10000)):
            v = f.get(key, 0 if key != "ms" else 500)
            if not is_int(v) or not lo <= v <= hi:
                errors.append(f"{at}: {key} must be a whole number from {lo} to {hi}")


def check(meta, frames):
    """Every problem with a pet, as readable lines; empty when it can be packed."""
    e = []
    n = len(frames)
    if not is_int(meta.get("format")) or meta["format"] != FORMAT:
        e.append(f"format must be {FORMAT}")
    name = meta.get("name")
    if (not isinstance(name, str) or not 1 <= len(name) <= 24 or len(name.encode("utf-8")) > MAX_NAME_BYTES
            or not name.isprintable()):
        e.append(f"name must be 1 to 24 printable characters, at most {MAX_NAME_BYTES} bytes as UTF-8")
    if n == 0:
        e.append("the sheet has no frames")
    if n > MAX_FRAMES:
        e.append(f"{n} frames, at most {MAX_FRAMES}")
    colors = meta.get("colors")
    if not isinstance(colors, dict):
        e.append("colors must be an object")
        colors = {}
    for key in COLOR_KEYS:
        if not isinstance(colors.get(key), str) or not HEX.match(colors[key]):
            e.append(f"colors.{key} must be a colour like \"#E6F0F5\"")
    for key in set(colors) - set(COLOR_KEYS):
        e.append(f"colors.{key} is not a known colour ({', '.join(COLOR_KEYS)})")
    anims = meta.get("animations")
    if not isinstance(anims, dict) or not anims:
        e.append("animations must be an object with at least one animation")
        anims = {}
    if len(anims) > MAX_ANIMS:
        e.append(f"{len(anims)} animations, at most {MAX_ANIMS}")
    enums = {}
    for aname, entries in anims.items():
        if not NAME_RE.fullmatch(aname) or len(aname) > MAX_ANIM_NAME:
            e.append(f"animation name {aname!r}: up to {MAX_ANIM_NAME} lowercase letters, digits, _ and one optional /")
        elif aname == "count":
            e.append("animation name 'count' is reserved")
        else:
            other = enums.setdefault(enum_of(aname), aname)
            if other != aname:
                e.append(f"animation names {other!r} and {aname!r} are too alike (both become {enum_of(aname)})")
        frame_list(entries, f"animation {aname}", n, e)
    for r in REACTIONS:
        if r not in anims:
            e.append(f"animation {r!r} is required")
    moods = meta.get("moods")
    if not isinstance(moods, dict):
        e.append("moods must be an object")
        moods = {}
    for m in MOODS:
        md = moods.get(m)
        if not isinstance(md, dict):
            e.append(f"moods.{m} is required")
            continue
        rest = md.get("rest")
        if not is_int(rest) or not 0 <= rest < n:
            e.append(f"moods.{m}.rest: frame {rest!r} is not in the sheet")
        idle = md.get("idle")
        if not isinstance(idle, dict) or not idle:
            e.append(f"moods.{m}.idle needs at least one animation")
            continue
        for aname, wgt in idle.items():
            if aname not in anims:
                e.append(f"moods.{m}.idle: no animation called {aname!r}")
            if not is_int(wgt) or not 1 <= wgt <= 255:
                e.append(f"moods.{m}.idle.{aname}: weight must be 1 to 255")
    for m in set(moods) - set(MOODS):
        e.append(f"moods.{m} is not a mood ({', '.join(MOODS)})")
    play = meta.get("play")
    if not isinstance(play, dict):
        e.append("play must be an object")
        play = {}
    for key in ("snack", "heart"):
        frame_list(play.get(key), f"play.{key}", n, e)
    pads = play.get("pads")
    if not isinstance(pads, list) or len(pads) != 4 or not all(isinstance(c, str) and HEX.match(c) for c in pads):
        e.append("play.pads must be four colours like \"#4FD1C5\"")
    known = {"format", "name", "sheet", "colors", "animations", "moods", "play"}
    for key in set(meta) - known:
        e.append(f"{key} is not a known field")
    return e


def must_check(meta, frames):
    errors = check(meta, frames)
    if errors:
        raise PetError("\n".join(errors))


# ----------------------------------------------------------------------------- pack
def pack_bits(frame):
    out = bytearray((W * H + 7) // 8)
    for i, v in enumerate(frame):
        if v:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def unpack_bits(data):
    return bytes((data[i // 8] >> (7 - i % 8)) & 1 for i in range(W * H))


def used_frames(meta):
    """Every frame number the metadata refers to."""
    refs = {f["frame"] for a in meta["animations"].values() for f in a}
    refs |= {meta["moods"][m]["rest"] for m in MOODS}
    refs |= {f["frame"] for key in ("snack", "heart") for f in meta["play"][key]}
    return refs


def build(meta, frames):
    """The .petpack bytes for a checked pet."""
    must_check(meta, frames)
    frames = frames[:max(used_frames(meta)) + 1]  # blank cells after the last one used are not packed
    body = {k: v for k, v in meta.items() if k != "sheet"}
    body.update(frame_w=W, frame_h=H, frame_count=len(frames))
    js = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b"\0" * (-len(js) % 4)
    if len(js) > MAX_JSON:
        raise PetError(f"pet.json packs to {len(js)} bytes, at most {MAX_JSON}")
    tail = struct.pack("<I", len(js)) + js + b"".join(pack_bits(f) for f in frames)
    total = 20 + len(tail)
    return MAGIC + struct.pack("<II", FORMAT, total) + struct.pack("<I", zlib.crc32(tail) & 0xFFFFFFFF) + tail


def parse(blob):
    """(meta, frames) back from .petpack bytes, with the checks a loader must make; raises PetError on anything the
    firmware should refuse."""
    if len(blob) < 24 or blob[:8] != MAGIC:
        raise PetError("not a pet pack (bad magic)")
    fmt, total, crc, jlen = struct.unpack_from("<IIII", blob, 8)
    if fmt != FORMAT:
        raise PetError(f"pack format {fmt}, this tool reads {FORMAT}")
    if total != len(blob):
        raise PetError(f"pack says {total} bytes, file has {len(blob)}")
    if zlib.crc32(blob[20:]) & 0xFFFFFFFF != crc:
        raise PetError("pack checksum does not match")
    if jlen % 4 or jlen > MAX_JSON or 24 + jlen > total:
        raise PetError(f"pack metadata length {jlen} is out of range")
    try:
        meta = json.loads(blob[24:24 + jlen].rstrip(b"\0").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PetError(f"pack metadata is not valid JSON ({e})")
    if not isinstance(meta, dict):
        raise PetError("pack metadata is not a JSON object")
    fw, fh, count = meta.pop("frame_w", None), meta.pop("frame_h", None), meta.pop("frame_count", None)
    if (fw, fh) != (W, H):
        raise PetError(f"pack frames are {fw}x{fh}, format {FORMAT} is {W}x{H}")
    size = (W * H + 7) // 8
    if not is_int(count) or not 1 <= count <= MAX_FRAMES or 24 + jlen + count * size != total:
        raise PetError("pack frame count does not match its length")
    off = 24 + jlen
    frames = [unpack_bits(blob[off + i * size:off + (i + 1) * size]) for i in range(count)]
    must_check(meta, frames)
    return meta, frames


# ----------------------------------------------------------------------------- built-in C
def enum_of(aname):
    return "PET_ANIM_" + aname.upper().replace("/", "_")


def write_c(meta, frames, out_dir=C_DIR):
    """sprites.h/.c for the firmware's built-in pet: the frames the animations and rest frames use, in sheet order."""
    must_check(meta, frames)
    anims = meta["animations"]
    used = sorted({f["frame"] for a in anims.values() for f in a} | {meta["moods"][m]["rest"] for m in MOODS})
    remap = {old: new for new, old in enumerate(used)}
    h = ["/* Generated by tools/petpack.py from pets/; edit the pet folder and rerun it. */", "#pragma once",
         "#include <stdint.h>", '#include "lvgl.h"', "",
         f"#define PET_SPRITE_W {W}", f"#define PET_SPRITE_H {H}", "",
         "typedef struct { uint16_t img; int8_t dx; int8_t dy; uint16_t ms; } pet_frame_t;",
         "typedef struct { const char *name; const pet_frame_t *frames; uint16_t count; } pet_anim_t;",
         "typedef struct { uint16_t anim; uint8_t weight; } pet_idle_choice_t;", "",
         "enum { " + ", ".join(f"PET_MOOD_{m.upper()}" for m in MOODS) + ", PET_MOOD_CLASSES };",
         "enum {"]
    h += [f"    {enum_of(a)}," for a in anims]
    h += ["    PET_ANIM_COUNT", "};", "",
          "extern const lv_image_dsc_t *const pet_images[];", "extern const uint16_t pet_image_count;",
          "extern const pet_anim_t pet_anims[PET_ANIM_COUNT];",
          "extern const uint16_t pet_base_image[PET_MOOD_CLASSES];",
          "extern const pet_idle_choice_t *const pet_idle_sets[PET_MOOD_CLASSES];",
          "extern const uint8_t pet_idle_set_len[PET_MOOD_CLASSES];", ""]
    c = ["/* Generated by tools/petpack.py from pets/; edit the pet folder and rerun it. */", '#include "sprites.h"', ""]
    for i, old in enumerate(used):
        vals = ",".join("255" if v else "0" for v in frames[old])
        c.append(f"static const uint8_t __attribute__((aligned(4))) pet_img_{i:03d}_map[{W * H}] = {{{vals}}};")
        c.append(f"static const lv_image_dsc_t pet_img_{i:03d} = {{ .header = {{ .magic = LV_IMAGE_HEADER_MAGIC, "
                 f".cf = LV_COLOR_FORMAT_A8, .flags = 0, .w = {W}, .h = {H}, .stride = {W} }}, "
                 f".data_size = {W * H}, .data = pet_img_{i:03d}_map }};")
    c.append("")
    c.append("const lv_image_dsc_t *const pet_images[] = {" + ", ".join(f"&pet_img_{i:03d}" for i in range(len(used))) + "};")
    c.append(f"const uint16_t pet_image_count = {len(used)};")
    c.append("")
    for a, fl in anims.items():
        body = ", ".join(f"{{{remap[f['frame']]}, {f.get('dx', 0)}, {f.get('dy', 0)}, {f.get('ms', 500)}}}" for f in fl)
        c.append(f"static const pet_frame_t {enum_of(a).lower()}_frames[] = {{{body}}};")
    c.append("")
    c.append("const pet_anim_t pet_anims[PET_ANIM_COUNT] = {")
    for a, fl in anims.items():
        c.append(f'    [{enum_of(a)}] = {{ "{a}", {enum_of(a).lower()}_frames, {len(fl)} }},')
    c.append("};")
    c.append("")
    c.append("const uint16_t pet_base_image[PET_MOOD_CLASSES] = {"
             + ", ".join(str(remap[meta["moods"][m]["rest"]]) for m in MOODS) + "};")
    for m in MOODS:
        body = ", ".join(f"{{{enum_of(a)}, {w}}}" for a, w in meta["moods"][m]["idle"].items())
        c.append(f"static const pet_idle_choice_t idle_{m}[] = {{{body}}};")
    c.append("const pet_idle_choice_t *const pet_idle_sets[PET_MOOD_CLASSES] = {" + ", ".join(f"idle_{m}" for m in MOODS) + "};")
    c.append("const uint8_t pet_idle_set_len[PET_MOOD_CLASSES] = {"
             + ", ".join(str(len(meta["moods"][m]["idle"])) for m in MOODS) + "};")
    c.append("")
    Path(out_dir, "sprites.h").write_text("\n".join(h))
    Path(out_dir, "sprites.c").write_text("\n".join(c))
    return len(used)


# ----------------------------------------------------------------------------- command line
def rgb(hexstr):
    return tuple(int(hexstr[i:i + 2], 16) for i in (1, 3, 5))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("check", "preview", "build", "c"):
        p = sub.add_parser(cmd)
        p.add_argument("folder")
        if cmd in ("preview", "build"):
            p.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    try:
        meta, frames = load(a.folder)
        if a.cmd == "check":
            must_check(meta, frames)
            print(f"OK: {meta['name']}, {len(frames)} frames, {len(meta['animations'])} animations")
        elif a.cmd == "preview":
            must_check(meta, frames)
            col = meta["colors"]
            write_sheet(a.out, frames, ink=rgb(col["ink"]), paper=rgb(col["background"]), cell=rgb(col["panel"]),
                        scale=3, gap=4)
            print(f"preview: {a.out} (frame k is row k // {COLS}, column k % {COLS})")
        elif a.cmd == "build":
            blob = build(meta, frames)
            Path(a.out).write_bytes(blob)
            print(f"{a.out}: {len(blob)} bytes, {len(frames)} frames, {len(meta['animations'])} animations")
        else:
            n = write_c(meta, frames)
            print(f"{C_DIR / 'sprites.c'}: {n} frames, {len(meta['animations'])} animations")
    except PetError as e:
        print(f"{a.folder}:\n" + "\n".join(f"  {line}" for line in str(e).splitlines()), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
