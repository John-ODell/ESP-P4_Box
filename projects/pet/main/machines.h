#pragma once
#include <stddef.h>
#include "lvgl.h"
#include "pet_def.h"

/* The Machines tab (#108): whether the bridge and the machines on Tailscale the pet depends on are up, from the
 * bridge's retained pet/machines (bridge/petbrain/machines.py). The list screen (todo.c) creates and shows it. All
 * functions with the BSP display lock held. */
lv_obj_t *machines_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb);   /* the scrolling page */
void machines_draw(void);                                /* ages and the bridge's silence move on: draw when shown */
const char *machines_summary(void);                      /* the header line under the tabs */
void machines_on_msg(const char *payload, size_t len);   /* a pet/machines message, retained or fresh */
