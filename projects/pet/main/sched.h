#pragma once
#include <stdbool.h>

/* Clock, SNTP, backlight schedule, night sleep. */
void sched_start(void);          /* call once with the display lock held (creates an LVGL timer) */
void sched_network_up(void);     /* call when an IP arrives: starts SNTP once */
void sched_touch(void);          /* any touch: wake the backlight for PET_WAKE_SECONDS */
bool sched_is_night(void);
