#!/usr/bin/env python3
"""Collect a pet build's flash images and write the manifest the Workbench flasher installs from (#51).

    python3 tools/make_manifest.py --build projects/pet/build --version 1.0.0 --out dist

copies each image in the build's flash_args (bootloader, partition table, OTA data, app) to dist/ as
desk-pet-esp32p4-rev1-v1.0.0-<part>.bin and writes dist/manifest.json listing them with their offsets. They stay
separate parts, not one merged image, because a merged image fills the gaps between them with 0xFF, and the NVS
partition holding the box's saved Wi-Fi and bridge settings sits in one of those gaps: a reinstall without "erase
first" would wipe them. The script refuses a part that overlaps NVS. The chip revision range and flash size come from
the build's sdkconfig.json, so the manifest cannot drift from what the images were built for. Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import struct

NAME = "Desk pet"
CHIP = "ESP32-P4"
REPO = "John-ODell/ESP-P4_Box"
BAUD = 921600          # the CH343 has flashed at this many times (notes/flashing.md)
CONSOLE_BAUD = 115200
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
NO_HEADER = {"partition-table", "ota-data-initial"}  # every other part is an ESP image and starts with 0xE9


def read_sdkconfig(path):
    """The build's config/sdkconfig.json: Kconfig names without CONFIG_, typed values."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_flash_args(path):
    """[(offset, file)] from a build's flash_args, sorted by offset. Option lines are skipped: the build's images
    already carry their flash mode and size, and the flasher writes them as they are."""
    parts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            words = line.split()
            if not words or words[0].startswith("--"):
                continue
            if len(words) != 2:
                raise ValueError(f"unexpected flash_args line: {line.strip()!r}")
            parts.append((int(words[0], 0), words[1]))
    if not parts:
        raise ValueError("flash_args lists no images")
    return sorted(parts)


def nvs_ranges(table):
    """[(start, end)] of every NVS partition in a binary partition table."""
    ranges = []
    for i in range(0, len(table) - 31, 32):
        magic, ptype, subtype, offset, size = struct.unpack_from("<HBBII", table, i)
        if magic != 0x50AA:  # 0xEBEB is the MD5 entry, 0xFFFF the end
            break
        if ptype == 1 and subtype == 2:
            ranges.append((offset, offset + size))
    return ranges


def part_name(file, app):
    stem = os.path.splitext(os.path.basename(file))[0].replace("_", "-")
    return "app" if stem == app else stem


def asset_name(version, part):
    return f"desk-pet-esp32p4-rev1-v{version}-{part}.bin"


def check_parts(parts):
    """`parts` is [(offset, part name, bytes)] sorted by offset. Raises ValueError on anything a flasher should not
    write: no partition table, overlaps, a part over NVS, an ESP image without its header."""
    tables = [data for _, name, data in parts if name == "partition-table"]
    if len(tables) != 1:
        raise ValueError("expected exactly one partition table among the parts")
    nvs = nvs_ranges(tables[0])
    if not nvs:
        raise ValueError("the partition table has no NVS partition")
    end = 0
    for offset, name, data in parts:
        if offset < end:
            raise ValueError(f"{name} at {hex(offset)} overlaps the part before it")
        end = offset + len(data)
        for lo, hi in nvs:
            if offset < hi and lo < end:
                raise ValueError(f"{name} ({hex(offset)}-{hex(end)}) would overwrite NVS ({hex(lo)}-{hex(hi)})")
        if name not in NO_HEADER and data[:1] != b"\xe9":
            raise ValueError(f"{name} does not start with an ESP image header (0xE9)")


def manifest(version, parts, cfg):
    """`parts` is [(offset, asset name, bytes)]."""
    rev_min = int(cfg["ESP_REV_MIN_FULL"])
    rev_max = int(cfg["ESP_REV_MAX_FULL"])
    if cfg.get("IDF_TARGET") != "esp32p4":
        raise ValueError(f"built for {cfg.get('IDF_TARGET')!r}, not esp32p4")
    if rev_max >= 300:
        raise ValueError(f"revision range {rev_min}-{rev_max} includes 3.x; the release is the rev1.x build")
    return {
        "v": 1,
        "name": NAME,
        "version": version,
        "chip": CHIP,
        "chip_rev_min": rev_min,
        "chip_rev_max": rev_max,
        "flash_size": cfg["ESPTOOLPY_FLASHSIZE"],
        "baud": BAUD,
        "parts": [{
            "path": path,
            "offset": offset,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        } for offset, path, data in parts],
        "console_baud": CONSOLE_BAUD,
        "notes": f"https://github.com/{REPO}/releases/tag/pet-v{version}",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", required=True, help="the project's build directory (has flash_args and config/)")
    ap.add_argument("--version", required=True, help="X.Y.Z, the tag without pet-v")
    ap.add_argument("--out", required=True, help="directory for the images and manifest.json")
    ap.add_argument("--app", default="pet", help="the app image's file name without .bin (default: pet)")
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version):
        ap.error("--version must be X.Y.Z")

    cfg = read_sdkconfig(os.path.join(args.build, "config", "sdkconfig.json"))
    named = []
    for offset, file in read_flash_args(os.path.join(args.build, "flash_args")):
        with open(os.path.join(args.build, file), "rb") as f:
            named.append((offset, part_name(file, args.app), f.read()))
    check_parts(named)

    os.makedirs(args.out, exist_ok=True)
    assets = []
    for offset, name, data in named:
        asset = asset_name(args.version, name)
        with open(os.path.join(args.out, asset), "wb") as f:
            f.write(data)
        assets.append((offset, asset, data))
    m = manifest(args.version, assets, cfg)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    for p in m["parts"]:
        print(f"{hex(p['offset']):>8}  {p['size']:>8}  {p['path']}")
    print(f"chip {CHIP} rev {m['chip_rev_min']}-{m['chip_rev_max']}, flash {m['flash_size']}")


if __name__ == "__main__":
    main()
