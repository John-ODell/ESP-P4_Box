/* Screenshots over the LAN (#86): see shot_cmd.h. LVGL renders the active screen again into a buffer of its own (the
 * panel's frame buffers are not read), so what comes out is exactly what the face draws, at full resolution. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include "bsp/esp-bsp.h"
#include "lvgl.h"
#include "shot_cmd.h"

#define SHOT_URL_MAX     200
#define SHOT_TASK_STACK  6144
#define SHOT_HTTP_TIMEOUT_MS 15000
#define SHOT_CHUNK       (16 * 1024)   /* bytes per HTTP write */

static const char *TAG = "shot";
static volatile bool s_busy;   /* one shot at a time: set on the MQTT task, cleared when the task ends */

/* POST the pixels, or with an empty body and X-Error when there are none, so screenshot.py stops waiting either way. */
static void upload(const char *url, const lv_draw_buf_t *buf, const char *error)
{
    esp_http_client_config_t cfg = {.url = url, .method = HTTP_METHOD_POST, .timeout_ms = SHOT_HTTP_TIMEOUT_MS};
    esp_http_client_handle_t h = esp_http_client_init(&cfg);
    if (!h) {
        ESP_LOGW(TAG, "no memory for the HTTP client");
        return;
    }
    size_t len = 0;
    char num[16];
    if (buf) {
        len = (size_t)buf->header.stride * buf->header.h;
        snprintf(num, sizeof num, "%u", (unsigned)buf->header.w);
        esp_http_client_set_header(h, "X-Width", num);
        snprintf(num, sizeof num, "%u", (unsigned)buf->header.h);
        esp_http_client_set_header(h, "X-Height", num);
        snprintf(num, sizeof num, "%u", (unsigned)buf->header.stride);
        esp_http_client_set_header(h, "X-Stride", num);
        esp_http_client_set_header(h, "X-Format", "RGB565");
    } else {
        esp_http_client_set_header(h, "X-Error", error);
    }
    esp_http_client_set_header(h, "Content-Type", "application/octet-stream");
    int64_t t0 = esp_log_timestamp();
    esp_err_t err = esp_http_client_open(h, (int)len);
    for (size_t off = 0; err == ESP_OK && off < len; ) {
        size_t n = len - off < SHOT_CHUNK ? len - off : SHOT_CHUNK;
        int wrote = esp_http_client_write(h, (const char *)buf->data + off, (int)n);
        if (wrote <= 0) {
            err = ESP_FAIL;
        } else {
            off += (size_t)wrote;
        }
    }
    int status = 0;
    if (err == ESP_OK && esp_http_client_fetch_headers(h) >= 0) {
        status = esp_http_client_get_status_code(h);
    }
    if (err != ESP_OK || status != 200) {
        ESP_LOGW(TAG, "upload to %s failed: %s, HTTP %d", url, esp_err_to_name(err), status);
    } else if (buf) {
        ESP_LOGI(TAG, "sent %ux%u (%u bytes) in %lld ms", (unsigned)buf->header.w, (unsigned)buf->header.h,
                 (unsigned)len, (long long)(esp_log_timestamp() - t0));
    }
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
}

static void shot_task(void *arg)
{
    char *url = arg;
    lv_draw_buf_t *buf = NULL;
    const char *error = NULL;
    if (!bsp_display_lock(2000)) {
        error = "the display stayed busy for 2 s";
    } else {
        int64_t t0 = esp_log_timestamp();
        buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);   /* about 1 MB, from PSRAM */
        bsp_display_unlock();
        if (buf) {
            ESP_LOGI(TAG, "snapshot %ux%u in %lld ms", (unsigned)buf->header.w, (unsigned)buf->header.h,
                     (long long)(esp_log_timestamp() - t0));
        } else {
            error = "no memory for the snapshot";
        }
    }
    if (error) {
        ESP_LOGW(TAG, "%s", error);
    }
    upload(url, buf, error);
    if (buf) {
        lv_draw_buf_destroy(buf);
    }
    free(url);
    s_busy = false;
    vTaskDelete(NULL);
}

bool shot_handle_cmd(const char *payload, size_t len)
{
    cJSON *root = cJSON_ParseWithLength(payload, len);
    const cJSON *u = cJSON_GetObjectItemCaseSensitive(root, "shot");
    if (!cJSON_IsString(u) || !u->valuestring) {
        cJSON_Delete(root);
        return false;
    }
    const char *url = u->valuestring;
    if (strncmp(url, "http://", 7) != 0 || strlen(url) > SHOT_URL_MAX) {
        ESP_LOGW(TAG, "cmd ignored: the url must be plain http on the LAN and at most 200 characters");
    } else if (s_busy) {
        ESP_LOGW(TAG, "cmd ignored: a screenshot is already on its way");
    } else {
        char *copy = strdup(url);
        s_busy = true;
        if (!copy || xTaskCreate(shot_task, "shot", SHOT_TASK_STACK, copy, 3, NULL) != pdPASS) {
            ESP_LOGW(TAG, "no memory for the screenshot task");
            free(copy);
            s_busy = false;
        }
    }
    cJSON_Delete(root);
    return true;
}
