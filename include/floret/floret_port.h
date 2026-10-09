// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret_port.h
//
//  Everything Floret needs from the hardware. A port implements these five
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
//  Function: floret_port_lock
//      Hold off interrupt handlers. The kernel uses this only around going
//      to sleep: it locks, checks that there is still no work, sleeps, reads
//      the clock, and unlocks. On Cortex-M: save PRIMASK, then __disable_irq().
//
//  Returns:
//      State to pass to floret_port_unlock()
//------------------------------------------------------------------------------
uint32_t floret_port_lock(void);

//------------------------------------------------------------------------------
//  Function: floret_port_unlock
//      Restore the interrupt state saved by floret_port_lock(). Interrupts
//      that became pending meanwhile are handled now.
//------------------------------------------------------------------------------
void floret_port_unlock(uint32_t state);

//------------------------------------------------------------------------------
//  Function: floret_port_sleep
//      Sleep until an interrupt is pending. Called with interrupts held off
//      by floret_port_lock(): a pending interrupt must still end the sleep,
//      but its handler runs only after floret_port_unlock(). This is how
//      Cortex-M WFI behaves, so on Cortex-M: __DSB(); __WFI().
//
//      Because the kernel checks for work with interrupts held off and then
//      sleeps, an event signaled just before the sleep can't be missed: its
//      interrupt is still pending, so the sleep ends at once. And because
//      the handler runs after the kernel has read the clock, interrupt time
//      is never counted as idle time.
//------------------------------------------------------------------------------
void floret_port_sleep(void);

#ifdef __cplusplus
}
#endif

#endif // FLORET_PORT_H
