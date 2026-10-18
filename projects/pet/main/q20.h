#pragma once
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* 20 Questions (#78): the pet asks yes-or-no questions from a model on the bridge, opened from the play menu. All
 * functions with the BSP display lock held. */
void q20_create(lv_obj_t *parent, const pet_def_t *pet);  /* after says_create */
void q20_open(void);
void q20_on_msg(const char *payload, size_t len);         /* a fresh (not retained) pet/q20/ask message */
