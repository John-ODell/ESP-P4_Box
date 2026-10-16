#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef enum {
    NET_STATUS_CONNECTING,
    NET_STATUS_GOT_IP,
    NET_STATUS_DISCONNECTED,
} net_status_t;

typedef void (*net_status_cb_t)(net_status_t status, const char *detail);

/* Brings up ESP-Hosted + Wi-Fi station with the credentials from secrets.h and keeps reconnecting. */
esp_err_t net_start(net_status_cb_t cb);

/* Blocks until an IP is held or `timeout_ms` passes. */
bool net_wait_ip(uint32_t timeout_ms);
