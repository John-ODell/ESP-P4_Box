# Flashing notes (2026-10-15)

## What worked
- Board on the CH343P USB-UART port: `/dev/cu.usbmodem5B610404781` (USB 1a86:55d3, "USB Single Serial").
- ESP-IDF v5.5.5 already installed at `~/esp/esp-idf` with the esp32p4 toolchain. Activate with `source ~/esp/esp-idf/export.sh`.
- Chip: ESP32-P4 **revision v1.3** -> Brookesia's default `sdkconfig.defaults` (rev1_3 profile) is correct. Do NOT use `sdkconfig.defaults.rev3_x`.
- Brookesia build: ~2450 steps, app is 0x6896f0 bytes (18% free in the 8 MB app partition).
- Full flash at 921600 baud succeeded, all hashes verified:

```bash
source ~/esp/esp-idf/export.sh
idf.py -C vendor/waveshare-4b/firmware/brookesia -p /dev/cu.usbmodem5B610404781 -b 921600 flash
```

## Gotchas
- Brookesia's console is UART0 on GPIO37/38 at **2,000,000 baud** (not 115200). `idf.py monitor` picks that up from sdkconfig.
- After flashing, the screen stayed blank and nothing came out of the UART. Repeated DTR/RTS reset attempts from
  pyserial then left esptool unable to connect at all ("No serial data received") even though the port still enumerated.
- **Fix: unplug and replug the USB cable.** On the cold start the Brookesia launcher came up. This matches Waveshare's
  note to cold-start once with the UART monitor disconnected, then attach the monitor. The hard reset over RTS right
  after flashing does not count as a cold start for this firmware.
- Takeaway: after `idf.py flash`, replug (or power-cycle) rather than relying on the RTS reset or `monitor`.
- The native USB-OTG port (other Type-C) is a secondary console (USB-Serial/JTAG, vendor 303a) and may be more reliable for monitoring.

## Outcome
Brookesia factory firmware (rev1_3 build) is running on the board as of 2026-10-15.

## Monitoring later
1. Replug, confirm `esptool.py --port /dev/cu.usbmodem5B610404781 chip_id` works.
2. `idf.py -C vendor/waveshare-4b/firmware/brookesia -p /dev/cu.usbmodem5B610404781 monitor` (Ctrl+] to quit).

## Update 2026-10-15 evening: plain ESP-IDF apps do not need the replug

- Flashed `projects/wifi-check` (a plain ESP-IDF app, console on UART0 at 115200) with
  `idf.py -p /dev/cu.usbmodem5B610404781 -b 921600 flash`. The app booted immediately after esptool's
  "Hard resetting via RTS pin" and logged normally. The replug requirement above is a Brookesia quirk, not a board rule.
- Keep the rule anyway for anything that brings up the display or the C6 in an unusual way, and do a replug whenever the
  port stops answering.
- `tools/serial_tail.py` opens the port without touching DTR/RTS and just prints what arrives. Use it instead of
  `idf.py monitor` when you want zero resets, for example right after a replug (`--wait` polls for the port node).
- **Trap:** the vendor's shared `vendor/waveshare-4b/config/sdkconfig.defaults` (pulled in by every vendor example's
  CMakeLists) selects the **rev3.x** profile (`CONFIG_ESP32P4_REV_MIN_300=y`, 250 MHz PSRAM). Do not build vendor
  examples in place for this rev1.3 board; copy them under `projects/` with our own `sdkconfig.defaults` carrying
  `CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y`, `CONFIG_ESP32P4_REV_MIN_100=y`, `CONFIG_SPIRAM_SPEED_200M=y`. The bootloader
  log should show `Min chip rev: v1.0 / Max chip rev: v1.99 / Chip rev: v1.3`.
- A plain app's partition table lives at 0x10000 (IDF default); Brookesia's custom table is at 0x8000. Flashing one over
  the other is fine, `idf.py flash` rewrites bootloader, table, and app.
- C6 / Wi-Fi findings are in `notes/c6-wifi.md`.

## Update 2026-10-15 late: hello-face (projects/pet) and serial gotchas

- `projects/pet` (BSP 3.0.1 + esp_lvgl_adapter 0.6.3 + LVGL 9.3.0, rev1.x profile) flashed and booted after esptool's
  RTS reset, no replug. Boot log worth knowing: `st7703: version: 2.0.0`, `Display initialized`, `GT911 found at 0x5D`,
  `Touch input device registered successfully (IRQ mode: disabled)`, `LVGL task started successfully`.
- Harmless warnings on every boot: `ledc: GPIO 26 is not usable, maybe conflict with others` (backlight PWM still works),
  `i2c.master: Please check pull-up resistances`, and `LV_USE_GESTURE_RECOGNITION is disabled` (LVGL's classic
  indev swipe gestures still fire; the newer multi-touch recogniser is just off).
- **Serial-reader gotcha:** right after a reset the ROM emits garbage bytes at the console baud, including NULs. Piping
  that through `grep` makes grep decide the stream is binary and print nothing at all, which looks like a dead board.
  `tools/serial_tail.py` now strips control bytes; if you pipe raw serial through grep anyway, use `grep -a`.
- Build sizes: wifi-check 0xa5540 bytes, pet hello-face 0xbcf10 bytes in an 8 MB app partition.
- **Milestone 2 touch verified on the board (2026-10-15 19:50):** 69 taps, swipes in all four directions, and long
  presses all logged by `projects/pet` with coordinates spanning 0..719 on both axes, no touch calibration needed
  (`touch_flags` all zero in the BSP default config). Quirk for later: LVGL fires `LV_EVENT_CLICKED` on release even
  after a `LV_EVENT_GESTURE` or `LV_EVENT_LONG_PRESSED`, so the pet's input code must swallow the tap that follows a
  swipe or long press.

## Update 2026-10-15 20:05: milestone 4 (Wi-Fi + MQTT) on the board

- `projects/pet` with ESP-Hosted + esp-mqtt: boot to "state rendered" takes about 9 s (hosted link up at 3.6 s, IP at
  8.5 s, MQTT connected and retained state drawn at 8.8 s). Flashed after esptool's RTS reset, no replug.
- The box got `10.0.0.156` by DHCP; the Mac (broker) is `10.0.0.65`, baked into `projects/pet/main/secrets.h`. Give
  the Mac a DHCP reservation on the router, or the box loses the broker when the lease changes.
- Live updates verified: three `pet/state` messages published from the Mac in a row (sad + streak at risk, level-up
  event, real state) were each received and parsed within a second; the `events` bitmask came through as 0x1 for
  `level_up`.
- Mosquitto 2.x refuses non-local connections unless the config has `listener 1883 0.0.0.0`; with
  `allow_anonymous false` and a password file both the box and the bridge log in as `pet`. The macOS application
  firewall was off on this Mac, so no prompt appeared; if it is on, allow `mosquitto` when asked.
- App size with Wi-Fi and MQTT: 0x17c140 bytes, 81% of the 8 MB partition free.

## 2026-10-15 late: milestone 5 asset pipeline decisions

- Character frames are 48x40 one-bit sprites emitted by `tools/gen_sprites.py` as LVGL **A8** images (alpha mask,
  1 byte/pixel, 1.9 KB per frame) and drawn by one `lv_image` with `lv_image_set_scale(256 * 12)`,
  `lv_image_set_antialias(false)` (nearest neighbour, crisp 12x12 blocks) and `image_recolor` for the ink colour. LVGL
  9.3's software renderer supports transformed A8 (`transform_a8` in `lv_draw_sw_transform.c`), confirmed in the
  managed component before committing to the format. Pre-scaled RGB frames were rejected: 576x480 per frame would be
  0.5 to 1 MB each.
- 36 unique frames / 28 animations / 68 KiB of flash after de-duplicating identical compositions.
- Time: `esp_netif_sntp` against pool.ntp.org once an IP arrives, POSIX TZ string in `main/pet_config.h`.

## 2026-10-15 20:50: the box had been rebooting every 288 s since milestone 4 (root cause and fix)

- **Symptom.** Mosquitto logged a new `pet-box` connection every 288 ± 1 s from 20:01 (milestone 4 firmware) to
  20:45 (milestone 5), idle or busy. Nobody noticed because every check so far was shorter than five minutes. The UART
  (`tools/serial_tail.py`) showed the same four lines in every boot at an uptime of 287.6 to 288.2 s, followed by a
  software reset, about 17 s of blank screen, and the retained state coming back:
  ```
  E (287600) H_SDIO_DRV: sdio_write_task: 0: Failed to send data: 258 68 68
  E (287600) H_SDIO_DRV: sdio_write_task: 1: Failed to send data: 258 68 68
  E (287607) H_SDIO_DRV: Unrecoverable host sdio state
  I (287607) os_wrapper_esp: Restarting host
  ```
  258 is `ESP_ERR_INVALID_ARG` (0x102), not a timeout (that would be 263). The 68-byte packet is the MQTT keepalive
  ping; when a test publish happened to land there it was the 70-byte PUBACK instead.
- **Root cause (P4 host side, not the C6).** ESP-Hosted 2.12.11 carves its SDIO transmit pool out of one 64-byte
  aligned PSRAM allocation in 1600-byte blocks. `projects/pet` runs the L2 cache with 128-byte lines
  (`CONFIG_CACHE_L2_CACHE_LINE_128B`, chosen for display bandwidth), and 1600 mod 128 = 64, so every second block
  is misaligned for DMA out of PSRAM. The driver pads each write to a 512-byte block (`H_SDIO_TX_BLOCK_ONLY_XFER`),
  the IDF SDMMC driver rejects the misaligned buffer with `ESP_ERR_INVALID_ARG`, the hosted driver retries once with
  the same buffer and then reboots the host (`CONFIG_ESP_HOSTED_TRANSPORT_RESTART_ON_FAILURE=y`). Upstream report:
  esp-hosted-mcu issue 252; Espressif says the fix shipped in ESP-Hosted-MCU 2.13.0.
- **Why exactly 288 s.** The pool hands back the most recently freed block, so one packet at a time keeps reusing
  the same, aligned block. lwIP re-requests an ARP entry when it is used after 270 s of age
  (`ARP_AGE_REREQUEST_USED_UNICAST` in `etharp.c`). The broker's entry is created at the MQTT connect, about 16.7 s
  after boot, so the first TCP send after roughly 286.7 s (the next keepalive ping or a PUBACK) is preceded by a
  unicast ARP request: two buffers are in flight, the second one is the misaligned block, and the host reboots. Any
  burst of two outgoing frames would do the same; the ARP timer just made it periodic.
- **Fix.** `CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM=n` in `projects/pet/sdkconfig.defaults` (and flipped in the
  live `sdkconfig`): the pools move to internal RAM, where 64-byte alignment is all the L1 cache needs.
  `projects/wifi-check` keeps the default 64-byte L2 line and was never affected. The alternative is bumping
  `esp_hosted` to 2.13.0 (`esp_wifi_remote` 1.6.3 allows `>=2.11,<3.0`); not done tonight, to keep the pair the
  vendor docs pin.
- **Verified.** See the next entry.
- **Other milestone 5 facts.** Each boot now takes three attempts to join Wi-Fi (reason 1 at 7.4 s, reason 205 at
  10.8 s, IP at 16.6 s, MQTT at 16.8 s), versus a first-try join at 8.5 s during milestone 4; the backoff reconnect in
  `net.c` handles it, cause not chased. The RTC keeps time across `esp_restart`, so the clock shows at once after a
  soft reset, before SNTP answers. Two bridge processes with the same MQTT client id kick each other off the broker
  ("session taken over" in the Mosquitto log); `publish.py` now appends its pid to the id.

## 2026-10-15 21:05: fix verified

- Rebuilt `projects/pet` with `CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM=n`, flashed at 20:53, recorded the UART for
  600 s: zero `Failed to send`, zero restarts, nine 60-second heartbeats through 548 s of uptime (well past the 288 s
  mark and the ARP re-request at about 279 s), and a live `pet/state` at 455 s applied normally. Mosquitto shows one
  `pet-box` connection at 20:54:04 and no "session taken over" since; before the fix that happened every 288 s.
- Cost: free internal RAM settles at about 142 KB with the pools internal (the `alive` log line now prints it next to
  the total); PSRAM free is unchanged at about 29 MB. Boot timing unchanged: IP 8.6 s, MQTT 8.65 s, retained state
  drawn 8.67 s after reset.

## 2026-10-15 22:00: "it turns on then off" was the clock-based backlight schedule, now an idle timer

- John's words: "if I unplug it it boots up, connects to wifi, then it goes black", "that's really fast off of the
  boot, it looks like it shut down on itself so I pressed reset". Timeline: the box boots at full brightness, SNTP
  syncs the clock about 12 s later, the old schedule sees 21:00+ and drops the backlight to 25% in one step (4% after
  23:00). The hard step to a dark level reads as a crash. Not a reboot: the fixed firmware had been up since 21:49:53
  with his taps in the log, and `Setting LCD backlight: 25%` appeared 60 s after each tap.
- Replaced by John's spec, an idle timer: full after boot, after any touch and whenever the state changes; dim after
  30 s without a touch (75% of full); off after 10 min; anything waiting for a tap holds full brightness; at night
  (23:00-07:00) the dim is 40% and the off timer 3 min, with the sleeping face while idle. Levels glide through the
  LEDC hardware fade on the BSP's channel (`CONFIG_BSP_DISPLAY_BRIGHTNESS_LEDC_CH`, 10-bit at 5 kHz). Everything is
  in `projects/pet/main/pet_config.h`; each change is logged by `sched`.
- Also added: `esp_reset_reason()` at boot and in every alive line (esptool's RTS reset shows as `power-on`), and the
  touch wake fires on finger-down. Unexplained and now attributable next time: Mosquitto saw the box silent from
  21:22:58 to 21:49:52 and two boots around 21:49:35 and 21:49:53 (John pressed reset at least once).
- Odd one: a 90 s capture at 21:52 contained one garbled burst where an alive line should have been, while captures a
  minute later were clean at the same 115200 baud. Not understood; watch for it.

## 2026-10-16: OTA partition table for projects/pet (issue #16; built, not yet flashed)

- `projects/pet/partitions.csv` changes from one 8 MB `factory` app to `otadata` + `ota_0` + `ota_1` (4 MB each), with
  nvs, phy_init and the 7 MB SPIFFS `storage` as before. The first build with this table has to go in over USB with
  the usual `idf.py -C projects/pet -p /dev/cu.usbmodem5B610404781 -b 921600 flash`, which writes the bootloader,
  the new table, a blank `otadata` and the app into `ota_0`. After that `tools/ota_push.py` updates the box over the
  LAN (`projects/pet/README.md`, "Updating over Wi-Fi").
- The table and the bootloader change only over USB. An image pushed over the air goes into whichever slot the table
  on the box names and locates itself at run time (`esp_ota_get_running_partition`), so one `pet.bin` serves both
  slots; an image bigger than the 4 MB slot fails the write (`ESP_ERR_INVALID_SIZE`), and one that cannot boot is
  rolled back. An edit to `partitions.csv` therefore does nothing on the box until the next cable flash, and until
  then the checkout's picture of the box is wrong, so leave the file alone unless a cable flash is planned.
- `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` is on. Lines the code prints: at boot `ota: running pet <version> (built
  ...) from ota_0 at 0x20000`, with `first boot of this image: rolled back at the next reset unless MQTT connects`
  appended after an update; `ota: image <version> confirmed: it stays` once MQTT is up; during an update
  `ota: fetching http://...`, `ota: image <version> built ..., N bytes, writing ota_1`, a percentage line every 10 %,
  then `ota: written and verified; rebooting into <version>`. After a rollback the next boot says `ota: image
  <version> in ota_1 was rolled back: it never confirmed` (the bootloader's own line about it is at debug level) and
  the retained `pet/ota` carries `rolled_back`. A cable-flashed image boots with a blank `otadata`: on that first
  boot the bootloader writes `ota_seq=1, VALID` for `ota_0` (`Set actual ota_seq=1 in otadata[0]` on the UART), so
  it is never on trial and no confirm line appears after a cable flash.
- Recovery: `idf.py -p <port> flash` brings back a working `ota_0`; `idf.py -p <port> erase-otadata` alone makes the
  bootloader boot `ota_0` again without rewriting the app.
- A checkout that built the old table already has a `projects/pet/sdkconfig`, and IDF reads `sdkconfig.defaults` only
  when there is none, so the two new options would be missing from it. `main/ota.c` stops the build with a message
  saying so: delete `projects/pet/sdkconfig` and build again (the build directory can stay). The table itself needs
  no such step, the CSV is read at every build.

## 2026-10-16 afternoon: backlight off step and touch wake confirmed on the UART (#3)

Firmware: the #1 idle-timer build (main at ae89721; no log line carries a build ID), box on the CH343P UART port,
read with `tools/serial_tail.py` only, which never toggles DTR/RTS. The only reset was the cable plug-in, which every
alive line reports as `reset power-on`. Three separate tail sessions over one boot: uptime runs on continuously from
one to the next, and the tap count never changes across the gaps between them.

- **Off after ten idle minutes, after a power-on.** First tail session, started about 4:20 pm CDT, right after the
  plug-in:

  ```
  I (32100) sched: backlight 60% -> 45% (idle 30 s, dim)
  I (602499) sched: backlight 45% -> 0% (idle 600 s, off)
  ```

  No touch in between (`taps 0` in every alive line from 68607 to 548615 ms), Wi-Fi and MQTT up throughout. A bridge
  `pet/state` at about 4:25 pm did not wake the screen, so none of `xp`, `today.total` or `events` had changed
  (`projects/pet/main/main.c:140`). This off step was later ended by a `pet/state` wake at 932613 ms, not by a touch.
- **A touch from off brings it back.** Same boot, third tail session, about 5:16 pm CDT. The last touch before the off
  was tap #6 at 2782913 ms, 600249 ms earlier; the alive line in between still says `taps 6`:

  ```
  I (3383162) sched: backlight 45% -> 0% (idle 600 s, off)
  I (3428709) pet: alive: wifi up, mqtt up, state 2026-10-16T17:15:43-05:00, taps 6, ...
  I (3453862) sched: backlight 0% -> 60% (active)
  I (3453862) pet: touch down  x=419 y=500
  I (3453952) pet: touch up    x=419 y=500
  I (3453952) pet: tap #7      x=419 y=500
  ```

  The wake fires on finger-down (`LV_EVENT_PRESSED`), as designed. Strictly, issue #3 asked for the off and the touch
  wake in one capture after a power-on; they are in one boot after a power-on, in two tail sessions.
- **Why it took three tries: the bridge poll and the off timer line up.** The bridge polls GitHub every 10 minutes and
  the off timer is 10 minutes. On a day with ongoing GitHub activity, each poll changes the state, which wakes the box
  and restarts the timer, so the next off lands within a second or so of the next poll and the screen is dark only
  for that moment. Seen at about 4:45 pm: off at 1533354 ms, `pet/state` at 1534403 ms, `activity: pet state changed`
  at 1534406 ms, backlight back on at 1534409 ms. Taps break the rhythm; otherwise a busy day keeps the box lit.
  Tracked in #29.
- **The native USB-C port carries no log on this firmware.** With the cable in the other port the box enumerates as
  Espressif `ESP32_P4` (VID 0x303a) at `/dev/cu.usbmodem214301`, but 100 s of listening there produced nothing,
  although the local `projects/pet/sdkconfig` has `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y` (that file is
  untracked, so it may differ from the flashed build). Use the UART port (`/dev/cu.usbmodem5B610404781`,
  "USB Single Serial") for logs and flashing.
