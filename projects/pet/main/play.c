/* Play state (#63). Happiness drifts down one point every PET_HAPPY_DECAY_MIN awake minutes, never below
 * PET_HAPPY_FLOOR, and goes up with feeding (and later petting and Pet Says). Snacks left = today's contributions
 * minus snacks eaten today; the eaten count resets at local midnight once the clock is set. Until both the clock and
 * a bridge total for the new day are in, there are no snacks to give (play_snacks() is -1). Saved to NVS namespace
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
static int s_today = -1;        /* today.total from the bridge, -1 until the first state */
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

/* Local date as YYYYMMDD, or 0 while the clock is unset (same test as sched.c). */
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

static void check_day(void)
{
    unsigned day = today_key();
    if (day == 0 || day == s_day) {
        return;
    }
    if (s_eaten) {
        ESP_LOGI(TAG, "new day %u: eaten count %d -> 0", day, s_eaten);
    }
    s_eaten = 0;
    s_day = day;
    s_today = -1;   /* the bridge's total is yesterday's until its next state */
    save("new day");
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

void play_set_today(int total)
{
    check_day();
    s_today = total < 0 ? 0 : total;
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
    check_day();
    if (s_today < 0 || s_day == 0) {   /* no total yet, or no date to count eaten snacks against */
        return -1;
    }
    return s_today > s_eaten ? s_today - s_eaten : 0;
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
    save("fed");
    ESP_LOGI(TAG, "fed: %d left, happiness %d", play_snacks(), s_happy);
    return true;
}

void play_add(int delta, const char *why)
{
    set_happy(s_happy + delta);
    ESP_LOGI(TAG, "%s: happiness %d", why, s_happy);
}
