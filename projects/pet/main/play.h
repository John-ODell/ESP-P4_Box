#pragma once
#include <stdbool.h>

/* Play state (#63): the pet's own happiness meter and today's snacks, kept apart from the GitHub numbers (mood, XP,
 * streak only ever come from the bridge). Saved in NVS. Apart from play_init, which runs at boot before the display
 * starts, every function runs with the BSP display lock held, which keeps the MQTT task and the LVGL task from
 * touching this at the same time. */
void play_init(void);                   /* load from NVS; call once after nvs_flash_init */
void play_set_today(int total, const char *ts); /* today.total and the state's ts (the bridge's local time) */
void play_tick(bool asleep);            /* once a minute: slow drift down while awake, midnight reset, saving */
int play_happiness(void);               /* 0..100 */
int play_snacks(void);                  /* snacks left today; -1 until the clock is set and a total for today is in */
int play_eaten(void);                   /* snacks eaten today */
bool play_feed(void);                   /* eat one snack: false if none is left */
int play_pet(void);                     /* a stroke: adds happiness with diminishing returns; returns the gain */
void play_add(int delta, const char *why); /* anything else that cheers the pet up */
int play_says_best(void);               /* Pet Says best score */
bool play_says_done(int score);         /* a finished game: happiness (less for each recent game, none for score 0)
                                           and the best score saved; true on a new best */
