// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  port_host.h - controls for the unit-test port (see port_host.c)
//------------------------------------------------------------------------------
#ifndef PORT_HOST_H
#define PORT_HOST_H

#include <stdint.h>

extern uint32_t port_host_now;                                                  // value floret_port_now() returns
extern uint32_t port_host_notifies;                                             // count of floret_port_notify() calls

#endif // PORT_HOST_H
