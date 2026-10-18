# Pet packs (.petpack), format 1

Written by `tools/petpack.py build` from a pet folder (`pets/README.md`). Read by the firmware's loader (#67) and by
`petpack.parse()`. All integers are little-endian.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `PETPACK\0` |
| 8 | 4 | `format`, 1 |
| 12 | 4 | `total`, the length of the whole pack in bytes |
| 16 | 4 | CRC-32 (zlib / IEEE 802.3, the same as `esp_rom_crc32_le(0, ...)`) of bytes 20 to `total` |
| 20 | 4 | `json_len`, a multiple of 4 |
| 24 | `json_len` | the metadata: UTF-8 JSON, then NUL padding to a multiple of 4 |
| 24 + `json_len` | `frame_count` x 240 | the frames, 1 bit per pixel |

**Metadata** is `pet.json` without `sheet`, plus `frame_w` (48), `frame_h` (40) and `frame_count`. Its fields and
limits are the ones `petpack.py check` enforces and `pets/README.md` describes: `format`, `name`, `colors`,
`animations`, `moods`, `play`. The loader treats a pack as unusable if any of them is missing or out of range, and
falls back to the built-in pet.

**Frames** are 48x40 = 1920 bits = 240 bytes each, in sheet order, rows top to bottom, pixels left to right, the first
pixel in the most significant bit of each byte. A 1 bit is ink. Frame numbers in the metadata index this list.

**Limits** (format 1): at most 1024 frames, 128 animations, 64 frames per animation; `dx`/`dy` -8 to 8; `ms` 20 to
10000; idle weights 1 to 255. A full pack is at most about 250 KB, so it fits easily in the 7 MB `storage` partition,
where the loader expects it at offset 0.

**Compatibility.** A change that older firmware could misread gets a new `format` number. Unknown JSON fields are an
error in `check`, so a newer field cannot slip into an older pack unnoticed.
