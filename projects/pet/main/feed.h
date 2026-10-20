#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>
#include "lvgl.h"
#include "pet_def.h"

/* The updater feed (#92): what was opened, merged, closed and committed on GitHub, from the bridge's retained pet/feed
 * (bridge/petbrain/feed.py). It is the Updates tab of the list screen (todo.c), which creates and shows it. All
 * functions with the BSP display lock held. */
/* The scrolling container. press_cb gets every press on it; tap_cb a click on an open PR or issue row, with the row's
 * index as user data (#104). */
lv_obj_t *feed_create(lv_obj_t *parent, const pet_def_t *pet, lv_event_cb_t press_cb, lv_event_cb_t tap_cb);
void feed_draw(void);                                    /* the ages move on: draw again when the tab is shown */
const char *feed_summary(void);                          /* the header line under the tabs */
void feed_ago(char *buf, size_t size, int64_t t, time_t now);   /* "5m ago", "3h ago", "2d ago"; "" before SNTP */
void feed_on_msg(const char *payload, size_t len);       /* a pet/feed message, retained or fresh */
int feed_key(int i);                                     /* row i's line, 0 if a tap cannot add it: is it still there? */
bool feed_ref(int i, char *buf, size_t size);            /* "repo#74", as the to-do list shows the ref */
bool feed_add(int i);                                    /* send row i on pet/feed/add; false if not sent */
