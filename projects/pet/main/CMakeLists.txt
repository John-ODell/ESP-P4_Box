idf_component_register(SRCS "main.c" "face.c" "anim.c" "sprites.c" "sched.c" "net.c" "mqtt_link.c" "pet_state.c" "pet_notify.c" "ota.c" "settings.c" "pet_pack.c" "menu.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_wifi esp_event nvs_flash esp_netif esp_timer esp_hw_support esp_driver_ledc lwip mqtt json
                                  esp_https_ota esp_http_client app_update esp_app_format esp_partition)
