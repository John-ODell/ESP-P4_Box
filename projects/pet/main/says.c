/* Pet Says (#65). Four pads around the pet, drawn small in the middle: the pet shows a sequence, one step longer each
 * round, and the player taps it back. A wrong tap ends the game: the pet mopes, then the score and best score show
 * (best kept in NVS by play.c), with a celebration for a new best; every finished game adds a little happiness.
 * Close, or PET_SAYS_IDLE_MS without a touch while it waits for the player, returns to the pet without a score. The
 * overlay takes every touch, so nothing here acknowledges an AI session; MQTT and notices update the face beneath. */
#include "says.h"
#include "anim.h"
#include "play.h"
#include "pet_config.h"
#include "sched.h"
#include "esp_log.h"
#include "esp_random.h"

static const char *TAG = "says";

#define C(name) lv_color_hex(s_pet->colors.name)
#define MAX_STEPS 64

enum { PAD_TOP, PAD_RIGHT, PAD_BOTTOM, PAD_LEFT, PADS };
static const lv_area_t PAD_AREA[PADS] = {
    {120, 16, 599, 209},    /* top */
    {520, 226, 703, 493},   /* right */
    {120, 510, 599, 703},   /* bottom */
    {16, 226, 199, 493},    /* left */
};
#define PET_X     240       /* the pet at scale 5 (240x200) between the pads */
#define PET_Y     262
#define PET_SCALE 5

typedef enum { ST_IDLE, ST_SHOW, ST_INPUT, ST_OVER } state_t;

static const pet_def_t *s_pet;
static lv_obj_t *s_root, *s_pad[PADS], *s_status, *s_score, *s_result, *s_result_text;
static lv_timer_t *s_timer, *s_idle;
static state_t s_state;
static uint8_t s_seq[MAX_STEPS];
static int s_len, s_pos;           /* sequence length; next step to show or to be tapped */
static bool s_lit;                 /* during ST_SHOW: a pad is lit now */
static int s_sprite_index;         /* the pet's place among the screen's children, to put it back */

static void light(int pad, bool on)
{
    lv_obj_set_style_bg_opa(s_pad[pad], on ? LV_OPA_COVER : LV_OPA_30, 0);
}

static int step_ms(void)   /* how long a step stays lit: quicker as the sequence grows, never below 280 ms */
{
    int ms = 650 - 30 * s_len;
    return ms < 280 ? 280 : ms;
}

static void close_game(const char *why)
{
    if (s_state == ST_IDLE) {
        return;
    }
    ESP_LOGI(TAG, "close: %s (length %d)", why, s_len);
    s_state = ST_IDLE;
    lv_timer_pause(s_timer);
    lv_timer_pause(s_idle);
    for (int i = 0; i < PADS; i++) {
        light(i, false);
    }
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_result, LV_OBJ_FLAG_HIDDEN);
    anim_set_view(PET_SPRITE_X, PET_SPRITE_Y, PET_SPRITE_SCALE);
    lv_obj_move_to_index(anim_obj(), s_sprite_index);
    sched_hold(SCHED_HOLD_GAME, false);
}

static void next_round(void)
{
    if (s_len < MAX_STEPS) {
        s_seq[s_len++] = esp_random() % PADS;
    }
    s_pos = 0;
    s_lit = false;
    s_state = ST_SHOW;
    lv_label_set_text_fmt(s_status, "round %d: watch", s_len);
    lv_timer_pause(s_idle);
    lv_timer_set_period(s_timer, 700);   /* a breath before the first step */
    lv_timer_reset(s_timer);
    lv_timer_resume(s_timer);
}

/* Drives the show: each step lit for step_ms(), dark for half that, then the player's turn. Also ends a tapped flash
 * and paces the pause between rounds. */
static void timer_cb(lv_timer_t *t)
{
    switch (s_state) {
    case ST_SHOW:
        if (s_lit) {
            light(s_seq[s_pos], false);
            s_lit = false;
            s_pos++;
            lv_timer_set_period(t, step_ms() / 2);
        } else if (s_pos < s_len) {
            light(s_seq[s_pos], true);
            s_lit = true;
            lv_timer_set_period(t, step_ms());
        } else {
            s_state = ST_INPUT;
            s_pos = 0;
            lv_label_set_text_fmt(s_status, "round %d: your turn", s_len);
            lv_timer_pause(t);
            lv_timer_reset(s_idle);
            lv_timer_resume(s_idle);
        }
        break;
    case ST_INPUT:   /* a tapped pad's flash is over */
        for (int i = 0; i < PADS; i++) {
            light(i, false);
        }
        lv_timer_pause(t);
        if (s_pos == s_len) {
            next_round();
        }
        break;
    default:
        lv_timer_pause(t);
        break;
    }
}

static void game_over(int pad)
{
    int score = s_len - 1;   /* rounds repeated in full */
    s_state = ST_OVER;
    lv_timer_pause(s_idle);
    lv_timer_pause(s_timer);
    for (int i = 0; i < PADS; i++) {
        light(i, false);
    }
    light(s_seq[s_pos], true);   /* the pad it should have been */
    int best_before = play_says_best();
    bool best = play_says_done(score);
    ESP_LOGI(TAG, "game over: tapped %d, wanted %d; score %d, best %d%s", pad, s_seq[s_pos], score,
             play_says_best(), best ? " (new)" : "");
    if (best) {
        anim_play(PET_REACT_CELEBRATE, false);
        lv_label_set_text_fmt(s_result_text, "New best!\nscore %d   (was %d)", score, best_before);
    } else {
        anim_play_named("sad/mope", PET_REACT_NOTICE);
        lv_label_set_text_fmt(s_result_text, "score %d\nbest %d", score, play_says_best());
    }
    lv_label_set_text(s_status, "");
    lv_label_set_text_fmt(s_score, "best %d", play_says_best());
    lv_obj_remove_flag(s_result, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_result);   /* over the bottom pad, clear of the pet so its mope shows */
}

static void pad_cb(lv_event_t *e)
{
    int pad = (int)(intptr_t)lv_event_get_user_data(e);
    sched_touch();
    if (s_state != ST_INPUT || s_pos >= s_len) {
        return;   /* watching, between rounds, or over: taps do nothing */
    }
    lv_timer_reset(s_idle);
    if (pad != s_seq[s_pos]) {
        game_over(pad);
        return;
    }
    s_pos++;
    for (int i = 0; i < PADS; i++) {
        light(i, i == pad);
    }
    if (s_pos == s_len) {
        lv_label_set_text_fmt(s_status, "round %d: got it", s_len);
        lv_timer_pause(s_idle);
    }
    lv_timer_set_period(s_timer, s_pos == s_len ? 700 : 220);   /* the last tap holds a little longer */
    lv_timer_reset(s_timer);
    lv_timer_resume(s_timer);
}

static void idle_cb(lv_timer_t *t)
{
    (void)t;
    close_game("idle");
}

static void poke_cb(lv_event_t *e)
{
    (void)e;
    sched_touch();
    if (s_state == ST_OVER) {
        lv_timer_reset(s_idle);
    }
}

static void again_cb(lv_event_t *e)
{
    (void)e;
    lv_obj_add_flag(s_result, LV_OBJ_FLAG_HIDDEN);
    for (int i = 0; i < PADS; i++) {
        light(i, false);   /* the pad game_over showed */
    }
    lv_label_set_text_fmt(s_score, "best %d", play_says_best());
    s_len = 0;
    next_round();
}

static void close_cb(lv_event_t *e)
{
    (void)e;
    close_game("Close");
}

static lv_obj_t *text_button(lv_obj_t *parent, const char *text, lv_event_cb_t cb)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_style_bg_color(b, C(background), 0);
    lv_obj_set_style_bg_color(b, C(accent), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(b, C(accent), 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_radius(b, 18, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_set_style_text_font(b, &lv_font_montserrat_24, 0);   /* on the button: the label never gets PRESSED */
    lv_obj_set_style_text_color(b, C(ink), 0);
    lv_obj_set_style_text_color(b, C(background), LV_STATE_PRESSED);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, poke_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);
    return b;
}

void says_create(lv_obj_t *parent, const pet_def_t *pet)
{
    s_pet = pet;
    s_root = lv_obj_create(parent);
    lv_obj_remove_style_all(s_root);
    lv_obj_set_size(s_root, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(s_root, C(background), 0);
    lv_obj_set_style_bg_opa(s_root, LV_OPA_COVER, 0);
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_HIDDEN);   /* takes every touch while open */
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_root, poke_cb, LV_EVENT_PRESSED, NULL);

    for (int i = 0; i < PADS; i++) {
        const lv_area_t *a = &PAD_AREA[i];
        lv_obj_t *p = lv_obj_create(s_root);
        lv_obj_remove_style_all(p);
        lv_obj_set_pos(p, a->x1, a->y1);
        lv_obj_set_size(p, a->x2 - a->x1 + 1, a->y2 - a->y1 + 1);
        lv_obj_set_style_radius(p, 28, 0);
        lv_obj_set_style_bg_color(p, lv_color_hex(pet->pads[i]), 0);
        lv_obj_set_style_border_color(p, lv_color_hex(pet->pads[i]), 0);
        lv_obj_set_style_border_width(p, 4, 0);
        lv_obj_add_flag(p, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(p, LV_OBJ_FLAG_GESTURE_BUBBLE | LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_event_cb(p, pad_cb, LV_EVENT_PRESSED, (void *)(intptr_t)i);   /* on finger-down: snappier */
        s_pad[i] = p;
        light(i, false);
    }

    s_score = lv_label_create(s_root);
    lv_obj_set_style_text_font(s_score, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_score, C(dim), 0);
    lv_obj_align(s_score, LV_ALIGN_TOP_MID, 0, 220);
    s_status = lv_label_create(s_root);
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_status, C(ink), 0);
    lv_obj_align(s_status, LV_ALIGN_TOP_MID, 0, 470);

    lv_obj_t *x = text_button(s_root, LV_SYMBOL_CLOSE, close_cb);   /* top-right corner, clear of the pads */
    lv_obj_set_size(x, 88, 88);
    lv_obj_set_pos(x, 616, 16);

    /* The result card is the screen's child, not the game's, so it can sit above the pet's sprite. */
    s_result = lv_obj_create(parent);
    lv_obj_set_size(s_result, 600, LV_SIZE_CONTENT);
    lv_obj_align(s_result, LV_ALIGN_BOTTOM_MID, 0, -16);
    lv_obj_set_style_bg_color(s_result, C(panel), 0);
    lv_obj_set_style_bg_opa(s_result, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(s_result, C(accent), 0);
    lv_obj_set_style_border_width(s_result, 3, 0);
    lv_obj_set_style_radius(s_result, 24, 0);
    lv_obj_set_style_pad_all(s_result, 24, 0);
    lv_obj_set_style_pad_row(s_result, 16, 0);
    lv_obj_set_flex_flow(s_result, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_result, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_remove_flag(s_result, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(s_result, poke_cb, LV_EVENT_PRESSED, NULL);
    s_result_text = lv_label_create(s_result);
    lv_obj_set_style_text_font(s_result_text, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(s_result_text, C(ink), 0);
    lv_obj_set_style_text_align(s_result_text, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_t *row = lv_obj_create(s_result);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), 80);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 16, 0);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_t *again = text_button(row, "Play again", again_cb);
    lv_obj_set_height(again, 80);
    lv_obj_set_flex_grow(again, 1);
    lv_obj_t *done = text_button(row, "Close", close_cb);
    lv_obj_set_height(done, 80);
    lv_obj_set_flex_grow(done, 1);
    lv_obj_add_flag(s_result, LV_OBJ_FLAG_HIDDEN);

    s_timer = lv_timer_create(timer_cb, 700, NULL);
    lv_timer_pause(s_timer);
    s_idle = lv_timer_create(idle_cb, PET_SAYS_IDLE_MS, NULL);
    lv_timer_pause(s_idle);
}

void says_open(void)
{
    if (s_state != ST_IDLE) {
        return;
    }
    ESP_LOGI(TAG, "open, best %d", play_says_best());
    lv_label_set_text_fmt(s_score, "best %d", play_says_best());
    lv_obj_add_flag(s_result, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_root);
    lv_obj_t *sprite = anim_obj();   /* the pet plays along, small, above the game's background */
    s_sprite_index = lv_obj_get_index(sprite);
    lv_obj_move_foreground(sprite);
    anim_set_view(PET_X, PET_Y, PET_SCALE);
    sched_hold(SCHED_HOLD_GAME, true);
    s_len = 0;
    next_round();
}

bool says_is_open(void)
{
    return s_state != ST_IDLE;
}
