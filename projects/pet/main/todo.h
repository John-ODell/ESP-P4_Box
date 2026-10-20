#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* The to-do list (#90): the bridge's list from the retained pet/todo (#89), opened by a left or right swipe from the
 * pet. A tap on an item ticks or unticks it at once and sends pet/todo/tick. All functions with the BSP display lock
 * held. */
void todo_create(lv_obj_t *parent, const pet_def_t *pet);  /* after q20_create, so it draws over everything */
void todo_open(void);
void todo_on_msg(const char *payload, size_t len);         /* a pet/todo message, retained or fresh */
void todo_feed_changed(void);                              /* after feed_on_msg: the header line follows the feed */
