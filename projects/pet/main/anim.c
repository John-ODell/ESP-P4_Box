/* Sprite player: idle animations drawn by weight per mood, reactions that preempt them, a sleep loop. */
#include <stdlib.h>
#include <string.h>
#include "anim.h"
#include "esp_log.h"
#include "esp_random.h"

static const char *TAG = "anim";

static const pet_def_t *s_pet;
static lv_obj_t *s_img;
static lv_timer_t *s_timer;
static int s_x, s_y, s_scale;
static int s_mood = PET_MOOD_NEUTRAL;
static bool s_sleeping;

static int s_anim = -1;        /* current animation or -1 when resting on the base frame */
static int s_frame;
static bool s_reaction;        /* current animation is a reaction (not idle) */
static bool s_loop;            /* reaction loops until acked */
static int s_repeat;           /* remaining extra plays of a non-looping reaction */
static int s_pet_anim;         /* what petting plays: the pet's happy wiggle, else its celebrate */

static int s_img_id, s_dx, s_dy;   /* what is on screen, to redraw it when the view changes */

static void show_image(int img, int dx, int dy)
{
    s_img_id = img;
    s_dx = dx;
    s_dy = dy;
    lv_image_set_src(s_img, s_pet->images[img]);
    lv_obj_set_pos(s_img, s_x + dx * s_scale, s_y + dy * s_scale);
}

static void rest(void)
{
    s_anim = -1;
    show_image(s_pet->rest[s_mood], 0, 0);
    /* Pause on the resting face before the next idle animation, 0.8 .. 3.0 s. */
    lv_timer_set_period(s_timer, 800 + (esp_random() % 2200));
    lv_timer_reset(s_timer);
}

static int pick_idle(void)
{
    const pet_idle_choice_t *set = s_pet->idle[s_mood];
    int n = s_pet->idle_len[s_mood], total = 0;
    for (int i = 0; i < n; i++) {
        total += set[i].weight;
    }
    int r = esp_random() % total;
    for (int i = 0; i < n; i++) {
        if (r < set[i].weight) {
            return set[i].anim;
        }
        r -= set[i].weight;
    }
    return set[0].anim;
}

static void start(int anim, bool reaction)
{
    s_anim = anim;
    s_frame = 0;
    s_reaction = reaction;
    const pet_frame_t *f = &s_pet->anims[anim].frames[0];
    show_image(f->img, f->dx, f->dy);
    lv_timer_set_period(s_timer, f->ms);
    lv_timer_reset(s_timer);
}

static void tick(lv_timer_t *t)
{
    if (s_anim < 0) {
        /* Resting time is over: pick what comes next. */
        if (s_sleeping) {
            start(s_pet->reaction[PET_REACT_SLEEP], true);
        } else {
            start(pick_idle(), false);
        }
        return;
    }
    const pet_anim_t *a = &s_pet->anims[s_anim];
    s_frame++;
    if (s_frame >= a->count) {
        if (s_reaction && s_loop) {
            s_frame = 0;                      /* level-up, notice, sleep: loop until acked or released */
        } else if (s_reaction && s_repeat > 0) {
            s_repeat--;
            s_frame = 0;
        } else if (s_sleeping) {
            s_loop = true;                    /* a reaction that ends at night goes straight back to sleep */
            start(s_pet->reaction[PET_REACT_SLEEP], true);
            return;
        } else {
            rest();
            return;
        }
    }
    const pet_frame_t *f = &a->frames[s_frame];
    show_image(f->img, f->dx, f->dy);
    lv_timer_set_period(s_timer, f->ms);
}

void anim_init(lv_obj_t *parent, const pet_def_t *pet, int x, int y, int scale)
{
    s_pet = pet;
    s_x = x;
    s_y = y;
    s_scale = scale;
    s_img = lv_image_create(parent);
    lv_image_set_pivot(s_img, 0, 0);
    lv_image_set_scale(s_img, 256 * scale);
    lv_image_set_antialias(s_img, false);
    lv_obj_set_style_image_recolor_opa(s_img, LV_OPA_COVER, 0);
    lv_obj_set_style_image_recolor(s_img, lv_color_hex(pet->colors.ink), 0);
    lv_obj_remove_flag(s_img, LV_OBJ_FLAG_CLICKABLE);
    s_timer = lv_timer_create(tick, 1000, NULL);
    s_pet_anim = pet->reaction[PET_REACT_CELEBRATE];
    for (int i = 0; i < pet->anim_count; i++) {
        if (strcmp(pet->anims[i].name, "happy/wiggle") == 0) {
            s_pet_anim = i;
            break;
        }
    }
    rest();
    ESP_LOGI(TAG, "%s: %u frames, %u animations, scale %dx", pet->name, (unsigned)pet->image_count,
             (unsigned)pet->anim_count, scale);
}

void anim_set_ink(lv_color_t color)
{
    lv_obj_set_style_image_recolor(s_img, color, 0);
}

void anim_set_mood_class(int mood_class)
{
    if (mood_class < 0 || mood_class >= PET_MOOD_CLASSES) {
        return;
    }
    s_mood = mood_class;
    if (s_anim < 0 || !s_reaction) {
        rest();     /* swap the resting face now; idle animations pick the new set from here on */
    }
}

void anim_play(int reaction, bool loop_until_ack)
{
    if (reaction < 0 || reaction >= PET_REACTIONS) {
        return;
    }
    int anim_id = s_pet->reaction[reaction];
    s_loop = loop_until_ack;
    s_repeat = loop_until_ack ? 0 : 1;   /* a one-shot reaction plays twice, about 2 s for celebrate */
    ESP_LOGI(TAG, "play %s%s", s_pet->anims[anim_id].name, loop_until_ack ? " (until tap)" : "");
    start(anim_id, true);
}

void anim_set_sleeping(bool sleeping)
{
    if (s_sleeping == sleeping) {
        return;
    }
    s_sleeping = sleeping;
    if (sleeping) {
        if (!(s_reaction && s_loop)) {
            start(s_pet->reaction[PET_REACT_SLEEP], true);
            s_loop = true;
        }
    } else if (s_anim == s_pet->reaction[PET_REACT_SLEEP]) {
        s_loop = false;
        rest();
    }
}

bool anim_ack(void)
{
    if (s_anim >= 0 && s_reaction && s_loop && s_anim != s_pet->reaction[PET_REACT_SLEEP]) {
        s_loop = false;
        rest();
        return true;
    }
    return false;
}

void anim_pet(void)
{
    if (s_anim == s_pet_anim) {
        return;   /* already wiggling: keep going rather than restart on every stroke */
    }
    if (anim_waiting()) {
        return;   /* a level-up or a notice waits for a tap */
    }
    s_loop = false;
    s_repeat = 0;
    start(s_pet_anim, true);
}

void anim_play_named(const char *name, int fallback)
{
    for (int i = 0; i < s_pet->anim_count; i++) {
        if (strcmp(s_pet->anims[i].name, name) == 0) {
            s_loop = false;
            s_repeat = 0;
            ESP_LOGI(TAG, "play %s", name);
            start(i, true);
            return;
        }
    }
    anim_play(fallback, false);
}

void anim_set_view(int x, int y, int scale)
{
    s_x = x;
    s_y = y;
    s_scale = scale;
    lv_image_set_scale(s_img, 256 * scale);
    show_image(s_img_id, s_dx, s_dy);
}

lv_obj_t *anim_obj(void)
{
    return s_img;
}

bool anim_waiting(void)
{
    return s_anim >= 0 && s_reaction && s_loop && s_anim != s_pet->reaction[PET_REACT_SLEEP];
}

bool anim_reaction_active(void)
{
    return s_anim >= 0 && s_reaction;
}
