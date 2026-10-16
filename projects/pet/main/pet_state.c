#include <string.h>
#include "pet_state.h"
#include "cJSON.h"
#include "esp_log.h"

static const char *TAG = "state";

static int geti(const cJSON *o, const char *k, int dflt)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsNumber(v) ? v->valueint : dflt;
}

static bool getb(const cJSON *o, const char *k)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsTrue(v);
}

static void gets_(const cJSON *o, const char *k, char *dst, size_t n)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsString(v) && v->valuestring) {
        strlcpy(dst, v->valuestring, n);
    } else {
        dst[0] = '\0';
    }
}

bool pet_state_parse(const char *json, size_t len, pet_state_t *out)
{
    memset(out, 0, sizeof(*out));
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) {
        ESP_LOGW(TAG, "bad JSON near: %.20s", cJSON_GetErrorPtr() ? cJSON_GetErrorPtr() : "?");
        return false;
    }
    out->v = geti(root, "v", 0);
    gets_(root, "ts", out->ts, sizeof(out->ts));
    gets_(root, "login", out->login, sizeof(out->login));
    out->level = geti(root, "level", 1);
    out->xp = geti(root, "xp", 0);
    out->xp_into = geti(root, "xp_into", 0);
    out->xp_next = geti(root, "xp_next", 100);
    out->mood = geti(root, "mood", 5);
    gets_(root, "mood_label", out->mood_label, sizeof(out->mood_label));
    out->streak = geti(root, "streak", 0);
    out->streak_at_risk = getb(root, "streak_at_risk");
    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "today");
    if (cJSON_IsObject(t)) {
        out->today.total = geti(t, "total", 0);
        out->today.commits = geti(t, "commits", 0);
        out->today.prs = geti(t, "prs", 0);
        out->today.issues = geti(t, "issues", 0);
        out->today.reviews = geti(t, "reviews", 0);
        out->today.other = geti(t, "other", 0);
        out->today.xp = geti(t, "xp", 0);
    }
    out->goal = geti(root, "goal", 20);
    out->goal_hit = getb(root, "goal_hit");
    out->big_day = getb(root, "big_day");
    const cJSON *ev = cJSON_GetObjectItemCaseSensitive(root, "events");
    const cJSON *e;
    cJSON_ArrayForEach(e, ev) {
        if (!cJSON_IsString(e)) {
            continue;
        }
        if (!strcmp(e->valuestring, "level_up")) {
            out->events |= PET_EVENT_LEVEL_UP;
        } else if (!strcmp(e->valuestring, "goal_hit")) {
            out->events |= PET_EVENT_GOAL_HIT;
        } else if (!strcmp(e->valuestring, "big_day")) {
            out->events |= PET_EVENT_BIG_DAY;
        }
    }
    cJSON_Delete(root);
    out->valid = (out->v == 1);
    if (!out->valid) {
        ESP_LOGW(TAG, "unsupported state schema v=%d", out->v);
    }
    return out->valid;
}
