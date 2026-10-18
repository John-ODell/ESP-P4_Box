/* Pet packs (#67): the pet's look, read from the start of the `storage` partition at boot. The format and its limits
 * are in notes/pet-packs.md, and tools/petpack.py's parse() is the reference for what a loader must refuse. A pack
 * that fails any check is logged and the built-in pet is drawn instead; a pack never stops the box from booting. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pet_pack.h"
#include "sprites.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"

static const char *TAG = "pet_pack";

#define PACK_HEADER     24
#define PACK_FORMAT     1
#define FRAME_PIXELS    (PET_SPRITE_W * PET_SPRITE_H)
#define FRAME_BYTES     (FRAME_PIXELS / 8)
#define MAX_JSON        (64 * 1024)
#define MAX_FRAMES      1024
#define MAX_ANIMS       128
#define MAX_ANIM_FRAMES 64
#define MAX_ANIM_NAME   32
#define MAX_NAME_CHARS  24
#define MAX_NAME_BYTES  48
#define MAX_PACK        (PACK_HEADER + MAX_JSON + MAX_FRAMES * FRAME_BYTES)

static const char *const MOOD_KEYS[PET_MOOD_CLASSES] = {"sad", "neutral", "happy", "ecstatic", "worried"};
static const char *const REACTION_KEYS[PET_REACTIONS] = {"celebrate", "notice", "sleep"};

static char s_why[112];   /* the first problem found, for the log */

static bool bad(const char *fmt, ...)
{
    if (!s_why[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(s_why, sizeof(s_why), fmt, ap);
        va_end(ap);
    }
    return false;
}

/* One PSRAM block holds everything a loaded pet needs, so a failure frees it in one go. */
typedef struct { uint8_t *base; size_t used, size; } arena_t;

static void *take(arena_t *a, size_t n)
{
    size_t at = (a->used + 7) & ~(size_t)7;
    if (at + n > a->size) {
        return NULL;   /* sized up front from the counts, so this means a bug, not a bad pack */
    }
    a->used = at + n;
    return a->base + at;
}

/* A JSON whole number in [lo, hi]. cJSON cannot tell 1 from 1.0, so 1.0 passes here; petpack.py refuses it. */
static bool int_value(const cJSON *v, int lo, int hi, int *out)
{
    if (!cJSON_IsNumber(v) || v->valuedouble < lo || v->valuedouble > hi || v->valuedouble != (int)v->valuedouble) {
        return false;
    }
    *out = (int)v->valuedouble;
    return true;
}

static bool int_field(const cJSON *obj, const char *key, int lo, int hi, int dflt, int *out, const char *where)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (!v && dflt != INT32_MIN) {
        *out = dflt;
        return true;
    }
    if (!int_value(v, lo, hi, out)) {
        return bad("%s.%s must be a whole number from %d to %d", where, key, lo, hi);
    }
    return true;
}

static bool colour(const cJSON *v, uint32_t *out, const char *where)
{
    const char *s = cJSON_GetStringValue(v);
    if (!s || strlen(s) != 7 || s[0] != '#' || strspn(s + 1, "0123456789abcdefABCDEF") != 6) {
        return bad("%s must be a colour like \"#4FD1C5\"", where);
    }
    *out = strtoul(s + 1, NULL, 16);
    return true;
}

static int list_len(const cJSON *v)
{
    return cJSON_IsArray(v) ? cJSON_GetArraySize(v) : -1;
}

static bool frame_list(const cJSON *arr, int nframes, pet_frame_t *out, uint16_t *count, const char *where)
{
    int n = list_len(arr);
    if (n < 1 || n > MAX_ANIM_FRAMES) {
        return bad("%s needs 1 to %d frames", where, MAX_ANIM_FRAMES);
    }
    const cJSON *f;
    int i = 0, k, dx, dy, ms;
    cJSON_ArrayForEach(f, arr) {
        if (!cJSON_IsObject(f)) {
            return bad("%s: each frame is an object", where);
        }
        if (!int_field(f, "frame", 0, nframes - 1, INT32_MIN, &k, where) || !int_field(f, "dx", -8, 8, 0, &dx, where)
            || !int_field(f, "dy", -8, 8, 0, &dy, where) || !int_field(f, "ms", 20, 10000, 500, &ms, where)) {
            return false;
        }
        out[i++] = (pet_frame_t){ .img = k, .dx = dx, .dy = dy, .ms = ms };
    }
    *count = n;
    return true;
}

static int anim_index(const pet_def_t *def, const char *name)
{
    for (int i = 0; i < def->anim_count; i++) {
        if (strcmp(def->anims[i].name, name) == 0) {
            return i;
        }
    }
    return -1;
}

/* Counts first, so one block can be sized before anything is filled in; shape errors surface here. */
typedef struct { int anims, anim_frames, idle; } counts_t;

static bool count(const cJSON *root, counts_t *c)
{
    const cJSON *anims = cJSON_GetObjectItemCaseSensitive(root, "animations");
    const cJSON *moods = cJSON_GetObjectItemCaseSensitive(root, "moods");
    const cJSON *play = cJSON_GetObjectItemCaseSensitive(root, "play");
    if (!cJSON_IsObject(anims) || !cJSON_IsObject(moods) || !cJSON_IsObject(play)) {
        return bad("animations, moods and play are required objects");
    }
    memset(c, 0, sizeof(*c));
    const cJSON *a;
    cJSON_ArrayForEach(a, anims) {
        int n = list_len(a);
        if (n < 1 || n > MAX_ANIM_FRAMES) {
            return bad("animation %.32s needs 1 to %d frames", a->string, MAX_ANIM_FRAMES);
        }
        c->anims++;
        c->anim_frames += n;
    }
    if (c->anims < 1 || c->anims > MAX_ANIMS) {
        return bad("%d animations, 1 to %d allowed", c->anims, MAX_ANIMS);
    }
    for (int m = 0; m < PET_MOOD_CLASSES; m++) {
        const cJSON *idle = cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(moods, MOOD_KEYS[m]), "idle");
        int n = cJSON_IsObject(idle) ? cJSON_GetArraySize(idle) : 0;
        if (n < 1 || n > MAX_ANIMS) {
            return bad("moods.%s.idle needs 1 to %d animations", MOOD_KEYS[m], MAX_ANIMS);
        }
        c->idle += n;
    }
    for (int i = 0; i < 2; i++) {
        int n = list_len(cJSON_GetObjectItemCaseSensitive(play, i ? "heart" : "snack"));
        if (n < 1 || n > MAX_ANIM_FRAMES) {
            return bad("play.%s needs 1 to %d frames", i ? "heart" : "snack", MAX_ANIM_FRAMES);
        }
        c->anim_frames += n;
    }
    return true;
}

static bool fill(const cJSON *root, int nframes, pet_def_t *def, arena_t *ar, pet_frame_t *frames,
                 pet_idle_choice_t *idle)
{
    int fmt;
    if (!int_field(root, "format", PACK_FORMAT, PACK_FORMAT, INT32_MIN, &fmt, "pack")) {
        return false;
    }
    const cJSON *fw = cJSON_GetObjectItemCaseSensitive(root, "frame_w");
    const cJSON *fh = cJSON_GetObjectItemCaseSensitive(root, "frame_h");
    const cJSON *fc = cJSON_GetObjectItemCaseSensitive(root, "frame_count");
    int w, h, n;
    if (!int_value(fw, PET_SPRITE_W, PET_SPRITE_W, &w) || !int_value(fh, PET_SPRITE_H, PET_SPRITE_H, &h)) {
        return bad("frames must be %dx%d", PET_SPRITE_W, PET_SPRITE_H);
    }
    if (!int_value(fc, nframes, nframes, &n)) {
        return bad("frame_count does not match the pack's length (%d frames)", nframes);
    }

    const char *name = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(root, "name"));
    size_t len = name ? strlen(name) : 0, chars = 0;
    for (size_t i = 0; i < len; i++) {
        unsigned char ch = name[i];
        if (ch < 0x20 || ch == 0x7f) {
            len = 0;
        }
        chars += (ch & 0xc0) != 0x80;
    }
    if (len < 1 || len > MAX_NAME_BYTES || chars > MAX_NAME_CHARS) {
        return bad("name must be 1 to %d printable characters", MAX_NAME_CHARS);
    }
    char *copy = take(ar, len + 1);
    memcpy(copy, name, len + 1);
    def->name = copy;

    static const char *const keys[8] = {"ink", "sleep_ink", "background", "panel", "dim", "accent", "warn", "good"};
    uint32_t *slots[8] = {&def->colors.ink, &def->colors.sleep_ink, &def->colors.background, &def->colors.panel,
                          &def->colors.dim, &def->colors.accent, &def->colors.warn, &def->colors.good};
    const cJSON *colors = cJSON_GetObjectItemCaseSensitive(root, "colors");
    char where[48];
    for (int i = 0; i < 8; i++) {
        snprintf(where, sizeof(where), "colors.%s", keys[i]);
        if (!colour(cJSON_GetObjectItemCaseSensitive(colors, keys[i]), slots[i], where)) {
            return false;
        }
    }

    pet_anim_t *anims = (pet_anim_t *)def->anims;
    const cJSON *a;
    cJSON_ArrayForEach(a, cJSON_GetObjectItemCaseSensitive(root, "animations")) {
        size_t nl = strlen(a->string);
        if (nl < 1 || nl > MAX_ANIM_NAME || strspn(a->string, "abcdefghijklmnopqrstuvwxyz0123456789_/") != nl) {
            return bad("animation name \"%.32s\" is not allowed", a->string);
        }
        pet_anim_t *an = &anims[def->anim_count];
        char *nm = take(ar, nl + 1);
        memcpy(nm, a->string, nl + 1);
        an->name = nm;
        an->frames = frames;
        snprintf(where, sizeof(where), "animation %.32s", nm);
        if (!frame_list(a, nframes, frames, &an->count, where)) {
            return false;
        }
        frames += an->count;
        def->anim_count++;
    }
    for (int r = 0; r < PET_REACTIONS; r++) {
        int i = anim_index(def, REACTION_KEYS[r]);
        if (i < 0) {
            return bad("animation %s is required", REACTION_KEYS[r]);
        }
        def->reaction[r] = i;
    }

    const cJSON *moods = cJSON_GetObjectItemCaseSensitive(root, "moods");
    for (int m = 0; m < PET_MOOD_CLASSES; m++) {
        const cJSON *mood = cJSON_GetObjectItemCaseSensitive(moods, MOOD_KEYS[m]);
        int rest;
        snprintf(where, sizeof(where), "moods.%s", MOOD_KEYS[m]);
        if (!int_field(mood, "rest", 0, nframes - 1, INT32_MIN, &rest, where)) {
            return false;
        }
        def->rest[m] = rest;
        def->idle[m] = idle;
        const cJSON *choice;
        cJSON_ArrayForEach(choice, cJSON_GetObjectItemCaseSensitive(mood, "idle")) {
            int i = anim_index(def, choice->string), weight;
            if (i < 0) {
                return bad("moods.%s.idle: no animation called %.32s", MOOD_KEYS[m], choice->string);
            }
            if (!int_value(choice, 1, 255, &weight)) {
                return bad("moods.%s.idle.%.32s: weight must be 1 to 255", MOOD_KEYS[m], choice->string);
            }
            *idle++ = (pet_idle_choice_t){ .anim = i, .weight = weight };
            def->idle_len[m]++;
        }
    }

    const cJSON *play = cJSON_GetObjectItemCaseSensitive(root, "play");
    def->snack = (pet_anim_t){ .name = "snack", .frames = frames };
    if (!frame_list(cJSON_GetObjectItemCaseSensitive(play, "snack"), nframes, frames, &def->snack.count, "play.snack")) {
        return false;
    }
    frames += def->snack.count;
    def->heart = (pet_anim_t){ .name = "heart", .frames = frames };
    if (!frame_list(cJSON_GetObjectItemCaseSensitive(play, "heart"), nframes, frames, &def->heart.count, "play.heart")) {
        return false;
    }
    const cJSON *pads = cJSON_GetObjectItemCaseSensitive(play, "pads");
    if (list_len(pads) != 4) {
        return bad("play.pads needs 4 colours");
    }
    for (int i = 0; i < 4; i++) {
        snprintf(where, sizeof(where), "play.pads[%d]", i);
        if (!colour(cJSON_GetArrayItem(pads, i), &def->pads[i], where)) {
            return false;
        }
    }
    return true;
}

/* cJSON allocates every node and key on its own, and on this heap each malloc costs about 90 us (measured on the
 * box: a 64 KB pack took 2.5 s to parse and 0.4 s to free). While a pack loads, cJSON takes memory from a few large
 * PSRAM chunks instead, and they are all freed together. The hooks are global, which is safe only because this runs
 * at boot before anything else uses cJSON. */
typedef struct chunk { struct chunk *next; size_t used, cap; uint8_t data[]; } chunk_t;
#define CHUNK_SIZE (64 * 1024)
static chunk_t *s_chunks;

static void *chunk_malloc(size_t n)
{
    n = (n + 7) & ~(size_t)7;
    if (!s_chunks || s_chunks->used + n > s_chunks->cap) {
        size_t cap = n > CHUNK_SIZE ? n : CHUNK_SIZE;
        chunk_t *c = heap_caps_malloc(sizeof(chunk_t) + cap, MALLOC_CAP_SPIRAM);
        if (!c) {
            return NULL;
        }
        *c = (chunk_t){ .next = s_chunks, .used = 0, .cap = cap };
        s_chunks = c;
    }
    void *p = s_chunks->data + s_chunks->used;
    s_chunks->used += n;
    return p;
}

static void chunk_free(void *p)
{
    (void)p;   /* everything goes at once in chunks_release */
}

static void chunks_release(void)
{
    while (s_chunks) {
        chunk_t *next = s_chunks->next;
        free(s_chunks);
        s_chunks = next;
    }
}

static const pet_def_t *load(void)
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "storage");
    if (!part) {
        bad("no storage partition");
        return NULL;
    }
    uint8_t hdr[PACK_HEADER];
    esp_err_t err = esp_partition_read(part, 0, hdr, sizeof(hdr));
    if (err != ESP_OK) {
        bad("reading storage failed (%s)", esp_err_to_name(err));
        return NULL;
    }
    if (memcmp(hdr, "PETPACK", 8) != 0) {
        static const uint8_t erased[8] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
        bad(memcmp(hdr, erased, 8) == 0 ? "no pet pack in storage" : "storage does not start with a pet pack");
        return NULL;
    }
    uint32_t fmt, total, crc, jlen;
    memcpy(&fmt, hdr + 8, 4);
    memcpy(&total, hdr + 12, 4);
    memcpy(&crc, hdr + 16, 4);
    memcpy(&jlen, hdr + 20, 4);
    if (fmt != PACK_FORMAT) {
        bad("pack format %u, this firmware reads %d", (unsigned)fmt, PACK_FORMAT);
        return NULL;
    }
    if (total < PACK_HEADER || total > MAX_PACK || total > part->size) {
        bad("pack size %u is out of range", (unsigned)total);
        return NULL;
    }
    if (jlen % 4 || jlen > MAX_JSON || PACK_HEADER + jlen > total || (total - PACK_HEADER - jlen) % FRAME_BYTES) {
        bad("pack metadata length %u does not fit the pack", (unsigned)jlen);
        return NULL;
    }
    int nframes = (total - PACK_HEADER - jlen) / FRAME_BYTES;
    if (nframes < 1 || nframes > MAX_FRAMES) {
        bad("pack has %d frames, 1 to %d allowed", nframes, MAX_FRAMES);
        return NULL;
    }

    const uint8_t *buf;   /* mapped, not copied: flash reads into PSRAM go through a small bounce buffer */
    esp_partition_mmap_handle_t map;
    err = esp_partition_mmap(part, 0, total, ESP_PARTITION_MMAP_DATA, (const void **)&buf, &map);
    if (err != ESP_OK) {
        bad("mapping the pack failed (%s)", esp_err_to_name(err));
        return NULL;
    }
    cJSON *root = NULL;
    arena_t ar = {0};
    pet_def_t *def = NULL;
    if (esp_rom_crc32_le(0, buf + 20, total - 20) != crc) {
        bad("pack checksum does not match");
        goto out;
    }
    size_t n = jlen;   /* by length: when the JSON fills json_len exactly there is no NUL after it */
    while (n && buf[PACK_HEADER + n - 1] == 0) {
        n--;
    }
    cJSON_Hooks hooks = { .malloc_fn = chunk_malloc, .free_fn = chunk_free };
    cJSON_InitHooks(&hooks);
    root = cJSON_ParseWithLength((const char *)buf + PACK_HEADER, n);
    if (!cJSON_IsObject(root)) {
        bad("pack metadata is not a JSON object");
        goto out;
    }
    counts_t c;
    if (!count(root, &c)) {
        goto out;
    }
    ar.size = sizeof(pet_def_t) + nframes * (sizeof(lv_image_dsc_t) + sizeof(void *) + FRAME_PIXELS)
              + c.anims * (sizeof(pet_anim_t) + MAX_ANIM_NAME + 1 + 8) + c.anim_frames * sizeof(pet_frame_t)
              + c.idle * sizeof(pet_idle_choice_t) + MAX_NAME_BYTES + 1 + 16 * 8;
    ar.base = heap_caps_calloc(1, ar.size, MALLOC_CAP_SPIRAM);
    if (!ar.base) {
        bad("no memory for a %u-byte pet", (unsigned)ar.size);
        goto out;
    }
    def = take(&ar, sizeof(*def));
    def->anims = take(&ar, c.anims * sizeof(pet_anim_t));
    pet_frame_t *frames = take(&ar, c.anim_frames * sizeof(pet_frame_t));
    pet_idle_choice_t *idle = take(&ar, c.idle * sizeof(pet_idle_choice_t));
    if (!fill(root, nframes, def, &ar, frames, idle)) {
        def = NULL;
        goto out;
    }

    lv_image_dsc_t *dsc = take(&ar, nframes * sizeof(lv_image_dsc_t));
    const lv_image_dsc_t **images = take(&ar, nframes * sizeof(void *));
    uint8_t *pixels = take(&ar, nframes * FRAME_PIXELS);
    const uint8_t *bits = buf + PACK_HEADER + jlen;
    for (int i = 0; i < nframes; i++) {
        uint8_t *px = pixels + i * FRAME_PIXELS;
        for (int p = 0; p < FRAME_PIXELS; p++) {   /* 1 bit per pixel, first pixel in the top bit */
            px[p] = (bits[i * FRAME_BYTES + p / 8] >> (7 - p % 8)) & 1 ? 255 : 0;
        }
        dsc[i] = (lv_image_dsc_t){
            .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8, .flags = 0,
                        .w = PET_SPRITE_W, .h = PET_SPRITE_H, .stride = PET_SPRITE_W },
            .data_size = FRAME_PIXELS, .data = px };
        images[i] = &dsc[i];
    }
    def->images = images;
    def->image_count = nframes;

out:
    (void)root;   /* no cJSON_Delete: its nodes live in the chunks */
    chunks_release();
    cJSON_InitHooks(NULL);
    esp_partition_munmap(map);
    if (!def) {
        free(ar.base);
    }
    return def;
}

const pet_def_t *pet_pack_load(void)
{
    s_why[0] = 0;
    int64_t t0 = esp_timer_get_time();
    const pet_def_t *def = load();
    if (def) {
        ESP_LOGI(TAG, "pet pack: %s, %u frames, %u animations, ink #%06lX, loaded in %d ms", def->name,
                 (unsigned)def->image_count, (unsigned)def->anim_count, (unsigned long)def->colors.ink,
                 (int)((esp_timer_get_time() - t0) / 1000));
        return def;
    }
    ESP_LOGW(TAG, "using the built-in %s: %s", pet_builtin.name, s_why);
    return &pet_builtin;
}
