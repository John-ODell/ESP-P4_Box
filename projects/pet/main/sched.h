#pragma once
#include <stdbool.h>

/* Clock, SNTP and the backlight idle timer. Every function here runs under the display lock (LVGL task or a
 * caller holding bsp_display_lock), except sched_network_up(), which only starts SNTP and is called from the
 * network event handler without it. */
void sched_start(void);                /* TZ, clock label, backlight timer; the screen starts at full brightness */
void sched_network_up(void);           /* start SNTP once an IP arrives */
void sched_touch(void);                /* any touch: full brightness, idle timers restart */
void sched_activity(const char *what); /* something happened (state change, event): same as a touch */
enum { SCHED_HOLD_FACE = 1, SCHED_HOLD_MENU = 2 };
void sched_hold(unsigned why, bool on); /* SCHED_HOLD_*: full brightness while any reason holds (a tap is awaited,
                                           the menu is open) */
bool sched_is_night(void);             /* true while the pet sleeps (idle at night) */
