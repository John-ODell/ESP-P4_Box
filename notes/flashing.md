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
