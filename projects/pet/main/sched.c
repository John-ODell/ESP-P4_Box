#include <stdlib.h>
#include <time.h>
#include <sys/time.h>
#include "sched.h"
#include "pet_config.h"
#include "face.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

static const char *TAG = "sched";
static bool s_sntp_started;
static int s_brightness = -1;
static time_t s_wake_until;
static bool s_night;
static bool s_time_valid;

static void set_brightness(int pct)
{
    if (pct != s_brightness) {
        s_brightness = pct;
        bsp_display_brightness_set(pct);
    }
}

static void tick(lv_timer_t *t)
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

    bool awake = now < s_wake_until;
    bool night = valid && (tm.tm_hour >= PET_NIGHT_START || tm.tm_hour < PET_NIGHT_END);
    bool evening = valid && !night && tm.tm_hour >= PET_EVENING_HOUR;
    int target = awake ? PET_BRIGHT_DAY : night ? PET_BRIGHT_NIGHT : evening ? PET_BRIGHT_EVENING : PET_BRIGHT_DAY;
    set_brightness(target);

    bool sleeping = night && !awake;
    if (sleeping != s_night) {
        s_night = sleeping;
        ESP_LOGI(TAG, "%s", sleeping ? "night: sleeping" : "awake");
        face_set_sleeping(sleeping);
    }
}

void sched_start(void)
{
    setenv("TZ", PET_TZ_POSIX, 1);
    tzset();
    lv_timer_create(tick, 1000, NULL);
    tick(NULL);
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
    s_wake_until = time(NULL) + PET_WAKE_SECONDS;
}

bool sched_is_night(void)
{
    return s_night;
}
