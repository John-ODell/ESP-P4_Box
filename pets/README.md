# Pets

Each folder here is one pet. `dolphin/` is the one built into the firmware.

```
pets/dolphin/
  frames.png   every frame, 48x40 pixels each, 8 per row (384 pixels wide), no gaps between them
  pet.json     what the frames mean: animations, moods, colours, play looks
```

## Make your own

1. Copy `dolphin/` to `pets/<yourpet>/`.
2. Draw in `frames.png` with any pixel editor (Aseprite, Piskel, GIMP, Photoshop with a 48x40 grid). A pixel is **ink**
   when it is dark and opaque. Black on white, or black on transparency, is the easy way. Any other pixel is empty.
   The box paints ink in the pet's `ink` colour (or `sleep_ink` while asleep), so the colours in the file do not
   matter. Frame `k` sits at row `k // 8`, column `k % 8`, counting from 0. The sheet is always 8 frames wide; add
   rows at the bottom for new frames. Blank cells keep their numbers, so nothing moves when you leave a gap.
3. Edit `pet.json` (below) to point animations at your frames.
4. Check and preview it:

   ```bash
   python3 tools/petpack.py check   pets/yourpet
   python3 tools/petpack.py preview pets/yourpet -o sheet.png
   ```

5. Pack it and write it to the box over USB (`pip install esptool` if you don't have it; the port is something like
   `/dev/cu.usbmodem...` on macOS, `/dev/ttyACM0` on Linux or `COM3` on Windows):

   ```bash
   python3 tools/petpack.py build pets/yourpet -o yourpet.petpack
   python3 -m esptool --chip esp32p4 -p PORT -b 921600 write_flash 0x820000 yourpet.petpack
   ```

   The box restarts and draws your pet. Only the pack's own bytes are written, so Wi-Fi settings and the firmware stay
   as they are. If the pack fails a check the box draws the dolphin and says why on the serial console (115200 baud,
   a line starting `pet_pack:`). To go back to the dolphin, erase the pack's first sector:
   `python3 -m esptool --chip esp32p4 -p PORT erase_region 0x820000 0x1000`. Sending a pack over Wi-Fi comes with #68.

To change the pet built into the firmware instead (what the box draws when there is no pack), edit `dolphin/` and run
`python3 tools/petpack.py c pets/dolphin`, which rewrites `projects/pet/main/sprites.c` and `sprites.h`. Then rebuild.

## pet.json

```json
{
  "format": 1,
  "name": "Dolphin",
  "sheet": "frames.png",
  "colors": { "ink": "#E6F0F5", "sleep_ink": "#7F8E9A", "background": "#0B0F14", "panel": "#151D26",
              "dim": "#5C6B78", "accent": "#4FD1C5", "warn": "#F6AD55", "good": "#68D391" },
  "animations": {
    "happy/wiggle": [{"frame": 10, "dx": -1, "ms": 120}, {"frame": 10, "dx": 1, "ms": 120}],
    "celebrate":    [{"frame": 24, "dy": -1, "ms": 90}, "..."],
    "notice": ["..."], "sleep": ["..."]
  },
  "moods": {
    "happy": { "rest": 10, "idle": { "happy/breathe": 3, "happy/blink": 3, "happy/wiggle": 2 } },
    "sad": {"...": "..."}, "neutral": {"...": "..."}, "ecstatic": {"...": "..."}, "worried": {"...": "..."}
  },
  "play": {
    "snack": [{"frame": 36, "ms": 300}, {"frame": 37, "ms": 300}],
    "heart": [{"frame": 38, "ms": 250}, {"frame": 39, "ms": 350}],
    "pads":  ["#4FD1C5", "#F6AD55", "#68D391", "#B794F4"]
  }
}
```

- **animations**: any number, each a list of frames. Every frame has `frame` (its number in the sheet), and optionally
  `dx` and `dy` to nudge it by -8 to 8 art pixels, and `ms` for how long it shows (20 to 10000, default 500). Names are
  lowercase letters, digits and `_`, up to 32 characters, with an optional `/`, like `happy/wiggle`. `celebrate` (goals, big days, level up), `notice` (an AI
  session waits) and `sleep` (looped at night) must exist.
- **moods**: all five, `sad`, `neutral`, `happy`, `ecstatic` and `worried` (the streak is at risk). `rest` is the frame
  shown between animations. `idle` lists the animations that play in that mood, each with a weight from 1 to 255:
  a weight of 4 plays four times as often as a weight of 1. Moods come from GitHub activity; a pet pack only changes
  how they look.
- **name**: what the box calls the pet, 1 to 24 printable characters.
- **colors**: `ink` is the pet itself, `sleep_ink` the pet at night, `background` and `panel` the screen and overlays,
  `dim` secondary text, `accent`, `warn` and `good` the highlights (level bar, streak at risk, goal hit).
- **play**: `snack` is what the pet eats when fed, `heart` floats up (in the `accent` colour) when it is petted, `pads`
  are the four Pet Says colours. Petting also plays the animation named `happy/wiggle` if the pet has one, else its
  `celebrate`.

`petpack.py check` lists every problem it finds, in plain words. The binary `.petpack` layout is in
[`notes/pet-packs.md`](../notes/pet-packs.md).
