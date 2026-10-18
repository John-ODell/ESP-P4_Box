# Pet packs (.petpack), format 1

Written by `tools/petpack.py build` from a pet folder (`pets/README.md`). Read by the firmware's loader (#67) and by
`petpack.parse()`. All integers are little-endian.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `PETPACK\0` |
| 8 | 4 | `format`, 1 |
| 12 | 4 | `total`, the length of the whole pack in bytes |
| 16 | 4 | CRC-32 of bytes 20 to `total`: zlib's `crc32`, which is `esp_rom_crc32_le(0, ...)` (that ROM function applies the `~` before and after itself, per `esp_rom_crc.h`) |
| 20 | 4 | `json_len`, a multiple of 4, at most 65536 |
| 24 | `json_len` | the metadata: UTF-8 JSON, then 0 to 3 NUL bytes of padding |
| 24 + `json_len` | `frame_count` x 240 | the frames, 1 bit per pixel |

**Metadata** is `pet.json` without `sheet`, plus `frame_w` (48), `frame_h` (40) and `frame_count`. Its fields and
limits are the ones `petpack.py check` enforces and `pets/README.md` describes: `format`, `name`, `colors`,
`animations`, `moods`, `play`. The loader treats a pack as unusable if any of them is missing, the wrong type or out
of range, and falls back to the built-in pet. `petpack.parse()` makes the same checks and is the reference.

- Read the JSON by length: take `json_len` bytes and drop trailing NULs. When the JSON is already a multiple of 4
  long there is no padding, so it is not NUL-terminated.
- Numbers are JSON whole numbers. `true`, `false` and `1.0` are not numbers here.
- A frame entry is `{"frame": n}` with optional `dx` (default 0), `dy` (default 0) and `ms` (default 500).
- `frame_count` must equal `(total - 24 - json_len) / 240`, and every frame number must be below it.

**Frames** are 48x40 = 1920 bits = 240 bytes each, in sheet order, rows top to bottom, pixels left to right, the first
pixel in the most significant bit of each byte. A 1 bit is ink. Frame numbers in the metadata index this list. Frame
`k` is the sheet cell at row `k // 8`, column `k % 8`, blank cells included, so numbers never shift; `build` leaves
out only the blank or unused cells after the highest frame the metadata uses.

**Limits** (format 1): at most 1024 frames, 128 animations, 64 frames per animation; animation names up to 32
characters (`[a-z0-9_]+` with one optional `/`); a pet name
of 1 to 24 printable characters and at most 48 bytes of UTF-8; `dx`/`dy` -8 to 8; `ms` 20 to 10000; idle weights 1 to
255; `json_len` at most 65536. So a pack is at most 24 + 65536 + 1024 x 240 = 311320 bytes (about 304 KB), which fits
easily in the 7 MB `storage` partition, where the loader expects it at offset 0 (flash address 0x820000 with the
current `partitions.csv`). Write one with `esptool write_flash 0x820000 pet.petpack`; erase the first 4 KB there to go
back to the built-in pet.

**The firmware loader** (`projects/pet/main/pet_pack.c`) makes the same checks as `parse()`, and before parsing it
refuses metadata nested more than 8 deep (format 1 needs 4; deeper JSON would overflow cJSON's recursion on the 10 KB
main task) and any `\u0000`. After parsing it refuses duplicate keys, objects with more than 128 fields, and text
after the JSON object. Where it is looser than `parse()`:

- cJSON cannot tell `1` from `1.0`, and it reads a leading zero (`01`) as a number, so the box accepts both.
- It ignores JSON fields it does not know.
- The name check is well-formed UTF-8 without control characters; `parse()` uses Python's `isprintable()`, which
  also refuses characters such as a no-break space or U+2028.

Packs from `petpack.py build` never contain any of these. `parse()` still takes the last of two equal keys (#70).

**Compatibility.** A change that older firmware could misread gets a new `format` number. Unknown JSON fields are an
error in `check`, so a newer field cannot slip into an older pack unnoticed.
