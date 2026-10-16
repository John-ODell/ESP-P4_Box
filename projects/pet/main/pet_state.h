#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Mirrors the `pet/state` JSON published by bridge/ (schema v1, see bridge/README.md). */
enum {
    PET_EVENT_LEVEL_UP = 1 << 0,
    PET_EVENT_GOAL_HIT = 1 << 1,
    PET_EVENT_BIG_DAY = 1 << 2,
};

typedef struct {
    bool valid;
    int v;
    char ts[32];
    char login[40];
    int level, xp, xp_into, xp_next;
    int mood;
    char mood_label[16];
    int streak;
    bool streak_at_risk;
    struct {
        int total, commits, prs, issues, reviews, other, xp;
    } today;
    int goal;
    bool goal_hit, big_day;
    uint32_t events;
} pet_state_t;

bool pet_state_parse(const char *json, size_t len, pet_state_t *out);
