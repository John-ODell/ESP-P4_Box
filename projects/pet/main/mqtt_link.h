#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#define PET_TOPIC_STATE  "pet/state"
#define PET_TOPIC_EVENT  "pet/event"
#define PET_TOPIC_NOTIFY "pet/notify"   /* retained: the AI sessions waiting for a person, newest first */
#define PET_TOPIC_ACK    "pet/ack"      /* box -> bridge: {"source", "session"} drops one entry */

/* `payload` is NUL-terminated. `retained` is the broker's retain flag: a replay of old state, not fresh news. */
typedef void (*mqtt_msg_cb_t)(const char *topic, const char *payload, size_t len, bool retained);
typedef void (*mqtt_status_cb_t)(bool connected);

esp_err_t mqtt_link_start(mqtt_msg_cb_t on_msg, mqtt_status_cb_t on_status);
bool mqtt_link_connected(void);
/* Queue a QoS 1 publish; the client's own task sends it, after the reconnect if the link is down now. Safe to call
 * from the LVGL task: it never touches the socket. False when the client is not up or its outbox is full. */
bool mqtt_link_publish(const char *topic, const char *payload);
