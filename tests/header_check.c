// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  header_check.c
//
//  Compiles every public header on its own (each must be self-contained) and
//  together. Built for the host and for a 32-bit target, in C and C++, so the
//  telemetry layout checks run where they matter. Contains no tests.
//------------------------------------------------------------------------------
#include "floret/floret_config.h"
#include "floret/floret_port.h"
#include "floret/floret.h"
#include "floret/floret_queue.h"
#include "floret/floret_telemetry.h"

int floret_header_check(void);

int floret_header_check(void)
{
    return (int)sizeof(floret_task_t) + (int)sizeof(floret_queue_t);
}
