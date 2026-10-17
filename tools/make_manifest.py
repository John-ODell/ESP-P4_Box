#!/usr/bin/env python3
"""Merge a pet build into one image and write the manifest the Workbench flasher installs from (#51).

    python3 tools/make_manifest.py --build projects/pet/build --version 1.0.0 --out dist

writes dist/desk-pet-esp32p4-rev1-v1.0.0.bin and dist/manifest.json. The image starts at the lowest offset in the
build's flash_args (the bootloader, 0x2000 on the ESP32-P4), and the manifest says so. The chip revision range and
flash size come from the build's sdkconfig, so the manifest cannot drift from what the image was built for.
Standard library only; the merge runs esptool from the ESP-IDF environment.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

NAME = "Desk pet"
CHIP = "ESP32-P4"
REPO = "John-ODell/ESP-P4_Box"
BAUD = 921600          # the CH343 has flashed at this many times (notes/flashing.md)
CONSOLE_BAUD = 115200
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def read_sdkconfig(path):
    """The build's config/sdkconfig.json: Kconfig names without CONFIG_, typed values."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_flash_args(path):
    """(esptool option words, [(offset, file)]) from a build's flash_args."""
    options, parts = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            words = line.split()
            if not words:
                continue
            if words[0].startswith("--"):
                options += words
            elif len(words) == 2:
                parts.append((int(words[0], 0), words[1]))
            else:
                raise ValueError(f"unexpected flash_args line: {line.strip()!r}")
    if not parts:
        raise ValueError("flash_args lists no images")
    return options, sorted(parts)


def image_name(version):
    return f"desk-pet-esp32p4-rev1-v{version}.bin"


def manifest(version, path, data, offset, cfg):
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
        }],
        "console_baud": CONSOLE_BAUD,
        "notes": f"https://github.com/{REPO}/releases/tag/pet-v{version}",
    }


def merge(build, options, parts, offset, out_path, python=sys.executable):
    """esptool merge_bin, raw, starting at `offset` so byte 0 of the file is the bootloader's first byte."""
    cmd = [python, "-m", "esptool", "--chip", "esp32p4", "merge_bin", "-o", os.path.abspath(out_path),
           "--target-offset", hex(offset)] + options
    for off, name in parts:
        cmd += [hex(off), name]
    subprocess.run(cmd, cwd=build, check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", required=True, help="the project's build directory (has flash_args and config/)")
    ap.add_argument("--version", required=True, help="X.Y.Z, the tag without pet-v")
    ap.add_argument("--out", required=True, help="directory for the image and manifest.json")
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version):
        ap.error("--version must be X.Y.Z")

    cfg = read_sdkconfig(os.path.join(args.build, "config", "sdkconfig.json"))
    options, parts = read_flash_args(os.path.join(args.build, "flash_args"))
    offset = parts[0][0]
    os.makedirs(args.out, exist_ok=True)
    name = image_name(args.version)
    image = os.path.join(args.out, name)
    merge(args.build, options, parts, offset, image)
    with open(image, "rb") as f:
        data = f.read()
    if data[:1] != b"\xe9":
        raise SystemExit(f"{name} does not start with an ESP image header (0xE9)")
    m = manifest(args.version, name, data, offset, cfg)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    print(f"{name}: {len(data)} bytes at {hex(offset)}, sha256 {m['parts'][0]['sha256']}")
    print(f"chip {CHIP} rev {m['chip_rev_min']}-{m['chip_rev_max']}, flash {m['flash_size']}")


if __name__ == "__main__":
    main()
