/* Milestone 5: sprite character with mood-weighted idle animations and reactions, stats swipe, clock,
 * backlight idle timer, on top of the milestone 4 Wi-Fi + MQTT plumbing. Milestone 6: the AI sessions waiting for
 * a person (the bridge's retained pet/notify list) show as a bubble and a source tint; a tap acknowledges. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "face.h"
#include "net.h"
#include "mqtt_link.h"
#include "settings.h"
#include "ota.h"
#include "pet_state.h"
#include "pet_notify.h"
#include "sched.h"
#include "pet_config.h"

static const char *TAG = "pet";
static int s_taps;
static bool s_wifi_up, s_mqtt_up;
static bool s_wifi_set_up, s_mqtt_set_up; /* false: nothing saved or built in, so the box waits for setup (#50) */
static pet_state_t s_state;
static pet_notify_t s_notify;   /* the waiting sessions, newest first; touched only with the display lock held */
static pet_notice_t s_top_seen; /* the newest entry ever on top of the list, same lock; later than it is news */
static int s_shown;             /* index in s_notify of the entry in the bubble, 0 the newest; same lock */
static bool s_swallow_click;   /* LVGL fires CLICKED after a gesture or long press; ignore that one */

static const char *gesture_name(lv_dir_t d)
{
    switch (d) {
    case LV_DIR_LEFT: return "left";
    case LV_DIR_RIGHT: return "right";
    case LV_DIR_TOP: return "up";
    case LV_DIR_BOTTOM: return "down";
    default: return "none";
    }
}

static int notify_index(const pet_notify_t *l, const pet_notice_t *key)
{
    for (int i = 0; i < l->n; i++) {
        if (strcmp(l->items[i].source, key->source) == 0 && strcmp(l->items[i].session, key->session) == 0) {
            return i;
        }
    }
    return -1;
}

/* Display lock held: draw the waiting session at s_shown (the newest unless a bubble tap moved on), or nothing. */
static void show_notify(bool announce)
{
    if (s_notify.n == 0) {
        s_shown = 0;
        face_clear_notice();
        return;
    }
    if (s_shown >= s_notify.n) {
        s_shown = 0; /* the shown one was the oldest and went away: round to the newest */
    }
    face_set_notice(&s_notify.items[s_shown], s_shown, s_notify.count, s_notify.n, announce);
}

/* LVGL task, lock held: a tap on the bubble shows the next waiting session (the next older one; after the oldest,
 * round to the newest) without acknowledging any. */
static void notice_cycle(void)
{
    s_shown = (s_shown + 1) % s_notify.n;
    ESP_LOGI(TAG, "cycle: %d of %d (%d held here), %s/%s", s_shown + 1, s_notify.count, s_notify.n,
             s_notify.items[s_shown].source, s_notify.items[s_shown].session);
    show_notify(false);
}

/* LVGL task, lock held: a tap acknowledges the session shown. The bridge drops it and republishes the list; the box
 * drops it locally too, so the bubble moves on even while the bridge is down. */
static void notice_tap(void)
{
    if (s_notify.n == 0) {
        return;
    }
    if (s_shown >= s_notify.n) {
        s_shown = 0;
    }
    pet_notice_t top = s_notify.items[s_shown];
    char *json = pet_notify_ack_json(top.source, top.session);
    bool queued = json && mqtt_link_publish(PET_TOPIC_ACK, json, false);
    free(json);
    ESP_LOGI(TAG, "ack %s/%s%s", top.source, top.session, queued ? "" : " (not sent)");
    memmove(&s_notify.items[s_shown], &s_notify.items[s_shown + 1],
            (size_t)(s_notify.n - s_shown - 1) * sizeof(pet_notice_t)); /* s_shown now names the next older one */
    s_notify.n--;
    if (s_notify.count > 0) {
        s_notify.count--;
    }
    if (s_notify.count < s_notify.n) {
        s_notify.count = s_notify.n;
    }
    show_notify(false);
}

/* Called from the LVGL task; the lock is already held. */
static void touch_event_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    lv_indev_t *indev = lv_indev_active();
    lv_point_t p = {0};
    if (indev) {
        lv_indev_get_point(indev, &p);
    }
    char buf[48];
    switch (code) {
    case LV_EVENT_PRESSED:
        sched_touch(); /* brighten on finger-down, not on release */
        ESP_LOGI(TAG, "touch down  x=%d y=%d", (int)p.x, (int)p.y);
        break;
    case LV_EVENT_RELEASED:
        ESP_LOGI(TAG, "touch up    x=%d y=%d", (int)p.x, (int)p.y);
        break;
    case LV_EVENT_CLICKED:
        sched_touch();
        if (s_swallow_click) {
            s_swallow_click = false;
            break;
        }
        if (s_notify.n > 1 && face_point_in_bubble(p)) {
            ESP_LOGI(TAG, "tap on the bubble x=%d y=%d", (int)p.x, (int)p.y);
            notice_cycle();
            break;
        }
        s_taps++;
        ESP_LOGI(TAG, "tap #%d      x=%d y=%d", s_taps, (int)p.x, (int)p.y);
        snprintf(buf, sizeof(buf), "tap %d at %d,%d", s_taps, (int)p.x, (int)p.y);
        face_set_status(buf);
        notice_tap();
        face_ack();
        break;
    case LV_EVENT_LONG_PRESSED:
        s_swallow_click = true;
        sched_touch();
        ESP_LOGI(TAG, "long press  x=%d y=%d (settings come later)", (int)p.x, (int)p.y);
        face_set_status("long press: settings come in a later milestone");
        break;
    case LV_EVENT_GESTURE: {
        s_swallow_click = true;
        sched_touch();
        lv_dir_t d = lv_indev_get_gesture_dir(indev);
        ESP_LOGI(TAG, "swipe %s -> stats", gesture_name(d));
        face_toggle_stats();
        break;
    }
    default:
        break;
    }
}

static const char *reset_reason_name(void)
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_EXT: return "external pin";
    case ESP_RST_SW: return "software restart";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt watchdog";
    case ESP_RST_TASK_WDT: return "task watchdog";
    case ESP_RST_WDT: return "other watchdog";
    case ESP_RST_DEEPSLEEP: return "deep sleep wake";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    case ESP_RST_EFUSE: return "efuse error";
    case ESP_RST_PWR_GLITCH: return "power glitch";
    case ESP_RST_CPU_LOCKUP: return "cpu lockup";
    default: return "unknown";
    }
}

static void refresh_link_line(void)
{
    const char *txt = !s_wifi_set_up ? "not set up: wifi"
                      : !s_wifi_up   ? "wifi: connecting"
                      : !s_mqtt_set_up ? "wifi ok, not set up: bridge"
                      : s_mqtt_up    ? "wifi + mqtt ok"
                                     : "wifi ok, mqtt: connecting";
    if (bsp_display_lock(200)) {
        face_set_link(txt);
        bsp_display_unlock();
    }
}

static void on_net(net_status_t st, const char *detail)
{
    s_wifi_up = (st == NET_STATUS_GOT_IP);
    if (s_wifi_up) {
        sched_network_up();
    }
    refresh_link_line();
}

static void on_mqtt_status(bool connected)
{
    s_mqtt_up = connected;
    refresh_link_line();
    if (connected) {
        ota_confirm_running(); /* an image on trial has now proven itself: keep it (#16) */
    }
}

static void on_cmd(const char *payload, size_t len, bool retained)
{
    if (retained) { /* a command left on the broker would run again at every boot: never obey one */
        ESP_LOGW(TAG, "cmd: retained message ignored: %.*s", (int)len, payload);
        return;
    }
    if (!ota_handle_cmd(payload, len)) {
        ESP_LOGW(TAG, "cmd: not understood: %.*s", (int)len, payload);
    }
}

static void on_notify(const char *payload, size_t len, bool retained)
{
    static pet_notify_t nf; /* 1.8 KB: not on the MQTT task's stack; this handler runs in that one task only */
    if (!pet_notify_parse(payload, len, &nf)) {
        return;
    }
    if (!bsp_display_lock(500)) {
        ESP_LOGW(TAG, "could not take the display lock; notify list not drawn");
        return;
    }
    /* News is a top entry the box has not had before: a later ts than any so far, or another session within the same
     * second that was not in the list already. An older one surfacing after a clear or a tap is not news, and neither
     * is a retained message, which is a replay (boot, reconnect): show it and hold the screen, no reaction. */
    bool news = false;
    if (nf.n > 0) {
        int c = strcmp(nf.items[0].ts, s_top_seen.ts);
        bool other = strcmp(nf.items[0].source, s_top_seen.source) != 0 ||
                     strcmp(nf.items[0].session, s_top_seen.session) != 0;
        news = !retained && (c > 0 || (c == 0 && other && notify_index(&s_notify, &nf.items[0]) < 0));
        if (c >= 0) {
            s_top_seen = nf.items[0];
        }
    }
    /* Keep the entry in the bubble through a list update (a cycle in progress). When it is gone (acknowledged or
     * cleared from the Mac), the next older one still listed takes its place, as after a local ack, whatever else
     * the update changed (a retained replay after an outage can add entries above it as well). News, or nothing
     * older left, goes to the newest. */
    int keep = -1;
    if (!news && s_notify.n > 0) {
        for (int i = s_shown; i < s_notify.n && keep < 0; i++) {
            keep = notify_index(&nf, &s_notify.items[i]);
        }
    }
    s_notify = nf;
    s_shown = keep < 0 ? 0 : keep;
    if (nf.n > 0) {
        ESP_LOGI(TAG, "notify: %d pending, newest %s/%s %s%s", nf.count, nf.items[0].source, nf.items[0].session,
                 nf.items[0].kind, retained ? " (retained)" : "");
    } else {
        ESP_LOGI(TAG, "notify: nothing pending%s", retained ? " (retained)" : "");
    }
    show_notify(news);
    if (news) {
        sched_activity("session notification");
    }
    bsp_display_unlock();
}

static void on_mqtt_msg(const char *topic, const char *payload, size_t len, bool retained)
{
    if (strcmp(topic, PET_TOPIC_STATE) == 0) {
        pet_state_t st;
        if (!pet_state_parse(payload, len, &st)) {
            return;
        }
        bool changed = s_state.valid && (st.xp != s_state.xp || st.today.total != s_state.today.total || st.events);
        s_state = st;
        ESP_LOGI(TAG, "state: lv%d xp%d (%d/%d) mood %d %s streak %d%s today %d/%d%s%s events=0x%lx%s",
                 st.level, st.xp, st.xp_into, st.xp_next, st.mood, st.mood_label, st.streak,
                 st.streak_at_risk ? " AT RISK" : "", st.today.total, st.goal, st.goal_hit ? " goal" : "",
                 st.big_day ? " BIG" : "", (unsigned long)st.events, retained ? " (retained)" : "");
        if (bsp_display_lock(500)) {
            /* A retained message is a replay of old state: render it, but do not re-celebrate. */
            face_apply_state(&st, !retained);
            if (changed && !retained) {
                sched_activity("pet state changed"); /* wake the screen: something happened */
            }
            bsp_display_unlock();
        } else {
            ESP_LOGW(TAG, "could not take the display lock; state not drawn");
        }
    } else if (strcmp(topic, PET_TOPIC_NOTIFY) == 0) {
        on_notify(payload, len, retained);
    } else if (strcmp(topic, PET_TOPIC_EVENT) == 0) {
        /* Each accepted event, for the UART; the screen follows the retained pet/notify list instead. */
        ESP_LOGI(TAG, "event: %.*s", (int)len, payload);
    } else if (strcmp(topic, PET_TOPIC_CMD) == 0) {
        on_cmd(payload, len, retained);
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "starting display");
    ESP_LOGI(TAG, "boot: reset reason %s", reset_reason_name());
    ota_report_boot();
    lv_display_t *disp = bsp_display_start();
    ESP_ERROR_CHECK(disp != NULL ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(bsp_display_backlight_on());
    ESP_ERROR_CHECK(bsp_display_brightness_set(PET_BRIGHT_FULL));
    ESP_LOGI(TAG, "display %dx%d up, touch indev %s", (int)lv_display_get_horizontal_resolution(disp),
             (int)lv_display_get_vertical_resolution(disp), bsp_display_get_input_dev() ? "ok" : "MISSING");

    ESP_ERROR_CHECK(bsp_display_lock(0) ? ESP_OK : ESP_ERR_TIMEOUT);
    lv_obj_t *scr = lv_screen_active();
    face_create(scr);
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, touch_event_cb, LV_EVENT_ALL, NULL);
    sched_start();
    bsp_display_unlock();
    ESP_LOGI(TAG, "face ready");

    pet_settings_t settings;
    settings_load(&settings);
    s_wifi_set_up = settings.wifi_src != SETTINGS_NONE;
    s_mqtt_set_up = settings.mqtt_src != SETTINGS_NONE;
    ESP_ERROR_CHECK(net_start(&settings, on_net));
    s_wifi_set_up = net_set_up(); /* false too when the driver refused the saved values */
    if (s_wifi_set_up && !net_wait_ip(30000)) {
        ESP_LOGW(TAG, "no IP after 30 s; starting MQTT anyway, it will retry");
    }
    if (s_mqtt_set_up) {
        esp_err_t err = mqtt_link_start(&settings, on_mqtt_msg, on_mqtt_status);
        if (err != ESP_OK) { /* settings come from NVS now: a bad one must not become a reboot loop */
            ESP_LOGE(TAG, "MQTT did not start (%s); bridge treated as not set up", esp_err_to_name(err));
            s_mqtt_set_up = false;
        }
    } else {
        ESP_LOGW(TAG, "no bridge address saved: MQTT not started");
    }
    refresh_link_line();
    memset(&settings, 0, sizeof(settings)); /* mqtt_link and the Wi-Fi driver keep their own copies */

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(60000));
        ESP_LOGI(TAG, "alive: wifi %s, mqtt %s, state %s, pending %d, taps %d, free heap %u (internal %u), reset %s",
                 s_wifi_up ? "up" : "down", s_mqtt_up ? "up" : "down", s_state.valid ? s_state.ts : "none",
                 s_notify.count, s_taps,
                 (unsigned)esp_get_free_heap_size(), (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                 reset_reason_name());
    }
}
