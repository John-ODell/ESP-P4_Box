/* Wi-Fi station through esp_wifi_remote / ESP-Hosted on the ESP32-C6, with backoff reconnect. */
#include <string.h>
#include "net.h"
#include "secrets.h"
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
        notify(NET_STATUS_CONNECTING, PET_WIFI_SSID);
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        xEventGroupClearBits(s_eg, GOT_IP_BIT);
        ESP_LOGW(TAG, "disconnected from '%s' reason=%d", PET_WIFI_SSID, d ? d->reason : -1);
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

esp_err_t net_start(net_status_cb_t cb)
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

    wifi_config_t wc = {
        .sta = {
            .ssid = PET_WIFI_SSID,
            .password = PET_WIFI_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "station started, joining '%s'", PET_WIFI_SSID);
    return ESP_OK;
}

bool net_wait_ip(uint32_t timeout_ms)
{
    EventBits_t bits = xEventGroupWaitBits(s_eg, GOT_IP_BIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeout_ms));
    return (bits & GOT_IP_BIT) != 0;
}
