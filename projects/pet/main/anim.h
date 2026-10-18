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
bool anim_waiting(void);                           /* a reaction (the level-up) loops until a tap */
void anim_pet(void);                               /* stroked: a happy wiggle once, unless a level-up or notice is
                                                      looping */
void anim_set_ink(lv_color_t color);
void anim_play_named(const char *name, int fallback); /* one animation by its pack name, once, as a reaction; the
                                                         PET_REACT_* fallback if the pet has none by that name */
void anim_set_view(int x, int y, int scale);      /* move and resize the pet (Pet Says draws it small) */
lv_obj_t *anim_obj(void);                         /* the sprite, for stacking order */
