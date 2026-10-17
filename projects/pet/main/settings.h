#pragma once
#include <stdbool.h>
#include "esp_err.h"

/* The box's Wi-Fi and bridge settings, kept in NVS so one public image can be set up after it is flashed (#50).
 * A local secrets.h, when the build has one, supplies each group NVS does not hold, so dev builds behave as before. */

#define SETTINGS_SSID_MAX  32   /* bytes, as in wifi_config_t */
#define SETTINGS_PASS_MAX  63   /* WPA2 passphrase; empty means an open network */
#define SETTINGS_MQTT_MAX  64   /* each of uri, user, password */

typedef enum {
    SETTINGS_NONE,      /* nothing saved and nothing built in */
    SETTINGS_SAVED,     /* from NVS */
    SETTINGS_BUILT_IN,  /* from secrets.h */
} settings_source_t;

typedef struct {
    char wifi_ssid[SETTINGS_SSID_MAX + 1];
    char wifi_pass[SETTINGS_PASS_MAX + 1];
    char mqtt_uri[SETTINGS_MQTT_MAX + 1];
    char mqtt_user[SETTINGS_MQTT_MAX + 1];
    char mqtt_pass[SETTINGS_MQTT_MAX + 1];
    settings_source_t wifi_src, mqtt_src;
} pet_settings_t;

/* Fills `out` from NVS, falling back to secrets.h per group. Call after nvs_flash_init(). */
void settings_load(pet_settings_t *out);

/* NULL when the values are acceptable, otherwise a short reason for a person to read. */
const char *settings_check_wifi(const char *ssid, const char *pass);
const char *settings_check_mqtt(const char *uri, const char *user, const char *pass);

/* Check, then save one group. ESP_ERR_INVALID_ARG when the check fails. */
esp_err_t settings_save_wifi(const char *ssid, const char *pass);
esp_err_t settings_save_mqtt(const char *uri, const char *user, const char *pass);

/* Clears everything saved. A build with secrets.h falls back to it on the next load. */
esp_err_t settings_forget(void);
