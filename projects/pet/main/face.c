/* The face scene: the sprite character in the middle, the numbers around the edge, a stats overlay on swipe, and
 * the speech bubble plus source tint while an AI session waits. */
#include <stdio.h>
#include <string.h>
#include "face.h"
#include "anim.h"
#include "pet_config.h"
#include "sched.h"
#include "esp_log.h"

/* Colours come from the pet (pet_pack.c), set once in face_create. */
static const pet_def_t *s_pet;
#define BG_COLOR     lv_color_hex(s_pet->colors.background)
#define PANEL_COLOR  lv_color_hex(s_pet->colors.panel)
#define INK_COLOR    lv_color_hex(s_pet->colors.ink)
#define DIM_COLOR    lv_color_hex(s_pet->colors.dim)
#define ACCENT_COLOR lv_color_hex(s_pet->colors.accent)
#define WARN_COLOR   lv_color_hex(s_pet->colors.warn)
#define GOOD_COLOR   lv_color_hex(s_pet->colors.good)
#define SLEEP_INK    lv_color_hex(s_pet->colors.sleep_ink)

static const char *TAG = "face";
static lv_obj_t *s_level, *s_xp_bar, *s_xp_text, *s_streak, *s_clock, *s_today, *s_mood, *s_link, *s_status, *s_banner;
static lv_obj_t *s_stats, *s_stats_body, *s_stats_bar;
static lv_timer_t *s_banner_timer, *s_stats_timer;
static pet_state_t s_last;
static lv_obj_t *s_bubble, *s_bubble_who, *s_bubble_what, *s_bubble_title, *s_bubble_count;
static bool s_banner_sticky;   /* the level-up banner waits for a tap */
static bool s_notice_on;       /* an AI session waits: bubble up, character tinted */
static lv_color_t s_notice_color;
static bool s_sleeping;

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

/* The character's colour: the waiting AI's colour wins, then the sleep tint, then plain ink. */
static void apply_ink(void)
{
    anim_set_ink(s_notice_on ? s_notice_color : (s_sleeping ? SLEEP_INK : INK_COLOR));
}

/* The backlight stays at full while anything on screen waits for a tap. */
static void apply_hold(void)
{
    sched_hold(SCHED_HOLD_FACE, s_banner_sticky || s_notice_on);
}

static void banner_timeout(lv_timer_t *t)
{
    show(s_banner, false);
    s_banner_timer = NULL;
    lv_timer_delete(t);
}

static lv_obj_t *bubble_line(lv_obj_t *parent, const lv_font_t *font, lv_color_t color, int lines)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_text(l, "");
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_size(l, PET_BUBBLE_W - 2 * 18 - 2 * 3, lines * (lv_font_get_line_height(font) + 2));
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT); /* a fixed box, so a long name ends in dots instead of growing */
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
    return l;
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
    s_banner_sticky = sticky;
    if (s_banner_timer) {
        lv_timer_delete(s_banner_timer);
        s_banner_timer = NULL;
    }
    if (!sticky) {
        s_banner_timer = lv_timer_create(banner_timeout, PET_BANNER_MS, NULL);
    }
}

void face_create(lv_obj_t *parent, const pet_def_t *pet)
{
    s_pet = pet;
    lv_obj_set_style_bg_color(parent, BG_COLOR, 0);
    lv_obj_set_style_bg_opa(parent, LV_OPA_COVER, 0);
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);

    anim_init(parent, pet, PET_SPRITE_X, PET_SPRITE_Y, PET_SPRITE_SCALE);

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

    /* Speech bubble, top right over the character's shoulder, hidden until an AI session waits. Created before the
     * stats overlay so the overlay draws above it. */
    s_bubble = lv_obj_create(parent);
    lv_obj_remove_style_all(s_bubble);
    lv_obj_set_width(s_bubble, PET_BUBBLE_W);
    lv_obj_set_height(s_bubble, LV_SIZE_CONTENT);
    lv_obj_align(s_bubble, LV_ALIGN_TOP_RIGHT, -20, 104);
    lv_obj_set_style_bg_color(s_bubble, PANEL_COLOR, 0);
    lv_obj_set_style_bg_opa(s_bubble, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(s_bubble, 24, 0);
    lv_obj_set_style_border_width(s_bubble, 3, 0);
    lv_obj_set_style_border_color(s_bubble, ACCENT_COLOR, 0);
    lv_obj_set_style_pad_all(s_bubble, 18, 0);
    lv_obj_set_style_pad_row(s_bubble, 6, 0);
    lv_obj_set_flex_flow(s_bubble, LV_FLEX_FLOW_COLUMN);
    lv_obj_remove_flag(s_bubble, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    s_bubble_who = bubble_line(s_bubble, &lv_font_montserrat_24, ACCENT_COLOR, 1);
    s_bubble_what = bubble_line(s_bubble, &lv_font_montserrat_24, INK_COLOR, 1);
    s_bubble_title = bubble_line(s_bubble, &lv_font_montserrat_16, DIM_COLOR, 2);
    s_bubble_count = bubble_line(s_bubble, &lv_font_montserrat_16, DIM_COLOR, 1);
    show(s_bubble, false);

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
            anim_play(PET_REACT_CELEBRATE, true);
            apply_hold(); /* keep the screen on until the tap */
        } else {
            ESP_LOGI(TAG, "%s -> celebrate", (s->events & PET_EVENT_BIG_DAY) ? "big day" : "goal hit");
            banner((s->events & PET_EVENT_BIG_DAY) ? "BIG DAY!" : "daily goal hit", GOOD_COLOR, false);
            anim_play(PET_REACT_CELEBRATE, false);
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
    s_sleeping = sleeping;
    apply_ink();
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
    s_banner_sticky = false;
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
    apply_hold();
}

static lv_color_t source_color(const char *source)
{
    if (!strcmp(source, "claude")) {
        return lv_color_hex(PET_COLOR_CLAUDE);
    }
    if (!strcmp(source, "codex")) {
        return lv_color_hex(PET_COLOR_CODEX);
    }
    if (!strcmp(source, "local")) {
        return lv_color_hex(PET_COLOR_LOCAL);
    }
    return lv_color_hex(PET_COLOR_OTHER);
}

static const char *kind_text(const char *kind)
{
    if (!strcmp(kind, "needs_input")) {
        return "needs your input";
    }
    if (!strcmp(kind, "done")) {
        return "finished";
    }
    if (!strcmp(kind, "error")) {
        return "hit an error";
    }
    return kind[0] ? kind : "is waiting";
}

void face_set_notice(const pet_notice_t *n, int pos, int count, int held, bool announce)
{
    s_notice_on = true;
    s_notice_color = source_color(n->source);
    lv_label_set_text(s_bubble_who, n->session);
    lv_obj_set_style_text_color(s_bubble_who, s_notice_color, 0);
    lv_label_set_text_fmt(s_bubble_what, "%s %s", n->source, kind_text(n->kind));
    lv_obj_set_style_text_color(s_bubble_what, strcmp(n->kind, "error") == 0 ? WARN_COLOR : INK_COLOR, 0);
    lv_label_set_text(s_bubble_title, n->title);
    show(s_bubble_title, n->title[0] != '\0');
    if (count > 1 && held > 1) {
        lv_label_set_text_fmt(s_bubble_count, "%d of %d waiting. Tap here for the next", pos + 1, count);
    } else if (count > 1) { /* the box holds one of several (taps while the bridge was away): nothing to cycle to */
        lv_label_set_text_fmt(s_bubble_count, "%d of %d waiting", pos + 1, count);
    }
    show(s_bubble_count, count > 1);
    lv_obj_set_style_border_color(s_bubble, s_notice_color, 0);
    show(s_bubble, true);
    apply_ink();
    if (announce) {
        ESP_LOGI(TAG, "notice: %s %s %s%s", n->source, n->session, n->kind,
                 s_banner_sticky ? " (level-up celebration keeps playing)" : "");
        if (!s_banner_sticky) { /* the notice reaction would end the until-tap celebration loop */
            anim_play(PET_REACT_NOTICE, false);
        }
    }
    apply_hold();
}

bool face_point_in_bubble(lv_point_t p)
{
    if (!s_notice_on || !lv_obj_has_flag(s_stats, LV_OBJ_FLAG_HIDDEN)) {
        return false; /* the stats overlay draws above the bubble: while it is up, a tap anywhere closes it */
    }
    lv_obj_update_layout(s_bubble); /* else the coords are from the last refresh; a bubble line may have come or gone */
    lv_area_t a;
    lv_obj_get_coords(s_bubble, &a);
    return p.x >= a.x1 && p.x <= a.x2 && p.y >= a.y1 && p.y <= a.y2;
}

void face_clear_notice(void)
{
    if (!s_notice_on) {
        return; /* an empty list again (reconnect replay, bridge start): nothing to undo, and not activity */
    }
    ESP_LOGI(TAG, "no session waiting");
    s_notice_on = false;
    show(s_bubble, false);
    apply_ink();
    apply_hold();
}
