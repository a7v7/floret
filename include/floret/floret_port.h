// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_port.h
//
//  Everything Floret needs from the hardware. A port implements these four
//  functions; the kernel contains no vendor code. See docs/design.md,
//  section 8.
//------------------------------------------------------------------------------
#ifndef FLORET_PORT_H
#define FLORET_PORT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

//------------------------------------------------------------------------------
//  Function: floret_port_now
//      Read a free-running 32-bit counter. It must keep counting while the
//      CPU sleeps (so not the Cortex-M cycle counter, which stops), and it
//      must wrap from 0xFFFFFFFF to 0. Used for Flight Test timing and queue
//      timestamps.
//
//  Returns:
//      Current counter value
//------------------------------------------------------------------------------
uint32_t floret_port_now(void);

//------------------------------------------------------------------------------
//  Function: floret_port_now_hz
//      Frequency of floret_port_now().
//
//  Returns:
//      Counts per second
//------------------------------------------------------------------------------
uint32_t floret_port_now_hz(void);

//------------------------------------------------------------------------------
//  Function: floret_port_idle_wait
//      Sleep until something may have happened. Called by the dispatcher
//      when no task is ready.
//
//      Must return immediately if floret_port_notify() was called since the
//      last return from this function; otherwise an event signaled just
//      before the sleep would wait for an unrelated wake-up. Spurious
//      returns are harmless. On Cortex-M: __WFE().
//------------------------------------------------------------------------------
void floret_port_idle_wait(void);

//------------------------------------------------------------------------------
//  Function: floret_port_notify
//      Wake floret_port_idle_wait(), or make its next call return at once.
//      Called by floret_signal() and floret_tick(), so it must be safe from
//      any interrupt. On Cortex-M: __SEV().
//------------------------------------------------------------------------------
void floret_port_notify(void);

#ifdef __cplusplus
}
#endif

#endif // FLORET_PORT_H
