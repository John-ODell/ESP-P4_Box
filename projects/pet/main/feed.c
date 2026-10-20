/* The updater feed (#92). The bridge asks GitHub for your latest pull requests, issues and commit counts each poll and
 * publishes them retained on pet/feed, newest first, at most FEED_MAX lines: one per PR or issue in its latest state,
 * one per repo per day of commits. Here each line is a row: what happened (coloured by kind), the title, and the repo
 * with how long ago. A drag scrolls and a sideways swipe closes the list. An open PR or issue is a button: a tap sends
 * it on pet/feed/add and the bridge puts it on the to-do list (#104); the list screen (todo.c) handles the tap. */
#include "feed.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "mqtt_link.h"

static const char *TAG = "feed";

#define C(name) lv_color_hex(s_pet->colors.name)
#define FEED_MAX  10     /* the bridge sends at most 10 */
#define TEXT_MAX  76     /* the bridge sends at most 70 characters */
#define REPO_MAX  34     /* and repo names of at most 32 */
#define SYNCED    1577836800   /* 2020-01-01: before this the clock has not been set by SNTP */

enum { K_PR_OPEN, K_PR_MERGED, K_PR_CLOSED, K_ISSUE_OPEN, K_ISSUE_CLOSED, K_ISSUE_DROPPED, K_COMMITS, K_COUNT };
static const char *const KIND_KEY[K_COUNT] = {
    "pr_open", "pr_merged", "pr_closed", "issue_open", "issue_closed", "issue_dropped", "commits",
};
static const char *const KIND_LABEL[K_COUNT] = {
    "PR opened", "Merged", "PR closed", "Issue opened", "Issue done", "Issue dropped", "Committed",
};

typedef struct {
    int64_t t;
    uint8_t kind;
    int n;   /* the PR or issue number, 0 for commits */
    char repo[REPO_MAX];
    char text[TEXT_MAX];
} line_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_box, *s_empty;   /* s_box's children: FEED_MAX rows, then s_empty */
static line_t *s_lines;             /* FEED_MAX of them in PSRAM: static RAM has little room left (#96) */
static int s_n;
static bool s_have;                 /* a pet/feed has arrived since boot */

static lv_color_t kind_color(int k)
{
    switch (k) {
    case K_PR_MERGED:
    case K_ISSUE_CLOSED: return C(good);
    case K_PR_OPEN:
    case K_ISSUE_OPEN: return C(accent);
    case K_ISSUE_DROPPED: return C(warn);
    case K_COMMITS: return C(ink);
    default: return C(dim);
    }
}

/* Whole calendar days from `t`'s date to today's, here. GitHub's commit day starts at midnight Pacific, which is 2 am
 * here, on the same date, so its date is the day GitHub counted. */
static int days_ago(time_t t, time_t now)
{
    struct tm a, b;
    localtime_r(&t, &a);
    localtime_r(&now, &b);
    a.tm_hour = b.tm_hour = 12;   /* noon: a daylight saving change cannot move either to another date */
    a.tm_min = b.tm_min = a.tm_sec = b.tm_sec = 0;
    a.tm_isdst = b.tm_isdst = -1;
    double d = difftime(mktime(&b), mktime(&a)) / 86400;
    return d < 0 ? 0 : (int)(d + 0.5);
}

/* "5m ago", "3h ago", "2d ago" for Unix time `t`. Empty before SNTP has synced. */
void feed_ago(char *buf, size_t size, int64_t t, time_t now)
{
    buf[0] = '\0';
    if (now < SYNCED || t <= 0) {
        return;
    }
    int64_t s = (int64_t)now - t;
    if (s < 0) {
        s = 0;   /* the box's clock a little behind GitHub's */
    }
    if (s < 60) {
        snprintf(buf, size, "just now");
    } else if (s < 3600) {
        snprintf(buf, size, "%lldm ago", (long long)(s / 60));
    } else if (s < 86400) {
        snprintf(buf, size, "%lldh ago", (long long)(s / 3600));
    } else {
        snprintf(buf, size, "%lldd ago", (long long)(s / 86400));
    }
}

static void age(char *buf, size_t size, const line_t *l, time_t now)
{
    if (l->kind == K_COMMITS && now >= SYNCED && l->t > 0) {
        int d = days_ago((time_t)l->t, now);
        snprintf(buf, size, d == 0 ? "today" : d == 1 ? "yesterday" : "%dd ago", d);
    } else {
        feed_ago(buf, size, l->t, now);
    }
}

void feed_draw(void)
{
    time_t now = time(NULL);
    for (int i = 0; i < FEED_MAX; i++) {
        lv_obj_t *row = lv_obj_get_child(s_box, i);   /* children: the kind, the text, the repo and age */
        if (i >= s_n) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        const line_t *l = &s_lines[i];
        lv_obj_t *kind = lv_obj_get_child(row, 0), *text = lv_obj_get_child(row, 1), *meta = lv_obj_get_child(row, 2);
        lv_label_set_text(kind, KIND_LABEL[l->kind]);
        lv_obj_set_style_text_color(kind, kind_color(l->kind), 0);
        lv_label_set_text(text, l->text);
        char when[24];
        age(when, sizeof when, l, now);
        bool tap = feed_key(i) != 0;
        lv_label_set_text_fmt(meta, "%s%s%s%s", l->repo, when[0] ? ", " : "", when,
                              tap ? "   " LV_SYMBOL_PLUS " To do" : "");
        if (tap) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        } else {
            lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE);   /* its touches go to the list, which scrolls */
        }
        lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN);
    }
    if (s_n == 0) {
        lv_label_set_text(s_empty, !s_have ? "No updates from the bridge yet." : "Nothing on GitHub in the last week.");
        lv_obj_remove_flag(s_empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_empty, LV_OBJ_FLAG_HIDDEN);
    }
}

int feed_key(int i)
{
    if (i < 0 || i >= s_n || s_lines[i].n <= 0 ||
        (s_lines[i].kind != K_PR_OPEN && s_lines[i].kind != K_ISSUE_OPEN)) {
        return 0;
    }
    uint32_t h = 5381;   /* the repo and the number: another repo's #74 is another line */
    for (const char *c = s_lines[i].repo; *c; c++) {
        h = h * 33 + (uint8_t)*c;
    }
    int key = (int)((h * 31 + (uint32_t)s_lines[i].n) & 0x7fffffff);
    return key ? key : 1;
}

bool feed_ref(int i, char *buf, size_t size)
{
    if (feed_key(i) == 0) {
        return false;
    }
    snprintf(buf, size, "%s#%d", s_lines[i].repo, s_lines[i].n);
    return true;
}

bool feed_add(int i)
{
    if (feed_key(i) == 0 || !mqtt_link_connected()) {
        return false;
    }
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "r", s_lines[i].repo);
    cJSON_AddNumberToObject(j, "n", s_lines[i].n);
    char *text = cJSON_PrintUnformatted(j);
    bool sent = text && mqtt_link_publish(PET_TOPIC_FEED_ADD, text, false);
    ESP_LOGI(TAG, "%s %s", text ? text : "?", sent ? "sent" : "NOT sent");
    cJSON_free(text);
    cJSON_Delete(j);
    return sent;
}

const char *feed_summary(void)
{
    return s_n > 0 ? "Latest on GitHub, newest first" : "";
}

void feed_on_msg(const char *payload, size_t len)
{
    cJSON *j = cJSON_ParseWithLength(payload, len);
    const cJSON *items = cJSON_GetObjectItem(j, "items");
    if (!cJSON_IsArray(items)) {
        ESP_LOGW(TAG, "pet/feed not understood (%u bytes)", (unsigned)len);
        cJSON_Delete(j);
        return;
    }
    int n = 0;
    const cJSON *el;
    cJSON_ArrayForEach(el, items) {
        const cJSON *t = cJSON_GetObjectItem(el, "t"), *k = cJSON_GetObjectItem(el, "k");
        const cJSON *r = cJSON_GetObjectItem(el, "r"), *x = cJSON_GetObjectItem(el, "x");
        const cJSON *num = cJSON_GetObjectItem(el, "n");
        if (n >= FEED_MAX || !cJSON_IsString(k) || !cJSON_IsString(x)) {
            continue;
        }
        int kind = -1;
        for (int i = 0; i < K_COUNT; i++) {
            if (strcmp(k->valuestring, KIND_KEY[i]) == 0) {
                kind = i;
            }
        }
        if (kind < 0) {   /* a kind added on the bridge later: skipped, not drawn wrong */
            continue;
        }
        line_t *l = &s_lines[n++];
        l->kind = (uint8_t)kind;
        /* a time the box cannot use only loses the age */
        l->t = cJSON_IsNumber(t) && t->valuedouble > 0 && t->valuedouble < 4e9 ? (int64_t)t->valuedouble : 0;
        snprintf(l->repo, sizeof l->repo, "%s", cJSON_IsString(r) ? r->valuestring : "");
        snprintf(l->text, sizeof l->text, "%s", x->valuestring);
        /* only a whole number the bridge can find again; a repo cut to fit would not be found either */
        l->n = cJSON_IsNumber(num) && num->valuedouble >= 1 && num->valuedouble <= 9999999 &&
               num->valuedouble == (double)(int)num->valuedouble && cJSON_IsString(r) &&
               strlen(r->valuestring) < sizeof l->repo ? (int)num->valuedouble : 0;
    }
    s_n = n;
    s_have = true;
    cJSON_Delete(j);
    ESP_LOGI(TAG, "feed: %d lines", s_n);
    feed_draw();
}

static void style_text(lv_obj_t *l, const lv_font_t *font, lv_color_t color)
{
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
}

static lv_obj_t *make_row(void)
{
    lv_obj_t *row = lv_obj_create(s_box);   /* feed_draw finds the parts by child index */
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_pad_hor(row, 20, 0);
    lv_obj_set_style_pad_ver(row, 14, 0);
    lv_obj_set_style_pad_row(row, 6, 0);
    lv_obj_set_style_radius(row, 18, 0);
    lv_obj_set_style_bg_color(row, C(panel), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(row, lv_color_mix(C(accent), C(panel), 96), LV_STATE_PRESSED);   /* the accent kind
                                                                                                  label still shows */
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_COLUMN);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);   /* feed_draw makes open ones buttons */

    lv_obj_t *kind = lv_label_create(row);
    style_text(kind, &lv_font_montserrat_16, C(dim));
    lv_obj_t *text = lv_label_create(row);
    style_text(text, &lv_font_montserrat_24, C(ink));
    lv_obj_set_width(text, LV_PCT(100));
    lv_label_set_long_mode(text, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(text, 4, 0);
    lv_obj_t *meta = lv_label_create(row);
    style_text(meta, &lv_font_montserrat_16, C(dim));
    lv_obj_set_width(meta, LV_PCT(100));
    lv_label_set_long_mode(meta, LV_LABEL_LONG_DOT);
    return row;
}

lv_obj_t *feed_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb, lv_event_cb_t tap_cb)
{
    s_pet = pet;
    s_lines = heap_caps_calloc(FEED_MAX, sizeof(line_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(s_lines);
    s_box = lv_obj_create(parent);
    lv_obj_remove_style_all(s_box);
    lv_obj_set_flex_flow(s_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_box, 12, 0);
    lv_obj_set_style_pad_bottom(s_box, 16, 0);
    lv_obj_set_scroll_dir(s_box, LV_DIR_VER);   /* only up and down, so a sideways swipe is a gesture */
    lv_obj_set_scrollbar_mode(s_box, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_add_event_cb(s_box, press_cb, LV_EVENT_PRESSED, NULL);
    for (int i = 0; i < FEED_MAX; i++) {
        lv_obj_t *row = make_row();
        lv_obj_add_event_cb(row, press_cb, LV_EVENT_PRESSED, NULL);
        lv_obj_add_event_cb(row, tap_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
    }
    s_empty = lv_label_create(s_box);
    style_text(s_empty, &lv_font_montserrat_24, C(dim));
    lv_obj_set_width(s_empty, LV_PCT(100));
    lv_obj_set_style_pad_top(s_empty, 200, 0);
    lv_label_set_long_mode(s_empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(s_empty, LV_TEXT_ALIGN_CENTER, 0);
    feed_draw();
    return s_box;
}
