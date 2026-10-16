/* The face scene: the sprite character in the middle, the numbers around the edge, a stats overlay on swipe. */
#include <stdio.h>
#include "face.h"
#include "anim.h"
#include "pet_config.h"
#include "esp_log.h"

#define BG_COLOR     lv_color_hex(0x0b0f14)
#define PANEL_COLOR  lv_color_hex(0x151d26)
#define INK_COLOR    lv_color_hex(0xe6f0f5)
#define DIM_COLOR    lv_color_hex(0x5c6b78)
#define ACCENT_COLOR lv_color_hex(0x4fd1c5)
#define WARN_COLOR   lv_color_hex(0xf6ad55)
#define GOOD_COLOR   lv_color_hex(0x68d391)
#define SLEEP_INK    lv_color_hex(0x7f8e9a)

static const char *TAG = "face";
static lv_obj_t *s_level, *s_xp_bar, *s_xp_text, *s_streak, *s_clock, *s_today, *s_mood, *s_link, *s_status, *s_banner;
static lv_obj_t *s_stats, *s_stats_body, *s_stats_bar;
static lv_timer_t *s_banner_timer, *s_stats_timer;
static pet_state_t s_last;

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, lv_color_t color, lv_align_t align, int x, int y)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_text(l, "");
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_align(l, align, x, y);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
    return l;
}

static lv_obj_t *bar(lv_obj_t *parent, int w)
{
    lv_obj_t *b = lv_bar_create(parent);
    lv_obj_set_size(b, w, 14);
    lv_obj_set_style_bg_color(b, DIM_COLOR, LV_PART_MAIN);
    lv_obj_set_style_bg_color(b, ACCENT_COLOR, LV_PART_INDICATOR);
    lv_obj_set_style_radius(b, 7, LV_PART_MAIN);
    lv_obj_set_style_radius(b, 7, LV_PART_INDICATOR);
    lv_bar_set_range(b, 0, 100);
    lv_bar_set_value(b, 0, LV_ANIM_OFF);
    lv_obj_remove_flag(b, LV_OBJ_FLAG_CLICKABLE);
    return b;
}

static void show(lv_obj_t *o, bool on)
{
    if (on) {
        lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
}

static void banner_timeout(lv_timer_t *t)
{
    show(s_banner, false);
    s_banner_timer = NULL;
    lv_timer_delete(t);
}

static void stats_timeout(lv_timer_t *t)
{
    show(s_stats, false);
    s_stats_timer = NULL;
    lv_timer_delete(t);
}

static void banner(const char *text, lv_color_t color, bool sticky)
{
    lv_label_set_text(s_banner, text);
    lv_obj_set_style_text_color(s_banner, color, 0);
    show(s_banner, true);
    if (s_banner_timer) {
        lv_timer_delete(s_banner_timer);
        s_banner_timer = NULL;
    }
    if (!sticky) {
        s_banner_timer = lv_timer_create(banner_timeout, PET_BANNER_MS, NULL);
    }
}

void face_create(lv_obj_t *parent)
{
    lv_obj_set_style_bg_color(parent, BG_COLOR, 0);
    lv_obj_set_style_bg_opa(parent, LV_OPA_COVER, 0);
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);

    anim_init(parent, PET_SPRITE_X, PET_SPRITE_Y, PET_SPRITE_SCALE);

    s_level = label(parent, &lv_font_montserrat_48, ACCENT_COLOR, LV_ALIGN_TOP_LEFT, 24, 16);
    lv_label_set_text(s_level, "LV -");
    s_xp_bar = bar(parent, 220);
    lv_obj_align(s_xp_bar, LV_ALIGN_TOP_LEFT, 26, 78);
    s_xp_text = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_TOP_LEFT, 26, 98);
    lv_label_set_text(s_xp_text, "waiting for the bridge");

    s_clock = label(parent, &lv_font_montserrat_32, INK_COLOR, LV_ALIGN_TOP_RIGHT, -24, 16);
    lv_label_set_text(s_clock, "--:--");
    s_streak = label(parent, &lv_font_montserrat_24, DIM_COLOR, LV_ALIGN_TOP_RIGHT, -24, 60);

    s_today = label(parent, &lv_font_montserrat_32, INK_COLOR, LV_ALIGN_BOTTOM_MID, 0, -56);
    s_mood = label(parent, &lv_font_montserrat_24, DIM_COLOR, LV_ALIGN_BOTTOM_LEFT, 24, -20);
    s_link = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_BOTTOM_RIGHT, -24, -24);
    lv_label_set_text(s_link, "booting");
    s_status = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_BOTTOM_MID, 0, -24);

    s_banner = label(parent, &lv_font_montserrat_32, GOOD_COLOR, LV_ALIGN_TOP_MID, 0, 130);
    show(s_banner, false);

    /* Stats overlay, hidden until a swipe. */
    s_stats = lv_obj_create(parent);
    lv_obj_remove_style_all(s_stats);
    lv_obj_set_size(s_stats, 620, 460);
    lv_obj_center(s_stats);
    lv_obj_set_style_bg_color(s_stats, PANEL_COLOR, 0);
    lv_obj_set_style_bg_opa(s_stats, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(s_stats, 28, 0);
    lv_obj_set_style_border_width(s_stats, 2, 0);
    lv_obj_set_style_border_color(s_stats, ACCENT_COLOR, 0);
    lv_obj_set_style_pad_all(s_stats, 28, 0);
    lv_obj_remove_flag(s_stats, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title = label(s_stats, &lv_font_montserrat_24, ACCENT_COLOR, LV_ALIGN_TOP_LEFT, 0, 0);
    lv_label_set_text(title, "stats");
    s_stats_body = label(s_stats, &lv_font_montserrat_24, INK_COLOR, LV_ALIGN_TOP_LEFT, 0, 44);
    lv_obj_set_width(s_stats_body, 560);
    lv_label_set_long_mode(s_stats_body, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(s_stats_body, 10, 0);
    lv_label_set_text(s_stats_body, "no state from the bridge yet");
    s_stats_bar = bar(s_stats, 560);
    lv_obj_align(s_stats_bar, LV_ALIGN_BOTTOM_LEFT, 0, -36);
    lv_obj_t *foot = label(s_stats, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_BOTTOM_RIGHT, 0, 0);
    lv_label_set_text(foot, "tap to close");
    show(s_stats, false);
}

static void fill_stats(const pet_state_t *s)
{
    lv_label_set_text_fmt(s_stats_body,
                          "today  %d of %d%s\n"
                          "commits %d   PRs %d   issues %d   reviews %d   other %d\n"
                          "today's xp  %d\n"
                          "streak  %d day%s%s\n"
                          "level %d   %d / %d xp   (%d total)\n"
                          "mood  %s  %d/10",
                          s->today.total, s->goal, s->big_day ? "   BIG DAY" : s->goal_hit ? "   goal hit" : "",
                          s->today.commits, s->today.prs, s->today.issues, s->today.reviews, s->today.other,
                          s->today.xp, s->streak, s->streak == 1 ? "" : "s", s->streak_at_risk ? "   at risk" : "",
                          s->level, s->xp_into, s->xp_next, s->xp, s->mood_label, s->mood);
    int pct = s->xp_next > 0 ? (s->xp_into * 100) / s->xp_next : 0;
    lv_bar_set_value(s_stats_bar, pct > 100 ? 100 : pct, LV_ANIM_OFF);
}

static int mood_class(const pet_state_t *s)
{
    if (s->streak_at_risk) {
        return PET_MOOD_WORRIED;
    }
    if (s->mood <= 2) {
        return PET_MOOD_SAD;
    }
    if (s->mood <= 5) {
        return PET_MOOD_NEUTRAL;
    }
    if (s->mood <= 8) {
        return PET_MOOD_HAPPY;
    }
    return PET_MOOD_ECSTATIC;
}

void face_apply_state(const pet_state_t *s, bool celebrate)
{
    char buf[96];
    s_last = *s;
    lv_label_set_text_fmt(s_level, "LV %d", s->level);
    int pct = s->xp_next > 0 ? (s->xp_into * 100) / s->xp_next : 0;
    lv_bar_set_value(s_xp_bar, pct > 100 ? 100 : pct, LV_ANIM_ON);
    lv_label_set_text_fmt(s_xp_text, "%d / %d xp", s->xp_into, s->xp_next);

    if (s->streak <= 0) {
        lv_label_set_text(s_streak, "no streak");
        lv_obj_set_style_text_color(s_streak, DIM_COLOR, 0);
    } else {
        lv_label_set_text_fmt(s_streak, "%d day streak%s", s->streak, s->streak_at_risk ? "  !" : "");
        lv_obj_set_style_text_color(s_streak, s->streak_at_risk ? WARN_COLOR : INK_COLOR, 0);
    }

    snprintf(buf, sizeof(buf), "today %d / %d%s%s", s->today.total, s->goal, s->goal_hit ? "  " LV_SYMBOL_OK : "",
             s->big_day ? "   BIG DAY" : "");
    lv_label_set_text(s_today, buf);
    lv_obj_set_style_text_color(s_today, s->big_day ? GOOD_COLOR : INK_COLOR, 0);
    lv_label_set_text_fmt(s_mood, "%s  %d/10", s->mood_label[0] ? s->mood_label : "mood", s->mood);
    fill_stats(s);

    anim_set_mood_class(mood_class(s));

    if (celebrate && s->events) {
        if (s->events & PET_EVENT_LEVEL_UP) {
            ESP_LOGI(TAG, "level up -> celebrate until tap");
            banner("LEVEL UP!  tap to continue", GOOD_COLOR, true);
            anim_play(PET_ANIM_CELEBRATE, true);
        } else {
            ESP_LOGI(TAG, "%s -> celebrate", (s->events & PET_EVENT_BIG_DAY) ? "big day" : "goal hit");
            banner((s->events & PET_EVENT_BIG_DAY) ? "BIG DAY!" : "daily goal hit", GOOD_COLOR, false);
            anim_play(PET_ANIM_CELEBRATE, false);
        }
    }
}

void face_set_link(const char *text)
{
    lv_label_set_text(s_link, text);
}

void face_set_status(const char *text)
{
    lv_label_set_text(s_status, text);
}

void face_set_clock(const char *hhmm)
{
    lv_label_set_text(s_clock, hhmm);
}

void face_set_sleeping(bool sleeping)
{
    anim_set_ink(sleeping ? SLEEP_INK : INK_COLOR);
    anim_set_sleeping(sleeping);
}

void face_toggle_stats(void)
{
    bool visible = !lv_obj_has_flag(s_stats, LV_OBJ_FLAG_HIDDEN);
    if (s_stats_timer) {
        lv_timer_delete(s_stats_timer);
        s_stats_timer = NULL;
    }
    show(s_stats, !visible);
    if (!visible) {
        s_stats_timer = lv_timer_create(stats_timeout, PET_STATS_MS, NULL);
    }
}

void face_ack(void)
{
    if (s_banner_timer) {
        lv_timer_delete(s_banner_timer);
        s_banner_timer = NULL;
    }
    show(s_banner, false);
    if (!lv_obj_has_flag(s_stats, LV_OBJ_FLAG_HIDDEN)) {
        if (s_stats_timer) {
            lv_timer_delete(s_stats_timer);
            s_stats_timer = NULL;
        }
        show(s_stats, false);
    }
    if (anim_ack()) {
        ESP_LOGI(TAG, "reaction acknowledged");
    }
}
