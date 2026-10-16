#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Mirrors the retained `pet/notify` list the bridge publishes (bridge/README.md, "Session notifications"): the AI
 * sessions that wait for John, newest first. Field sizes follow the bridge's caps in bridge/petbrain/events.py; a
 * shorter buffer would truncate a name and the acknowledgement would then miss its entry. */
#define PET_NOTIFY_SHOWN 8

typedef struct {
    char source[33];   /* claude, codex, local, ... (lower case) */
    char session[65];  /* the session's name: the project folder for Claude Code and Codex */
    char kind[16];     /* needs_input | done | error */
    char title[81];    /* first line of what the session said, may be empty */
    char ts[32];       /* when the entry was last updated (ISO 8601, the bridge's clock) */
} pet_notice_t;

typedef struct {
    bool valid;
    int count;                              /* entries the bridge holds; can exceed n */
    int n;                                  /* entries listed here */
    pet_notice_t items[PET_NOTIFY_SHOWN];   /* newest first */
} pet_notify_t;

bool pet_notify_parse(const char *json, size_t len, pet_notify_t *out);

/* The `pet/ack` message that drops one entry at the bridge: {"source": ..., "session": ...}. malloc'd; caller frees. */
char *pet_notify_ack_json(const char *source, const char *session);
