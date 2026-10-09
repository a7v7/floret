// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_atomic.h (internal)
//
//  The few atomic operations Floret uses, in one place. They are the GCC /
//  Clang __atomic builtins, which work on plain uint32_t (so public structs
//  stay valid C++) and compile to LDREX/STREX on Cortex-M3/M4/M7. On parts
//  without LDREX/STREX (Cortex-M0/M0+) the compiler calls __atomic_*_4
//  helpers, which that port must provide.
//------------------------------------------------------------------------------
#ifndef FLORET_ATOMIC_H
#define FLORET_ATOMIC_H

#include <stdint.h>

static inline uint32_t fa_load(const uint32_t* p)
{
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static inline void fa_store(uint32_t* p, uint32_t v)
{
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

static inline uint32_t fa_or(uint32_t* p, uint32_t v)                           // returns the previous value
{
    return __atomic_fetch_or(p, v, __ATOMIC_ACQ_REL);
}

static inline uint32_t fa_and(uint32_t* p, uint32_t v)                          // returns the previous value
{
    return __atomic_fetch_and(p, v, __ATOMIC_ACQ_REL);
}

static inline void fa_inc(uint32_t* p)
{
    (void)__atomic_fetch_add(p, 1u, __ATOMIC_ACQ_REL);
}

#endif // FLORET_ATOMIC_H
