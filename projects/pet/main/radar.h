#pragma once
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* The contribution radar (#105): the four counts GitHub's profile radar is drawn from, from the bridge's retained
 * pet/radar (bridge/petbrain/radar.py). It is the Radar tab of the list screen (todo.c), which creates and shows it.
 * All functions with the BSP display lock held. */
lv_obj_t *radar_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb);   /* the page */
void radar_draw(void);
const char *radar_summary(void);                         /* the header line under the tabs */
void radar_on_msg(const char *payload, size_t len);      /* a pet/radar message, retained or fresh */
