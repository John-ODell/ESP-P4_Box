idf_component_register(SRCS "main.c" "face.c" "net.c" "mqtt_link.c" "pet_state.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_wifi esp_event nvs_flash esp_netif esp_timer mqtt json)
