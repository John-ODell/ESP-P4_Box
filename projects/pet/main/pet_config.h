#pragma once
/* Non-secret tunables for the box. Secrets (Wi-Fi, MQTT) live in secrets.h. */

#define PET_TZ_POSIX        "CST6CDT,M3.2.0,M11.1.0"   /* America/Chicago */
#define PET_NTP_SERVER      "pool.ntp.org"

#define PET_SPRITE_SCALE    12       /* 48x40 art -> 576x480 on the 720x720 panel */
#define PET_SPRITE_X        72
#define PET_SPRITE_Y        120

#define PET_BRIGHT_DAY      60       /* percent */
#define PET_BRIGHT_EVENING  25
#define PET_BRIGHT_NIGHT    4
#define PET_EVENING_HOUR    21       /* dim from here */
#define PET_NIGHT_START     23       /* sleep animation + night brightness from here ... */
#define PET_NIGHT_END       7        /* ... until here */
#define PET_WAKE_SECONDS    60       /* a touch at night wakes the box for this long */

#define PET_BANNER_MS       4000     /* goal / big-day banner auto-hide */
#define PET_STATS_MS        12000    /* stats overlay auto-hide */
