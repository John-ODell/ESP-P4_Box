#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* The updater feed (#92): what was opened, merged, closed and committed on GitHub, from the bridge's retained pet/feed
 * (bridge/petbrain/feed.py). It is the Updates tab of the list screen (todo.c), which creates and shows it. All
 * functions with the BSP display lock held. */
lv_obj_t *feed_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb);   /* the scrolling container */
void feed_draw(void);                                    /* the ages move on: draw again when the tab is shown */
const char *feed_summary(void);                          /* the header line under the tabs */
void feed_on_msg(const char *payload, size_t len);       /* a pet/feed message, retained or fresh */
