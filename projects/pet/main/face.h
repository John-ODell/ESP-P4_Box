#pragma once
#include "lvgl.h"

/* Builds the static hello-face on `parent` (normally the active screen). Caller holds the LVGL lock. */
void face_create(lv_obj_t *parent);

/* Updates the small status line under the face. Caller holds the LVGL lock. */
void face_set_status(const char *text);
