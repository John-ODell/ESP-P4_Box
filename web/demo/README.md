# Desk pet web demo

The box's face screen in a web page (#116), for people to try the pet before they flash a box. It draws a pet pack
(`pets/<name>/pet.json` and `frames.png`) the way the firmware does:

- the 720x720 layout and fonts of `projects/pet/main/face.c`, scaled to the width it is given;
- the 48x40 art drawn 12x, recoloured with the pack's colours;
- the sprite player of `anim.c`, unchanged in its rules: idle animations drawn by weight per mood, 0.8 to 3 s on the
  resting face between them, reactions that cut in, a one-shot reaction played twice, the sleep loop.

The numbers are samples; nothing talks to GitHub or a bridge.

## Try it

From the repo root:

```bash
python3 -m http.server 8000
```

Then open http://localhost:8000/web/demo/. The page has a picker for the Ember and Dolphin packs.

## What a visitor can do

| On the screen, as on the box | Button |
|---|---|
| Stroke the pet: a heart for every 150 px of travel, and a wiggle. A tap on the pet gives one heart | Pet |
| Swipe up or down: the stats overlay (closes after 12 s, or on a tap) | Stats |
| Tap the bubble when several sessions wait: the next one | |
| Tap anywhere else: acknowledge the banner, else the session shown | |
| | Feed: a fish drops, the pet celebrates, "Yum!  2 snacks left" (three snacks) |
| | A session waits: Claude, Codex and a local tool in turn. The bubble shows it, the pet's ink takes the tool's colour, and it plays its notice animation |
| | Merge a PR: the celebration and "Merged #117!" |
| | Night / Day: the sleep loop in the pack's sleep ink, with the screen dimmed |
| | The mood picker: the five moods and their idle animations. Worried puts "!" on the streak |

A sideways swipe opens the list on the box. Here it says so on the status line.

Things that differ from the box:

- A tap on the pet pets it. On the box it opens the play menu.
- The clock is the visitor's, 24-hour like the box.
- Under `prefers-reduced-motion`, the pet rests between the things a visitor does and the hearts don't rise.
- The screen takes touches for strokes and swipes (`touch-action: none`), so on a phone the page scrolls from outside
  the screen.

## Using it in a page

No framework and no build step. Three files, plus a pack:

```
pet-demo.js     the module: export mountPetDemo(element, { pack }) and loadPack(url)
pet-demo.css    the styles, all under .pet-demo
pets/ember/     pet.json and frames.png, unchanged from this repo
```

```html
<link rel="stylesheet" href="/css/pet-demo.css" />
<div id="pet-demo"></div>
<script type="module">
  import { mountPetDemo } from "/js/pet-demo.js";
  mountPetDemo(document.getElementById("pet-demo"), { pack: "/assets/desk-pet/ember/pet.json" });
</script>
```

- `pack` is the URL of a `pet.json`. Its `sheet` (`frames.png`) is loaded relative to it.
- `mountPetDemo` returns a promise of `{ destroy }`, which stops every timer and empties the element.
- The demo is at most 520 px wide and fills narrower containers.
- The box uses Montserrat (400 and 500). The demo asks for it first, then falls back to Inter and the system font. To
  match the box, load Montserrat; to match the page around it, set `--pet-demo-font`.
- The colours come from the pack. The buttons take the page's text colour and the pack's accent.
