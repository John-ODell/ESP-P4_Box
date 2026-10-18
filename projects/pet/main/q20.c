/* 20 Questions (#78). The person thinks of something and the pet asks yes-or-no questions. The bridge gets each turn
 * from a model (bridge/petbrain/q20.py) and sends it on pet/q20/ask; the box sends every tap back on pet/q20. A
 * question and a guess each use one of 20 turns. Right ends the game with a celebration, Wrong on the last turn with a
 * mope, and either way a finished game adds a little happiness. The box only shows text and sends taps: it never holds
 * an API key. Close, PET_Q20_IDLE_MS without a touch, or PET_Q20_REPLY_MS without word from the bridge ends it, so
 * nothing hangs. As in Pet Says the overlay takes every touch, so nothing here acknowledges an AI session. */
#include "q20.h"
#include <stdio.h>
#include <string.h>
#include "anim.h"
#include "cJSON.h"
#include "face.h"
#include "mqtt_link.h"
#include "play.h"
#include "pet_config.h"
#include "sched.h"
#include "esp_log.h"
#include "esp_random.h"

static const char *TAG = "q20";

#define C(name) lv_color_hex(s_pet->colors.name)
#define TURNS     20
#define PET_X     264       /* the pet at scale 4 (192x160) above the question */
#define PET_Y     96
#define PET_SCALE 4
#define TEXT_MAX  160       /* the bridge sends at most 120 characters; anything longer is cut */

typedef enum { ST_IDLE, ST_WAIT, ST_ASK, ST_GUESS, ST_OVER } state_t;
enum { BTN_A, BTN_B, BTN_C, BTNS };

static const pet_def_t *s_pet;
static lv_obj_t *s_root, *s_turn, *s_text, *s_btn[BTNS], *s_btn_label[BTNS];
static lv_timer_t *s_idle, *s_reply;
static state_t s_state;
static uint32_t s_game;   /* this game's number, so a late reply to an earlier game is ignored */
static int s_n;           /* the turn on screen; while waiting, the one before the turn asked for */
static bool s_look_left;  /* the pet looks one way, then the other, while it thinks */
static int s_sprite_index;

static bool send(const char *op, const char *answer)
{
    char buf[96];
    if (answer) {
        snprintf(buf, sizeof buf, "{\"game\":%lu,\"op\":\"%s\",\"n\":%d,\"a\":\"%s\"}", (unsigned long)s_game, op, s_n,
                 answer);
    } else {
        snprintf(buf, sizeof buf, "{\"game\":%lu,\"op\":\"%s\"}", (unsigned long)s_game, op);
    }
    bool queued = mqtt_link_publish(PET_TOPIC_Q20, buf, false);
    ESP_LOGI(TAG, "%s %s", buf, queued ? "sent" : "NOT sent");
    return queued;
}

/* Up to three buttons in the bottom row; NULL hides one and the others share its room. */
static void buttons(const char *a, const char *b, const char *c)
{
    const char *text[BTNS] = {a, b, c};
    for (int i = 0; i < BTNS; i++) {
        if (text[i]) {
            lv_label_set_text(s_btn_label[i], text[i]);
            lv_obj_remove_flag(s_btn[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(s_btn[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
}

/* Waiting on the bridge, only the reply timer runs; waiting on the person, only the idle timer. */
static void timers(bool waiting)
{
    lv_timer_t *on = waiting ? s_reply : s_idle;
    lv_timer_pause(waiting ? s_idle : s_reply);
    lv_timer_reset(on);
    lv_timer_resume(on);
}

static void react(int reaction, const char *name)
{
    if (anim_waiting()) {
        return;   /* a level-up loop waits for a tap after the game: leave it be */
    }
    if (name) {
        anim_play_named(name, reaction);
    } else {
        anim_play(reaction, false);
    }
}

static void wait_for_bridge(const char *text)
{
    s_state = ST_WAIT;
    lv_label_set_text(s_text, text);
    buttons(NULL, NULL, NULL);
    timers(true);
    s_look_left = !s_look_left;
    react(PET_REACT_NOTICE, s_look_left ? "neutral/look_left" : "neutral/look_right");
}

static void game_over(const char *text, bool finished)
{
    s_state = ST_OVER;
    lv_label_set_text(s_text, text);
    buttons("Play again", "Close", NULL);
    timers(false);
    if (finished) {
        play_add(PET_Q20_HAPPY, "20 questions");
        face_refresh_play();
    }
}

static void show_error(const char *why)
{
    char buf[TEXT_MAX];
    snprintf(buf, sizeof buf, "%s", why);
    ESP_LOGW(TAG, "game %lu: %s", (unsigned long)s_game, buf);
    lv_label_set_text(s_turn, "Something went wrong");
    game_over(buf, false);
}

static void start_game(void)
{
    s_game = (esp_random() & 0x7fffffff) | 1;   /* positive and never 0, so it reads the same as JSON on both sides */
    s_n = 0;
    lv_label_set_text(s_turn, "20 Questions");
    if (!send("start", NULL)) {
        show_error("Not connected to the bridge, so there is no one to ask the questions.");
        return;
    }
    wait_for_bridge("Think of something: an animal, a thing, a place, anything. I'll try to guess it!");
}

static void close_game(const char *why)
{
    if (s_state == ST_IDLE) {
        return;
    }
    ESP_LOGI(TAG, "close: %s (turn %d)", why, s_n);
    if (s_state != ST_OVER) {
        send("quit", NULL);   /* the bridge forgets the game; harmless if it never hears */
    }
    s_state = ST_IDLE;
    lv_timer_pause(s_idle);
    lv_timer_pause(s_reply);
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    anim_set_view(PET_SPRITE_X, PET_SPRITE_Y, PET_SPRITE_SCALE);
    lv_obj_move_to_index(anim_obj(), s_sprite_index);
    sched_hold(SCHED_HOLD_GAME, false);
}

static void answer(const char *a)
{
    if (!send("answer", a)) {
        show_error("Lost the connection to the bridge.");
        return;
    }
    wait_for_bridge("Hmm...");
}

static void btn_cb(lv_event_t *e)
{
    static const char *const ANSWER[BTNS] = {"yes", "no", "unsure"};
    int b = (int)(intptr_t)lv_event_get_user_data(e);
    switch (s_state) {
    case ST_ASK:
        answer(ANSWER[b]);
        break;
    case ST_GUESS:
        if (b == BTN_A) {
            send("answer", "right");   /* the bridge ends its side; the box does not wait for it */
            ESP_LOGI(TAG, "guessed it on turn %d", s_n);
            char buf[48];
            snprintf(buf, sizeof buf, "I got it in %d!", s_n);
            game_over(buf, true);
            react(PET_REACT_CELEBRATE, NULL);
        } else if (s_n >= TURNS) {
            send("answer", "wrong");
            ESP_LOGI(TAG, "out of turns");
            game_over("You stumped me! I give up.", true);
            react(PET_REACT_NOTICE, "sad/mope");
        } else {
            answer("wrong");
        }
        break;
    case ST_OVER:
        if (b == BTN_A) {
            start_game();
        } else {
            close_game("Close");
        }
        break;
    default:
        break;
    }
}

static void poke_cb(lv_event_t *e)
{
    (void)e;
    sched_touch();
    if (s_state == ST_ASK || s_state == ST_GUESS || s_state == ST_OVER) {
        lv_timer_reset(s_idle);
    }
}

static void close_cb(lv_event_t *e)
{
    (void)e;
    close_game("Close");
}

static void idle_cb(lv_timer_t *t)
{
    (void)t;
    close_game("idle");
}

static void reply_cb(lv_timer_t *t)
{
    (void)t;
    send("quit", NULL);
    show_error("No answer from the bridge. Is it running?");
}

void q20_on_msg(const char *payload, size_t len)
{
    if (s_state != ST_WAIT) {
        return;   /* closed, or a repeat of a turn already on screen */
    }
    cJSON *j = cJSON_ParseWithLength(payload, len);
    if (!j) {
        return;
    }
    const cJSON *game = cJSON_GetObjectItem(j, "game"), *n = cJSON_GetObjectItem(j, "n");
    const cJSON *q = cJSON_GetObjectItem(j, "q"), *guess = cJSON_GetObjectItem(j, "guess");
    const cJSON *err = cJSON_GetObjectItem(j, "error");
    char buf[TEXT_MAX];
    if (!cJSON_IsNumber(game) || (uint32_t)game->valuedouble != s_game) {
        ;   /* another game's reply */
    } else if (cJSON_IsString(err)) {
        show_error(err->valuestring);
    } else if (cJSON_IsNumber(n) && n->valueint == s_n + 1 && n->valueint <= TURNS &&
               (cJSON_IsString(q) || cJSON_IsString(guess))) {
        s_n = n->valueint;
        if (cJSON_IsString(guess)) {
            s_state = ST_GUESS;
            snprintf(buf, sizeof buf, "Is it %s?", guess->valuestring);
            lv_label_set_text_fmt(s_turn, "Guess, turn %d of %d", s_n, TURNS);
            buttons("Right!", "Wrong", NULL);
        } else {
            s_state = ST_ASK;
            snprintf(buf, sizeof buf, "%s", q->valuestring);
            lv_label_set_text_fmt(s_turn, "Question %d of %d", s_n, TURNS);
            buttons("Yes", "No", "Not sure");
        }
        lv_label_set_text(s_text, buf);
        timers(false);
        ESP_LOGI(TAG, "turn %d: %s", s_n, buf);
    }
    cJSON_Delete(j);
}

static lv_obj_t *text_button(lv_obj_t *parent, const char *text, lv_event_cb_t cb, int id, lv_obj_t **label)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_style_bg_color(b, C(background), 0);
    lv_obj_set_style_bg_color(b, C(accent), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(b, C(accent), 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_radius(b, 18, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_set_style_text_font(b, &lv_font_montserrat_32, 0);   /* on the button: the label never gets PRESSED */
    lv_obj_set_style_text_color(b, C(ink), 0);
    lv_obj_set_style_text_color(b, C(background), LV_STATE_PRESSED);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, poke_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, (void *)(intptr_t)id);
    if (label) {
        *label = l;
    }
    return b;
}

void q20_create(lv_obj_t *parent, const pet_def_t *pet)
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

    s_turn = lv_label_create(s_root);
    lv_obj_set_style_text_font(s_turn, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_turn, C(dim), 0);
    lv_obj_set_pos(s_turn, 32, 46);

    s_text = lv_label_create(s_root);   /* the question, between the pet and the buttons */
    lv_obj_set_width(s_text, 656);
    lv_obj_set_pos(s_text, 32, 284);
    lv_label_set_long_mode(s_text, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_font(s_text, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(s_text, C(ink), 0);
    lv_obj_set_style_text_align(s_text, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(s_text, 6, 0);

    lv_obj_t *row = lv_obj_create(s_root);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, 656, 150);
    lv_obj_set_pos(row, 32, 538);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 16, 0);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_GESTURE_BUBBLE);
    for (int i = 0; i < BTNS; i++) {
        s_btn[i] = text_button(row, "", btn_cb, i, &s_btn_label[i]);
        lv_obj_set_height(s_btn[i], 150);
        lv_obj_set_flex_grow(s_btn[i], 1);
    }

    lv_obj_t *x = text_button(s_root, LV_SYMBOL_CLOSE, close_cb, 0, NULL);   /* top-right corner */
    lv_obj_set_size(x, 88, 88);
    lv_obj_set_pos(x, 616, 16);

    s_idle = lv_timer_create(idle_cb, PET_Q20_IDLE_MS, NULL);
    lv_timer_pause(s_idle);
    s_reply = lv_timer_create(reply_cb, PET_Q20_REPLY_MS, NULL);
    lv_timer_pause(s_reply);
}

void q20_open(void)
{
    if (s_state != ST_IDLE) {
        return;
    }
    ESP_LOGI(TAG, "open");
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_root);
    lv_obj_t *sprite = anim_obj();   /* the pet asks, small, above the game's background */
    s_sprite_index = lv_obj_get_index(sprite);
    lv_obj_move_foreground(sprite);
    anim_set_view(PET_X, PET_Y, PET_SCALE);
    sched_hold(SCHED_HOLD_GAME, true);
    start_game();
}
