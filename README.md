# ESP-P4_Box

A GitHub-powered desk pet for the **Waveshare ESP32-P4-WIFI6-Touch-LCD-4B**, a 4-inch 720×720 touch box.

The pet works like the dolphin on a Flipper Zero, but it is fed by your GitHub activity instead of by using the device. Commits, pull requests, issues, and code reviews earn it XP. It levels up, keeps a streak, gets happier on good days, and sulks when you go quiet. Later milestones add notifications from Claude Code and Codex sessions, so the box lights up when a session finishes or needs you.

## How it works

```
 GitHub GraphQL ──► bridge (Python, home Mac) ──► Mosquitto (Mac) ──Wi-Fi──► box (ESP32-P4 + LVGL)
                     computes level, XP, mood,      pet/state (retained)     renders face + stats,
                     streak, goal, events           pet/event                touch to acknowledge
```

- **The bridge** runs on the home Mac. It holds the GitHub token, pulls your contribution data every few minutes, runs the pet's rules, and publishes a small JSON state over MQTT. Details in [bridge/README.md](bridge/README.md).
- **The box** joins your 2.4 GHz Wi-Fi through its ESP32-C6 co-processor, subscribes to the state, and draws it. It never sees a GitHub token. Details in [projects/pet/README.md](projects/pet/README.md).

Keeping the brain on the Mac means rules can change without reflashing, and a phone client could render the same state later.

## Status

| Milestone | State |
|---|---|
| 1. ESP32-C6 Wi-Fi works | Done. The C6 ships with a working ESP-Hosted image. See [notes/c6-wifi.md](notes/c6-wifi.md). |
| 2. Hello-face firmware, display and touch | Done and verified on the board. |
| 3. Pet brain with tests, read-only GitHub reader | Done. |
| 4. MQTT from Mac to box, real state on screen | Done and verified on the board. |
| 5. Pixel-art character, idle animations, reactions, stats swipe | Next. |
| 6. Claude Code and Codex session notifications | Planned. |
| 7. Bridge launches at login, setup docs | Planned. |

What the screen shows today: level and XP bar, streak, today's contributions against the daily goal, a "BIG DAY" badge at 100 or more, mood, and connection status. A level-up shows a banner that a tap dismisses.

## Pet rules

| Deed | XP each | Daily cap (XP) |
|---|---|---|
| Commit | 1 | 30 |
| Pull request | 3 | 10 |
| Issue | 3 | 10 |
| Code review | 4 | 10 |

- **Levels** need 100, 300, 700, 1500, then 3000 total XP, doubling after that.
- **Mood** runs 0 to 10. Active days raise it and each idle day lowers it by one.
- **Streak** counts consecutive days with any contribution. From 6 pm the box warns if today is still empty.
- **Daily goal** is 20 contributions. 100 or more is a big day.

The full rules, message format, and known limits are in [bridge/README.md](bridge/README.md).

## Repo layout

```
projects/pet/        box firmware: ESP-IDF 5.5.5, LVGL 9, Waveshare BSP, Wi-Fi, MQTT
projects/wifi-check/ standalone Wi-Fi scan and join test for the C6
bridge/              Mac-side brain, GitHub reader, MQTT publisher, Mosquitto setup, tests
tools/serial_tail.py passive serial reader that never resets the board
notes/               flashing notes, C6 findings, original project brief
vendor/waveshare-4b/ Waveshare's official repo as a submodule (examples, BSP, schematics)
```

## Quick start

Prerequisites: ESP-IDF v5.5.5 at `~/esp/esp-idf`, Homebrew, Python 3.11 or newer.

1. **Clone with the submodule.**

   ```bash
   git clone --recurse-submodules git@github.com:John-ODell/ESP-P4_Box.git
   ```

2. **Set up the bridge.** Install Mosquitto, create the venv, copy `bridge/.env.example` to `bridge/.env`, and run the broker setup script. Step by step in [bridge/README.md](bridge/README.md).

3. **Fill in the box secrets.** Copy `projects/pet/main/secrets.h.example` to `secrets.h` and add your 2.4 GHz Wi-Fi, the Mac's LAN IP, and the MQTT login from `bridge/.env`. Both secret files are gitignored.

4. **Build and flash.**

   ```bash
   source ~/esp/esp-idf/export.sh
   idf.py -C projects/pet set-target esp32p4
   idf.py -C projects/pet -p /dev/cu.usbmodem5B610404781 -b 921600 flash
   ```

5. **Run the bridge.**

   ```bash
   cd bridge
   mosquitto/run.sh
   .venv/bin/python -m petbrain.publish
   ```

The box shows your real state about 9 seconds after boot.

## Board notes

| Part | Detail |
|---|---|
| MCU | ESP32-P4, silicon revision v1.3, dual-core RISC-V at 360 MHz |
| Memory | 32 MB PSRAM, 32 MB flash |
| Display | 4-inch IPS 720×720, ST7703 over MIPI DSI |
| Touch | GT911, 5-point capacitive |
| Wireless | ESP32-C6 co-processor over SDIO. Wi-Fi 6 on 2.4 GHz only, Bluetooth 5 LE only. |
| Audio | Dual mics, ES8311 codec, amplifier with a 2-pin speaker header. Not yet checked whether a speaker is fitted inside the case. |
| Storage | microSD slot |
| Not on this unit | Camera module, Ethernet jack, 86 Panel bottom board |

Things that will bite you:

- **Use the rev1.x config profile.** This board is silicon v1.3. Waveshare's shared config selects the rev3.x profile, so never build vendor examples in place. Copy them under `projects/` with our `sdkconfig.defaults`.
- **Serial port** is `/dev/cu.usbmodem5B610404781`, the CH343P USB-UART on the board's UART port. In the web flasher it is the "USB Single Serial" entry. The other entry is the Mac's own Bluetooth port.
- **Console baud** is 115200 for our firmware and 2,000,000 for Waveshare's Brookesia firmware.
- **If the port stops answering,** unplug and replug the cable. Brookesia also needs a replug after flashing before it boots.
- **2.4 GHz only.** The box cannot join 5 or 6 GHz networks. Dual-band routers that share one network name work fine.
- **The Mac needs a fixed IP.** Give it a DHCP reservation, because the box has the broker's address built in.

Full flashing history and gotchas are in [notes/flashing.md](notes/flashing.md).

## Links

- [Waveshare wiki](https://docs.waveshare.com/ESP32-P4-WIFI6-Touch-LCD-4B)
- [Schematics and datasheets](https://docs.waveshare.com/ESP32-P4-WIFI6-Touch-LCD-4B/Resources-And-Documents)
- [Waveshare GitHub repo](https://github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-4B)
