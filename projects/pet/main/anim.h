#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_def.h"

/* Sprite player for the character. All calls with the BSP display lock held. */
void anim_init(lv_obj_t *parent, const pet_def_t *pet, int x, int y, int scale);
void anim_set_mood_class(int mood_class);          /* PET_MOOD_* ; picks the idle set and resting frame */
void anim_play(int reaction, bool loop_until_ack); /* PET_REACT_*; preempts idle */
void anim_set_sleeping(bool sleeping);             /* night mode: loop the sleep animation */
bool anim_ack(void);                               /* tap: stop a looping reaction; true if one was running */
bool anim_reaction_active(void);
void anim_set_ink(lv_color_t color);
