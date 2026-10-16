#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_state.h"

/* The face scene. All functions must be called with the BSP display lock held. */
void face_create(lv_obj_t *parent);
void face_apply_state(const pet_state_t *s, bool celebrate);
void face_set_link(const char *text);     /* bottom-right: wifi / mqtt status */
void face_set_status(const char *text);   /* bottom centre, small: debug feedback */
void face_set_clock(const char *hhmm);
void face_set_sleeping(bool sleeping);
void face_toggle_stats(void);             /* swipe: peek at the numbers */
void face_ack(void);                      /* tap: dismiss banner, stats, and a waiting reaction */
