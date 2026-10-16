# projects/pet

Firmware for the GitHub desk pet on the Waveshare ESP32-P4-WIFI6-Touch-LCD-4B. ESP-IDF v5.5.5, LVGL 9.3.0 through the
`waveshare/esp32_p4_wifi6_touch_lcd_4b` 3.0.1 BSP. Built for the rev1.x silicon profile (see `sdkconfig.defaults`).

Current state: **milestone 2, hello-face.** BSP display and touch up, one static placeholder face drawn with LVGL
primitives, every touch event logged on the UART and echoed on screen.

```bash
source ~/esp/esp-idf/export.sh
idf.py -C projects/pet set-target esp32p4        # first time only
idf.py -C projects/pet build
idf.py -C projects/pet -p /dev/cu.usbmodem5B610404781 -b 921600 flash
python tools/serial_tail.py -t 60                 # passive UART reader, no DTR/RTS toggling
```

Console is UART0 at 115200. Layout:

- `main/main.c`: boot, BSP start, touch event logging (down, up, tap, long press, swipe).
- `main/face.c`: the placeholder face. Milestone 5 replaces this with generated pixel-art image arrays.
- `partitions.csv`: nvs, phy_init, 8 MB factory app, 7 MB SPIFFS `storage`.
