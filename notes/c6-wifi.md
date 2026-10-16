# ESP32-C6 co-processor and Wi-Fi on the P4 box (2026-10-15)

## Verdict (milestone 1)

**The C6 ships with a working ESP-Hosted slave image.** No C6 flashing was needed to get Wi-Fi scanning on the P4.

Evidence from `projects/wifi-check` (ESP-IDF v5.5.5, `esp_hosted` 2.12.11, `esp_wifi_remote` 1.6.3, rev1.x profile):

```
I transport: Identified slave [esp32c6]
I transport: capabilities: 0xd      (WLAN, HCI over SDIO, BLE only)
W transport: Version mismatch: Host [2.12.0] > Co-proc [0.0.0] ==> Upgrade co-proc to avoid RPC timeouts
I transport: Slave chip Id[12]
I wifi-check: esp_wifi_init OK
I wifi-check: esp_wifi_start OK
I wifi-check: sta mac d8:85:ac:f9:b4:d4
I wifi-check: scan found 36 APs
```

Scan listed real neighbourhood SSIDs with channels, RSSI, and auth modes, so RPC, SDIO data path, and the radio all work.

**Station connect verified too** (same evening, with a 2.4 GHz WPA2 SSID in `secrets.h`): the box associated and got a
DHCP lease; the app's verdict line read `RESULT: connected to '<ssid>' with IP`. So the full chain the pet needs
(init, scan, connect, DHCP) works on the factory C6 image. No C6 reflash required for this project.

## What the old slave image costs

The factory slave predates version reporting ("Co-proc [0.0.0]"). With it:

- `esp_hosted_get_coprocessor_fwversion`, `esp_hosted_get_cp_info`, and `esp_hosted_get_coprocessor_app_desc` time out
  (1 s, 1 s, 5 s). Do not call them in the pet firmware; they only add startup delay.
- Basic station RPCs (init, start, scan, connect) and DHCP work.
- The host logs one `Version mismatch` warning per boot. Harmless.

## Upgrade path if the old slave ever becomes a problem

Not done yet; only do this if connect/DHCP/MQTT turn out flaky.

- The `esp_hosted` component bundles the slave source (`managed_components/espressif__esp_hosted/slave/`) and a
  host-driven OTA example (`examples/host_performs_slave_ota/`). The host streams a C6 image over the existing SDIO link
  with `esp_hosted_slave_ota_begin/write/end`; no UART access to the C6 is needed.
- Build the slave with `idf.py set-target esp32c6` from that `slave/` directory (SDIO transport, defaults in
  `sdkconfig.defaults.esp32c6`), then feed `build/network_adapter.bin` to the OTA example.
- Pin the pair: host `esp_hosted` 2.12.11 with slave 2.12.11. Keep a copy of the image used.
- Risk: there is no documented C6 UART/boot header on this board, so a failed OTA that leaves the C6 unbootable has no
  easy recovery. The OTA writes the inactive slot and only switches on success, which limits that risk.

## Board facts confirmed

- SDIO slot 1, 4-bit, 40 MHz: CLK 18, CMD 19, D0..D3 14..17, slave reset GPIO 54 (reset active high). This is the
  `CONFIG_ESP_HOSTED_P4_DEV_BOARD_FUNC_BOARD=y` pin map, same as Brookesia uses.
- Station MAC `d8:85:ac:f9:b4:d4`.
- The C6 is 2.4 GHz only. The Mac here sits on a 6 GHz WPA3 SSID; the box needs a 2.4 GHz SSID.

## How to rerun the check

```bash
source ~/esp/esp-idf/export.sh
idf.py -C projects/wifi-check -p /dev/cu.usbmodem5B610404781 -b 921600 flash
python tools/serial_tail.py -t 40
```

To test an actual connection, copy `projects/wifi-check/main/secrets.h.example` to `secrets.h` (gitignored), fill in a
2.4 GHz SSID and password, rebuild, reflash.

## Update 2026-10-15 20:50: periodic "Unrecoverable host sdio state" reboots were a host bug

The pet firmware rebooted every 288 s with `H_SDIO_DRV: Failed to send data: 258`. That is not the old slave: it is
the ESP-Hosted 2.12.11 host putting its SDIO transmit pool in PSRAM with 64-byte alignment while the P4 L2 cache
runs 128-byte lines (esp-hosted-mcu issue 252). Details, the ARP timer that made it periodic, and the fix
(`CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM=n`) are in `flashing.md`. No slave OTA was needed.

Recovery path, should a slave OTA ever be attempted and fail: Waveshare's wiki page for this board says it carries
2.54 mm 4-pin pads for flashing the ESP32-C6 directly (C6 U0TXD/U0RXD, with C6 IO9 pulled low for download mode,
per the FAQ on the sibling ESP32-P4-WIFI6 board). Not checked against the schematic here; verify the pinout before
relying on it.
