#pragma once
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* The CI tab (#107): the latest checks on your recently pushed repos, from the bridge's retained pet/ci
 * (bridge/petbrain/ci.py). The list screen (todo.c) creates and shows it. All functions with the BSP display lock
 * held. */
lv_obj_t *ci_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb);   /* the scrolling page */
void ci_draw(void);                                      /* the ages move on: draw again when the tab is shown */
const char *ci_summary(void);                            /* the header line under the tabs */
void ci_on_msg(const char *payload, size_t len);         /* a pet/ci message, retained or fresh */
