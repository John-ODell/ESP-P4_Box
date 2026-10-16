/* The face scene: a placeholder character drawn with LVGL primitives plus the stats the brief asks for
 * (level, XP bar, today vs goal, streak, mood). Milestone 5 swaps the primitives for pixel-art image arrays. */
#include <stdio.h>
#include "face.h"

#define BG_COLOR     lv_color_hex(0x0b0f14)
#define INK_COLOR    lv_color_hex(0xe6f0f5)
#define DIM_COLOR    lv_color_hex(0x5c6b78)
#define ACCENT_COLOR lv_color_hex(0x4fd1c5)
#define WARN_COLOR   lv_color_hex(0xf6ad55)
#define GOOD_COLOR   lv_color_hex(0x68d391)

static lv_obj_t *s_eye_l, *s_eye_r, *s_brow_l, *s_brow_r;
static lv_obj_t *s_mouth_smile, *s_mouth_flat, *s_mouth_frown;
static lv_obj_t *s_level, *s_xp_bar, *s_xp_text, *s_streak, *s_today, *s_mood, *s_link, *s_status, *s_banner;

static lv_obj_t *blob(lv_obj_t *parent, int w, int h, int x, int y, lv_color_t color, int radius)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_size(o, w, h);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_style_bg_color(o, color, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    return o;
}

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

static lv_obj_t *mouth_arc(lv_obj_t *parent, int x, int y, int a0, int a1, int width)
{
    lv_obj_t *m = lv_arc_create(parent);
    lv_obj_remove_style(m, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(m, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(m, 160, 160);
    lv_obj_set_pos(m, x, y);
    lv_arc_set_bg_angles(m, a0, a1);
    lv_arc_set_value(m, 0);
    lv_obj_set_style_arc_width(m, width, LV_PART_MAIN);
    lv_obj_set_style_arc_color(m, BG_COLOR, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(m, true, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(m, LV_OPA_TRANSP, LV_PART_INDICATOR);
    return m;
}

void face_create(lv_obj_t *parent)
{
    lv_obj_set_style_bg_color(parent, BG_COLOR, 0);
    lv_obj_set_style_bg_opa(parent, LV_OPA_COVER, 0);
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);

    /* Body, snout, fin. */
    blob(parent, 440, 300, 140, 230, INK_COLOR, 150);
    blob(parent, 120, 90, 540, 360, INK_COLOR, 45);
    blob(parent, 80, 110, 320, 150, INK_COLOR, 20);

    /* Eyes with highlights, brows hidden unless worried. */
    s_eye_l = blob(parent, 54, 54, 300, 320, BG_COLOR, 6);
    s_eye_r = blob(parent, 54, 54, 440, 320, BG_COLOR, 6);
    blob(parent, 16, 16, 308, 328, INK_COLOR, 2);
    blob(parent, 16, 16, 448, 328, INK_COLOR, 2);
    s_brow_l = blob(parent, 64, 12, 295, 296, BG_COLOR, 4);
    s_brow_r = blob(parent, 64, 12, 435, 296, BG_COLOR, 4);
    lv_obj_add_flag(s_brow_l, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_brow_r, LV_OBJ_FLAG_HIDDEN);

    /* Three mouths, one visible at a time. */
    s_mouth_smile = mouth_arc(parent, 320, 330, 25, 155, 12);
    s_mouth_frown = mouth_arc(parent, 320, 420, 205, 335, 12);
    s_mouth_flat = blob(parent, 90, 12, 355, 440, BG_COLOR, 6);
    lv_obj_add_flag(s_mouth_frown, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_mouth_flat, LV_OBJ_FLAG_HIDDEN);

    /* Stats around the edges. */
    s_level = label(parent, &lv_font_montserrat_48, ACCENT_COLOR, LV_ALIGN_TOP_LEFT, 24, 16);
    lv_label_set_text(s_level, "LV -");
    s_xp_bar = lv_bar_create(parent);
    lv_obj_set_size(s_xp_bar, 220, 14);
    lv_obj_align(s_xp_bar, LV_ALIGN_TOP_LEFT, 26, 78);
    lv_obj_set_style_bg_color(s_xp_bar, DIM_COLOR, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_xp_bar, ACCENT_COLOR, LV_PART_INDICATOR);
    lv_obj_set_style_radius(s_xp_bar, 7, LV_PART_MAIN);
    lv_obj_set_style_radius(s_xp_bar, 7, LV_PART_INDICATOR);
    lv_bar_set_range(s_xp_bar, 0, 100);
    lv_bar_set_value(s_xp_bar, 0, LV_ANIM_OFF);
    lv_obj_remove_flag(s_xp_bar, LV_OBJ_FLAG_CLICKABLE);
    s_xp_text = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_TOP_LEFT, 26, 98);
    lv_label_set_text(s_xp_text, "waiting for the bridge");

    s_streak = label(parent, &lv_font_montserrat_32, INK_COLOR, LV_ALIGN_TOP_RIGHT, -24, 24);
    s_today = label(parent, &lv_font_montserrat_32, INK_COLOR, LV_ALIGN_BOTTOM_MID, 0, -56);
    s_mood = label(parent, &lv_font_montserrat_24, DIM_COLOR, LV_ALIGN_BOTTOM_LEFT, 24, -20);
    s_link = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_BOTTOM_RIGHT, -24, -24);
    lv_label_set_text(s_link, "booting");
    s_status = label(parent, &lv_font_montserrat_16, DIM_COLOR, LV_ALIGN_BOTTOM_MID, 0, -24);

    s_banner = label(parent, &lv_font_montserrat_32, GOOD_COLOR, LV_ALIGN_TOP_MID, 0, 130);
    lv_obj_add_flag(s_banner, LV_OBJ_FLAG_HIDDEN);
}

static void show(lv_obj_t *o, bool on)
{
    if (on) {
        lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
}

static void set_mood_face(int mood, bool worried)
{
    bool sad = mood <= 2, neutral = mood >= 3 && mood <= 5, ecstatic = mood >= 9;
    show(s_mouth_frown, sad);
    show(s_mouth_flat, neutral);
    show(s_mouth_smile, !sad && !neutral);
    lv_arc_set_bg_angles(s_mouth_smile, ecstatic ? 10 : 25, ecstatic ? 170 : 155);
    lv_obj_set_style_arc_width(s_mouth_smile, ecstatic ? 16 : 12, LV_PART_MAIN);
    /* Sad: droopy half-closed eyes. */
    lv_obj_set_size(s_eye_l, 54, sad ? 30 : 54);
    lv_obj_set_size(s_eye_r, 54, sad ? 30 : 54);
    lv_obj_set_y(s_eye_l, sad ? 334 : 320);
    lv_obj_set_y(s_eye_r, sad ? 334 : 320);
    show(s_brow_l, worried);
    show(s_brow_r, worried);
}

void face_apply_state(const pet_state_t *s, bool celebrate)
{
    char buf[96];
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
    set_mood_face(s->mood, s->streak_at_risk);

    if (celebrate && s->events) {
        const char *txt = (s->events & PET_EVENT_LEVEL_UP) ? "LEVEL UP!  tap to continue"
                          : (s->events & PET_EVENT_BIG_DAY) ? "BIG DAY!" : "daily goal hit";
        lv_label_set_text(s_banner, txt);
        show(s_banner, true);
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

void face_ack(void)
{
    show(s_banner, false);
}
