# projects/pet

Firmware for the GitHub desk pet on the Waveshare ESP32-P4-WIFI6-Touch-LCD-4B. ESP-IDF v5.5.5, LVGL 9.3.0 through the
`waveshare/esp32_p4_wifi6_touch_lcd_4b` 3.0.1 BSP. Built for the rev1.x silicon profile (see `sdkconfig.defaults`).

Current state: **milestone 4.** BSP display and touch up, placeholder face drawn with LVGL primitives, Wi-Fi through the
ESP32-C6 (ESP-Hosted over SDIO), MQTT subscription to the bridge's `pet/state`, and the face renders level, XP bar,
today vs goal, streak, and mood. Touch events are logged on the UART; a tap dismisses the event banner.

Before building, copy `main/secrets.h.example` to `main/secrets.h` (gitignored) and fill in the 2.4 GHz SSID and
password, the Mac's IP, and the MQTT user/password from `bridge/.env`.

```bash
source ~/esp/esp-idf/export.sh
idf.py -C projects/pet set-target esp32p4        # first time only
idf.py -C projects/pet build
idf.py -C projects/pet -p /dev/cu.usbmodem5B610404781 -b 921600 flash
python tools/serial_tail.py -t 60                 # passive UART reader, no DTR/RTS toggling
```

Console is UART0 at 115200. Layout:

- `main/main.c`: boot, BSP start, touch logging, wiring between network, MQTT, and the face.
- `main/face.c`: the placeholder face and the stats around it. Milestone 5 replaces the primitives with generated
  pixel-art image arrays.
- `main/net.c`: Wi-Fi station via `esp_wifi_remote` / ESP-Hosted with backoff reconnect.
- `main/mqtt_link.c`: esp-mqtt client, subscribes to `pet/state` and `pet/event`.
- `main/pet_state.c`: parses the state JSON (schema v1, see `bridge/README.md`).
- `partitions.csv`: nvs, phy_init, 8 MB factory app, 7 MB SPIFFS `storage`.
