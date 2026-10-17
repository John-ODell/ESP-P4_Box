/* Wi-Fi station through esp_wifi_remote / ESP-Hosted on the ESP32-C6, with backoff reconnect. */
#include <string.h>
#include "net.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_log.h"

static const char *TAG = "net";
#define GOT_IP_BIT BIT0

static EventGroupHandle_t s_eg;
static net_status_cb_t s_cb;
static esp_timer_handle_t s_retry_timer;
static int s_failures;
static char s_ip[16];
static char s_ssid[SETTINGS_SSID_MAX + 1]; /* empty: not set up, so never join */

static void notify(net_status_t st, const char *detail)
{
    if (s_cb) {
        s_cb(st, detail);
    }
}

static void retry_cb(void *arg)
{
    ESP_LOGI(TAG, "reconnecting (attempt %d)", s_failures + 1);
    esp_wifi_connect();
}

static void schedule_retry(void)
{
    uint32_t delay_ms = 1000u << (s_failures < 5 ? s_failures : 5); /* 1s .. 32s */
    s_failures++;
    esp_timer_stop(s_retry_timer);
    esp_timer_start_once(s_retry_timer, (uint64_t)delay_ms * 1000);
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_ssid[0] == '\0') {
            notify(NET_STATUS_NOT_SET_UP, NULL);
            return;
        }
        notify(NET_STATUS_CONNECTING, s_ssid);
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        xEventGroupClearBits(s_eg, GOT_IP_BIT);
        if (s_ssid[0] == '\0') {
            return;
        }
        ESP_LOGW(TAG, "disconnected from '%s' reason=%d", s_ssid, d ? d->reason : -1);
        notify(NET_STATUS_DISCONNECTED, "wifi lost");
        schedule_retry();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&e->ip_info.ip));
        ESP_LOGI(TAG, "got ip %s", s_ip);
        s_failures = 0;
        xEventGroupSetBits(s_eg, GOT_IP_BIT);
        notify(NET_STATUS_GOT_IP, s_ip);
    }
}

esp_err_t net_start(const pet_settings_t *settings, net_status_cb_t cb)
{
    s_cb = cb;
    s_eg = xEventGroupCreate();
    const esp_timer_create_args_t targs = {.callback = retry_cb, .name = "wifi-retry"};
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_retry_timer));

    ESP_ERROR_CHECK(esp_netif_init());
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    esp_netif_create_default_wifi_sta();

    ESP_LOGI(TAG, "esp_wifi_init (ESP-Hosted link to the C6)...");
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_event, NULL, NULL));

    /* The settings store is the one saved copy of the password, so "forget" really forgets it. */
    err = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_set_storage(RAM): %s", esp_err_to_name(err));
    }
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    if (settings->wifi_src != SETTINGS_NONE) {
        strlcpy(s_ssid, settings->wifi_ssid, sizeof(s_ssid));
        wifi_config_t wc = {
            .sta = {
                /* an empty password is an open network, which a WPA2 threshold would refuse */
                .threshold.authmode = settings->wifi_pass[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN,
                .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
            },
        };
        memcpy(wc.sta.ssid, settings->wifi_ssid, strlen(settings->wifi_ssid)); /* may fill all 32 bytes, no NUL */
        strlcpy((char *)wc.sta.password, settings->wifi_pass, sizeof(wc.sta.password));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    }
    /* Started even when not set up, so the radio is ready for the setup screen's scan. */
    ESP_ERROR_CHECK(esp_wifi_start());
    if (s_ssid[0]) {
        ESP_LOGI(TAG, "station started, joining '%s'", s_ssid);
    } else {
        ESP_LOGW(TAG, "station started, not set up: no Wi-Fi saved");
    }
    return ESP_OK;
}

bool net_wait_ip(uint32_t timeout_ms)
{
    EventBits_t bits = xEventGroupWaitBits(s_eg, GOT_IP_BIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeout_ms));
    return (bits & GOT_IP_BIT) != 0;
}
