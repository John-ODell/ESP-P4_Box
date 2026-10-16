/* Updates over the LAN (#16): fetch a pet.bin over plain HTTP into the other app slot, reboot into it, keep it only
 * once MQTT connects again (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE makes the bootloader undo it otherwise). */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_ota_ops.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "face.h"
#include "mqtt_link.h"
#include "ota.h"
#include "sdkconfig.h"

/* IDF reads sdkconfig.defaults only when there is no sdkconfig yet. A checkout that built before this file existed
 * would silently come up without rollback and without plain-HTTP OTA; stop the build instead. */
#if !CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE || !CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP
#error "projects/pet/sdkconfig predates the OTA options in sdkconfig.defaults: delete it and build again"
#endif

#define OTA_URL_MAX     200
#define OTA_TASK_STACK  10240
#define OTA_HTTP_TIMEOUT_MS 15000

static const char *TAG = "ota";
static volatile bool s_busy;  /* one update at a time: set on the MQTT task, cleared by fail() on whichever task gave
                               * up; a success ends in a reboot */
static char s_rolled_back[32]; /* version of the image in the other slot that the bootloader rolled back, if any */

static void screen(const char *text)
{
    if (bsp_display_lock(200)) {
        face_set_status(text);
        bsp_display_unlock();
    }
}

/* pet/ota, retained: the last thing the box said about its firmware. Fields that do not apply are left out. */
static void report(const char *state, const char *version, int pct, const char *error, const char *slot, bool confirmed)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "state", state);
    if (version) {
        cJSON_AddStringToObject(o, "version", version);
    }
    if (pct >= 0) {
        cJSON_AddNumberToObject(o, "pct", pct);
    }
    if (error) {
        cJSON_AddStringToObject(o, "error", error);
    }
    if (slot) {
        cJSON_AddStringToObject(o, "slot", slot);
        cJSON_AddBoolToObject(o, "confirmed", confirmed);
        if (s_rolled_back[0]) {
            cJSON_AddStringToObject(o, "rolled_back", s_rolled_back);
        }
        const esp_app_desc_t *me = esp_app_get_description();
        char built[40];
        snprintf(built, sizeof built, "%s %s", me->date, me->time);
        cJSON_AddStringToObject(o, "built", built);
    }
    char *json = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (json) {
        mqtt_link_publish(PET_TOPIC_OTA, json, true);
        free(json);
    }
}

static void fail(const char *why, const char *version)
{
    ESP_LOGE(TAG, "update failed, %s; still running %s", why, esp_app_get_description()->version);
    report("failed", version, -1, why, NULL, false);
    screen("update failed");
    s_busy = false;
}

static void fail_err(const char *step, esp_err_t err, const char *version)
{
    char why[80];
    snprintf(why, sizeof why, "%s: %s", step, esp_err_to_name(err));
    fail(why, version);
}

static void ota_task(void *arg)
{
    char *url = arg;
    const esp_app_desc_t *me = esp_app_get_description();
    esp_http_client_config_t http = { .url = url, .timeout_ms = OTA_HTTP_TIMEOUT_MS, .keep_alive_enable = true };
    esp_https_ota_config_t cfg = { .http_config = &http };
    esp_https_ota_handle_t h = NULL;
    esp_app_desc_t img = { 0 };

    ESP_LOGI(TAG, "fetching %s (running %s from %s)", url, me->version, esp_ota_get_running_partition()->label);
    screen("updating...");
    report("downloading", NULL, 0, NULL, NULL, false);

    esp_err_t err = esp_https_ota_begin(&cfg, &h);
    if (err != ESP_OK) {
        fail_err("fetch", err, NULL); /* no server, a 404, a 5xx or a non-image body all end here */
        goto out;
    }
    err = esp_https_ota_get_img_desc(h, &img);
    if (err != ESP_OK) {
        esp_https_ota_abort(h);
        fail_err("header", err, NULL);
        goto out;
    }
    img.version[sizeof img.version - 1] = '\0'; /* straight off the network, before any hash check: do not trust */
    img.project_name[sizeof img.project_name - 1] = '\0'; /* the NULs */
    img.date[sizeof img.date - 1] = '\0';
    img.time[sizeof img.time - 1] = '\0';
    if (strncmp(img.project_name, me->project_name, sizeof img.project_name) != 0) {
        esp_https_ota_abort(h);
        char why[96];
        snprintf(why, sizeof why, "not a %.20s image (project %.20s)", me->project_name, img.project_name);
        fail(why, img.version);
        goto out;
    }
    int total = esp_https_ota_get_image_size(h);
    ESP_LOGI(TAG, "image %s built %s %s, %d bytes, writing %s", img.version, img.date, img.time, total,
             esp_ota_get_next_update_partition(NULL)->label);

    int shown = -1;
    while ((err = esp_https_ota_perform(h)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        int got = esp_https_ota_get_image_len_read(h);
        int pct = total > 0 ? (int)((int64_t)got * 100 / total) : 0;
        if (pct / 10 > shown) { /* a line every 10 % */
            shown = pct / 10;
            char txt[32];
            snprintf(txt, sizeof txt, "updating %d%%", pct);
            ESP_LOGI(TAG, "%d%% (%d of %d bytes)", pct, got, total);
            screen(txt);
            report("downloading", img.version, pct, NULL, NULL, false);
        }
    }
    if (err != ESP_OK) {
        esp_https_ota_abort(h);
        fail_err("download", err, img.version);
        goto out;
    }
    if (!esp_https_ota_is_complete_data_received(h)) {
        esp_https_ota_abort(h);
        fail("download: image ended early", img.version);
        goto out;
    }
    err = esp_https_ota_finish(h); /* checks the image's own SHA-256, sets the boot slot, frees h either way */
    if (err != ESP_OK) {
        fail_err("verify", err, img.version);
        goto out;
    }
    ESP_LOGI(TAG, "written and verified; rebooting into %s, kept only if MQTT connects again", img.version);
    report("rebooting", img.version, 100, NULL, NULL, false);
    screen("rebooting...");
    vTaskDelay(pdMS_TO_TICKS(2000)); /* let the pet/ota publish and the UART line out */
    esp_restart();
out:
    free(url);
    vTaskDelete(NULL);
}

static bool on_trial(const esp_partition_t *run)
{
    esp_ota_img_states_t st;
    return esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY;
}

void ota_report_boot(void)
{
    const esp_partition_t *run = esp_ota_get_running_partition();
    const esp_app_desc_t *me = esp_app_get_description();
    ESP_LOGI(TAG, "running %s %s (built %s %s) from %s at 0x%" PRIx32 "%s", me->project_name, me->version, me->date,
             me->time, run->label, run->address,
             on_trial(run) ? "; first boot of this image: rolled back at the next reset unless MQTT connects" : "");
    /* After a rollback the bootloader has marked the other slot ABORTED; its own line about that is at debug level,
     * so say it here, and in the pet/ota "running" report, so the Mac learns it too. */
    const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
    esp_ota_img_states_t st;
    if (other && esp_ota_get_state_partition(other, &st) == ESP_OK && st == ESP_OTA_IMG_ABORTED) {
        esp_app_desc_t d;
        if (esp_ota_get_partition_description(other, &d) == ESP_OK) {
            d.version[sizeof d.version - 1] = '\0';
            strlcpy(s_rolled_back, d.version, sizeof s_rolled_back);
        } else {
            strlcpy(s_rolled_back, "unknown", sizeof s_rolled_back);
        }
        ESP_LOGW(TAG, "image %s in %s was rolled back: it never confirmed", s_rolled_back, other->label);
    }
}

void ota_confirm_running(void)
{
    const esp_partition_t *run = esp_ota_get_running_partition();
    const esp_app_desc_t *me = esp_app_get_description();
    bool confirmed = true;
    if (on_trial(run)) {
        esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
        confirmed = err == ESP_OK;
        if (confirmed) {
            ESP_LOGI(TAG, "image %s confirmed: it stays", me->version);
        } else {
            ESP_LOGE(TAG, "could not confirm image %s: %s", me->version, esp_err_to_name(err));
        }
    }
    report("running", me->version, -1, NULL, run->label, confirmed);
}

bool ota_handle_cmd(const char *payload, size_t len)
{
    cJSON *root = cJSON_ParseWithLength(payload, len);
    const cJSON *u = cJSON_GetObjectItemCaseSensitive(root, "ota");
    char *url = cJSON_IsString(u) && u->valuestring ? strdup(u->valuestring) : NULL;
    cJSON_Delete(root);
    if (!url) {
        return false;
    }
    if (strncmp(url, "http://", 7) != 0 || strlen(url) > OTA_URL_MAX) {
        ESP_LOGW(TAG, "cmd ignored: the url must be plain http on the LAN and short: %s", url);
    } else if (s_busy) {
        ESP_LOGW(TAG, "cmd ignored: an update is already running");
    } else {
        s_busy = true;
        if (xTaskCreate(ota_task, "ota", OTA_TASK_STACK, url, 4, NULL) == pdPASS) {
            return true; /* the task owns url now */
        }
        fail("no memory for the update task", NULL);
    }
    free(url);
    return true;
}
