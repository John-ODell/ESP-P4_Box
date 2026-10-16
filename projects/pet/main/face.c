/* Hello-face: a placeholder character drawn with LVGL primitives.
 * Milestone 5 replaces this with generated pixel-art image arrays. */
#include "face.h"

#define BG_COLOR     lv_color_hex(0x0b0f14)
#define INK_COLOR    lv_color_hex(0xe6f0f5)
#define ACCENT_COLOR lv_color_hex(0x4fd1c5)

static lv_obj_t *s_status;

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

void face_create(lv_obj_t *parent)
{
    lv_obj_set_style_bg_color(parent, BG_COLOR, 0);
    lv_obj_set_style_bg_opa(parent, LV_OPA_COVER, 0);
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);

    /* Head: a wide rounded body, slightly below centre, lots of empty space around it. */
    blob(parent, 440, 300, 140, 230, INK_COLOR, 150);
    /* Beak / snout hint on the right side. */
    blob(parent, 120, 90, 540, 360, INK_COLOR, 45);
    /* Dorsal fin. */
    blob(parent, 80, 110, 320, 150, INK_COLOR, 20);

    /* Eyes: dark on light, chunky like a 1-bit sprite scaled up. */
    blob(parent, 54, 54, 300, 320, BG_COLOR, 6);
    blob(parent, 54, 54, 440, 320, BG_COLOR, 6);
    /* Eye highlights. */
    blob(parent, 16, 16, 308, 328, INK_COLOR, 2);
    blob(parent, 16, 16, 448, 328, INK_COLOR, 2);

    /* Mouth: a short arc. */
    lv_obj_t *mouth = lv_arc_create(parent);
    lv_obj_remove_style(mouth, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(mouth, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(mouth, 160, 160);
    lv_obj_set_pos(mouth, 320, 330);
    lv_arc_set_bg_angles(mouth, 25, 155);
    lv_arc_set_value(mouth, 0);
    lv_obj_set_style_arc_width(mouth, 12, LV_PART_MAIN);
    lv_obj_set_style_arc_color(mouth, BG_COLOR, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(mouth, true, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(mouth, LV_OPA_TRANSP, LV_PART_INDICATOR);

    /* Title and status, small, out of the way. */
    lv_obj_t *title = lv_label_create(parent);
    lv_label_set_text(title, "pet  ·  hello-face");
    lv_obj_set_style_text_color(title, ACCENT_COLOR, 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 24, 20);

    s_status = lv_label_create(parent);
    lv_label_set_text(s_status, "tap me");
    lv_obj_set_style_text_color(s_status, INK_COLOR, 0);
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_32, 0);
    lv_obj_align(s_status, LV_ALIGN_BOTTOM_MID, 0, -40);
}

void face_set_status(const char *text)
{
    if (s_status) {
        lv_label_set_text(s_status, text);
    }
}
