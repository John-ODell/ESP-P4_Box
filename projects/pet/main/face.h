#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_state.h"

/* All functions must be called with the BSP display lock held. */
void face_create(lv_obj_t *parent);
void face_apply_state(const pet_state_t *s, bool celebrate);
void face_set_link(const char *text);     /* small corner line: wifi / mqtt status */
void face_set_status(const char *text);   /* small bottom line: debug feedback */
void face_ack(void);                      /* tap: dismiss the event banner */
