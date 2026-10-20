/* The contribution radar (#105). GitHub's profile draws its Contribution activity radar from four counts over the
 * last year: commits, code review, pull requests and issues. The bridge searches for them once an hour and publishes
 * them retained on pet/radar. Here each is a bar as long as its share of the largest, with the count and its share of
 * all four; the slice furthest behind is in the warning colour, and a line under the bars names it. When the smallest
 * is within 80% of the largest the radar is close to a bullseye and every bar is in the good colour. */
#include "radar.h"
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "radar";

#define C(name) lv_color_hex(s_pet->colors.name)
#define SLICES    4
#define COUNT_MAX 9999999
#define BULLSEYE  80     /* smallest / largest, percent, at or above which the shape is a bullseye */
#define TRACK_W   260   /* leaves the count room for "9999999  100%" */

/* In the order bullseye.sh prints them */
static const char *const KEY[SLICES] = {"c", "r", "p", "i"};
static const char *const NAME[SLICES] = {"Commits", "Code review", "Pull requests", "Issues"};
static const char *const LOWER[SLICES] = {"commits", "code review", "pull requests", "issues"};

typedef struct {
    int n[SLICES];
    char since[12];   /* "2025-10-20" */
    bool have;
} radar_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_box;   /* children: SLICES rows (the name, the track with its fill, the count), then the verdict */
static radar_t *s_r;      /* in PSRAM: static RAM has little room left (#96) */
static char *s_summary;   /* 48 bytes, just after it */

void radar_draw(void)
{
    lv_obj_t *verdict = lv_obj_get_child(s_box, SLICES);
    int total = 0, max = 0, min = 0, hi = 0, lo = 0;
    for (int i = 0; i < SLICES; i++) {
        int n = s_r->n[i];
        total += n;
        if (i == 0 || n > max) {
            max = n;
            hi = i;
        }
        if (i == 0 || n < min) {
            min = n;
            lo = i;
        }
    }
    bool bullseye = max > 0 && 100 * min / max >= BULLSEYE;
    for (int i = 0; i < SLICES; i++) {
        lv_obj_t *row = lv_obj_get_child(s_box, i);
        if (!s_r->have) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN);
        lv_obj_t *fill = lv_obj_get_child(lv_obj_get_child(row, 1), 0), *count = lv_obj_get_child(row, 2);
        int n = s_r->n[i];
        int w = max > 0 ? (int)((long long)TRACK_W * n / max) : 0;
        lv_obj_set_width(fill, w < 6 && n > 0 ? 6 : w);   /* a slice with anything in it shows */
        lv_obj_set_style_bg_color(fill, bullseye ? C(good) : i == lo ? C(warn) : C(accent), 0);
        lv_label_set_text_fmt(count, "%d  %d%%", n, total > 0 ? (int)((100LL * n + total / 2) / total) : 0);
    }
    if (!s_r->have) {
        lv_label_set_text(verdict, "No radar from the bridge yet.");
    } else if (max == 0) {
        lv_label_set_text(verdict, "Nothing on GitHub in the last year.");
    } else if (bullseye) {
        lv_label_set_text(verdict, "Close to a bullseye. Keep each piece of work to one issue, one pull request, "
                                   "one commit and one review.");
    } else {
        lv_label_set_text_fmt(verdict, "Furthest behind: %s, %d short of %s.", LOWER[lo], max - min, LOWER[hi]);
    }
    lv_obj_set_style_text_color(verdict, s_r->have && max > 0 && !bullseye ? C(warn) : C(dim), 0);
}

const char *radar_summary(void)
{
    if (!s_r->have) {
        return "";
    }
    snprintf(s_summary, 48, "Last 365 days, since %s", s_r->since);
    return s_summary;
}

void radar_on_msg(const char *payload, size_t len)
{
    cJSON *j = cJSON_ParseWithLength(payload, len);
    int n[SLICES];
    bool ok = cJSON_IsObject(j);
    for (int i = 0; ok && i < SLICES; i++) {
        const cJSON *v = cJSON_GetObjectItem(j, KEY[i]);
        ok = cJSON_IsNumber(v) && v->valuedouble >= 0 && v->valuedouble <= COUNT_MAX;
        n[i] = ok ? (int)v->valuedouble : 0;
    }
    if (!ok) {   /* a radar missing a slice would point at the wrong one */
        ESP_LOGW(TAG, "pet/radar not understood (%u bytes)", (unsigned)len);
        cJSON_Delete(j);
        return;
    }
    const cJSON *since = cJSON_GetObjectItem(j, "since");
    memcpy(s_r->n, n, sizeof n);
    snprintf(s_r->since, sizeof s_r->since, "%s", cJSON_IsString(since) ? since->valuestring : "?");
    s_r->have = true;
    cJSON_Delete(j);
    ESP_LOGI(TAG, "radar: commits %d, review %d, PRs %d, issues %d", n[0], n[1], n[2], n[3]);
    radar_draw();
}

static lv_obj_t *text(lv_obj_t *parent, const lv_font_t *font, lv_color_t color)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
    return l;
}

static lv_obj_t *plain(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    return o;
}

lv_obj_t *radar_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb)
{
    s_pet = pet;
    s_r = heap_caps_calloc(1, sizeof(radar_t) + 48, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(s_r);
    s_summary = (char *)(s_r + 1);
    s_box = lv_obj_create(parent);
    lv_obj_remove_style_all(s_box);
    lv_obj_remove_flag(s_box, LV_OBJ_FLAG_SCROLLABLE);   /* nothing to scroll: a sideways swipe is a gesture */
    lv_obj_add_event_cb(s_box, press_cb, LV_EVENT_PRESSED, NULL);
    for (int i = 0; i < SLICES; i++) {
        lv_obj_t *row = plain(s_box);   /* radar_draw finds the parts by child index */
        lv_obj_set_size(row, LV_PCT(100), 72);
        lv_obj_set_pos(row, 0, 24 + i * 96);
        lv_obj_t *name = text(row, &lv_font_montserrat_24, C(ink));
        lv_label_set_text(name, NAME[i]);
        lv_obj_align(name, LV_ALIGN_LEFT_MID, 0, 0);
        lv_obj_t *track = plain(row);
        lv_obj_set_size(track, TRACK_W, 32);
        lv_obj_align(track, LV_ALIGN_LEFT_MID, 190, 0);
        lv_obj_set_style_radius(track, 10, 0);
        lv_obj_set_style_bg_color(track, C(panel), 0);
        lv_obj_set_style_bg_opa(track, LV_OPA_COVER, 0);
        lv_obj_t *fill = plain(track);
        lv_obj_set_size(fill, 0, 32);
        lv_obj_set_style_radius(fill, 10, 0);
        lv_obj_set_style_bg_opa(fill, LV_OPA_COVER, 0);
        lv_obj_t *count = text(row, &lv_font_montserrat_24, C(ink));
        lv_obj_align(count, LV_ALIGN_LEFT_MID, 190 + TRACK_W + 20, 0);
    }
    lv_obj_t *verdict = text(s_box, &lv_font_montserrat_24, C(dim));
    lv_obj_set_width(verdict, LV_PCT(100));
    lv_label_set_long_mode(verdict, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(verdict, 6, 0);
    lv_obj_set_pos(verdict, 0, 24 + SLICES * 96 + 16);
    radar_draw();
    return s_box;
}
