#pragma once
#include <stdbool.h>

/* Clock, SNTP and the backlight idle timer. Every function here runs under the display lock (LVGL task or a
 * caller holding bsp_display_lock). */
void sched_start(void);                /* TZ, clock label, backlight timer; the screen starts at full brightness */
void sched_network_up(void);           /* start SNTP once an IP arrives */
void sched_touch(void);                /* any touch: full brightness, idle timers restart */
void sched_activity(const char *what); /* something happened (state change, event): same as a touch */
void sched_hold(bool on);              /* something waits for a tap: hold full brightness until released */
bool sched_is_night(void);             /* true while the pet sleeps (idle at night) */
