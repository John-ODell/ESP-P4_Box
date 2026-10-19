#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Screenshots over the LAN (#86). tools/screenshot.py serves an upload address and says {"shot": "<url>"} on pet/cmd;
 * the box renders its screen with LVGL's snapshot (720x720 RGB565, the panel's format) and POSTs the pixels there,
 * with X-Width, X-Height, X-Stride and X-Format headers, or an empty body and X-Error when it has none. Nothing shows
 * on screen, so the shot holds only what the pet draws. */
bool shot_handle_cmd(const char *payload, size_t len);   /* a pet/cmd payload; false when it is not a shot command */
