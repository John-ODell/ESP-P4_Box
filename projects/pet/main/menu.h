#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_def.h"

/* The play menu, opened by a long press. All functions with the BSP display lock held. */
void menu_create(lv_obj_t *parent, const pet_def_t *pet);  /* after face_create, so it draws on top */
void menu_open(void);
void menu_close(const char *why);
bool menu_is_open(void);
