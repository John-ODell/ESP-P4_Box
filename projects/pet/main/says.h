#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_def.h"

/* Pet Says (#65): a Simon-style memory game over the face, opened from the play menu. All functions with the BSP
 * display lock held. */
void says_create(lv_obj_t *parent, const pet_def_t *pet);  /* after menu_create */
void says_open(void);
