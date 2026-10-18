/* The play menu (#62): a long press opens it over the pet. Feed closes the menu and drops a snack (#63), or says why
 * there is none; Pet Says starts the memory game (#65); Settings opens a stub page until #53. Close, a tap outside the
 * panel, or PET_MENU_IDLE_MS without a touch closes it. Its objects catch their own taps, which do not bubble to the
 * screen, so menu taps never acknowledge a waiting AI session; MQTT state and notices keep updating the face
 * underneath. */
#include "menu.h"
#include "face.h"
#include "play.h"
#include "says.h"
#include "pet_config.h"
#include "sched.h"
#include "esp_log.h"

static const char *TAG = "menu";

#define C(name) lv_color_hex(s_pet->colors.name)
#define PANEL_W 540
#define ITEM_H  96

enum { ITEM_FEED, ITEM_SAYS, ITEM_SETTINGS, ITEM_CLOSE, ITEMS };
static const char *const ITEM_LABEL[ITEMS] = {"Feed", "Pet Says", "Settings", "Close"};
static const char *const PAGE_TEXT[ITEM_CLOSE] = {
    "No snacks left today. Go make a commit!",   /* replaced while the bridge has not sent today's total */
    NULL,   /* Pet Says opens the game, not a page */
    "Coming with Wi-Fi setup.",
};

static const pet_def_t *s_pet;
static lv_obj_t *s_scrim, *s_panel, *s_page, *s_page_title, *s_page_text;
static lv_timer_t *s_idle;

/* Any finger-down in the menu: keep the screen bright and restart the idle countdown. */
static void poke_cb(lv_event_t *e)
{
    (void)e;
    sched_touch();
    lv_timer_reset(s_idle);
}

static void idle_cb(lv_timer_t *t)
{
    (void)t;
    menu_close("idle");
}

static void scrim_cb(lv_event_t *e)
{
    if (lv_event_get_target(e) == s_scrim) {   /* the dimmed area itself, not the panel or a page on it */
        menu_close("tap outside");
    }
}

static void show_page(int item)
{
    lv_label_set_text(s_page_title, ITEM_LABEL[item]);
    const char *text = PAGE_TEXT[item];
    if (item == ITEM_FEED && play_snacks() < 0) {
        text = "Snacks are ready once the clock is set and today's numbers arrive from the bridge.";
    }
    lv_label_set_text(s_page_text, text);
    lv_obj_add_flag(s_panel, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_page, LV_OBJ_FLAG_HIDDEN);
}

static void item_cb(lv_event_t *e)
{
    int item = (int)(intptr_t)lv_event_get_user_data(e);
    ESP_LOGI(TAG, "%s", ITEM_LABEL[item]);
    if (item == ITEM_CLOSE) {
        menu_close("Close");
    } else if (item == ITEM_SAYS) {
        menu_close("pet says");
        says_open();
    } else if (item == ITEM_FEED && play_snacks() > 0) {
        menu_close("feed");   /* the pet eats in full view */
        face_feed();          /* while a snack is still falling it says so instead */
    } else {
        show_page(item);
    }
}

static void back_cb(lv_event_t *e)
{
    (void)e;
    lv_obj_add_flag(s_page, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_panel, LV_OBJ_FLAG_HIDDEN);
}

static lv_obj_t *card(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_set_size(o, PANEL_W, LV_SIZE_CONTENT);
    lv_obj_center(o);
    lv_obj_set_style_bg_color(o, C(panel), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(o, C(accent), 0);
    lv_obj_set_style_border_width(o, 3, 0);
    lv_obj_set_style_radius(o, 24, 0);
    lv_obj_set_style_pad_all(o, 28, 0);
    lv_obj_set_style_pad_row(o, 18, 0);
    lv_obj_set_flex_flow(o, LV_FLEX_FLOW_COLUMN);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(o, poke_cb, LV_EVENT_PRESSED, NULL);
    return o;
}

static lv_obj_t *button(lv_obj_t *parent, const char *text, bool quiet, lv_event_cb_t cb, int item)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_size(b, LV_PCT(100), ITEM_H);
    lv_obj_set_style_bg_color(b, C(background), 0);
    lv_obj_set_style_bg_color(b, C(accent), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(b, quiet ? C(dim) : C(accent), 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_radius(b, 18, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    /* Text style on the button, inherited by the label: the label itself never gets LV_STATE_PRESSED. */
    lv_obj_set_style_text_font(b, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(b, quiet ? C(dim) : C(ink), 0);
    lv_obj_set_style_text_color(b, C(background), LV_STATE_PRESSED);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, poke_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, (void *)(intptr_t)item);
    return b;
}

void menu_create(lv_obj_t *parent, const pet_def_t *pet)
{
    s_pet = pet;
    s_scrim = lv_obj_create(parent);
    lv_obj_remove_style_all(s_scrim);
    lv_obj_set_size(s_scrim, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(s_scrim, C(background), 0);
    lv_obj_set_style_bg_opa(s_scrim, LV_OPA_70, 0);
    lv_obj_add_flag(s_scrim, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_scrim, LV_OBJ_FLAG_GESTURE_BUBBLE);   /* a swipe on the menu must not toggle the stats */
    lv_obj_remove_flag(s_scrim, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_scrim, poke_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_scrim, scrim_cb, LV_EVENT_CLICKED, NULL);

    s_panel = card(s_scrim);
    for (int i = 0; i < ITEMS; i++) {
        button(s_panel, ITEM_LABEL[i], i == ITEM_CLOSE, item_cb, i);
    }

    s_page = card(s_scrim);
    lv_obj_add_flag(s_page, LV_OBJ_FLAG_HIDDEN);
    s_page_title = lv_label_create(s_page);
    lv_obj_set_style_text_font(s_page_title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(s_page_title, C(accent), 0);
    s_page_text = lv_label_create(s_page);
    lv_obj_set_width(s_page_text, LV_PCT(100));
    lv_label_set_long_mode(s_page_text, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_font(s_page_text, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_page_text, C(ink), 0);
    lv_obj_set_style_pad_bottom(s_page_text, 12, 0);
    button(s_page, "Back", true, back_cb, 0);

    s_idle = lv_timer_create(idle_cb, PET_MENU_IDLE_MS, NULL);
    lv_timer_pause(s_idle);
}

void menu_open(void)
{
    if (menu_is_open()) {
        return;
    }
    ESP_LOGI(TAG, "open");
    lv_obj_add_flag(s_page, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_panel, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_scrim, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_scrim);
    sched_hold(SCHED_HOLD_MENU, true);
    lv_timer_reset(s_idle);
    lv_timer_resume(s_idle);
}

void menu_close(const char *why)
{
    if (!menu_is_open()) {
        return;
    }
    ESP_LOGI(TAG, "close: %s", why);
    lv_obj_add_flag(s_scrim, LV_OBJ_FLAG_HIDDEN);
    lv_timer_pause(s_idle);
    sched_hold(SCHED_HOLD_MENU, false);
}

bool menu_is_open(void)
{
    return s_scrim && !lv_obj_has_flag(s_scrim, LV_OBJ_FLAG_HIDDEN);
}
