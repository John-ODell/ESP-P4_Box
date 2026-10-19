#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "settings.h"

#define PET_TOPIC_STATE  "pet/state"
#define PET_TOPIC_EVENT  "pet/event"
#define PET_TOPIC_NOTIFY "pet/notify"   /* retained: the AI sessions waiting for a person, newest first */
#define PET_TOPIC_ACK    "pet/ack"      /* box -> bridge: {"source", "session"} drops one entry */
#define PET_TOPIC_CMD    "pet/cmd"      /* Mac -> box, never retained: {"ota": url}, {"pack": url, "sha256": hex}
                                           or {"shot": url} */
#define PET_TOPIC_OTA    "pet/ota"      /* box -> Mac, retained: the image it runs, or how an update is going */
#define PET_TOPIC_PACK   "pet/pack"     /* box -> Mac, retained: the pet it draws, or how a new pack is going */
#define PET_TOPIC_Q20    "pet/q20"      /* box -> bridge, never retained: 20 Questions start, answers, quit (#78) */
#define PET_TOPIC_Q20_ASK "pet/q20/ask" /* bridge -> box, never retained: the next question or guess, or an error */

/* `payload` is NUL-terminated. `retained` is the broker's retain flag: a replay of old state, not fresh news. */
typedef void (*mqtt_msg_cb_t)(const char *topic, const char *payload, size_t len, bool retained);
typedef void (*mqtt_status_cb_t)(bool connected);

/* Connects with the bridge address and login in `settings`, which must have them (mqtt_src != SETTINGS_NONE). */
esp_err_t mqtt_link_start(const pet_settings_t *settings, mqtt_msg_cb_t on_msg, mqtt_status_cb_t on_status);
bool mqtt_link_connected(void);
/* Queue a QoS 1 publish; the client's own task sends it, after the reconnect if the link is down now and comes back
 * within esp-mqtt's outbox expiry (30 s by default; older queued messages are dropped silently). Safe to call from
 * the LVGL task as far as the socket goes: it never touches it, but it does take the client's API mutex, which the
 * client holds while it dispatches a message to us (#19). False when the client is not up or its outbox is full. */
bool mqtt_link_publish(const char *topic, const char *payload, bool retain);
