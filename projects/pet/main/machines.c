/* The Machines tab (#108). Once a minute the bridge asks Tailscale about the machines the pet depends on and publishes
 * the answer retained on pet/machines, with when it sent it, when it started and when it last polled GitHub. Here the
 * first row is the bridge: up, with how long and how fresh its GitHub poll is, or quiet when nothing has come for
 * QUIET_S (the retained message is then the bridge's last word). Then one row per machine, down first: a mark (a tick
 * in the good colour, a cross in the warning colour, a question mark for a name Tailscale does not know), the name,
 * and "online" or when it was last seen. If the bridge could not ask Tailscale, that is said instead of the machines. */
#include "machines.h"
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "feed.h"

static const char *TAG = "machines";

#define C(name) lv_color_hex(s_pet->colors.name)
#define MACHINES_MAX 8      /* the bridge sends at most 8 */
#define MNAME_MAX    34     /* and names of at most 32 characters */
#define QUIET_S      180    /* the bridge sends every 60 s: three missed and it is quiet */
#define SYNCED       1577836800   /* 2020-01-01: before this the clock has not been set by SNTP */
#define TIME_MAX     4e9

enum { M_UP, M_DOWN, M_UNKNOWN };

typedef struct {
    int64_t t;
    uint8_t state;
    char name[MNAME_MAX];
} machine_t;

typedef struct {
    machine_t m[MACHINES_MAX];
    int n, down;
    int64_t at, up, poll, every;   /* 0: not sent */
    bool have;
    char err[84];
    char summary[48];
} machines_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_box;   /* children: the bridge's row, MACHINES_MAX rows (each: the mark, then a column of the name
                             and the detail), then the note */
static machines_t *s_d;   /* in PSRAM: static RAM has little room left (#96) */

static int64_t num(const cJSON *j, const char *key)
{
    const cJSON *v = cJSON_GetObjectItem(j, key);
    return cJSON_IsNumber(v) && v->valuedouble > 0 && v->valuedouble < TIME_MAX ? (int64_t)v->valuedouble : 0;
}

/* "3h", "2d": how long, for "up 3h" */
static void span(char *buf, size_t size, int64_t s)
{
    if (s < 3600) {
        snprintf(buf, size, "%lldm", (long long)(s / 60));
    } else if (s < 86400) {
        snprintf(buf, size, "%lldh", (long long)(s / 3600));
    } else {
        snprintf(buf, size, "%lldd", (long long)(s / 86400));
    }
}

/* "8:12 pm", or "Oct 19, 8:12 pm" when not today */
static void clock_text(char *buf, size_t size, int64_t t, time_t now)
{
    time_t tt = (time_t)t;
    struct tm a, b;
    localtime_r(&tt, &a);
    localtime_r(&now, &b);
    int h = a.tm_hour % 12 ? a.tm_hour % 12 : 12;
    static const char *const MON[12] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
    if (a.tm_year == b.tm_year && a.tm_yday == b.tm_yday) {
        snprintf(buf, size, "%d:%02d %s", h, a.tm_min, a.tm_hour < 12 ? "am" : "pm");
    } else {
        snprintf(buf, size, "%s %d, %d:%02d %s", MON[a.tm_mon], a.tm_mday, h, a.tm_min, a.tm_hour < 12 ? "am" : "pm");
    }
}

static void set_row(lv_obj_t *row, const char *symbol, lv_color_t mark_color, const char *name, const char *detail,
                    lv_color_t detail_color)
{
    lv_obj_t *mark = lv_obj_get_child(row, 0), *col = lv_obj_get_child(row, 1);
    lv_label_set_text(mark, symbol);
    lv_obj_set_style_text_color(mark, mark_color, 0);
    lv_label_set_text(lv_obj_get_child(col, 0), name);
    lv_obj_t *d = lv_obj_get_child(col, 1);
    lv_label_set_text(d, detail);
    lv_obj_set_style_text_color(d, detail_color, 0);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN);
}

static bool bridge_quiet(time_t now)
{
    return s_d->have && now >= SYNCED && s_d->at > 0 && (int64_t)now - s_d->at > QUIET_S;
}

void machines_draw(void)
{
    time_t now = time(NULL);
    bool synced = now >= SYNCED;
    char detail[96], a[24], b[24];
    lv_obj_t *bridge = lv_obj_get_child(s_box, 0), *note = lv_obj_get_child(s_box, 1 + MACHINES_MAX);

    if (!s_d->have) {
        lv_obj_add_flag(bridge, LV_OBJ_FLAG_HIDDEN);
    } else if (!synced) {
        set_row(bridge, LV_SYMBOL_MINUS, C(dim), "Bridge", "Waiting for the clock to set", C(dim));
    } else if (bridge_quiet(now)) {
        clock_text(a, sizeof a, s_d->at, now);
        snprintf(detail, sizeof detail, "Quiet since %s: is the Mac awake?", a);
        set_row(bridge, LV_SYMBOL_CLOSE, C(warn), "Bridge", detail, C(warn));
    } else {
        span(a, sizeof a, (int64_t)now - s_d->up);
        bool late = s_d->poll > 0 && s_d->every > 0 && (int64_t)now - s_d->poll > 2 * s_d->every + 60;
        if (s_d->poll <= 0) {
            snprintf(detail, sizeof detail, "Up %s, no GitHub poll yet", a);
        } else {
            feed_ago(b, sizeof b, s_d->poll, now);
            snprintf(detail, sizeof detail, "Up %s, GitHub polled %s%s", a, b, late ? ": overdue" : "");
        }
        set_row(bridge, late ? LV_SYMBOL_WARNING : LV_SYMBOL_OK, late ? C(warn) : C(good), "Bridge", detail,
                late ? C(warn) : C(dim));
    }
    for (int i = 0; i < MACHINES_MAX; i++) {
        lv_obj_t *row = lv_obj_get_child(s_box, 1 + i);
        if (i >= s_d->n) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const machine_t *m = &s_d->m[i];
        if (m->state == M_UP) {
            set_row(row, LV_SYMBOL_OK, C(good), m->name, "Online", C(dim));
        } else if (m->state == M_DOWN) {
            feed_ago(a, sizeof a, m->t, now);
            snprintf(detail, sizeof detail, a[0] ? "Offline, last seen %s" : "Offline", a);
            set_row(row, LV_SYMBOL_CLOSE, C(warn), m->name, detail, C(warn));
        } else {
            set_row(row, "?", C(dim), m->name, "Not on the tailnet", C(dim));
        }
    }
    if (!s_d->have) {
        lv_label_set_text(note, "No word from the bridge yet.");
    } else if (s_d->err[0]) {
        lv_label_set_text(note, s_d->err);
    } else if (s_d->n == 0) {
        lv_label_set_text(note, "No machines to watch. Name them in PET_MACHINES in bridge/.env.");
    } else {
        lv_label_set_text(note, "");
    }
    lv_obj_set_style_text_color(note, s_d->err[0] ? C(warn) : C(dim), 0);
}

const char *machines_summary(void)
{
    time_t now = time(NULL);
    if (!s_d->have) {
        return "";
    }
    if (bridge_quiet(now)) {
        return "The bridge has gone quiet";
    }
    if (s_d->err[0]) {
        return "Could not ask Tailscale";
    }
    if (s_d->down == 0) {
        snprintf(s_d->summary, sizeof s_d->summary, s_d->n ? "All up" : "");
    } else if (s_d->down == 1) {
        for (int i = 0; i < s_d->n; i++) {
            if (s_d->m[i].state == M_DOWN) {
                snprintf(s_d->summary, sizeof s_d->summary, "%s is down", s_d->m[i].name);
            }
        }
    } else {
        snprintf(s_d->summary, sizeof s_d->summary, "%d of %d down", s_d->down, s_d->n);
    }
    return s_d->summary;
}

void machines_on_msg(const char *payload, size_t len)
{
    cJSON *j = cJSON_ParseWithLength(payload, len);
    const cJSON *items = cJSON_GetObjectItem(j, "items");
    if (!cJSON_IsArray(items)) {
        ESP_LOGW(TAG, "pet/machines not understood (%u bytes)", (unsigned)len);
        cJSON_Delete(j);
        return;
    }
    int n = 0, down = 0;
    const cJSON *el;
    cJSON_ArrayForEach(el, items) {
        const cJSON *name = cJSON_GetObjectItem(el, "n"), *s = cJSON_GetObjectItem(el, "s");
        if (n >= MACHINES_MAX || !cJSON_IsString(name) || !cJSON_IsString(s)) {
            continue;
        }
        machine_t *m = &s_d->m[n++];
        m->state = strcmp(s->valuestring, "up") == 0 ? M_UP : strcmp(s->valuestring, "down") == 0 ? M_DOWN : M_UNKNOWN;
        m->t = num(el, "t");
        snprintf(m->name, sizeof m->name, "%s", name->valuestring);
        down += m->state == M_DOWN;
    }
    const cJSON *err = cJSON_GetObjectItem(j, "err");
    s_d->n = n;
    s_d->down = down;
    s_d->at = num(j, "at");
    s_d->up = num(j, "up");
    s_d->poll = num(j, "poll");
    s_d->every = num(j, "every");
    snprintf(s_d->err, sizeof s_d->err, "%s", cJSON_IsString(err) ? err->valuestring : "");
    bool was = s_d->have;
    s_d->have = true;
    cJSON_Delete(j);
    if (!was || down || s_d->err[0]) {   /* every minute: log only what is worth a line */
        ESP_LOGI(TAG, "machines: %d, %d down%s%s", n, down, s_d->err[0] ? ", " : "", s_d->err);
    }
    machines_draw();
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
    lv_obj_t *row = lv_obj_create(s_box);   /* machines_draw finds the parts by child index */
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
    lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);

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
    lv_obj_t *name = text(col, &lv_font_montserrat_24, C(ink));
    lv_obj_set_width(name, LV_PCT(100));
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    lv_obj_t *detail = text(col, &lv_font_montserrat_16, C(dim));
    lv_obj_set_width(detail, LV_PCT(100));
    lv_label_set_long_mode(detail, LV_LABEL_LONG_DOT);
}

lv_obj_t *machines_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb)
{
    s_pet = pet;
    s_d = heap_caps_calloc(1, sizeof(machines_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(s_d);
    s_box = lv_obj_create(parent);
    lv_obj_remove_style_all(s_box);
    lv_obj_set_flex_flow(s_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_box, 12, 0);
    lv_obj_set_style_pad_bottom(s_box, 16, 0);
    lv_obj_set_scroll_dir(s_box, LV_DIR_VER);   /* only up and down, so a sideways swipe is a gesture */
    lv_obj_set_scrollbar_mode(s_box, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_add_event_cb(s_box, press_cb, LV_EVENT_PRESSED, NULL);
    for (int i = 0; i < 1 + MACHINES_MAX; i++) {
        make_row();
    }
    lv_obj_t *note = text(s_box, &lv_font_montserrat_24, C(dim));
    lv_obj_set_width(note, LV_PCT(100));
    lv_obj_set_style_pad_top(note, 16, 0);
    lv_label_set_long_mode(note, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(note, LV_TEXT_ALIGN_CENTER, 0);
    machines_draw();
    return s_box;
}
