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
