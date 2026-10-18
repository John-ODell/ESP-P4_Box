/* Pet packs over the LAN (#68): see pack_cmd.h. The pet in use lives in PSRAM (pet_pack.c copies every frame and
 * string out of flash), so overwriting the storage partition under it is safe; the new pet is drawn after the reboot,
 * because the face, menu and game are all built around one pet at boot. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_http_client.h"
#include "mbedtls/sha256.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "face.h"
#include "mqtt_link.h"
#include "pet_pack.h"
#include "pack_cmd.h"

#define PACK_URL_MAX     200
#define PACK_MAX_BYTES   (512 * 1024)   /* well over the largest pack format 1 allows (about 304 KB) */
#define PACK_TASK_STACK  10240          /* as the main task the boot loader runs on: same parse, same recursion */
#define PACK_HTTP_TIMEOUT_MS 15000

static const char *TAG = "pack";
static volatile bool s_busy;   /* one pack at a time: set on the MQTT task, cleared when the task gives up */

typedef struct {
    char url[PACK_URL_MAX + 1];
    uint8_t sha[32];
} job_t;

static void screen(const char *text)
{
    if (bsp_display_lock(200)) {
        face_set_status(text);
        bsp_display_unlock();
    }
}

/* pet/pack, retained: the last thing the box said about its pet pack. Fields that do not apply are left out. */
static void report(const char *state, const char *pet, int pct, const char *error, uint32_t crc)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "state", state);
    if (pet) {
        cJSON_AddStringToObject(o, "pet", pet);
    }
    if (pct >= 0) {
        cJSON_AddNumberToObject(o, "pct", pct);
    }
    if (error) {
        cJSON_AddStringToObject(o, "error", error);
    }
    if (crc) {
        char hex[9];
        snprintf(hex, sizeof hex, "%08lx", (unsigned long)crc);
        cJSON_AddStringToObject(o, "crc", hex);
    }
    char *json = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (json) {
        mqtt_link_publish(PET_TOPIC_PACK, json, true);
        free(json);
    }
}

static void fail(const char *why)
{
    ESP_LOGE(TAG, "pack not installed, %s; the current pet stays", why);
    report("failed", NULL, -1, why, 0);
    screen("pack failed");
}

/* The whole body of url into a new PSRAM buffer, or NULL with the reason in why. */
static uint8_t *download(const char *url, int *len_out, char *why, size_t why_len)
{
    esp_http_client_config_t cfg = { .url = url, .timeout_ms = PACK_HTTP_TIMEOUT_MS };
    esp_http_client_handle_t h = esp_http_client_init(&cfg);
    if (!h) {
        snprintf(why, why_len, "fetch: no memory for the HTTP client");
        return NULL;
    }
    uint8_t *buf = NULL;
    esp_err_t err = esp_http_client_open(h, 0);
    if (err != ESP_OK) {
        snprintf(why, why_len, "fetch: %s", esp_err_to_name(err));
        goto out;
    }
    int64_t len = esp_http_client_fetch_headers(h);
    int status = esp_http_client_get_status_code(h);
    if (status != 200) {
        snprintf(why, why_len, "fetch: HTTP %d", status);
        goto out;
    }
    if (len <= 0 || len > PACK_MAX_BYTES) {
        snprintf(why, why_len, "fetch: length %lld is missing or over %d bytes", (long long)len, PACK_MAX_BYTES);
        goto out;
    }
    buf = heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
    if (!buf) {
        snprintf(why, why_len, "no memory for %lld bytes", (long long)len);
        goto out;
    }
    int got = 0, shown = -1;
    while (got < len) {
        int n = esp_http_client_read(h, (char *)buf + got, len - got);
        if (n <= 0) {
            snprintf(why, why_len, "download: ended after %d of %lld bytes", got, (long long)len);
            free(buf);
            buf = NULL;
            goto out;
        }
        got += n;
        int pct = (int)((int64_t)got * 100 / len);
        if (pct / 25 > shown) {   /* packs are small: a line every 25 % */
            shown = pct / 25;
            ESP_LOGI(TAG, "%d%% (%d of %lld bytes)", pct, got, (long long)len);
            report("downloading", NULL, pct, NULL, 0);
        }
    }
    *len_out = got;
out:
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return buf;
}

/* Erase just the sectors the pack needs, write it, and read it back. */
static esp_err_t write_storage(const uint8_t *buf, int len, char *why, size_t why_len)
{
    const esp_partition_t *part =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "storage");
    if (!part || (size_t)len > part->size) {
        snprintf(why, why_len, part ? "pack is larger than the storage partition" : "no storage partition");
        return ESP_FAIL;
    }
    size_t span = (len + part->erase_size - 1) / part->erase_size * part->erase_size;
    esp_err_t err = esp_partition_erase_range(part, 0, span);
    if (err == ESP_OK) {
        err = esp_partition_write(part, 0, buf, len);
    }
    if (err != ESP_OK) {
        snprintf(why, why_len, "write: %s (storage now holds no pack: the built-in pet shows after a reboot)",
                 esp_err_to_name(err));
        return err;
    }
    static uint8_t chunk[1024];   /* this task only */
    for (int off = 0; off < len; off += sizeof chunk) {
        int n = len - off < (int)sizeof chunk ? len - off : (int)sizeof chunk;
        err = esp_partition_read(part, off, chunk, n);
        if (err != ESP_OK || memcmp(chunk, buf + off, n) != 0) {
            snprintf(why, why_len, "write: read-back differs at byte %d", off);
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

static void pack_task(void *arg)
{
    job_t *job = arg;
    char why[128] = "";
    char name[64] = "";
    uint32_t crc = 0;
    int len = 0;
    ESP_LOGI(TAG, "fetching %s", job->url);
    screen("new pet...");
    report("downloading", NULL, 0, NULL, 0);

    uint8_t *buf = download(job->url, &len, why, sizeof why);
    if (!buf) {
        fail(why);
        goto out;
    }
    uint8_t sha[32];
    mbedtls_sha256(buf, len, sha, 0);
    if (memcmp(sha, job->sha, sizeof sha) != 0) {
        fail("sha256 does not match: the download is not the pack the Mac sent");
        goto out;
    }
    report("checking", NULL, -1, NULL, 0);
    if (!pet_pack_check(buf, len, name, sizeof name, &crc)) {
        snprintf(why, sizeof why, "check: %s", pet_pack_why());
        fail(why);
        goto out;
    }
    ESP_LOGI(TAG, "%s: %d bytes, crc %08lx, checks passed; writing storage", name, len, (unsigned long)crc);
    report("writing", name, -1, NULL, crc);
    if (write_storage(buf, len, why, sizeof why) != ESP_OK) {
        fail(why);
        goto out;
    }
    ESP_LOGI(TAG, "%s written; rebooting to draw it", name);
    report("rebooting", name, 100, NULL, crc);
    screen("new pet: rebooting...");
    vTaskDelay(pdMS_TO_TICKS(2000));   /* let the pet/pack publish and the UART line out */
    esp_restart();
out:
    free(buf);
    free(job);
    s_busy = false;
    vTaskDelete(NULL);
}

static bool parse_sha(const char *hex, uint8_t out[32])
{
    if (!hex || strlen(hex) != 64) {
        return false;
    }
    for (int i = 0; i < 32; i++) {
        unsigned v;
        char two[3] = {hex[2 * i], hex[2 * i + 1], 0};
        if (!strchr("0123456789abcdefABCDEF", two[0]) || !strchr("0123456789abcdefABCDEF", two[1]) ||
            sscanf(two, "%2x", &v) != 1) {
            return false;
        }
        out[i] = (uint8_t)v;
    }
    return true;
}

bool pack_handle_cmd(const char *payload, size_t len)
{
    cJSON *root = cJSON_ParseWithLength(payload, len);
    const cJSON *u = cJSON_GetObjectItemCaseSensitive(root, "pack");
    if (!cJSON_IsString(u) || !u->valuestring) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON *h = cJSON_GetObjectItemCaseSensitive(root, "sha256");
    job_t *job = calloc(1, sizeof *job);
    const char *url = u->valuestring;
    if (!job) {
        fail("no memory for the pack job");
    } else if (strncmp(url, "http://", 7) != 0 || strlen(url) > PACK_URL_MAX) {
        ESP_LOGW(TAG, "cmd ignored: the url must be plain http on the LAN and short: %.80s", url);
    } else if (!parse_sha(cJSON_IsString(h) ? h->valuestring : NULL, job->sha)) {
        ESP_LOGW(TAG, "cmd ignored: sha256 must be 64 hex digits");
    } else if (s_busy) {
        ESP_LOGW(TAG, "cmd ignored: a pack is already on its way");
    } else {
        strlcpy(job->url, url, sizeof job->url);
        s_busy = true;
        if (xTaskCreate(pack_task, "pack", PACK_TASK_STACK, job, 4, NULL) == pdPASS) {
            job = NULL;   /* the task owns it now */
        } else {
            s_busy = false;
            fail("no memory for the pack task");
        }
    }
    free(job);
    cJSON_Delete(root);
    return true;
}

void pack_report_running(const char *pet_name)
{
    uint32_t crc = pet_pack_loaded_crc();
    report("running", pet_name, -1, crc ? NULL : pet_pack_why(), crc);
}
