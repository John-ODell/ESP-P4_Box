# Brief: "Dolphin" desk pet for the Waveshare ESP32-P4 box

Paste this into a new Claude Code session opened in `~/Desktop/ESP-P4_Box`.

---

## What I want

Build a Flipper Zero style desk pet that runs on my Waveshare **ESP32-P4-WIFI6-Touch-LCD-4B** (4-inch 720x720 capacitive touch box). The pet's mood, XP, and level are driven by my **GitHub contribution activity**, the same data shown on my GitHub profile's contribution graph: daily counts for the last year plus the breakdown into commits, issues, pull requests, and code reviews. On top of that, the box flashes and shows a card whenever a **Claude Code** or **Codex** session on my home Mac finishes or needs my input.

Mimic how the Flipper dolphin works. Do not copy Flipper's art or assets; design an original character.

## Where things stand (do not redo this)

- Repo: `~/Desktop/ESP-P4_Box`, pushed to `github.com/John-ODell/ESP-P4_Box`. Read `README.md` and `notes/flashing.md` first.
- Waveshare's official repo is a submodule at `vendor/waveshare-4b/`. It has 13 ESP-IDF examples, a reusable BSP (`waveshare/esp32_p4_wifi6_touch_lcd_4b` 3.0.1 on the ESP Component Registry, covers LCD, touch, audio), and the Brookesia factory firmware under `firmware/brookesia/`.
- ESP-IDF **v5.5.5** is installed at `~/esp/esp-idf` with the esp32p4 toolchain. Activate with `source ~/esp/esp-idf/export.sh`.
- The board is ESP32-P4 **silicon revision v1.3**. Use the rev1.x config profile (`CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y`, `CONFIG_ESP32P4_REV_MIN_100=y`, PSRAM 200 MHz). Never flash a rev3_x profile build.
- Serial port on the Mac: `/dev/cu.usbmodem5B610404781` (CH343P USB-UART). Brookesia's console runs at 2,000,000 baud. After `idf.py flash`, **unplug and replug the cable** before expecting the app to run; the RTS hard reset right after flashing does not count as a cold start on this board, and repeated DTR/RTS toggling from scripts can wedge the macOS serial driver until a replug.
- Brookesia is currently flashed and verified working: display, 5-point touch, launcher apps. It is slow. We are **not** building inside Brookesia; this is a standalone ESP-IDF + LVGL 9 project.
- Hardware present: display, GT911 touch, dual mics, ES8311 codec + NS4150B amp with a 2-pin speaker header (speaker possibly not fitted), microSD slot, ESP32-C6 co-processor for Wi-Fi 6 and BLE over SDIO, USB OTG. **Not present:** camera module, Ethernet jack, bottom board.
- **Unknown:** whether the ESP32-C6 already carries a usable ESP-Hosted image. Wi-Fi on the P4 depends on it. Read `vendor/waveshare-4b/docs/p4-c6-hosted-wifi.md`. Settling this is step one.

## Architecture

Two pieces, both in this repo:

1. **`projects/pet/`**: ESP-IDF firmware for the box. LVGL 9 via the Waveshare BSP. Single full-screen "face" scene plus overlay cards. Wi-Fi client via `esp_wifi_remote` / ESP-Hosted on the C6. Subscribes to the bridge and renders state. Holds no GitHub credentials.
2. **`bridge/`**: a small Python service that runs on my home Mac at login. It owns the GitHub token, polls GitHub's GraphQL `contributionsCollection` every 10 minutes, computes the pet state (the "brain"), and pushes a compact JSON state to the box. It also exposes a local HTTP endpoint that Claude Code hooks and Codex's `notify` setting call to post session events.

Transport: start with **MQTT** on the home LAN (Mosquitto on the Mac, box subscribes to `pet/state` and `pet/event`). Keep the state message small and self-describing so a second client (a phone app later) can render the same pet without redesign. Keep the brain in one pure Python module with unit tests so it can be ported.

## Pet mechanics (mimic the Flipper dolphin)

- **Deeds and XP.** Every contribution is a deed with a weight. Suggested weights: commit 1, pull request 3, issue 3, code review 4. Reviews and issues earn extra on purpose; I want to be nudged toward them.
- **Daily caps per category**, like the Flipper's per-category limits, so one 200-commit day does not break the curve. Suggested caps: commits 30, PRs 10, issues 10, reviews 10 XP-worth per day.
- **Levels** with widening thresholds (for example 100, 300, 700, 1500, 3000 and so on). A level-up is a celebration the pet performs and waits for me to tap to acknowledge, like the Flipper does.
- **Mood** ("butthurt" on the Flipper, inverted here to happiness 0 to 10). Rises with deeds, decays slowly on idle days, never falls below a floor in one day. Mood selects which idle animation set plays: sad, neutral, happy, ecstatic.
- **Streak.** Consecutive contribution days. The pet visibly cares: a streak badge, and a worried face the evening before a streak would break.
- **Today counter** with a **daily goal** (default 20). Hitting the goal triggers a small celebration. A big day (100 or more, like today's) triggers a big one.
- Passive by design. The pet reacts to what I already do. The only interactions are tap to acknowledge, swipe to peek at stats (today, streak, level, XP bar, the four-way breakdown), and long-press for settings. No chores, no reminders, nothing that nags.

## Notifications layer

- Claude Code hooks (`Stop`, `Notification`) and Codex's `notify` config POST `{source, kind, title, detail}` to the bridge. Kinds: `done`, `needs_input`, `error`.
- The box flashes the backlight or screen briefly, shows a card over the face with a color per kind (green done, amber needs input, red error), and the pet points at it until I tap. Multiple events stack as a count.
- Provide the actual hook JSON for `~/.claude/settings.json` and the Codex config snippet, and a `curl` one-liner to fake an event for testing.

## Visual direction

- Original pixel-art character, 1-bit or 2-color in the Flipper spirit, scaled up crisply on the 720x720 panel. Big face, lots of empty space, readable from across a room.
- Idle animations chosen randomly with weights per mood, like the Flipper's animation manifest. A handful of reactions: celebrate, level-up, sulk, notice-notification, sleep at night.
- Dark background, auto-dim backlight on a schedule, clock in a corner.
- Keep assets as LVGL image arrays generated by a script in the repo, so changing the character is a script run.

## Milestones, in order

1. Confirm whether the C6 has working Wi-Fi: build and flash `vendor/waveshare-4b/examples/esp-idf/wifi-station` (or the equivalent) and report the outcome. If it does not, document how to flash the ESP-Hosted slave image to the C6 and do it.
2. `projects/pet` hello-face: BSP up, one static face, touch tap logs. Flash, replug, verify.
3. Brain module in `bridge/` with tests: deeds, caps, XP, levels, mood, streak. Run it against my real GitHub data read-only and print the state.
4. MQTT plumbing: bridge publishes state, box renders mood/level/today/streak.
5. Animations and reactions, level-up acknowledgement, stats swipe.
6. Notification events from Claude Code and Codex, with the card overlay.
7. Launch-at-login for the bridge, README for setup.

## Constraints

- Never commit tokens or Wi-Fi credentials. Use `secrets.h` / `.env`, both already gitignored.
- Keep flashing notes in `notes/flashing.md` current when you learn something new about the board.
- Prefer the Component Registry BSP over hand-rolled drivers. Do not edit anything under `vendor/`.
- Commit at each milestone with a clear message. Ask before pushing.

## Done looks like

The box sits on my desk, shows an original dolphin-like character whose mood and level track my GitHub activity, celebrates when I hit my daily goal or a big day, and flashes a card whenever a Claude Code or Codex session on my Mac finishes or needs me. All of it runs from the repo with documented setup.
