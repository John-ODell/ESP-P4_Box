/* Play state (#63). Happiness drifts down one point every PET_HAPPY_DECAY_MIN awake minutes, never below
 * PET_HAPPY_FLOOR, and goes up with feeding (and later petting and Pet Says). Snacks left = today's contributions
 * minus snacks eaten today; the eaten count resets at local midnight once the clock is set. A total counts only on the
 * day its state was made (the date in its ts), so a retained or late state from yesterday gives no snacks; until the
 * clock is set and a total for today is in, play_snacks() is -1. Saved to NVS namespace
 * `pet_play` on feeding, at midnight, and at most every PET_PLAY_SAVE_MIN minutes for drift. */
#include <time.h>
#include "play.h"
#include "pet_config.h"
#include "esp_log.h"
#include "nvs.h"

static const char *TAG = "play";
#define NS "pet_play"

static int s_happy = PET_HAPPY_START;
static int s_eaten;
static unsigned s_day;          /* YYYYMMDD of s_eaten, 0 until the clock is first set */
static int s_total = -1;        /* today.total from the bridge, -1 until the first state */
static unsigned s_total_day;    /* YYYYMMDD the bridge made that total on */
static int s_awake_min, s_since_save_min;
static bool s_dirty;

static void save(const char *why)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, "happy", (uint8_t)s_happy);
        if (err == ESP_OK) {
            err = nvs_set_u16(h, "eaten", (uint16_t)s_eaten);
        }
        if (err == ESP_OK) {
            err = nvs_set_u32(h, "day", s_day);
        }
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "save (%s) failed: %s", why, esp_err_to_name(err));
        return;
    }
    s_dirty = false;
    s_since_save_min = 0;
    ESP_LOGI(TAG, "saved (%s): happiness %d, eaten %d, day %u", why, s_happy, s_eaten, s_day);
}

/* Local date as YYYYMMDD, or 0 while the clock is unset (same test as sched.c). The box and the bridge share a time
 * zone (both at home), so this and the date in a state's ts name the same day. */
static unsigned today_key(void)
{
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    if (tm.tm_year + 1900 < 2020) {
        return 0;
    }
    return (unsigned)((tm.tm_year + 1900) * 10000 + (tm.tm_mon + 1) * 100 + tm.tm_mday);
}

/* Today's date, with the eaten count moved to it if the day changed; 0 while the clock is unset. */
static unsigned check_day(void)
{
    unsigned day = today_key();
    if (day == 0 || day == s_day) {
        return day;
    }
    if (s_eaten) {
        ESP_LOGI(TAG, "new day %u: eaten count %d -> 0", day, s_eaten);
    }
    s_eaten = 0;
    s_day = day;
    save("new day");
    return day;
}

static void set_happy(int v)
{
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    if (v != s_happy) {
        s_happy = v;
        s_dirty = true;
    }
}

void play_init(void)
{
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) == ESP_OK) {
        uint8_t happy;
        uint16_t eaten;
        uint32_t day;
        if (nvs_get_u8(h, "happy", &happy) == ESP_OK && happy <= 100) {
            s_happy = happy;
        }
        if (nvs_get_u16(h, "eaten", &eaten) == ESP_OK) {
            s_eaten = eaten;
        }
        if (nvs_get_u32(h, "day", &day) == ESP_OK) {
            s_day = day;
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "happiness %d, eaten %d on %u", s_happy, s_eaten, s_day);
}

/* "2026-10-17T15:04:05-04:00" -> 20261017, or 0 if it does not start with a date. */
static unsigned ts_day(const char *ts)
{
    unsigned y, m, d;
    char t;
    if (!ts || sscanf(ts, "%4u-%2u-%2u%c", &y, &m, &d, &t) != 4 || t != 'T' || m < 1 || m > 12 || d < 1 || d > 31) {
        return 0;
    }
    return y * 10000 + m * 100 + d;
}

void play_set_today(int total, const char *ts)
{
    unsigned day = ts_day(ts);
    if (day == 0) {
        ESP_LOGW(TAG, "state ts \"%s\" has no date; total ignored", ts ? ts : "");
        return;
    }
    s_total = total < 0 ? 0 : total;
    s_total_day = day;
    check_day();
}

void play_tick(bool asleep)
{
    check_day();
    if (!asleep && ++s_awake_min >= PET_HAPPY_DECAY_MIN) {
        s_awake_min = 0;
        if (s_happy > PET_HAPPY_FLOOR) {
            set_happy(s_happy - 1);
        }
    }
    if (s_dirty && ++s_since_save_min >= PET_PLAY_SAVE_MIN) {
        save("drift");
    }
}

int play_happiness(void)
{
    return s_happy;
}

int play_snacks(void)
{
    unsigned day = check_day();
    if (day == 0 || s_total < 0 || s_total_day != day) {   /* no clock, no total yet, or yesterday's total */
        return -1;
    }
    return s_total > s_eaten ? s_total - s_eaten : 0;
}

int play_eaten(void)
{
    return s_eaten;
}

bool play_feed(void)
{
    if (play_snacks() <= 0) {
        return false;
    }
    s_eaten++;
    set_happy(s_happy + PET_HAPPY_FEED);
    s_dirty = true;   /* the eaten count changed even when happiness is already 100: a failed save retries */
    save("fed");
    ESP_LOGI(TAG, "fed: %d left, happiness %d", play_snacks(), s_happy);
    return true;
}

void play_add(int delta, const char *why)
{
    set_happy(s_happy + delta);
    ESP_LOGI(TAG, "%s: happiness %d", why, s_happy);
}
