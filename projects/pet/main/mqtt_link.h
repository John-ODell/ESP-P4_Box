#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#define PET_TOPIC_STATE "pet/state"
#define PET_TOPIC_EVENT "pet/event"

/* `payload` is NUL-terminated. `retained` is the broker's retain flag: a replay of old state, not fresh news. */
typedef void (*mqtt_msg_cb_t)(const char *topic, const char *payload, size_t len, bool retained);
typedef void (*mqtt_status_cb_t)(bool connected);

esp_err_t mqtt_link_start(mqtt_msg_cb_t on_msg, mqtt_status_cb_t on_status);
bool mqtt_link_connected(void);
