#pragma once
#include "pet_def.h"

/* The pet to draw: the pack at the start of the `storage` partition (notes/pet-packs.md) when it passes every check,
 * otherwise the built-in pet, with the reason logged. Call once at boot; the result lives as long as the program. */
const pet_def_t *pet_pack_load(void);
