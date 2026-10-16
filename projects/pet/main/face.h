#pragma once
#include <stdbool.h>
#include "lvgl.h"
#include "pet_state.h"
#include "pet_notify.h"

/* The face scene. All functions must be called with the BSP display lock held. */
void face_create(lv_obj_t *parent);
void face_apply_state(const pet_state_t *s, bool celebrate);
void face_set_link(const char *text);     /* bottom-right: wifi / mqtt status */
void face_set_status(const char *text);   /* bottom centre, small: debug feedback */
void face_set_clock(const char *hhmm);
void face_set_sleeping(bool sleeping);
void face_toggle_stats(void);             /* swipe: peek at the numbers */
void face_ack(void);                      /* tap: dismiss banner, stats, and a waiting reaction */
void face_set_notice(const pet_notice_t *n, int pos, int count, int held, bool announce); /* an AI session waits:
                                                      bubble (with "pos+1 of count waiting" when count > 1, plus "Tap
                                                      here for the next" when the box holds more than one: held),
                                                      source tint, backlight hold; announce plays the notice */
bool face_point_in_bubble(lv_point_t p);  /* on the bubble, with nothing drawn over it? a tap there cycles */
void face_clear_notice(void);             /* nothing waits: normal colours; hold released unless a banner waits */
