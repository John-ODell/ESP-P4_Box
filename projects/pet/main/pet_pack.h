#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "pet_def.h"

/* The pet to draw: the pack at the start of the `storage` partition (notes/pet-packs.md) when it passes every check,
 * otherwise the built-in pet, with the reason logged. Call once at boot, before anything else uses cJSON (it installs
 * cJSON's allocator hooks for good); the result lives as long as the program. */
const pet_def_t *pet_pack_load(void);

/* A whole pack in memory (#68): every check pet_pack_load() makes, without loading it. On success, the pet's name
 * and the pack's CRC; on failure the reason is in pet_pack_why(). One check at a time; any task. */
bool pet_pack_check(const uint8_t *buf, size_t len, char *name, size_t name_len, uint32_t *crc);
const char *pet_pack_why(void);
uint32_t pet_pack_loaded_crc(void);   /* the CRC of the pack in use, 0 when the built-in pet is drawn */
