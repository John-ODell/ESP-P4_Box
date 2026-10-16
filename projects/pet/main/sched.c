#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <sys/time.h>
#include "sched.h"
#include "pet_config.h"
#include "face.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_netif_sntp.h"
#include "driver/ledc.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

static const char *TAG = "sched";
static bool s_sntp_started;
static int s_brightness = PET_BRIGHT_FULL; /* app_main sets this level before sched_start() */
static int64_t s_last_activity_us;         /* esp_timer clock, so SNTP jumps cannot fake a long idle */
static bool s_hold;                        /* something waits for a tap: stay at full brightness */
static bool s_sleeping;
static bool s_time_valid;
static bool s_fade_ready;

/* The BSP drives the backlight with LEDC (10-bit, 5 kHz) on this channel. We reuse the channel for hardware fades so
 * a level change glides instead of snapping; a snap to a dark level reads as "the box turned itself off". */
#define BL_MODE LEDC_LOW_SPEED_MODE
#define BL_CH   ((ledc_channel_t)CONFIG_BSP_DISPLAY_BRIGHTNESS_LEDC_CH)

static void set_brightness(int pct, const char *why)
{
    if (pct == s_brightness) {
        return;
    }
    ESP_LOGI(TAG, "backlight %d%% -> %d%% (%s)", s_brightness, pct, why);
    int ms = pct > s_brightness ? PET_FADE_UP_MS : PET_FADE_DOWN_MS;
    s_brightness = pct;
    uint32_t duty = (1023u * (uint32_t)pct) / 100u;
    if (s_fade_ready) {
        ledc_fade_stop(BL_MODE, BL_CH);
        if (ledc_set_fade_with_time(BL_MODE, BL_CH, duty, ms) == ESP_OK &&
            ledc_fade_start(BL_MODE, BL_CH, LEDC_FADE_NO_WAIT) == ESP_OK) {
            return;
        }
    }
    bsp_display_brightness_set(pct);
}

/* Recompute the clock label, the backlight level and the sleep state. Runs once a second from an LVGL timer and
 * immediately on touch, activity and hold changes. */
static void update(void)
{
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    bool valid = tm.tm_year + 1900 >= 2020;
    if (valid != s_time_valid) {
        s_time_valid = valid;
        if (valid) {
            ESP_LOGI(TAG, "clock synced: %04d-%02d-%02d %02d:%02d", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                     tm.tm_hour, tm.tm_min);
        } else {
            ESP_LOGI(TAG, "clock not synced yet");
        }
    }
    char buf[8];
    if (valid) {
        snprintf(buf, sizeof(buf), "%02d:%02d", tm.tm_hour, tm.tm_min);
    } else {
        snprintf(buf, sizeof(buf), "--:--");
    }
    face_set_clock(buf);

    int64_t idle_s = (esp_timer_get_time() - s_last_activity_us) / 1000000;
    bool night = valid && (tm.tm_hour >= PET_NIGHT_START || tm.tm_hour < PET_NIGHT_END);
    int dim = PET_BRIGHT_FULL * (night ? PET_NIGHT_DIM_PERCENT : PET_DIM_PERCENT) / 100;
    int off_after = night ? PET_NIGHT_OFF_AFTER_S : PET_OFF_AFTER_S;
    int target;
    char why[48];
    if (s_hold) {
        target = PET_BRIGHT_FULL;
        snprintf(why, sizeof(why), "waiting for a tap");
    } else if (idle_s < PET_DIM_AFTER_S) {
        target = PET_BRIGHT_FULL;
        snprintf(why, sizeof(why), "active");
    } else if (off_after > 0 && idle_s >= off_after) {
        target = 0;
        snprintf(why, sizeof(why), "idle %d s, off%s", off_after, night ? ", night" : "");
    } else {
        target = dim;
        snprintf(why, sizeof(why), "idle %d s, dim%s", PET_DIM_AFTER_S, night ? ", night" : "");
    }
    set_brightness(target, why);

    bool sleeping = night && !s_hold && idle_s >= PET_DIM_AFTER_S;
    if (sleeping != s_sleeping) {
        s_sleeping = sleeping;
        ESP_LOGI(TAG, "%s", sleeping ? "night: sleeping" : "awake");
        face_set_sleeping(sleeping);
    }
}

static void tick(lv_timer_t *t)
{
    (void)t;
    update();
}

void sched_start(void)
{
    setenv("TZ", PET_TZ_POSIX, 1);
    tzset();
    s_last_activity_us = esp_timer_get_time();
    s_fade_ready = ledc_fade_func_install(0) == ESP_OK;
    if (!s_fade_ready) {
        ESP_LOGW(TAG, "ledc fade service unavailable, backlight changes will step");
    }
    lv_timer_create(tick, 1000, NULL);
    update();
}

void sched_network_up(void)
{
    if (s_sntp_started) {
        return;
    }
    s_sntp_started = true;
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG(PET_NTP_SERVER);
    esp_err_t err = esp_netif_sntp_init(&cfg);
    ESP_LOGI(TAG, "sntp start (%s): %s", PET_NTP_SERVER, esp_err_to_name(err));
}

void sched_touch(void)
{
    s_last_activity_us = esp_timer_get_time();
    update();
}

void sched_activity(const char *what)
{
    ESP_LOGI(TAG, "activity: %s", what);
    s_last_activity_us = esp_timer_get_time();
    update();
}

void sched_hold(bool on)
{
    if (on != s_hold) {
        s_hold = on;
        ESP_LOGI(TAG, "%s", on ? "hold: waiting for a tap" : "hold released");
    }
    if (!on) {
        s_last_activity_us = esp_timer_get_time();
    }
    update();
}

bool sched_is_night(void)
{
    return s_sleeping;
}
