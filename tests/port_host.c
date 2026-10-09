// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  port_host.c
//
//  Floret port for unit tests on a PC. Time is a variable the tests control,
//  so timing results are exact and repeatable.
//------------------------------------------------------------------------------
#include "floret/floret_port.h"
#include "port_host.h"

uint32_t port_host_now;                                                         // the fake clock
uint32_t port_host_notifies;                                                    // floret_port_notify() calls

uint32_t floret_port_now(void)
{
    return port_host_now;
}

uint32_t floret_port_now_hz(void)
{
    return 1000000u;                                                            // 1 count = 1 microsecond
}

void floret_port_idle_wait(void)
{
}

void floret_port_notify(void)
{
    port_host_notifies++;
}
