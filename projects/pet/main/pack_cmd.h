#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Pet packs over the LAN (#68). The Mac serves a .petpack over plain HTTP and says {"pack": "<url>", "sha256":
 * "<hex>"} on pet/cmd; the box downloads it into PSRAM, checks the SHA-256 and every check the boot loader makes,
 * writes it to the start of the storage partition and reboots into it. Anything that fails leaves the current pack
 * alone. Progress and the outcome go to the UART, the face's status line, and the retained pet/pack message. */
bool pack_handle_cmd(const char *payload, size_t len);   /* a pet/cmd payload; false when it is not a pack command */
void pack_report_running(const char *pet_name);         /* MQTT is up: say on pet/pack which pet the box draws */
bool pack_busy(void);                                   /* a pack is downloading or being written */
