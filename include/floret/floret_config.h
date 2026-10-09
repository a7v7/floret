// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_config.h
//
//  Build-time configuration. Every option has a default here; override it
//  with a compiler definition (-DFLORET_MAX_TASKS=8), or name a header of
//  your own with -DFLORET_CONFIG_FILE="my_floret_config.h".
//------------------------------------------------------------------------------
#ifndef FLORET_CONFIG_H
#define FLORET_CONFIG_H

#ifdef FLORET_CONFIG_FILE
#include FLORET_CONFIG_FILE
#endif

//------------------------------------------------------------------------------
//  FLORET_MAX_TASKS
//      Rows in the task table. The ready set is one 32-bit word, one bit per
//      task, so the maximum is 32.
//------------------------------------------------------------------------------
#ifndef FLORET_MAX_TASKS
#define FLORET_MAX_TASKS 16
#endif

#if (FLORET_MAX_TASKS < 1) || (FLORET_MAX_TASKS > 32)
#error "FLORET_MAX_TASKS must be between 1 and 32"
#endif

//------------------------------------------------------------------------------
//  FLORET_FLIGHT_TEST
//      1 keeps the telemetry block and per-dispatch timing (see
//      docs/flight_test.md); 0 removes both.
//------------------------------------------------------------------------------
#ifndef FLORET_FLIGHT_TEST
#define FLORET_FLIGHT_TEST 1
#endif

//------------------------------------------------------------------------------
//  FLORET_ASSERT(expr)
//      Called with conditions that must hold, such as a valid task id.
//      Default: nothing. Point it at your own handler during development.
//------------------------------------------------------------------------------
#ifndef FLORET_ASSERT
#define FLORET_ASSERT(expr) ((void)0)
#endif

#endif // FLORET_CONFIG_H
