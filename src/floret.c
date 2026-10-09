// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  floret.c
//
//  The Floret kernel: task table, events, strict-priority dispatch, the tick,
//  timers and Flight Test telemetry. See docs/design.md.
//
//  Concurrency: interrupts touch only pending[], ready_set and tick_count,
//  always through the atomics in floret_atomic.h. Everything else is owned
//  by thread context (main code and the handlers it dispatches).
//------------------------------------------------------------------------------
#include <stddef.h>
#include <string.h>

#include "floret/floret.h"
#include "floret/floret_port.h"
#include "floret/floret_telemetry.h"
#include "floret_atomic.h"

//------------------------------------------------------------------------------
//  Kernel state
//------------------------------------------------------------------------------
static const floret_task_t* table;                                              // the application's task table
static floret_id_t          task_count;

static uint32_t pending[FLORET_MAX_TASKS][FLORET_CLASS_COUNT];                  // events waiting (atomic)
static uint32_t masks[FLORET_MAX_TASKS][FLORET_CLASS_COUNT];                    // dispatchable bits (thread)
static uint32_t ready_set;                                                      // bit n: task n has events (atomic)

static uint32_t        tick_count;                                              // advanced by floret_tick() (atomic)
static uint32_t        timer_ticks;                                             // tick value timers last saw (thread)
static floret_timer_t* timers;                                                  // active timers (thread)

#if FLORET_FLIGHT_TEST
//------------------------------------------------------------------------------
//  The telemetry block. volatile + used: its reader is the debug probe, which
//  the compiler can't see, so it must not optimize the writes away.
//------------------------------------------------------------------------------
static volatile floret_telemetry_t floret_telemetry __attribute__((used));
#endif

//------------------------------------------------------------------------------
//  Local functions
//------------------------------------------------------------------------------

static inline uint32_t task_bit(floret_id_t id)
{
    return 1u << id;
}

//------------------------------------------------------------------------------
//  Function: post
//      Set events and mark the task ready, then wake the dispatcher. Shared
//      by floret_signal() and the kernel's own sys events.
//------------------------------------------------------------------------------
static void post(floret_id_t id, floret_class_t cls, uint32_t bits)
{
    if(bits == 0u)
        return;
    (void)fa_or(&pending[id][cls], bits);
    (void)fa_or(&ready_set, task_bit(id));
    floret_port_notify();
}

//------------------------------------------------------------------------------
//  Function: has_work
//      true if the task has dispatchable events in class first or later.
//------------------------------------------------------------------------------
static bool has_work(floret_id_t id, int first)
{
    for(int c = first; c < FLORET_CLASS_COUNT; c++)
    {
        if(fa_load(&pending[id][c]) & masks[id][c])
            return true;
    }
    return false;
}

//------------------------------------------------------------------------------
//  Function: service_timers
//      Signal every timer that has come due since the last call. A periodic
//      timer that fell behind fires once and keeps its phase.
//------------------------------------------------------------------------------
static void service_timers(void)
{
    const uint32_t now = fa_load(&tick_count);
    if(now == timer_ticks)
        return;                                                                 // no tick since last time
    timer_ticks = now;
#if FLORET_FLIGHT_TEST
    floret_telemetry.ticks = now;
#endif

    floret_timer_t** link = &timers;
    while(*link != NULL)
    {
        floret_timer_t* t = *link;
        if((int32_t)(now - t->due) < 0)
        {
            link = &t->next;                                                    // not due yet
            continue;
        }
        post(t->task, FLORET_USR, t->bits);
        if(t->period != 0u)
        {
            do
            {
                t->due += t->period;                                            // keep the phase, skip missed periods
            } while((int32_t)(now - t->due) >= 0);
            link = &t->next;
        }
        else
        {
            t->active = false;                                                  // one-shot: unlink
            *link     = t->next;
            t->next   = NULL;
        }
    }
}

#if FLORET_FLIGHT_TEST
//------------------------------------------------------------------------------
//  Function: service_uplink
//      Act on commands Downlink wrote into the uplink word, then clear it as
//      the acknowledgement.
//------------------------------------------------------------------------------
static void service_uplink(void)
{
    const uint32_t cmd = floret_telemetry.uplink;
    if(cmd == 0u)
        return;
    if(cmd & FLORET_UPLINK_RESET_MAX)
    {
        for(floret_id_t i = 0; i < task_count; i++)
            for(int c = 0; c < FLORET_CLASS_COUNT; c++)
                floret_telemetry.task[i].cls[c].max_time = 0u;
    }
    floret_telemetry.uplink = 0u;
}
#endif

//------------------------------------------------------------------------------
//  Function: dispatch
//      Call one handler, timing it for Flight Test.
//------------------------------------------------------------------------------
static void dispatch(floret_id_t id, int cls, uint32_t events)
{
    const floret_task_t*  t       = &table[id];
    const floret_event_fn handler = (cls == FLORET_ISR)   ? t->isr
                                    : (cls == FLORET_SYS) ? t->sys
                                                          : t->usr;
    if(handler == NULL)
        return;                                                                 // events for a missing handler are dropped

#if FLORET_FLIGHT_TEST
    const uint32_t start = floret_port_now();
    handler(events, t->arg);
    const uint32_t end     = floret_port_now();
    const uint32_t elapsed = end - start;

    volatile floret_class_stats_t* s = &floret_telemetry.task[id].cls[cls];
    s->time += elapsed;
    s->runs += 1u;
    if(elapsed > s->max_time)
        s->max_time = elapsed;
    floret_telemetry.now = end;
#else
    handler(events, t->arg);
#endif
}

//------------------------------------------------------------------------------
//  Interface functions
//------------------------------------------------------------------------------

void floret_init(const floret_task_t* tasks, floret_id_t count)
{
    FLORET_ASSERT(tasks != NULL);
    FLORET_ASSERT(count >= 1 && count <= FLORET_MAX_TASKS);
    if(count > FLORET_MAX_TASKS)
        count = FLORET_MAX_TASKS;

    table      = tasks;
    task_count = count;
    memset(pending, 0, sizeof(pending));
    memset(masks, 0xFF, sizeof(masks));                                         // everything dispatchable
    fa_store(&ready_set, 0u);
    fa_store(&tick_count, 0u);
    timer_ticks = 0u;
    timers      = NULL;

#if FLORET_FLIGHT_TEST
    floret_telemetry.magic      = FLORET_TELEMETRY_MAGIC;
    floret_telemetry.version    = FLORET_TELEMETRY_VERSION;
    floret_telemetry.clock_hz   = floret_port_now_hz();
    floret_telemetry.task_count = count;
    floret_telemetry.now        = floret_port_now();
    floret_telemetry.idle_time  = 0u;
    floret_telemetry.ticks      = 0u;
    floret_telemetry.uplink     = 0u;
    for(floret_id_t i = 0; i < FLORET_MAX_TASKS; i++)
    {
        floret_telemetry.task[i].name = (i < count) ? tasks[i].name : NULL;
        for(int c = 0; c < FLORET_CLASS_COUNT; c++)
        {
            floret_telemetry.task[i].cls[c].time     = 0u;
            floret_telemetry.task[i].cls[c].runs     = 0u;
            floret_telemetry.task[i].cls[c].max_time = 0u;
        }
    }
#endif
}

void floret_start(void)
{
    for(floret_id_t i = 0; i < task_count; i++)
    {
        if(table[i].init != NULL)
            table[i].init(table[i].arg, i);
    }
    for(floret_id_t i = 0; i < task_count; i++)
        post(i, FLORET_SYS, FLORET_SYS_START);
}

bool floret_step(void)
{
#if FLORET_FLIGHT_TEST
    service_uplink();
#endif
    service_timers();

    const uint32_t ready = fa_load(&ready_set);
    if(ready == 0u)
        return false;

    const floret_id_t id = (floret_id_t)__builtin_ctz(ready);                   // highest priority = lowest row
    (void)fa_and(&ready_set, ~task_bit(id));

    for(int c = 0; c < FLORET_CLASS_COUNT; c++)                                 // isr, then sys, then usr
    {
        const uint32_t mask   = masks[id][c];
        const uint32_t events = fa_and(&pending[id][c], ~mask) & mask;          // masked bits stay pending
        if(events == 0u)
            continue;
        if(has_work(id, c + 1))
            (void)fa_or(&ready_set, task_bit(id));                              // more classes waiting
        dispatch(id, c, events);
        break;                                                                  // one handler, then back to the top
    }
    return true;
}

_Noreturn void floret_run(void)
{
    for(;;)
    {
        if(floret_step())
            continue;
#if FLORET_FLIGHT_TEST
        const uint32_t start = floret_port_now();
        floret_port_idle_wait();
        const uint32_t end = floret_port_now();
        floret_telemetry.idle_time += end - start;
        floret_telemetry.now   = end;
        floret_telemetry.ticks = fa_load(&tick_count);
#else
        floret_port_idle_wait();
#endif
    }
}

bool floret_signal(floret_id_t task, floret_class_t cls, uint32_t bits)
{
    if(task >= task_count)
        return false;
    if(cls != FLORET_ISR && cls != FLORET_USR)
        return false;                                                           // sys belongs to the kernel
    post(task, cls, bits);
    return true;
}

void floret_mask(floret_id_t task, floret_class_t cls, uint32_t enable_bits)
{
    if(task >= task_count || (unsigned)cls >= FLORET_CLASS_COUNT)
        return;
    masks[task][cls] = enable_bits;
    if(fa_load(&pending[task][cls]) & enable_bits)
    {
        (void)fa_or(&ready_set, task_bit(task));                                // unmasking exposed pending events
        floret_port_notify();
    }
}

floret_id_t floret_find(const char* name)
{
    if(name == NULL)
        return FLORET_ID_INVALID;
    for(floret_id_t i = 0; i < task_count; i++)
    {
        if(table[i].name != NULL && strcmp(table[i].name, name) == 0)
            return i;
    }
    return FLORET_ID_INVALID;
}

floret_id_t floret_count(void)
{
    return task_count;
}

void floret_tick(void)
{
    fa_inc(&tick_count);
    floret_port_notify();
}

uint32_t floret_ticks(void)
{
    return fa_load(&tick_count);
}

void floret_timer_start(floret_timer_t* t, floret_id_t task, uint32_t usr_bits,
                        uint32_t delay_ticks, uint32_t period_ticks)
{
    FLORET_ASSERT(t != NULL && task < task_count);
    if(t == NULL || task >= task_count)
        return;
    floret_timer_stop(t);
    t->task   = task;
    t->bits   = usr_bits;
    t->period = period_ticks;
    t->due    = fa_load(&tick_count) + (delay_ticks ? delay_ticks : 1u);
    t->active = true;
    t->next   = timers;
    timers    = t;
}

void floret_timer_stop(floret_timer_t* t)
{
    if(t == NULL || !t->active)
        return;
    for(floret_timer_t** link = &timers; *link != NULL; link = &(*link)->next)
    {
        if(*link == t)
        {
            *link = t->next;
            break;
        }
    }
    t->next   = NULL;
    t->active = false;
}

bool floret_timer_active(const floret_timer_t* t)
{
    return t != NULL && t->active;
}

#if FLORET_FLIGHT_TEST
const volatile floret_telemetry_t* floret_telemetry_get(void)
{
    return &floret_telemetry;
}
#endif
