// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  port_host.c
//
//  Floret port for unit tests on a PC. Time is a variable the tests control,
//  so timing results are exact and repeatable. Sleeping advances the clock,
//  and a test can supply a pretend interrupt handler that "runs" when the
//  kernel unlocks, as a real pending interrupt would.
//------------------------------------------------------------------------------
#include <stddef.h>

#include "floret/floret_port.h"
#include "port_host.h"

uint32_t port_host_now;
uint32_t port_host_sleep_time;
uint32_t port_host_sleeps;
int      port_host_lock_depth;
void (*port_host_on_unlock)(void);

uint32_t floret_port_now(void)
{
    return port_host_now;
}

uint32_t floret_port_now_hz(void)
{
    return 1000000u;                                                            // 1 count = 1 microsecond
}

uint32_t floret_port_lock(void)
{
    return (uint32_t)port_host_lock_depth++;
}

void floret_port_unlock(uint32_t state)
{
    port_host_lock_depth = (int)state;
    if(port_host_lock_depth == 0 && port_host_on_unlock != NULL)
    {
        void (*isr)(void)   = port_host_on_unlock;                              // one pretend interrupt
        port_host_on_unlock = NULL;
        isr();
    }
}

void floret_port_sleep(void)
{
    port_host_sleeps++;
    port_host_now += port_host_sleep_time;
}
