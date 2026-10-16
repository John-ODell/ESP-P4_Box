/* Thin wrapper over esp-mqtt: connect to the bridge's broker, subscribe to pet/state, pet/event and pet/notify,
 * publish pet/ack. */
#include <stdlib.h>
#include <string.h>
#include "mqtt_link.h"
#include "secrets.h"
#include "mqtt_client.h"
#include "esp_log.h"

static const char *TAG = "mqtt";
static esp_mqtt_client_handle_t s_client;
static mqtt_msg_cb_t s_on_msg;
static mqtt_status_cb_t s_on_status;
static volatile bool s_connected;

static void handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_mqtt_event_handle_t ev = data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        s_connected = true;
        ESP_LOGI(TAG, "connected to %s", PET_MQTT_URI);
        esp_mqtt_client_subscribe(s_client, PET_TOPIC_STATE, 1);
        esp_mqtt_client_subscribe(s_client, PET_TOPIC_EVENT, 1);
        esp_mqtt_client_subscribe(s_client, PET_TOPIC_NOTIFY, 1);
        if (s_on_status) {
            s_on_status(true);
        }
        break;
    case MQTT_EVENT_DISCONNECTED:
        s_connected = false;
        ESP_LOGW(TAG, "disconnected");
        if (s_on_status) {
            s_on_status(false);
        }
        break;
    case MQTT_EVENT_SUBSCRIBED:
        ESP_LOGI(TAG, "subscribed (msg_id %d)", ev->msg_id);
        break;
    case MQTT_EVENT_DATA: {
        if (ev->current_data_offset != 0 || ev->total_data_len != ev->data_len) {
            ESP_LOGW(TAG, "fragmented message (%d bytes) dropped; raise buffer.size", ev->total_data_len);
            break;
        }
        char *topic = strndup(ev->topic, ev->topic_len);
        char *payload = malloc(ev->data_len + 1);
        if (topic && payload) {
            memcpy(payload, ev->data, ev->data_len);
            payload[ev->data_len] = '\0';
            ESP_LOGI(TAG, "%s (%d bytes%s)", topic, ev->data_len, ev->retain ? ", retained" : "");
            if (s_on_msg) {
                s_on_msg(topic, payload, ev->data_len, ev->retain);
            }
        }
        free(topic);
        free(payload);
        break;
    }
    case MQTT_EVENT_ERROR:
        if (ev->error_handle && ev->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
            ESP_LOGW(TAG, "transport error errno=%d", ev->error_handle->esp_transport_sock_errno);
        } else if (ev->error_handle && ev->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
            ESP_LOGW(TAG, "connection refused (%d): check MQTT user/password", ev->error_handle->connect_return_code);
        } else {
            ESP_LOGW(TAG, "error");
        }
        break;
    default:
        break;
    }
}

esp_err_t mqtt_link_start(mqtt_msg_cb_t on_msg, mqtt_status_cb_t on_status)
{
    s_on_msg = on_msg;
    s_on_status = on_status;
    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = PET_MQTT_URI,
        .credentials.username = PET_MQTT_USER,
        .credentials.client_id = "pet-box",
        .credentials.authentication.password = PET_MQTT_PASS,
        .session.keepalive = 30,
        .network.reconnect_timeout_ms = 5000,
        .buffer.size = 4096,
    };
    s_client = esp_mqtt_client_init(&cfg);
    if (!s_client) {
        return ESP_FAIL;
    }
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, handler, NULL));
    return esp_mqtt_client_start(s_client);
}

bool mqtt_link_connected(void)
{
    return s_connected;
}

bool mqtt_link_publish(const char *topic, const char *payload)
{
    if (!s_client) {
        return false;
    }
    int id = esp_mqtt_client_enqueue(s_client, topic, payload, 0, 1, 0, true);
    if (id < 0) {
        ESP_LOGW(TAG, "publish to %s not queued (%d)", topic, id);
        return false;
    }
    ESP_LOGI(TAG, "queued %s (msg_id %d%s)", topic, id, s_connected ? "" : ", link down, sent if it is back within 30 s");
    return true;
}
