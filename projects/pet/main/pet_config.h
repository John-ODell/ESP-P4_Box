#pragma once
/* Non-secret tunables for the box. Secrets (Wi-Fi, MQTT) live in secrets.h. */

#define PET_TZ_POSIX        "CST6CDT,M3.2.0,M11.1.0"   /* America/Chicago */
#define PET_NTP_SERVER      "pool.ntp.org"

#define PET_SPRITE_SCALE    12       /* 48x40 art -> 576x480 on the 720x720 panel */
#define PET_SPRITE_X        72
#define PET_SPRITE_Y        120

/* Backlight: an idle timer, not a clock schedule. Full brightness after boot, after any touch and whenever the pet
 * state changes; with no touch it dims after PET_DIM_AFTER_S and switches off after PET_OFF_AFTER_S. Anything that
 * waits for a tap (level-up banner, session notifications) holds full brightness until acknowledged. At night the
 * dim level is lower and the off timer shorter, and the face sleeps while idle. Levels are percent of LEDC duty. */
#define PET_BRIGHT_FULL         60   /* what "on" looks like */
#define PET_DIM_PERCENT         75   /* idle dim as a percentage of full; try 50 if 75 is still too bright */
#define PET_DIM_AFTER_S         30
#define PET_OFF_AFTER_S         600  /* 10 min without a touch; 0 keeps the dimmed screen on by day, night still switches off */
#define PET_NIGHT_START         23   /* night rules from here ... */
#define PET_NIGHT_END           7    /* ... until here */
#define PET_NIGHT_DIM_PERCENT   40
#define PET_NIGHT_OFF_AFTER_S   180  /* 3 min */
#define PET_FADE_UP_MS          300  /* brightening is quick, dimming is slow */
#define PET_FADE_DOWN_MS        1500

#define PET_BANNER_MS       4000     /* goal / big-day banner auto-hide */
#define PET_STATS_MS        12000    /* stats overlay auto-hide */
#define PET_MENU_IDLE_MS    7000     /* play menu closes after this long without a touch */
#define PET_SWIPE_COOLDOWN_MS 800    /* after a swipe toggles the stats, later swipes wait this long */

/* Play (#63): the pet's own happiness, apart from mood/XP/streak. */
#define PET_HAPPY_START      60      /* first boot */
#define PET_HAPPY_FLOOR      20      /* drift never goes below this */
#define PET_HAPPY_DECAY_MIN  15      /* one point down per this many awake minutes (asleep: no drift) */
#define PET_HAPPY_FEED       8       /* per snack */
#define PET_PLAY_SAVE_MIN    10      /* drift is saved to NVS at most this often; feeding saves at once */
#define PET_SNACK_SCALE      4       /* the falling fish: 48x40 art drawn 4x */
#define PET_STROKE_PX        150     /* finger travel over the pet per stroke: one heart, one bit of happiness */
#define PET_STROKE_TAP_PX    20      /* more travel than this on the pet and the press is no longer a tap */
#define PET_STROKE_JITTER_PX 3       /* smaller moves between touch samples are ignored */
#define PET_HAPPY_PET        3       /* happiness for a stroke while the pet is fresh ... */
#define PET_PET_FADE         4       /* ... one less after every this many strokes, down to nothing */
#define PET_SAYS_IDLE_MS     30000   /* Pet Says closes after this long waiting for a tap */
#define PET_SAYS_HAPPY       2       /* happiness for a finished game, plus one per PET_SAYS_HAPPY_PER rounds ... */
#define PET_SAYS_HAPPY_PER   4
#define PET_SAYS_HAPPY_MAX   6       /* ... up to this */
#define PET_HEART_SCALE      6       /* the hearts that float up from a stroke */

/* Session notifications (milestone 6): while an AI session waits for a person, the character takes that source's
 * colour and a bubble names the session. Sources are what the senders in bridge/hooks/ and bridge/notify.py put in
 * `source`; anything else gets PET_COLOR_OTHER. */
#define PET_COLOR_CLAUDE    0xD97757 /* Anthropic clay */
#define PET_COLOR_CODEX     0x10A37F /* OpenAI green */
#define PET_COLOR_LOCAL     0xB794F4 /* local models and scripts: violet */
#define PET_COLOR_OTHER     0x9AA5B1 /* any other source: grey */
#define PET_BUBBLE_W        400      /* speech bubble width; it sits top right, over the character's shoulder */
