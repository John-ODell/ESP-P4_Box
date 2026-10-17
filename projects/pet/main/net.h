#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "settings.h"

typedef enum {
    NET_STATUS_CONNECTING,
    NET_STATUS_GOT_IP,
    NET_STATUS_DISCONNECTED,
    NET_STATUS_NOT_SET_UP,  /* no Wi-Fi saved: the radio is up but never joins */
} net_status_t;

typedef void (*net_status_cb_t)(net_status_t status, const char *detail);

/* Brings up ESP-Hosted + Wi-Fi station and, when `settings` has Wi-Fi, joins it and keeps reconnecting. */
esp_err_t net_start(const pet_settings_t *settings, net_status_cb_t cb);

/* Blocks until an IP is held or `timeout_ms` passes. */
bool net_wait_ip(uint32_t timeout_ms);
