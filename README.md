# ESP-P4_Box

Projects and notes for the **Waveshare ESP32-P4-WIFI6-Touch-LCD-4B** (4-inch 720×720 touch display box).

- Wiki: https://docs.waveshare.com/ESP32-P4-WIFI6-Touch-LCD-4B
- Resources (schematics, datasheets, demos): https://docs.waveshare.com/ESP32-P4-WIFI6-Touch-LCD-4B/Resources-And-Documents
- Waveshare repo (examples, BSP, Brookesia firmware): https://github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-4B
- Web flasher: https://www.northbridgelandmark.com/workbench/

## Hardware at a glance

| Part | Detail |
|---|---|
| MCU | ESP32-P4NRW32, dual-core RISC-V @ 360 MHz + low-power core @ 40 MHz, FPU/DSP |
| Memory | 32 MB PSRAM (in package), 32 MB NOR flash |
| Display | 4" IPS 720×720, ST7703 over 2-lane MIPI DSI, PWM backlight |
| Touch | GT911 5-point capacitive (I2C: SDA GPIO7, SCL GPIO8, RST GPIO23) |
| Wireless | ESP32-C6-MINI-1U co-processor over SDIO: Wi-Fi 6 + Bluetooth 5 LE |
| Camera | 15-pin 1.0 mm MIPI CSI connector, 2 lanes (4B only) |
| Audio | Dual mics via ES7210 ADC (echo cancel), ES8311 codec, NS4150B amp, 2-pin header for 8 Ω / 2 W speaker |
| Storage | microSD, 4-bit SDIO 3.0 |
| USB | Type-C USB 2.0 High-Speed OTG (native P4) + Type-C USB-UART (CH343P) for power/flash |
| Ethernet | IP101 10/100 PHY on board (RJ45 only on the 86 Panel bottom board) |
| Power | USB-C, or DC 6–30 V screw terminal; RTC battery header (rechargeable only) |
| Expansion | P3 2.0 mm header (GPIO, I2C, UART), P2 bottom-board header |
| Buttons | BOOT, RESET |

## Connecting from this Mac

With the cable in the **USB-UART** port (the CH343P one), the board shows up as:

```
/dev/cu.usbmodem5B610404781
```

That is the CH343P (USB vendor `1a86`, product `55d3`, name "USB Single Serial"). No driver install needed on recent macOS.
The web flasher's device picker shows this as **"USB Single Serial"** or similar. The other entry it lists is the Mac's own
`Bluetooth-Incoming-Port`, which is not the board. Pick the WCH / USB Single Serial one.

If you instead plug into the **USB OTG** port (native ESP32-P4 USB-Serial/JTAG), it appears as an Espressif device
(vendor `303a`) and also works for flashing and `idf.py monitor`.

```bash
idf.py -p /dev/cu.usbmodem5B610404781 flash monitor
```

## Toolchain

- **ESP-IDF v5.5.x** is what Waveshare's examples and the Brookesia firmware target (v6.0.x also builds the examples).
- **Arduino-ESP32 3.3.x**, FQBN `esp32:esp32:esp32p4`, 32 MB flash, PSRAM on. P4 support in Arduino is still thinner than IDF.
- **BSP**: `waveshare/esp32_p4_wifi6_touch_lcd_4b` 3.0.1 from the ESP Component Registry handles LCD, touch, and audio.
- Wi-Fi on the P4 needs a matching `esp_hosted` image on the C6 co-processor. See `docs/p4-c6-hosted-wifi.md` in the Waveshare repo.

Quick build check once IDF is installed:

```bash
idf.py -C vendor/waveshare-4b/examples/esp-idf/hello_world set-target esp32p4
idf.py -C vendor/waveshare-4b/examples/esp-idf/hello_world build
```

## Repo layout

```
vendor/waveshare-4b/   git submodule of Waveshare's official repo (examples, BSP, schematics)
projects/              our own firmware projects, one folder each
notes/                 scratch notes, pinouts, findings
```

Clone with submodules:

```bash
git clone --recurse-submodules git@github.com:John-ODell/ESP-P4_Box.git
```

## Project ideas

Ranked roughly by how well they use what this board actually has (big touch screen, mics + speaker, camera, Wi-Fi 6, lots of RAM).

### Tier 1: plays to every strength

1. **Voice assistant box ("Claude in a box")**
   Wake word on-device with ESP-SR, stream mic audio to a small server (or straight to a speech API), send the text to the Claude API, speak the answer through the codec and show the transcript on the 720×720 screen. The dual mics with echo cancellation and the 32 MB PSRAM are exactly what this needs. Natural first "real" project.

2. **Smart-home wall panel**
   The 86-panel form factor is the intended use. Home Assistant over MQTT or the REST API, LVGL tiles for lights, thermostat, cameras, with the screen dimming via PWM backlight when nobody is around. Camera can double as a presence sensor.

3. **Video intercom / doorbell**
   MIPI CSI camera + mics + speaker + Wi-Fi. The P4 has a hardware JPEG encoder and H.264 encoder, so streaming to a phone or a second P4 box is feasible.

### Tier 2: strong single-feature projects

4. **On-device vision demo**
   ESP-DL on the P4 can run face detection, pedestrian detection, and small classifiers. Put bounding boxes on the live camera feed. Good way to learn the camera pipeline.

5. **QR / barcode scanner kiosk**
   Camera + touch UI. Scan a code, look it up over Wi-Fi, show the result. Could tie into inventory, tickets, or a golf-round check-in.

6. **Internet radio + local music player**
   Stream MP3/AAC over Wi-Fi, or play from the microSD. Album art on the big screen, touch controls, bluetooth LE remote.

7. **Status dashboard**
   GitHub Actions status, PR queue, server health, weather, calendar. Pulls JSON every minute and renders LVGL widgets. Low effort, very satisfying to have on the desk.

8. **Digital photo frame**
   Photos from microSD or a Wi-Fi share. Hardware JPEG decoder on the P4 makes 720×720 slideshows trivial.

### Tier 3: tying into other projects on this machine

9. **Golf scorecard / range companion** (pairs with `AP_Golf_Game`)
   Touch scorecard, hole map, shot timer, sync to a server at the end of the round. Runs off the DC terminal or a USB power bank.

10. **SDR / Flipper companion display** (pairs with `Flipper_RTLSDR`)
    Show a waterfall or decoded packets pushed from a Pi or laptop over Wi-Fi/USB. The P4 box is the screen; the SDR host does the DSP.

11. **USB macro pad / Stream Deck clone**
    Native USB OTG High-Speed lets the P4 enumerate as a HID keyboard. Touch buttons on screen send shortcuts to the Mac. Also works as a second-screen notification display.

12. **Thonny / MicroPython playground** (pairs with `thonny_updater`)
    MicroPython for the P4 is maturing. A REPL-driven display and touch sandbox for quick experiments.

### Starting point

Flash Waveshare's **ESP-Brookesia** firmware first (`firmware/brookesia/` in the submodule). It gives an Android-style launcher with apps, and it proves the display, touch, audio, and Wi-Fi all work before writing anything custom.
