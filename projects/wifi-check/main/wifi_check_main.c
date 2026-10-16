/* wifi-check: does the ESP32-C6 on the Waveshare ESP32-P4-WIFI6-Touch-LCD-4B
 * carry a working ESP-Hosted image?
 *
 * Derived from vendor/waveshare-4b/examples/esp-idf/wifi-station (public domain).
 * Adds: co-processor firmware/version report, an AP scan, and credentials from
 * a gitignored secrets.h instead of Kconfig.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_hosted.h"

#if __has_include("secrets.h")
#include "secrets.h"
#define HAVE_SECRETS 1
#else
#define HAVE_SECRETS 0
#define WIFI_CHECK_SSID     ""
#define WIFI_CHECK_PASSWORD ""
#endif

#define MAX_RETRY 5
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static const char *TAG = "wifi-check";
static EventGroupHandle_t s_wifi_event_group;
static int s_retry_num = 0;

static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGW(TAG, "disconnected, reason=%d", d ? d->reason : -1);
        if (s_retry_num < MAX_RETRY) {
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGI(TAG, "retry %d/%d", s_retry_num, MAX_RETRY);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR " gw:" IPSTR, IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.gw));
        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static const char *authmode_str(wifi_auth_mode_t m)
{
    switch (m) {
    case WIFI_AUTH_OPEN: return "open";
    case WIFI_AUTH_WEP: return "wep";
    case WIFI_AUTH_WPA_PSK: return "wpa";
    case WIFI_AUTH_WPA2_PSK: return "wpa2";
    case WIFI_AUTH_WPA_WPA2_PSK: return "wpa/wpa2";
    case WIFI_AUTH_WPA3_PSK: return "wpa3";
    case WIFI_AUTH_WPA2_WPA3_PSK: return "wpa2/wpa3";
    default: return "other";
    }
}

static void report_coprocessor(void)
{
    esp_hosted_coprocessor_fwver_t ver = {0};
    int rc = esp_hosted_get_coprocessor_fwversion(&ver);
    if (rc == 0) {
        ESP_LOGI(TAG, "C6 ESP-Hosted slave fw %lu.%lu.%lu (rev %ld pre %ld build %ld); host component %d.%d.%d",
                 (unsigned long)ver.major1, (unsigned long)ver.minor1, (unsigned long)ver.patch1,
                 (long)ver.revision, (long)ver.prerelease, (long)ver.build,
                 ESP_HOSTED_VERSION_MAJOR_1, ESP_HOSTED_VERSION_MINOR_1, ESP_HOSTED_VERSION_PATCH_1);
    } else {
        ESP_LOGE(TAG, "esp_hosted_get_coprocessor_fwversion failed rc=%d", rc);
    }

    uint32_t chip_id = 0;
    char target[32] = {0};
    rc = esp_hosted_get_cp_info(&chip_id, target, sizeof(target));
    if (rc == 0) {
        ESP_LOGI(TAG, "C6 chip id %lu target '%s'", (unsigned long)chip_id, target);
    } else {
        ESP_LOGW(TAG, "esp_hosted_get_cp_info rc=%d", rc);
    }

    esp_hosted_app_desc_t desc;
    memset(&desc, 0, sizeof(desc));
    if (esp_hosted_get_coprocessor_app_desc(&desc) == ESP_OK) {
        ESP_LOGI(TAG, "C6 app '%.32s' version '%.32s' idf '%.32s' built %.16s %.16s",
                 desc.project_name, desc.version, desc.idf_ver, desc.date, desc.time);
    } else {
        ESP_LOGW(TAG, "esp_hosted_get_coprocessor_app_desc failed");
    }
}

static void scan_once(void)
{
    wifi_scan_config_t scan_cfg = {0};
    scan_cfg.show_hidden = true;
    ESP_LOGI(TAG, "scanning...");
    esp_err_t err = esp_wifi_scan_start(&scan_cfg, true);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "scan failed: %s", esp_err_to_name(err));
        return;
    }
    uint16_t n = 0;
    esp_wifi_scan_get_ap_num(&n);
    ESP_LOGI(TAG, "scan found %u APs", n);
    if (n == 0) {
        return;
    }
    if (n > 20) {
        n = 20;
    }
    wifi_ap_record_t *recs = calloc(n, sizeof(*recs));
    if (!recs) {
        return;
    }
    esp_wifi_scan_get_ap_records(&n, recs);
    for (int i = 0; i < n; i++) {
        ESP_LOGI(TAG, "  %-32.32s ch%-3d rssi %4d %s", (const char *)recs[i].ssid, recs[i].primary, recs[i].rssi,
                 authmode_str(recs[i].authmode));
    }
    free(recs);
}

static void idle_report(const char *result)
{
    /* Keep printing so a monitor attached after a cold start still sees the verdict. */
    for (;;) {
        ESP_LOGI(TAG, "RESULT: %s", result);
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}

void app_main(void)
{
    /* Give a monitor attached right after replug a moment to catch the first lines. */
    vTaskDelay(pdMS_TO_TICKS(3000));
    ESP_LOGI(TAG, "wifi-check starting (ESP-IDF %s)", esp_get_idf_version());

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    ESP_LOGI(TAG, "esp_wifi_init (brings up ESP-Hosted over SDIO to the C6)...");
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_LOGI(TAG, "esp_wifi_init OK");

    report_coprocessor();

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "esp_wifi_start OK");

    uint8_t mac[6] = {0};
    if (esp_wifi_get_mac(WIFI_IF_STA, mac) == ESP_OK) {
        ESP_LOGI(TAG, "sta mac %02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }

    scan_once();

    if (!HAVE_SECRETS || strlen(WIFI_CHECK_SSID) == 0) {
        ESP_LOGW(TAG, "no secrets.h: skipping connect. Copy main/secrets.h.example to main/secrets.h and rebuild.");
        idle_report("hosted link up, scan done, connect skipped (no secrets.h)");
    }

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_CHECK_SSID,
            .password = WIFI_CHECK_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_LOGI(TAG, "connecting to '%s'...", WIFI_CHECK_SSID);
    ESP_ERROR_CHECK(esp_wifi_connect());

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT, pdFALSE, pdFALSE,
                                           portMAX_DELAY);
    if (bits & WIFI_CONNECTED_BIT) {
        idle_report("connected to '" WIFI_CHECK_SSID "' with IP");
    } else {
        idle_report("FAILED to connect to '" WIFI_CHECK_SSID "'");
    }
}
