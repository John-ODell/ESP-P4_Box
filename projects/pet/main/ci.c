/* The CI tab (#107). Each poll the bridge looks at the check suites on the latest commit of your recently pushed repos
 * and publishes the ones that ran or are running, retained on pet/ci: failures first, then running, then passing. Here
 * each is a row: a mark (a cross in the warning colour for a failure, arrows in the accent for a run in progress, a
 * tick in the good colour for a pass, a dash otherwise), the repo, and the workflow with how long ago. The rows take
 * no tap: a drag scrolls and a sideways swipe closes the list. */
#include "ci.h"
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "feed.h"

static const char *TAG = "ci";

#define C(name) lv_color_hex(s_pet->colors.name)
#define CI_MAX    10     /* the bridge sends at most 10 */
#define CI_NAME_MAX  34     /* and names of at most 32 characters */

enum { S_FAIL, S_RUN, S_PASS, S_OTHER, S_COUNT };
static const char *const STATE_KEY[S_COUNT] = {"fail", "run", "pass", "other"};

typedef struct {
    int64_t t;
    uint8_t state;
    char repo[CI_NAME_MAX];
    char workflow[CI_NAME_MAX];
} line_t;

typedef struct {
    line_t lines[CI_MAX];
    int n, fails, runs;
    bool have;
    char summary[48];
} ci_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_box;   /* children: CI_MAX rows (the mark, then a column of the repo and the workflow), then the
                             empty text */
static ci_t *s_ci;        /* in PSRAM: static RAM has little room left (#96) */

static void mark(lv_obj_t *l, int state)
{
    static const char *const SYMBOL[S_COUNT] = {LV_SYMBOL_CLOSE, LV_SYMBOL_REFRESH, LV_SYMBOL_OK, LV_SYMBOL_MINUS};
    lv_label_set_text(l, SYMBOL[state]);
    lv_obj_set_style_text_color(l, state == S_FAIL ? C(warn) : state == S_RUN ? C(accent) :
                                   state == S_PASS ? C(good) : C(dim), 0);
}

void ci_draw(void)
{
    time_t now = time(NULL);
    for (int i = 0; i < CI_MAX; i++) {
        lv_obj_t *row = lv_obj_get_child(s_box, i);
        if (i >= s_ci->n) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const line_t *l = &s_ci->lines[i];
        lv_obj_t *col = lv_obj_get_child(row, 1);
        mark(lv_obj_get_child(row, 0), l->state);
        lv_label_set_text(lv_obj_get_child(col, 0), l->repo);
        char when[24];
        feed_ago(when, sizeof when, l->t, now);
        lv_obj_t *meta = lv_obj_get_child(col, 1);
        lv_label_set_text_fmt(meta, "%s%s%s", l->workflow, when[0] ? ", " : "", when);
        lv_obj_set_style_text_color(meta, l->state == S_FAIL ? C(warn) : C(dim), 0);
        lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_t *empty = lv_obj_get_child(s_box, CI_MAX);
    if (s_ci->n == 0) {
        lv_label_set_text(empty, !s_ci->have ? "No CI from the bridge yet." :
                                 "No checks have run on your recently pushed repos.");
        lv_obj_remove_flag(empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(empty, LV_OBJ_FLAG_HIDDEN);
    }
}

const char *ci_summary(void)
{
    if (s_ci->n == 0) {
        return "";
    }
    if (s_ci->fails > 0) {
        snprintf(s_ci->summary, sizeof s_ci->summary, "%d failing%s", s_ci->fails,
                 s_ci->runs ? ", some still running" : "");
    } else if (s_ci->runs > 0) {
        snprintf(s_ci->summary, sizeof s_ci->summary, "Nothing failing, %d running", s_ci->runs);
    } else {
        snprintf(s_ci->summary, sizeof s_ci->summary, "All passing");
    }
    return s_ci->summary;
}

void ci_on_msg(const char *payload, size_t len)
{
    cJSON *j = cJSON_ParseWithLength(payload, len);
    const cJSON *items = cJSON_GetObjectItem(j, "items");
    if (!cJSON_IsArray(items)) {
        ESP_LOGW(TAG, "pet/ci not understood (%u bytes)", (unsigned)len);
        cJSON_Delete(j);
        return;
    }
    int n = 0, fails = 0, runs = 0;
    const cJSON *el;
    cJSON_ArrayForEach(el, items) {
        const cJSON *r = cJSON_GetObjectItem(el, "r"), *w = cJSON_GetObjectItem(el, "w");
        const cJSON *s = cJSON_GetObjectItem(el, "s"), *t = cJSON_GetObjectItem(el, "t");
        if (n >= CI_MAX || !cJSON_IsString(r) || !cJSON_IsString(w) || !cJSON_IsString(s)) {
            continue;
        }
        int state = S_OTHER;
        for (int i = 0; i < S_COUNT; i++) {
            if (strcmp(s->valuestring, STATE_KEY[i]) == 0) {
                state = i;
            }
        }
        line_t *l = &s_ci->lines[n++];
        l->state = (uint8_t)state;
        l->t = cJSON_IsNumber(t) && t->valuedouble > 0 && t->valuedouble < 4e9 ? (int64_t)t->valuedouble : 0;
        snprintf(l->repo, sizeof l->repo, "%s", r->valuestring);
        snprintf(l->workflow, sizeof l->workflow, "%s", w->valuestring);
        fails += state == S_FAIL;
        runs += state == S_RUN;
    }
    s_ci->n = n;
    s_ci->fails = fails;
    s_ci->runs = runs;
    s_ci->have = true;
    cJSON_Delete(j);
    ESP_LOGI(TAG, "ci: %d lines, %d failing, %d running", n, fails, runs);
    ci_draw();
}

static lv_obj_t *text(lv_obj_t *parent, const lv_font_t *font, lv_color_t color)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
    return l;
}

static void make_row(void)
{
    lv_obj_t *row = lv_obj_create(s_box);   /* ci_draw finds the parts by child index */
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_pad_hor(row, 20, 0);
    lv_obj_set_style_pad_ver(row, 14, 0);
    lv_obj_set_style_pad_column(row, 20, 0);
    lv_obj_set_style_radius(row, 18, 0);
    lv_obj_set_style_bg_color(row, C(panel), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);   /* touches go to the page: drags scroll */

    lv_obj_t *m = text(row, &lv_font_montserrat_32, C(dim));
    lv_obj_set_width(m, 40);
    lv_obj_set_style_text_align(m, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_t *col = lv_obj_create(row);
    lv_obj_remove_style_all(col);
    lv_obj_set_height(col, LV_SIZE_CONTENT);
    lv_obj_set_flex_grow(col, 1);
    lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(col, 4, 0);
    lv_obj_remove_flag(col, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *repo = text(col, &lv_font_montserrat_24, C(ink));
    lv_obj_set_width(repo, LV_PCT(100));
    lv_label_set_long_mode(repo, LV_LABEL_LONG_DOT);
    lv_obj_t *meta = text(col, &lv_font_montserrat_16, C(dim));
    lv_obj_set_width(meta, LV_PCT(100));
    lv_label_set_long_mode(meta, LV_LABEL_LONG_DOT);
}

lv_obj_t *ci_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb)
{
    s_pet = pet;
    s_ci = heap_caps_calloc(1, sizeof(ci_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(s_ci);
    s_box = lv_obj_create(parent);
    lv_obj_remove_style_all(s_box);
    lv_obj_set_flex_flow(s_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_box, 12, 0);
    lv_obj_set_style_pad_bottom(s_box, 16, 0);
    lv_obj_set_scroll_dir(s_box, LV_DIR_VER);   /* only up and down, so a sideways swipe is a gesture */
    lv_obj_set_scrollbar_mode(s_box, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_add_event_cb(s_box, press_cb, LV_EVENT_PRESSED, NULL);
    for (int i = 0; i < CI_MAX; i++) {
        make_row();
    }
    lv_obj_t *empty = text(s_box, &lv_font_montserrat_24, C(dim));
    lv_obj_set_width(empty, LV_PCT(100));
    lv_obj_set_style_pad_top(empty, 200, 0);
    lv_label_set_long_mode(empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(empty, LV_TEXT_ALIGN_CENTER, 0);
    ci_draw();
    return s_box;
}
