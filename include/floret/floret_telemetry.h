// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_telemetry.h
//
//  The telemetry block: the on-target part of Flight Test. The kernel keeps
//  it up to date; the Downlink host tool reads it over SWD while the target
//  runs, and writes commands into its uplink word.
//
//  The layout is a contract with Downlink: the offsets below are checked at
//  compile time on 32-bit targets. See docs/flight_test.md.
//------------------------------------------------------------------------------
#ifndef FLORET_TELEMETRY_H
#define FLORET_TELEMETRY_H

#include <stddef.h>
#include <stdint.h>

#include "floret/floret.h"

#if FLORET_FLIGHT_TEST

#ifdef __cplusplus
extern "C" {
#endif

#define FLORET_TELEMETRY_MAGIC   0x54524C46u                                    // "FLRT" in memory order
#define FLORET_TELEMETRY_VERSION 1u

//------------------------------------------------------------------------------
//  Uplink commands (bits in floret_telemetry_t.uplink)
//------------------------------------------------------------------------------
#define FLORET_UPLINK_RESET_MAX (1u << 0)                                       // reset every max_time

//------------------------------------------------------------------------------
//  Statistics for one handler (one class of one task). Times are in
//  floret_port_now() counts. All counters wrap.
//------------------------------------------------------------------------------
typedef struct {
    uint32_t time;                                                              // total time in this handler
    uint32_t runs;                                                              // dispatches of this handler
    uint32_t max_time;                                                          // longest single dispatch
} floret_class_stats_t;

typedef struct {
    const char*          name;                                                  // task name, from the task table
    floret_class_stats_t cls[FLORET_CLASS_COUNT];                               // indexed by floret_class_t
} floret_task_stats_t;

typedef struct {
    uint32_t            magic;                                                  // FLORET_TELEMETRY_MAGIC
    uint32_t            version;                                                // FLORET_TELEMETRY_VERSION
    uint32_t            clock_hz;                                               // floret_port_now_hz()
    uint32_t            task_count;                                             // valid entries in task[]
    uint32_t            now;                                                    // floret_port_now() at the last update
    uint32_t            idle_time;                                              // time spent in floret_port_idle_wait()
    uint32_t            ticks;                                                  // kernel ticks
    volatile uint32_t   uplink;                                                 // written by Downlink
    floret_task_stats_t task[FLORET_MAX_TASKS];
} floret_telemetry_t;

//------------------------------------------------------------------------------
//  Function: floret_telemetry_get
//      Read-only view of the telemetry block, for code on the target.
//      Downlink finds the block through the ELF symbol floret_telemetry.
//
//      The block is volatile: its real reader is the debug probe, which the
//      compiler can't see. Without volatile, GCC (especially with LTO) may
//      treat the block as write-only and delete the updates.
//------------------------------------------------------------------------------
const volatile floret_telemetry_t* floret_telemetry_get(void);

//------------------------------------------------------------------------------
//  Layout checks: the Downlink contract (32-bit targets only; a 64-bit host
//  build used for unit tests has wider pointers and is never read by
//  Downlink).
//------------------------------------------------------------------------------
#ifdef __cplusplus
#define FLORET_STATIC_ASSERT(expr, msg) static_assert(expr, msg)
#else
#define FLORET_STATIC_ASSERT(expr, msg) _Static_assert(expr, msg)
#endif

#if UINTPTR_MAX == 0xFFFFFFFFu
FLORET_STATIC_ASSERT(sizeof(floret_class_stats_t) == 12, "class stats are 12 bytes");
FLORET_STATIC_ASSERT(sizeof(floret_task_stats_t) == 40, "task stats are 40 bytes");
FLORET_STATIC_ASSERT(offsetof(floret_task_stats_t, cls) == 0x04, "isr stats at +0x04");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, magic) == 0x00, "magic at 0x00");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, version) == 0x04, "version at 0x04");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, clock_hz) == 0x08, "clock_hz at 0x08");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, task_count) == 0x0C, "task_count at 0x0C");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, now) == 0x10, "now at 0x10");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, idle_time) == 0x14, "idle_time at 0x14");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, ticks) == 0x18, "ticks at 0x18");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, uplink) == 0x1C, "uplink at 0x1C");
FLORET_STATIC_ASSERT(offsetof(floret_telemetry_t, task) == 0x20, "task[0] at 0x20");
FLORET_STATIC_ASSERT(sizeof(floret_telemetry_t) == 0x20 + 40 * FLORET_MAX_TASKS, "no padding");
#endif

#ifdef __cplusplus
}
#endif

#endif // FLORET_FLIGHT_TEST

#endif // FLORET_TELEMETRY_H
