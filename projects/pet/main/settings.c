/* Wi-Fi and bridge settings in NVS, with secrets.h as the dev fallback (#50). Never log a password. */
#include <string.h>
#include "settings.h"
#include "nvs.h"
#include "esp_log.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif

static const char *TAG = "settings";
#define NS "pet_cfg"

static bool get_str(nvs_handle_t h, const char *key, char *buf, size_t cap)
{
    size_t len = cap;
    return nvs_get_str(h, key, buf, &len) == ESP_OK;
}

void settings_load(pet_settings_t *out)
{
    memset(out, 0, sizeof(*out));
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) == ESP_OK) {
        if (get_str(h, "wifi_ssid", out->wifi_ssid, sizeof(out->wifi_ssid)) &&
            get_str(h, "wifi_pass", out->wifi_pass, sizeof(out->wifi_pass))) {
            out->wifi_src = SETTINGS_SAVED;
        }
        if (get_str(h, "mqtt_uri", out->mqtt_uri, sizeof(out->mqtt_uri)) &&
            get_str(h, "mqtt_user", out->mqtt_user, sizeof(out->mqtt_user)) &&
            get_str(h, "mqtt_pass", out->mqtt_pass, sizeof(out->mqtt_pass))) {
            out->mqtt_src = SETTINGS_SAVED;
        }
        nvs_close(h);
    }
#ifdef PET_WIFI_SSID
    if (out->wifi_src == SETTINGS_NONE) {
        strlcpy(out->wifi_ssid, PET_WIFI_SSID, sizeof(out->wifi_ssid));
        strlcpy(out->wifi_pass, PET_WIFI_PASSWORD, sizeof(out->wifi_pass));
        out->wifi_src = SETTINGS_BUILT_IN;
    }
#endif
#ifdef PET_MQTT_URI
    if (out->mqtt_src == SETTINGS_NONE) {
        strlcpy(out->mqtt_uri, PET_MQTT_URI, sizeof(out->mqtt_uri));
        strlcpy(out->mqtt_user, PET_MQTT_USER, sizeof(out->mqtt_user));
        strlcpy(out->mqtt_pass, PET_MQTT_PASS, sizeof(out->mqtt_pass));
        out->mqtt_src = SETTINGS_BUILT_IN;
    }
#endif
    if (out->wifi_ssid[0] == '\0') {
        out->wifi_src = SETTINGS_NONE;
    }
    if (out->mqtt_uri[0] == '\0') {
        out->mqtt_src = SETTINGS_NONE;
    }
    static const char *const names[] = {"none", "saved", "built in"};
    ESP_LOGI(TAG, "wifi: %s '%s', bridge: %s %s", names[out->wifi_src], out->wifi_ssid, names[out->mqtt_src],
             out->mqtt_uri);
}

static bool len_between(const char *s, size_t lo, size_t hi)
{
    size_t n = s ? strlen(s) : 0;
    return s && n >= lo && n <= hi;
}

const char *settings_check_wifi(const char *ssid, const char *pass)
{
    if (!len_between(ssid, 1, SETTINGS_SSID_MAX)) {
        return "network name must be 1 to 32 bytes";
    }
    if (!pass || (pass[0] != '\0' && !len_between(pass, 8, SETTINGS_PASS_MAX))) {
        return "Wi-Fi password must be empty or 8 to 63 characters";
    }
    return NULL;
}

const char *settings_check_mqtt(const char *uri, const char *user, const char *pass)
{
    if (!len_between(uri, 1, SETTINGS_MQTT_MAX) || strncmp(uri, "mqtt://", 7) != 0 || uri[7] == '\0') {
        return "bridge address must look like mqtt://host:port, up to 64 characters";
    }
    if (!len_between(user, 1, SETTINGS_MQTT_MAX) || !len_between(pass, 1, SETTINGS_MQTT_MAX)) {
        return "bridge user and password must be 1 to 64 characters";
    }
    return NULL;
}

/* Writes key/value pairs and commits, all or nothing as far as the commit goes. */
static esp_err_t save(const char *const *kv, int pairs)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    for (int i = 0; i < pairs && err == ESP_OK; i++) {
        err = nvs_set_str(h, kv[2 * i], kv[2 * i + 1]);
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t settings_save_wifi(const char *ssid, const char *pass)
{
    const char *why = settings_check_wifi(ssid, pass);
    if (why) {
        ESP_LOGW(TAG, "wifi not saved: %s", why);
        return ESP_ERR_INVALID_ARG;
    }
    const char *kv[] = {"wifi_ssid", ssid, "wifi_pass", pass};
    esp_err_t err = save(kv, 2);
    ESP_LOGI(TAG, "wifi '%s' %s", ssid, err == ESP_OK ? "saved" : esp_err_to_name(err));
    return err;
}

esp_err_t settings_save_mqtt(const char *uri, const char *user, const char *pass)
{
    const char *why = settings_check_mqtt(uri, user, pass);
    if (why) {
        ESP_LOGW(TAG, "bridge not saved: %s", why);
        return ESP_ERR_INVALID_ARG;
    }
    const char *kv[] = {"mqtt_uri", uri, "mqtt_user", user, "mqtt_pass", pass};
    esp_err_t err = save(kv, 3);
    ESP_LOGI(TAG, "bridge %s %s", uri, err == ESP_OK ? "saved" : esp_err_to_name(err));
    return err;
}

esp_err_t settings_forget(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK; /* nothing was ever saved */
    }
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(h);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    ESP_LOGI(TAG, "saved settings %s", err == ESP_OK ? "cleared" : esp_err_to_name(err));
    return err;
}
