#include <string.h>
#include "pet_notify.h"
#include "cJSON.h"
#include "esp_log.h"

static const char *TAG = "notify";

static void copy_str(const cJSON *o, const char *k, char *dst, size_t n)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsString(v) && v->valuestring) {
        strlcpy(dst, v->valuestring, n);
    } else {
        dst[0] = '\0';
    }
}

bool pet_notify_parse(const char *json, size_t len, pet_notify_t *out)
{
    memset(out, 0, sizeof(*out));
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) {
        ESP_LOGW(TAG, "bad JSON near: %.20s", cJSON_GetErrorPtr() ? cJSON_GetErrorPtr() : "?");
        return false;
    }
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(root, "v");
    const cJSON *count = cJSON_GetObjectItemCaseSensitive(root, "count");
    const cJSON *pending = cJSON_GetObjectItemCaseSensitive(root, "pending");
    if (!cJSON_IsNumber(v) || v->valueint != 1 || !cJSON_IsArray(pending)) {
        ESP_LOGW(TAG, "unsupported notify message (v=%d)", cJSON_IsNumber(v) ? v->valueint : -1);
        cJSON_Delete(root);
        return false;
    }
    const cJSON *e;
    cJSON_ArrayForEach(e, pending) {
        if (!cJSON_IsObject(e) || out->n >= PET_NOTIFY_SHOWN) {
            continue;
        }
        pet_notice_t *it = &out->items[out->n];
        copy_str(e, "source", it->source, sizeof(it->source));
        copy_str(e, "session", it->session, sizeof(it->session));
        copy_str(e, "kind", it->kind, sizeof(it->kind));
        copy_str(e, "title", it->title, sizeof(it->title));
        copy_str(e, "ts", it->ts, sizeof(it->ts));
        if (it->source[0] && it->session[0]) {
            out->n++;   /* an entry without its key could never be acknowledged: skip it */
        } else {
            memset(it, 0, sizeof(*it));
        }
    }
    out->count = (cJSON_IsNumber(count) && count->valueint >= out->n) ? count->valueint : out->n;
    cJSON_Delete(root);
    out->valid = true;
    return true;
}

char *pet_notify_ack_json(const char *source, const char *session)
{
    cJSON *o = cJSON_CreateObject();
    if (!o) {
        return NULL;
    }
    cJSON_AddStringToObject(o, "source", source);
    cJSON_AddStringToObject(o, "session", session);
    char *text = cJSON_PrintUnformatted(o);   /* cJSON escapes quotes and control characters for us */
    cJSON_Delete(o);
    return text;
}
