// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  port_host.h - controls for the unit-test port (see port_host.c)
//------------------------------------------------------------------------------
#ifndef PORT_HOST_H
#define PORT_HOST_H

#include <stdint.h>

extern uint32_t port_host_now;                                                  // value floret_port_now() returns
extern uint32_t port_host_sleep_time;                                           // clock advance per floret_port_sleep()
extern uint32_t port_host_sleeps;                                               // floret_port_sleep() calls
extern int      port_host_lock_depth;                                           // > 0 while locked
extern void (*port_host_on_unlock)(void);                                       // pretend interrupt, run once at unlock

#endif // PORT_HOST_H
