# projects/pet

Firmware for the GitHub desk pet on the Waveshare ESP32-P4-WIFI6-Touch-LCD-4B. ESP-IDF v5.5.5, LVGL 9.3.0 through the
`waveshare/esp32_p4_wifi6_touch_lcd_4b` 3.0.1 BSP. Built for the rev1.x silicon profile (see `sdkconfig.defaults`).

Current state: **milestone 5.** Original one-bit sprite character, drawn in `pets/dolphin/` (see `pets/README.md`), idle animations
picked by weight per mood (sad, neutral, happy, ecstatic, plus a worried set when the streak is at risk), a celebrate
reaction for goal and big-day events, a level-up celebration that loops until a tap, a sleep loop at night, a stats
overlay on swipe, a clock from SNTP, and a backlight idle timer. Underneath: Wi-Fi through the ESP32-C6 (ESP-Hosted over
SDIO) and an MQTT subscription to the bridge's `pet/state`. Milestone 6 (box side, #6 and #7) adds the session bubble,
the source tint and the waiting counter described below; that part is built and not yet verified on the board.

Interactions, all passive otherwise: **tap** acknowledges (the waiting session shown in the bubble, banner, stats, a
waiting level-up), **tap on the bubble** shows the next waiting session, **stroke the pet** (drag a finger over it) for
hearts, a happy wiggle and a little happiness (#64), **swipe up or down** starting off the pet peeks at the stats for
12 s, **swipe left or right** opens the to-do list (#90; a swipe within 0.8 s of the last one is ignored, so a flick
back does not undo it), **long press** off the pet
opens the play menu (Feed, Pet Says, 20 Questions, Settings, Close; #62). The pet's own area (the box its ink covers in
its resting frames, logged at boot as `face: pet area`) is for touching the pet: a drag there is petting however fast,
and a long press there does nothing (it does not acknowledge either); a tap there still acknowledges. The menu closes on
Close, a tap outside it, or 7 s without a touch, keeps the screen bright while open, and its taps never acknowledge a
waiting session.

**Play** (#63, `main/play.c`) is kept apart from the GitHub numbers. The pet earns one snack per contribution today
(the bridge's `today.total`, minus snacks already eaten today); **Feed** in the menu drops one on the pet, which
celebrates with a "Yum!" banner, or says why there is none (none left, or no clock or total for today yet; a total
counts only on the date in its state's `ts`, so yesterday's retained state gives no snacks after midnight). Happiness
(0 to 100, starts at 60) goes up 8 per snack and drifts down 1 every 15 awake minutes, never below 20. The stats
overlay has a play section with both. Happiness, eaten count and day live in NVS namespace `pet_play`, saved on every
feed, at midnight, and at most every 10 minutes for drift; the eaten count resets at local midnight. All numbers are
in `main/pet_config.h` (`PET_HAPPY_*`, `PET_PLAY_SAVE_MIN`, `PET_SNACK_SCALE`).

**Pet Says** (#65, `main/says.c`) is a memory game from the menu. Four pads in the pet's `play.pads` colours sit
around the pet, drawn at 5x in the middle. Each round the pet lights one more step; tap them back in order. Steps
quicken as the sequence grows (650 ms less 30 ms per step in the sequence, never under 280 ms). A wrong tap ends the
game: the pad that was wanted lights, the pet mopes (its `sad/mope`, else its notice), and after a moment a card shows
the score (rounds repeated in full) and the best, with Play again and Close. A new best plays the celebration. A game
that scored adds 2 happiness plus 1 per 4 rounds, up to 6, less 1 for each recent game (a minute forgets one;
`PET_SAYS_HAPPY*`); the best score is saved in NVS (`pet_play`, key `best`). The close button (X) in the top-right
corner, or 30 s without a tap while it waits for one (`PET_SAYS_IDLE_MS`), leaves without a score, and the result card
also closes after 30 s. The game takes every touch, so it never acknowledges an AI session, and keeps the screen
bright while open.

**20 Questions** (#78, `main/q20.c`) is a guessing game from the menu. Think of something, and the pet, drawn at 4x
above the question, asks yes-or-no questions; tap **Yes**, **No** or **Not sure**. A guess shows as "Is it ...?" with
**Right!** and **Wrong**. A question or a guess each uses one of 20 turns, and turn 20 is always a guess. Right ends the
game with the celebration and "I got it in N!", Wrong on turn 20 with a mope and "You stumped me!", and a wrong guess
before that goes on to the next turn. A finished game adds 3 happiness (`PET_Q20_HAPPY`). The questions come from a
model on the bridge (`bridge/petbrain/q20.py`, #82): the box sends `start`, each tap and `quit` on `pet/q20` and shows
what comes back on `pet/q20/ask`, so it never holds an API key. A turn takes about 5 to 10 s, while the pet looks
around. Close (X), 90 s without a touch (`PET_Q20_IDLE_MS`) or 50 s without a reply from the bridge (`PET_Q20_REPLY_MS`,
which then says so) ends the game; with no MQTT link it says so at once. Like Pet Says it takes every touch and keeps
the screen bright.

**To-do list** (#90, `main/todo.c`) is the bridge's list (#89) from the retained `pet/todo`: open items oldest first,
then the most recently done, 12 at most, each with the issue or PR it is about. A tap on an item ticks or unticks it at
once and sends `{"id", "done"}` on `pet/todo/tick`; the bridge's next `pet/todo` confirms it. With no MQTT link it says
so and ticks nothing. The header counts the open items, and says "12 of 15 shown" when the bridge holds more than the
box was sent. Drag up and down to scroll. A swipe left or right on the list, Close (X) or 45 s without a touch
(`PET_TODO_IDLE_MS`) goes back to the pet. Items are added on the Mac with `python3 bridge/todo.py add`. Like the
games it takes every touch and keeps the screen bright.

**Backlight** is an idle timer, not a clock schedule (all settings in `main/pet_config.h`). Full brightness
(`PET_BRIGHT_FULL`, 60%) after boot, after any touch, and whenever the pet's state changes. After 30 s without a
touch (`PET_DIM_AFTER_S`) it dims to 75% of full (`PET_DIM_PERCENT`; try 50 if that is still too bright). After 10
minutes (`PET_OFF_AFTER_S`; 0 keeps the dimmed screen on by day, the night timer below still applies) the backlight
switches off, and any touch brings it straight back. Once off it stays off until a touch or new activity, so the
07:00 change from the night timer to the day timer cannot flick it back on.
Between 23:00 and 07:00 (`PET_NIGHT_START`, `PET_NIGHT_END`) the dim level is 40% of full (`PET_NIGHT_DIM_PERCENT`),
the off timer is 3 minutes (`PET_NIGHT_OFF_AFTER_S`) and the face sleeps while idle. Anything that waits for a tap,
such as the level-up banner (and the session notifications of milestone 6), holds full brightness until you tap.
Level changes glide (`PET_FADE_UP_MS` 300, `PET_FADE_DOWN_MS` 1500) and each one is logged on the UART as
`sched: backlight 60% -> 45% (idle 30 s, dim)`. The screen never goes dark right after boot.

**Session notifications** (milestone 6, issue #6). The bridge keeps the list of AI sessions that wait for you and
publishes it retained on `pet/notify`, newest first (`bridge/README.md`, "Session notifications"). While that list is
not empty the character takes the colour of the newest entry's source (`PET_COLOR_CLAUDE`, `PET_COLOR_CODEX`,
`PET_COLOR_LOCAL`, anything else `PET_COLOR_OTHER`, all in `main/pet_config.h`), a bubble at the top right shows the
session name, who wants what ("claude needs your input", "codex finished", "local hit an error") and the event's
title, the notice reaction plays once per new event, and the backlight holds full brightness, day or night. A tap
acknowledges the session shown: the box publishes `pet/ack` with its source and session, the bridge drops the entry
and republishes the list, and the next waiting session, if any, takes over; the last tap restores the normal colours
and releases the backlight hold. The box drops the entry locally as well, so the bubble moves on even while the
bridge is down. Because `pet/notify` is retained, a session that is still waiting comes straight back after a power
cycle or a reconnect, without the reaction.

**Several sessions waiting** (issue #7). The list is newest first and the bubble starts on the newest; a fourth line
says `1 of 3 waiting. Tap here for the next`. A tap on the bubble moves to the next older session, changing the name,
the line under it and the tint to that session's source, without acknowledging anything, and rounds back to the
newest after the oldest. A tap anywhere else acknowledges the session shown: the count drops by one and the next
older one takes the bubble (the newest, if the shown one was the oldest). A new event puts the newest back in the
bubble; any other list update keeps the one shown, and when that one is gone (acknowledged or cleared from the Mac)
the bubble moves on as after a tap, to the next older one. The box keeps the newest eight (`PET_NOTIFY_SHOWN`) of the
`count` the bridge reports, so the cycle covers at most eight. With one session waiting, a tap on the bubble is a tap
like any other and acknowledges it; the same when the box holds only one of several (taps while the bridge was away),
and the line then reads just `1 of 3 waiting`. The stats overlay draws over the bubble: while it is up, a tap anywhere,
bubble included, closes it and acknowledges the session shown, as before.

The box keeps its Wi-Fi and bridge settings in NVS (`main/settings.c`, #50), so the same image works on any box and
is set up after flashing. A box with nothing saved shows `not set up` in the bottom-right corner. For your own dev
builds, `main/secrets.h` (gitignored) is optional: copy `main/secrets.h.example`, fill in the 2.4 GHz SSID and password,
the Mac's IP, and the MQTT user/password from `bridge/.env`, and the box uses those values for any group NVS does not
hold. The build works without the file. Adding it to a build folder that was configured without it is not noticed
(the check is `__has_include`), so run `idf.py fullclean` or touch `main/settings.c` after creating it.

```bash
source ~/esp/esp-idf/export.sh
idf.py -C projects/pet set-target esp32p4        # first time only
idf.py -C projects/pet build
idf.py -C projects/pet -p /dev/cu.usbmodem5B610404781 -b 921600 flash
python tools/serial_tail.py -t 60                 # passive UART reader, no DTR/RTS toggling
```

**Releases** (#51). Pushing a `pet-vX.Y.Z` tag runs `.github/workflows/release.yml`: it builds this project without
`secrets.h` from `sdkconfig.defaults`, and `tools/make_manifest.py` publishes its four images
(`desk-pet-esp32p4-rev1-vX.Y.Z-{bootloader,partition-table,ota-data-initial,app}.bin`) plus `manifest.json` for the
Workbench flasher. They stay separate rather than one merged image so that a reinstall without erasing leaves the NVS
partition, and the settings saved in it, alone. A box flashed with them boots to `not set up` until it is given
settings.

```bash
git tag pet-v1.0.0 && git push origin pet-v1.0.0
```

**Updating over Wi-Fi** (issue #16). The partition table has two app slots, so once it is on the box (one USB flash
with the commands above, because an app cannot rewrite the table it runs from) later builds go over the LAN:

```bash
idf.py -C projects/pet build
bridge/.venv/bin/python tools/ota_push.py         # serves build/pet.bin from this Mac and tells the box on pet/cmd
```

The tool serves the image over plain HTTP from the Mac's LAN address (port 8070; `--ip` and `--port` override it, and
the first time macOS may ask whether Python may accept incoming connections, which it must), publishes
`{"ota": "http://<mac>:8070/pet.bin"}` on `pet/cmd`, not retained, and prints the box's reports on `pet/ota`:
`downloading` with a percentage every 10 %, `rebooting`, then `running` with the new version and `confirmed` once
MQTT is back, or `failed` with the reason. The box writes the other slot while the face keeps running, checks the
image's own SHA-256, reboots into it, and `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` keeps the new image on trial until
MQTT connects (`ota: image <version> confirmed: it stays` on the UART). A build that resets before that, say one
with wrong Wi-Fi credentials, is rolled back by the bootloader at that reset and the old image runs again. The box
ignores a retained `pet/cmd` (a stale command must not run at every boot), accepts only `http://` URLs, and only
images whose project name is `pet`. The version is `git describe` of the checkout (`-dirty` when uncommitted), and
`pet/ota` is retained, so `mosquitto_sub -u <MQTT_USER> -P <MQTT_PASS> -t pet/ota -C 1` (the values from
`bridge/.env`; the broker takes no anonymous clients) shows the box's last report without the cable: `running` with
the version and slot after a connect, or `downloading`, `rebooting` or `failed` while an update is in flight or went
wrong; after a rollback the `running` report also carries `rolled_back` with the version that never confirmed, and
the UART says `ota: image <version> in ota_1 was rolled back: it never confirmed`. An image pushed over the air lands
in whichever slot the table on the box names and finds its own slot at run time, so one `pet.bin` serves both slots;
it has to fit the box's 4 MB slot (a bigger one fails the write) and one that cannot boot is rolled back. The table
and the bootloader themselves change only over USB: an edit to `partitions.csv` does nothing on the box until the
next cable flash, so leave it alone unless you plan one. The cable stays the way back when both slots are bad:
`idf.py flash` writes the table, a blank `otadata` and the app into `ota_0`. First build after this change in an
existing checkout: delete `projects/pet/sdkconfig` so the new `sdkconfig.defaults` options apply; the build stops
with a message if you forget (`notes/flashing.md`, 2026-10-16).

**Pet packs over Wi-Fi** (#68) go the same way, from the computer that runs the bridge: `bridge/.venv/bin/python
tools/pack_push.py pets/<name>` (or a ready `.petpack`; on Windows the venv's python is `bridge\.venv\Scripts\python`).
The tool builds and checks the pack, serves it on port 8071, and publishes `{"pack": "http://<mac>:8071/pet.petpack",
"sha256": "<hex>"}` on `pet/cmd`. The box (`main/pack_cmd.c`) downloads it into PSRAM (at most 512 KB), checks the
SHA-256 and then every check the boot loader makes (`pet_pack_check()`, about 0.2 s for the largest pack), and only then
erases the sectors it needs at the start of `storage`, writes it, reads it back and reboots to draw it: the face, menu
and game are built around one pet at boot. A failed download or check writes nothing and the current pet stays. Reports
go to the retained `pet/pack`: `downloading` with a percentage, `checking`, then `writing` and `rebooting` with the
pet's name and the pack's CRC, or `failed` with the reason (also for a command the box will not start: a bad URL or
SHA-256, a pack already on its way, or a firmware update running; an update is likewise refused while a pack installs);
after every MQTT connect, `running` with the pet drawn and its pack CRC (no CRC and the reason when it is the built-in
pet). The tool exits 0 once the box comes back `running` with the new pack's CRC.

**Screenshots** (#86): `bridge/.venv/bin/python tools/screenshot.py [out.png] [--scale 2]` saves what the screen shows
as a PNG. The tool listens on port 8072 and sends `{"shot": "http://<mac>:8072/shot"}` on `pet/cmd`; the box
(`main/shot_cmd.c`) renders its screen again with LVGL's snapshot into PSRAM (720x720 RGB565, about 1 MB) and POSTs the
pixels back, which takes about 2 s. Nothing appears on screen, so the picture holds only what the pet draws. A box that
cannot take the shot says why in an `X-Error` header and the tool exits 1; no answer within 20 s is a timeout. `--scale`
repeats each pixel so the art stays sharp when it is shown larger. First build after this change in an existing
checkout: delete `projects/pet/sdkconfig` so `CONFIG_LV_USE_SNAPSHOT` from `sdkconfig.defaults` applies; the build stops
with a message if you forget.

Console is UART0 at 115200. Layout:

- `main/main.c`: boot, BSP start, touch handling, wiring between network, MQTT, schedule, and the face.
- `main/face.c`: the scene: sprite in the middle, numbers around the edge, banner, session bubble, stats overlay.
- `main/shot_cmd.c`: `{"shot": url}` on `pet/cmd`: an LVGL snapshot of the screen, POSTed to the URL (#86).
- `main/menu.c`: the play menu over the pet. Feed drops a snack (`face_feed()`), Pet Says starts the game
  (`says_open()`), 20 Questions starts the other one (`q20_open()`), Settings opens a stub page until the on-box Wi-Fi
  setup (#53).
- `main/says.c`: Pet Says, the memory game, over the face with the pet drawn small in the middle.
- `main/q20.c`: 20 Questions over the face: shows the bridge's turns from `pet/q20/ask`, sends the taps on `pet/q20`.
- `main/todo.c`: the to-do list over the face: draws `pet/todo`, sends taps on `pet/todo/tick`.
- `main/play.c`: happiness and snacks, saved in NVS. Each stroke adds `PET_HAPPY_PET` (3), one less for every
  `PET_PET_FADE` (4) recent strokes, down to nothing; each minute forgets two recent strokes (at most 12 are kept),
  so the gain is back to full after 6 minutes' rest. Pet Says adds happiness with `play_says_done()`.
- `main/anim.c`: sprite player. Idle animations by weight for the current mood class, reactions that preempt them,
  the sleep loop. Frames are LVGL A8 images scaled 12x with antialiasing off, so each art pixel is a crisp 12x12 block.
- `main/pet_def.h`: what a pet is to the firmware (`pet_def_t`): frames, animations, idle sets per mood, colours and
  play looks. `anim.c` and `face.c` draw whichever one `main.c` hands them.
- `main/pet_pack.c`: at boot, reads a pet pack from the start of the `storage` partition (flash 0x820000), checks
  it (`notes/pet-packs.md`) and builds a `pet_def_t` in PSRAM; any problem is logged as `pet_pack: using the built-in
  Dolphin: <why>` and the built-in pet is used. Write a pack with `esptool write_flash 0x820000 pet.petpack` (#67) or
  `tools/pack_push.py` (#68). `pet_pack_check()` runs the same checks on a pack in memory. cJSON's allocator hooks are
  installed once at boot and send only the parsing task's allocations to the loader's PSRAM chunks.
- `main/pack_cmd.c`: pet packs over Wi-Fi: the `pet/cmd` pack command, download, checks, write, the `pet/pack`
  reports.
- `main/sprites.c` / `sprites.h`: **generated**, do not edit: the built-in pet, `pet_builtin`. Edit `pets/dolphin/`
  (frames.png and pet.json) and run `python3 tools/petpack.py c pets/dolphin`; `preview pets/dolphin -o sheet.png`
  shows it first (#66).
- `main/sched.c`: SNTP, clock label, backlight idle timer with LEDC fades (full, dim after 30 s, off after 10 min,
  night variants, hold while something waits for a tap). Tunables in `main/pet_config.h` (timezone is a POSIX TZ
  string there).
- `main/net.c`: Wi-Fi station via `esp_wifi_remote` / ESP-Hosted with backoff reconnect.
- `main/mqtt_link.c`: esp-mqtt client, subscribes to `pet/state`, `pet/event`, `pet/notify` and `pet/cmd`, publishes
  `pet/ack`, `pet/ota` and `pet/pack`.
- `main/pet_state.c`: parses the state JSON (schema v1, see `bridge/README.md`).
- `main/pet_notify.c`: parses the `pet/notify` list and builds the `pet/ack` message.
- `main/ota.c`: updates over Wi-Fi: the `pet/cmd` command, the download into the other slot, the trial-and-confirm
  step, the `pet/ota` reports.
- `partitions.csv`: nvs, otadata, phy_init, two 4 MB app slots `ota_0` and `ota_1`, 7 MB SPIFFS `storage`. Leave it
  alone: the table only changes over USB, and until that flash the checkout's picture of the box is wrong.

**Gotcha: reboots every ~5 minutes.** If the UART shows `H_SDIO_DRV: sdio_write_task: ... Failed to send data: 258`
followed by `Restarting host`, the ESP-Hosted memory pools are back in PSRAM. Keep
`CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM=n` from `sdkconfig.defaults` (why: `notes/flashing.md`, 2026-10-15 20:50).
