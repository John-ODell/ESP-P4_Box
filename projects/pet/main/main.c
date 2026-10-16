/* Milestone 2: BSP up, one static face, touch events logged. */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_err.h"
#include "nvs_flash.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "face.h"

static const char *TAG = "pet";
static int s_taps;

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
        ESP_LOGI(TAG, "touch down  x=%d y=%d", (int)p.x, (int)p.y);
        break;
    case LV_EVENT_RELEASED:
        ESP_LOGI(TAG, "touch up    x=%d y=%d", (int)p.x, (int)p.y);
        break;
    case LV_EVENT_CLICKED:
        s_taps++;
        ESP_LOGI(TAG, "tap #%d      x=%d y=%d", s_taps, (int)p.x, (int)p.y);
        snprintf(buf, sizeof(buf), "tap %d at %d,%d", s_taps, (int)p.x, (int)p.y);
        face_set_status(buf);
        break;
    case LV_EVENT_LONG_PRESSED:
        ESP_LOGI(TAG, "long press  x=%d y=%d", (int)p.x, (int)p.y);
        face_set_status("long press");
        break;
    case LV_EVENT_GESTURE: {
        lv_dir_t d = lv_indev_get_gesture_dir(indev);
        ESP_LOGI(TAG, "swipe %s", gesture_name(d));
        snprintf(buf, sizeof(buf), "swipe %s", gesture_name(d));
        face_set_status(buf);
        break;
    }
    default:
        break;
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
    lv_display_t *disp = bsp_display_start();
    ESP_ERROR_CHECK(disp != NULL ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(bsp_display_backlight_on());
    ESP_ERROR_CHECK(bsp_display_brightness_set(60));
    ESP_LOGI(TAG, "display %dx%d up, touch indev %s", (int)lv_display_get_horizontal_resolution(disp),
             (int)lv_display_get_vertical_resolution(disp), bsp_display_get_input_dev() ? "ok" : "MISSING");

    ESP_ERROR_CHECK(bsp_display_lock(0) ? ESP_OK : ESP_ERR_TIMEOUT);
    lv_obj_t *scr = lv_screen_active();
    face_create(scr);
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, touch_event_cb, LV_EVENT_ALL, NULL);
    bsp_display_unlock();

    ESP_LOGI(TAG, "hello-face ready; tap the screen");
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        ESP_LOGI(TAG, "alive, taps so far: %d, free heap %u", s_taps, (unsigned)esp_get_free_heap_size());
    }
}
