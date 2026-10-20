/* The to-do list (#90). The bridge keeps the list (bridge/petbrain/todo.py, #89) and publishes it retained on pet/todo:
 * open items oldest first, then the most recently done, at most TODO_MAX. A left or right swipe from the pet opens it
 * here; a tap on an item ticks or unticks it on screen at once and sends {"id", "done"} on pet/todo/tick, and the
 * bridge's next pet/todo confirms it. A left or right swipe on the list, Close or PET_TODO_IDLE_MS without a touch
 * goes back to the pet. Like the games it takes every touch, so nothing here acknowledges an AI session. */
#include "todo.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "mqtt_link.h"
#include "pet_config.h"
#include "sched.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "todo";

#define C(name) lv_color_hex(s_pet->colors.name)
#define TODO_MAX  12     /* the bridge sends at most 12 */
#define TEXT_MAX  96     /* the bridge sends at most 80 characters */
#define ID_MAX    9999999   /* the bridge's ids and counts stay far below this */
#define REF_MAX   24     /* "#85", or "repo#85" from "owner/repo#85": the end, with the number, is what is kept */

enum { FLAG_NONE, FLAG_OPEN, FLAG_DROPPED };   /* the bridge's "flag": none, "open", "dropped" */

typedef struct {
    int id;
    bool done;
    char text[TEXT_MAX];
    char ref[REF_MAX];
    uint8_t flag;   /* FLAG_*: ticked by hand while GitHub disagrees (#91) */
} item_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_root, *s_count, *s_list, *s_empty;
static lv_obj_t *s_row[TODO_MAX];   /* children: the tick box (its child the tick), the text, the ref */
static lv_timer_t *s_idle;
static item_t *s_items;   /* TODO_MAX of them, about 1.5 KB in PSRAM: static RAM has little room left (see todo_create) */
static int s_n;            /* items held */
static int s_open;         /* open items on the bridge, including any not sent */
static int s_count_all;    /* every item on the bridge */
static bool s_have;        /* a pet/todo has arrived since boot */
static bool s_is_open;
static bool s_swiped;      /* this press became a swipe: LVGL still sends CLICKED to the row it started on */
static const char *s_note; /* a one-off line in the header, until the next redraw */
static uint32_t s_opened_ms;   /* lv_tick when the list opened */
static int s_press_id;         /* the item in the row the finger went down on: the bridge reorders the list after a
                                  tick, so by the release that row may hold another item */

static void draw_row(int i)
{
    const item_t *it = &s_items[i];
    lv_obj_t *box = lv_obj_get_child(s_row[i], 0), *text = lv_obj_get_child(s_row[i], 1);
    lv_obj_t *tick = lv_obj_get_child(box, 0);
    lv_label_set_text(text, it->text);
    lv_obj_t *ref = lv_obj_get_child(s_row[i], 2);   /* the flag, when there is one, takes the ref's place */
    if (it->flag != FLAG_NONE) {
        lv_label_set_text_fmt(ref, "! %s %s", it->ref, it->flag == FLAG_OPEN ? "still open" : "closed, not done");
    } else {
        lv_label_set_text(ref, it->ref);
    }
    lv_obj_set_style_text_color(ref, it->flag != FLAG_NONE ? C(warn) : C(dim), 0);
    lv_obj_set_style_text_color(text, it->done ? C(dim) : C(ink), 0);
    lv_obj_set_style_text_decor(text, it->done ? LV_TEXT_DECOR_STRIKETHROUGH : LV_TEXT_DECOR_NONE, 0);
    lv_obj_set_style_bg_opa(box, it->done ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    if (it->done) {
        lv_obj_remove_flag(tick, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(tick, LV_OBJ_FLAG_HIDDEN);
    }
}

static void draw_count(void)
{
    if (s_note) {
        lv_label_set_text(s_count, s_note);
    } else if (!s_have) {
        lv_label_set_text(s_count, "");
    } else if (s_count_all > s_n) {   /* more on the bridge than the box was sent */
        lv_label_set_text_fmt(s_count, "%d open, %d of %d shown", s_open, s_n, s_count_all);
    } else {
        lv_label_set_text_fmt(s_count, "%d open", s_open);
    }
}

static void draw(void)
{
    for (int i = 0; i < TODO_MAX; i++) {
        if (i < s_n) {
            draw_row(i);
            lv_obj_remove_flag(s_row[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(s_row[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (s_n == 0) {
        lv_label_set_text(s_empty, !s_have ? "No list from the bridge yet." :
                                   "Nothing on the list.\n\nAdd one on the Mac:\npython3 bridge/todo.py add \"...\"");
        lv_obj_remove_flag(s_empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_empty, LV_OBJ_FLAG_HIDDEN);
    }
    draw_count();
}

static void close_list(const char *why)
{
    if (!s_is_open) {
        return;
    }
    ESP_LOGI(TAG, "close: %s", why);
    s_is_open = false;
    lv_timer_pause(s_idle);
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    sched_hold(SCHED_HOLD_LIST, false);
}

static void tick_cb(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    if (s_swiped || !s_is_open || i >= s_n) {
        return;
    }
    item_t *it = &s_items[i];
    if (it->id != s_press_id) {   /* the list was redrawn under the finger: do not tick what moved into the row */
        ESP_LOGI(TAG, "tap dropped: row %d held item %d at the press, %d now", i, s_press_id, it->id);
        return;
    }
    char buf[48];
    snprintf(buf, sizeof buf, "{\"id\":%d,\"done\":%s}", it->id, it->done ? "false" : "true");
    if (!mqtt_link_connected() || !mqtt_link_publish(PET_TOPIC_TODO_TICK, buf, false)) {
        ESP_LOGW(TAG, "%s NOT sent: no link to the bridge", buf);
        s_note = "Not connected to the bridge: nothing ticked";
        draw_count();
        s_note = NULL;
        return;
    }
    it->done = !it->done;
    if (!it->done) {
        it->flag = FLAG_NONE;   /* the bridge drops it on an untick too */
    }
    s_open += it->done ? -1 : 1;
    if (s_open < 0) {
        s_open = 0;
    }
    ESP_LOGI(TAG, "%s sent: %s", buf, it->text);
    draw_row(i);
    draw_count();
}

static void press_cb(lv_event_t *e)
{
    lv_obj_t *target = lv_event_get_current_target(e);
    s_press_id = 0;
    for (int i = 0; i < s_n; i++) {
        if (target == s_row[i]) {
            s_press_id = s_items[i].id;
        }
    }
    s_swiped = false;
    sched_touch();
    lv_timer_reset(s_idle);
}

static void gesture_cb(lv_event_t *e)
{
    (void)e;
    s_swiped = true;
    lv_dir_t d = lv_indev_get_gesture_dir(lv_indev_active());
    if (lv_tick_elaps(s_opened_ms) < PET_SWIPE_COOLDOWN_MS) {
        /* The swipe that opened the list: once the list is under the finger, LVGL reports it again, to the list */
        return;
    }
    if (d == LV_DIR_LEFT || d == LV_DIR_RIGHT) {
        close_list("swipe");
    }
}

static void close_cb(lv_event_t *e)
{
    (void)e;
    if (!s_swiped) {
        close_list("Close");
    }
}

static void idle_cb(lv_timer_t *t)
{
    (void)t;
    close_list("idle");
}

void todo_on_msg(const char *payload, size_t len)
{
    cJSON *j = cJSON_ParseWithLength(payload, len);
    const cJSON *items = cJSON_GetObjectItem(j, "items");
    if (!cJSON_IsArray(items)) {
        ESP_LOGW(TAG, "pet/todo not understood (%u bytes)", (unsigned)len);
        cJSON_Delete(j);
        return;
    }
    int n = 0, open_held = 0;
    const cJSON *el;
    cJSON_ArrayForEach(el, items) {
        const cJSON *id = cJSON_GetObjectItem(el, "id"), *text = cJSON_GetObjectItem(el, "text");
        const cJSON *done = cJSON_GetObjectItem(el, "done"), *ref = cJSON_GetObjectItem(el, "ref");
        const cJSON *flag = cJSON_GetObjectItem(el, "flag");
        /* ids are whole numbers from 1 (the bridge counts up); anything else could tick the wrong item */
        if (n >= TODO_MAX || !cJSON_IsNumber(id) || id->valuedouble < 1 || id->valuedouble > ID_MAX ||
            id->valuedouble != (double)(int)id->valuedouble || !cJSON_IsString(text)) {
            continue;
        }
        item_t *it = &s_items[n++];
        it->id = (int)id->valuedouble;
        it->done = cJSON_IsTrue(done);
        open_held += !it->done;
        snprintf(it->text, sizeof it->text, "%s", text->valuestring);
        const char *r = cJSON_IsString(ref) ? ref->valuestring : "";
        const char *slash = strrchr(r, '/');   /* "owner/repo#85" -> "repo#85": the number must survive */
        r = slash ? slash + 1 : r;
        if (strlen(r) >= sizeof it->ref) {
            const char *hash = strrchr(r, '#');
            r = hash ? hash : "";
        }
        snprintf(it->ref, sizeof it->ref, "%s", r);
        it->flag = !it->done || !cJSON_IsString(flag) ? FLAG_NONE :
                   strcmp(flag->valuestring, "open") == 0 ? FLAG_OPEN :
                   strcmp(flag->valuestring, "dropped") == 0 ? FLAG_DROPPED : FLAG_NONE;
    }
    const cJSON *open = cJSON_GetObjectItem(j, "open"), *count = cJSON_GetObjectItem(j, "count");
    s_n = n;
    s_open = cJSON_IsNumber(open) && open->valuedouble >= open_held && open->valuedouble <= ID_MAX ?
             (int)open->valuedouble : open_held;
    s_count_all = cJSON_IsNumber(count) && count->valuedouble >= n && count->valuedouble <= ID_MAX ?
                  (int)count->valuedouble : n;
    s_have = true;
    cJSON_Delete(j);
    ESP_LOGI(TAG, "list: %d open, %d held of %d", s_open, s_n, s_count_all);
    draw();
}

static void style_text(lv_obj_t *l, const lv_font_t *font, lv_color_t color)
{
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
}

/* One row: a tick box, the text (two lines at most) and the issue or PR it is about. The whole row is the button. */
static void make_row(int i)
{
    lv_obj_t *row = s_row[i] = lv_obj_create(s_list);   /* draw_row finds the parts by child index */
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_style_min_height(row, 84, 0);
    lv_obj_set_style_pad_hor(row, 16, 0);
    lv_obj_set_style_pad_ver(row, 14, 0);
    lv_obj_set_style_pad_column(row, 20, 0);
    lv_obj_set_style_radius(row, 18, 0);
    lv_obj_set_style_bg_color(row, C(panel), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(row, C(accent), LV_STATE_PRESSED);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);   /* drags scroll the list, and a sideways one closes it */
    lv_obj_add_event_cb(row, press_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(row, tick_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);

    lv_obj_t *box = lv_obj_create(row);
    lv_obj_remove_style_all(box);
    lv_obj_set_size(box, 48, 48);
    lv_obj_set_style_radius(box, 10, 0);
    lv_obj_set_style_border_width(box, 3, 0);
    lv_obj_set_style_border_color(box, C(accent), 0);
    lv_obj_set_style_bg_color(box, C(accent), 0);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *tick = lv_label_create(box);
    lv_label_set_text(tick, LV_SYMBOL_OK);
    style_text(tick, &lv_font_montserrat_24, C(background));
    lv_obj_center(tick);

    lv_obj_t *text = lv_label_create(row);
    style_text(text, &lv_font_montserrat_24, C(ink));
    lv_obj_set_flex_grow(text, 1);
    lv_label_set_long_mode(text, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(text, 4, 0);

    lv_obj_t *ref = lv_label_create(row);
    style_text(ref, &lv_font_montserrat_16, C(dim));
    lv_obj_set_style_max_width(ref, 260, 0);   /* room for "! #85 closed, not done" */
    lv_obj_set_height(ref, lv_font_get_line_height(&lv_font_montserrat_16));   /* one line, ending in dots */
    lv_label_set_long_mode(ref, LV_LABEL_LONG_DOT);
}

void todo_create(lv_obj_t *parent, const pet_def_t *pet)
{
    s_pet = pet;
    /* Not a static array: once the firmware's static RAM runs past 0x4FF20000 the box cannot start FreeRTOS (#96). */
    s_items = heap_caps_calloc(TODO_MAX, sizeof(item_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(s_items);
    s_root = lv_obj_create(parent);
    lv_obj_remove_style_all(s_root);
    lv_obj_set_size(s_root, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(s_root, C(background), 0);
    lv_obj_set_style_bg_opa(s_root, LV_OPA_COVER, 0);
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_HIDDEN);   /* takes every touch while open */
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_root, press_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_root, gesture_cb, LV_EVENT_GESTURE, NULL);   /* swipes on the rows bubble up to here */

    lv_obj_t *title = lv_label_create(s_root);
    lv_label_set_text(title, "To do");
    style_text(title, &lv_font_montserrat_32, C(accent));
    lv_obj_set_pos(title, 32, 30);
    s_count = lv_label_create(s_root);
    style_text(s_count, &lv_font_montserrat_24, C(dim));
    lv_obj_set_pos(s_count, 32, 76);

    s_list = lv_obj_create(s_root);
    lv_obj_remove_style_all(s_list);
    lv_obj_set_size(s_list, 656, 572);
    lv_obj_set_pos(s_list, 32, 128);
    lv_obj_set_flex_flow(s_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_list, 12, 0);
    lv_obj_set_style_pad_bottom(s_list, 16, 0);
    lv_obj_set_scroll_dir(s_list, LV_DIR_VER);   /* only up and down, so a sideways swipe is a gesture */
    lv_obj_set_scrollbar_mode(s_list, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_add_event_cb(s_list, press_cb, LV_EVENT_PRESSED, NULL);
    for (int i = 0; i < TODO_MAX; i++) {
        make_row(i);
    }

    s_empty = lv_label_create(s_root);
    style_text(s_empty, &lv_font_montserrat_24, C(dim));
    lv_obj_set_width(s_empty, 656);
    lv_label_set_long_mode(s_empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(s_empty, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_empty, LV_ALIGN_CENTER, 0, 20);

    lv_obj_t *x = lv_button_create(s_root);   /* top-right corner, as in the games */
    lv_obj_set_size(x, 88, 88);
    lv_obj_set_pos(x, 616, 16);
    lv_obj_set_style_bg_color(x, C(background), 0);
    lv_obj_set_style_bg_color(x, C(accent), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(x, C(accent), 0);
    lv_obj_set_style_border_width(x, 2, 0);
    lv_obj_set_style_radius(x, 18, 0);
    lv_obj_set_style_shadow_width(x, 0, 0);
    lv_obj_set_style_text_font(x, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(x, C(ink), 0);
    lv_obj_set_style_text_color(x, C(background), LV_STATE_PRESSED);
    lv_obj_t *xl = lv_label_create(x);
    lv_label_set_text(xl, LV_SYMBOL_CLOSE);
    lv_obj_center(xl);
    lv_obj_add_event_cb(x, press_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(x, close_cb, LV_EVENT_CLICKED, NULL);

    s_idle = lv_timer_create(idle_cb, PET_TODO_IDLE_MS, NULL);
    lv_timer_pause(s_idle);
    draw();
}

void todo_open(void)
{
    if (s_is_open) {
        return;
    }
    ESP_LOGI(TAG, "open: %d open, %d held", s_open, s_n);
    s_is_open = true;
    s_swiped = true;   /* the swipe that opened the list is still under the finger: its release clicks nothing */
    s_opened_ms = lv_tick_get();
    draw();
    lv_obj_scroll_to_y(s_list, 0, LV_ANIM_OFF);
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_root);
    lv_timer_reset(s_idle);
    lv_timer_resume(s_idle);
    sched_hold(SCHED_HOLD_LIST, true);
}
