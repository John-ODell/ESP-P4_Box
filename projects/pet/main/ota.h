#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Updates over the LAN (#16). The Mac serves a pet.bin over plain HTTP and says {"ota": "<url>"} on pet/cmd; the box
 * writes it into the other app slot, reboots into it, and the bootloader keeps that image only if ota_confirm_running()
 * is reached (we call it when MQTT connects). A reset before that boots the previous image again.
 * Progress and the outcome go to the UART, the status line of the face, and the retained pet/ota message. */

void ota_report_boot(void);                             /* UART: which image and slot run, and whether it is on trial */
void ota_confirm_running(void);                         /* MQTT is up: keep a trial image; publish pet/ota "running" */
bool ota_handle_cmd(const char *payload, size_t len);   /* a pet/cmd payload; false when it is not an ota command */
bool ota_busy(void);                                    /* an update is downloading or verifying */
