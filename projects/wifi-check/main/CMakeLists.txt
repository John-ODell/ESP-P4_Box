idf_component_register(SRCS "wifi_check_main.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_wifi esp_event nvs_flash esp_netif esp_hosted)
