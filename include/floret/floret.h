// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret.h
//
//  Floret: a run-to-completion kernel. Wait for the event, and do only
//  what's necessary to process it. See docs/design.md.
//
//  Context rules, in short:
//      floret_signal(), floret_tick()       - anywhere, including interrupts
//      everything else                      - thread context only (main code
//                                             and task handlers)
//------------------------------------------------------------------------------
#ifndef FLORET_H
#define FLORET_H

#include <stdbool.h>
#include <stdint.h>

#include "floret/floret_config.h"

#ifdef __cplusplus
extern "C" {
#endif

//------------------------------------------------------------------------------
//  Tasks
//------------------------------------------------------------------------------
typedef uint8_t floret_id_t;                                                    // row in the task table; row 0 is the highest priority

#define FLORET_ID_INVALID ((floret_id_t)0xFF)                                   // returned by floret_find() when no task matches

typedef enum {
    FLORET_ISR = 0,                                                             // events from interrupt handlers
    FLORET_SYS = 1,                                                             // system events, defined by Floret
    FLORET_USR = 2,                                                             // events from the application
    FLORET_CLASS_COUNT = 3
} floret_class_t;

typedef void (*floret_init_fn)(void* arg, floret_id_t id);                      // called once by floret_start()
typedef void (*floret_event_fn)(uint32_t events, void* arg);                    // called with this class's pending events

typedef struct {
    const char*     name;                                                       // used by floret_find() and Flight Test
    floret_init_fn  init;                                                       // may be NULL
    floret_event_fn isr;                                                        // may be NULL
    floret_event_fn sys;                                                        // may be NULL
    floret_event_fn usr;                                                        // may be NULL
    void*           arg;                                                        // passed to every function above
} floret_task_t;

//------------------------------------------------------------------------------
//  System events (FLORET_SYS). Every sys bit belongs to Floret.
//------------------------------------------------------------------------------
#define FLORET_SYS_START     (1u << 0)                                          // sent to every task after all init() calls
#define FLORET_SYS_STOP      (1u << 1)                                          // reserved: not sent in version 1
#define FLORET_SYS_ERROR     (1u << 2)                                          // reserved: not sent in version 1
#define FLORET_SYS_LOW_POWER (1u << 3)                                          // reserved: not sent in version 1

//------------------------------------------------------------------------------
//  Function: floret_init
//      Install the task table. The table must stay valid for the life of the
//      program (normally it is const and lives in flash). Call once, before
//      floret_start().
//
//  Parameters:
//      table - the task table; row 0 is the highest priority
//      count - number of rows, 1..FLORET_MAX_TASKS
//------------------------------------------------------------------------------
void floret_init(const floret_task_t* table, floret_id_t count);

//------------------------------------------------------------------------------
//  Function: floret_start
//      Call every task's init() in table order, then signal FLORET_SYS_START
//      to every task. Interrupts that signal tasks may be enabled before or
//      after this call; events wait until floret_run().
//------------------------------------------------------------------------------
void floret_start(void);

//------------------------------------------------------------------------------
//  Function: floret_run
//      Dispatch events forever: always the highest-priority ready task, one
//      handler call at a time; sleep when nothing is ready.
//------------------------------------------------------------------------------
#ifdef __cplusplus
[[noreturn]] void floret_run(void);
#else
_Noreturn void floret_run(void);
#endif

//------------------------------------------------------------------------------
//  Function: floret_step
//      Do one unit of work: process expired timers, then dispatch at most one
//      handler of the highest-priority ready task. Never sleeps.
//      floret_run() is this in a loop, sleeping when it returns false. Call it
//      directly to run Floret inside an existing main loop, or in tests.
//
//  Returns:
//      true if a task was ready, false if there was nothing to do
//------------------------------------------------------------------------------
bool floret_step(void);

//------------------------------------------------------------------------------
//  Function: floret_signal
//      Set event bits for a task. Lock-free and safe from any context,
//      including interrupts at any priority. Never masks interrupts.
//
//  Parameters:
//      task - task id
//      cls  - FLORET_ISR or FLORET_USR (FLORET_SYS belongs to the kernel)
//      bits - events to set; bits already pending are merged
//
//  Returns:
//      true, or false if task or cls is invalid
//------------------------------------------------------------------------------
bool floret_signal(floret_id_t task, floret_class_t cls, uint32_t bits);

//------------------------------------------------------------------------------
//  Function: floret_mask
//      Choose which events of a class are dispatched. Masked events stay
//      pending and are dispatched once unmasked. All bits start enabled.
//
//  Parameters:
//      task        - task id
//      cls         - event class
//      enable_bits - bits that may be dispatched
//------------------------------------------------------------------------------
void floret_mask(floret_id_t task, floret_class_t cls, uint32_t enable_bits);

//------------------------------------------------------------------------------
//  Function: floret_find
//      Look up a task by name.
//
//  Returns:
//      task id, or FLORET_ID_INVALID
//------------------------------------------------------------------------------
floret_id_t floret_find(const char* name);

//------------------------------------------------------------------------------
//  Function: floret_count
//      Number of tasks in the table.
//------------------------------------------------------------------------------
floret_id_t floret_count(void);

//------------------------------------------------------------------------------
//  Time: the tick (coarse, drives timers) - see docs/design.md, section 7.
//  Fine time comes from floret_port_now().
//------------------------------------------------------------------------------

//------------------------------------------------------------------------------
//  Function: floret_tick
//      Advance the kernel tick. Call from a periodic interrupt (on a Daisy,
//      the audio callback). Interrupt-safe and short: timers are processed
//      later, in the dispatcher.
//------------------------------------------------------------------------------
void floret_tick(void);

//------------------------------------------------------------------------------
//  Function: floret_ticks
//      Ticks since floret_start(). Wraps after 2^32 ticks.
//------------------------------------------------------------------------------
uint32_t floret_ticks(void);

//------------------------------------------------------------------------------
//  Timers
//
//  The caller owns each timer's storage (usually static). Treat the fields as
//  private. Start and stop timers from thread context only.
//------------------------------------------------------------------------------
typedef struct floret_timer {
    struct floret_timer* next;                                                  // private: active-timer list
    uint32_t             due;                                                   // private: tick of next expiry
    uint32_t             period;                                                // private: 0 = one-shot
    uint32_t             bits;                                                  // private: usr events to signal
    floret_id_t          task;                                                  // private: task to signal
    bool                 active;                                                // private
} floret_timer_t;

//------------------------------------------------------------------------------
//  Function: floret_timer_start
//      Start (or restart) a timer. On expiry it signals usr_bits to task.
//
//  Parameters:
//      t            - timer storage
//      task         - task to signal
//      usr_bits     - FLORET_USR events to signal on expiry
//      delay_ticks  - ticks until the first expiry (0 is treated as 1)
//      period_ticks - ticks between later expiries; 0 for a one-shot
//------------------------------------------------------------------------------
void floret_timer_start(floret_timer_t* t, floret_id_t task, uint32_t usr_bits,
                        uint32_t delay_ticks, uint32_t period_ticks);

//------------------------------------------------------------------------------
//  Function: floret_timer_stop
//      Stop a timer. Stopping an inactive timer does nothing.
//------------------------------------------------------------------------------
void floret_timer_stop(floret_timer_t* t);

//------------------------------------------------------------------------------
//  Function: floret_timer_active
//      true while the timer is running.
//------------------------------------------------------------------------------
bool floret_timer_active(const floret_timer_t* t);

#ifdef __cplusplus
}
#endif

#endif // FLORET_H
