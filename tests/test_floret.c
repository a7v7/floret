// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  test_floret.c - unit tests for the kernel and the queue, run on a PC
//------------------------------------------------------------------------------
#include <string.h>

#include "check.h"
#include "floret/floret.h"
#include "floret/floret_queue.h"
#include "floret/floret_telemetry.h"
#include "port_host.h"

//------------------------------------------------------------------------------
//  A dispatch log, filled in by the test handlers
//------------------------------------------------------------------------------
typedef struct {
    int      task;
    int      cls;
    uint32_t events;
} entry_t;

static entry_t log_[64];
static int     log_count;
static int     init_order[8];
static int     init_count;

typedef void (*action_fn)(int task, int cls, uint32_t events);
static action_fn action;                                                        // optional per-dispatch hook

static void record(int task, int cls, uint32_t events)
{
    if(log_count < (int)(sizeof(log_) / sizeof(log_[0])))
        log_[log_count++] = (entry_t){task, cls, events};
    if(action)
        action(task, cls, events);
}

static void h_init(void* arg, floret_id_t id)
{
    CHECK_EQ(*(int*)arg, id);
    init_order[init_count++] = id;
}
static void h_isr(uint32_t e, void* arg) { record(*(int*)arg, FLORET_ISR, e); }
static void h_sys(uint32_t e, void* arg) { record(*(int*)arg, FLORET_SYS, e); }
static void h_usr(uint32_t e, void* arg) { record(*(int*)arg, FLORET_USR, e); }

static int ids[3] = {0, 1, 2};

static const floret_task_t tasks[] = {
    {"alpha", h_init, h_isr, h_sys, h_usr, &ids[0]},
    {"beta", h_init, h_isr, h_sys, h_usr, &ids[1]},
    {"gamma", h_init, h_isr, h_sys, h_usr, &ids[2]},
};
enum { A = 0, B = 1, C = 2 };

static void drain(void)
{
    while(floret_step()) {}
}

// Fresh kernel, started, with the FLORET_SYS_START dispatches cleared away
static void setup(void)
{
    log_count     = 0;
    init_count    = 0;
    action        = NULL;
    port_host_now = 0;
    floret_init(tasks, 3);
    floret_start();
    drain();
    log_count = 0;
}

static int expect(int i, int task, int cls, uint32_t events)
{
    return i < log_count && log_[i].task == task && log_[i].cls == cls
           && log_[i].events == events;
}

//------------------------------------------------------------------------------
//  Kernel
//------------------------------------------------------------------------------
static void test_start(void)
{
    log_count  = 0;
    init_count = 0;
    action     = NULL;
    floret_init(tasks, 3);
    floret_start();
    CHECK_EQ(init_count, 3);
    CHECK(init_order[0] == A && init_order[1] == B && init_order[2] == C);
    CHECK_EQ(log_count, 0);                                                     // nothing dispatched yet
    drain();
    CHECK_EQ(log_count, 3);
    CHECK(expect(0, A, FLORET_SYS, FLORET_SYS_START));                          // in priority order
    CHECK(expect(1, B, FLORET_SYS, FLORET_SYS_START));
    CHECK(expect(2, C, FLORET_SYS, FLORET_SYS_START));
    CHECK(!floret_step());
}

static void test_priority(void)
{
    setup();
    floret_signal(C, FLORET_USR, 1);
    floret_signal(A, FLORET_USR, 2);
    drain();
    CHECK_EQ(log_count, 2);
    CHECK(expect(0, A, FLORET_USR, 2));                                         // higher priority first
    CHECK(expect(1, C, FLORET_USR, 1));
}

static void signal_a_from_b(int task, int cls, uint32_t events)
{
    (void)cls;
    (void)events;
    if(task == B)
        floret_signal(A, FLORET_USR, 4);
}

static void test_restart_from_top(void)
{
    setup();
    action = signal_a_from_b;
    floret_signal(B, FLORET_USR, 1);
    floret_signal(C, FLORET_USR, 1);
    drain();
    CHECK_EQ(log_count, 3);
    CHECK(expect(0, B, FLORET_USR, 1));
    CHECK(expect(1, A, FLORET_USR, 4));                                         // A jumps ahead of waiting C
    CHECK(expect(2, C, FLORET_USR, 1));
}

static void test_class_order(void)
{
    setup();
    floret_signal(A, FLORET_USR, 8);
    floret_signal(A, FLORET_ISR, 1);
    CHECK(floret_step());
    CHECK_EQ(log_count, 1);                                                     // one handler per step
    CHECK(expect(0, A, FLORET_ISR, 1));                                         // isr before usr
    CHECK(floret_step());
    CHECK(expect(1, A, FLORET_USR, 8));
    CHECK(!floret_step());
}

static int self_signals;
static void self_signal_twice(int task, int cls, uint32_t events)
{
    (void)cls;
    (void)events;
    if(task == A && self_signals < 2)
    {
        self_signals++;
        floret_signal(A, FLORET_USR, 1);
    }
}

static void test_snapshot(void)
{
    setup();
    self_signals = 0;
    action       = self_signal_twice;
    floret_signal(A, FLORET_USR, 1);
    CHECK(floret_step());
    CHECK_EQ(log_count, 1);                                                     // the re-signal waits for a later step
    drain();
    CHECK_EQ(log_count, 3);
}

static void test_starvation(void)
{
    setup();
    self_signals = 0;
    action       = self_signal_twice;
    floret_signal(C, FLORET_USR, 1);
    floret_signal(A, FLORET_USR, 1);
    drain();
    CHECK_EQ(log_count, 4);
    CHECK(expect(0, A, FLORET_USR, 1));                                         // A keeps the CPU while busy
    CHECK(expect(1, A, FLORET_USR, 1));
    CHECK(expect(2, A, FLORET_USR, 1));
    CHECK(expect(3, C, FLORET_USR, 1));                                         // C runs only when A is idle
}

static void test_masks(void)
{
    setup();
    floret_mask(A, FLORET_USR, 0x1);
    floret_signal(A, FLORET_USR, 0x3);
    drain();
    CHECK_EQ(log_count, 1);
    CHECK(expect(0, A, FLORET_USR, 0x1));                                       // 0x2 is held back
    floret_mask(A, FLORET_USR, 0xFFFFFFFFu);
    drain();
    CHECK_EQ(log_count, 2);
    CHECK(expect(1, A, FLORET_USR, 0x2));                                       // ... not lost
}

static void test_signal_rules(void)
{
    setup();
    CHECK(!floret_signal(A, FLORET_SYS, 1));                                    // sys belongs to the kernel
    CHECK(!floret_signal(3, FLORET_USR, 1));                                    // no such task
    CHECK(floret_signal(A, FLORET_USR, 0));                                     // nothing to do
    CHECK(!floret_step());
}

static void test_find(void)
{
    setup();
    CHECK_EQ(floret_find("beta"), B);
    CHECK_EQ(floret_find("delta"), FLORET_ID_INVALID);
    CHECK_EQ(floret_find(NULL), FLORET_ID_INVALID);
    CHECK_EQ(floret_count(), 3);
}

static void ticks(int n)
{
    for(int i = 0; i < n; i++)
        floret_tick();
}

static void test_timer_one_shot(void)
{
    static floret_timer_t t;
    setup();
    floret_timer_start(&t, B, 0x10, 3, 0);
    ticks(2);
    drain();
    CHECK_EQ(log_count, 0);
    ticks(1);
    drain();
    CHECK_EQ(log_count, 1);
    CHECK(expect(0, B, FLORET_USR, 0x10));                                      // timers signal usr bits
    CHECK(!floret_timer_active(&t));
    ticks(10);
    drain();
    CHECK_EQ(log_count, 1);
}

static void test_timer_periodic(void)
{
    static floret_timer_t t;
    setup();
    floret_timer_start(&t, C, 0x20, 2, 2);
    for(int i = 0; i < 6; i++)
    {
        floret_tick();
        drain();
    }
    CHECK_EQ(log_count, 3);                                                     // ticks 2, 4, 6
    ticks(9);                                                                   // fall behind by several periods
    drain();
    CHECK_EQ(log_count, 4);                                                     // ... fires once, no storm
    floret_timer_stop(&t);
    CHECK(!floret_timer_active(&t));
    ticks(10);
    drain();
    CHECK_EQ(log_count, 4);
    CHECK_EQ(floret_ticks(), 25);
}

static uint32_t cost;
static void take_time(int task, int cls, uint32_t events)
{
    (void)task;
    (void)cls;
    (void)events;
    port_host_now += cost;
}

static void test_telemetry(void)
{
    setup();
    const volatile floret_telemetry_t* t = floret_telemetry_get();
    CHECK_EQ(t->magic, FLORET_TELEMETRY_MAGIC);
    CHECK_EQ(t->version, FLORET_TELEMETRY_VERSION);
    CHECK_EQ(t->clock_hz, 1000000u);
    CHECK_EQ(t->task_count, 3);
    CHECK(strcmp(t->task[B].name, "beta") == 0);
    CHECK_EQ(t->task[A].cls[FLORET_SYS].runs, 1);                               // the start event

    action = take_time;
    cost   = 50;
    floret_signal(B, FLORET_USR, 1);
    drain();
    cost = 20;
    floret_signal(B, FLORET_USR, 1);
    drain();
    cost = 7;
    floret_signal(B, FLORET_ISR, 1);
    drain();
    CHECK_EQ(t->task[B].cls[FLORET_USR].runs, 2);
    CHECK_EQ(t->task[B].cls[FLORET_USR].time, 70);
    CHECK_EQ(t->task[B].cls[FLORET_USR].max_time, 50);
    CHECK_EQ(t->task[B].cls[FLORET_ISR].time, 7);                               // kept per class
    CHECK_EQ(t->now, port_host_now);

    ((volatile floret_telemetry_t*)t)->uplink = FLORET_UPLINK_RESET_MAX;        // as Downlink would
    floret_step();
    CHECK_EQ(t->uplink, 0);                                                     // acknowledged
    CHECK_EQ(t->task[B].cls[FLORET_USR].max_time, 0);
    CHECK_EQ(t->task[B].cls[FLORET_USR].time, 70);                              // totals untouched

    floret_tick();
    floret_step();
    CHECK_EQ(t->ticks, 1);
}

//------------------------------------------------------------------------------
//  Idle
//------------------------------------------------------------------------------
static void test_idle_sleeps(void)
{
    setup();
    port_host_sleeps     = 0;
    port_host_sleep_time = 100;
    const volatile floret_telemetry_t* t = floret_telemetry_get();
    const uint32_t idle_before = t->idle_time;
    floret_idle();
    CHECK_EQ(port_host_sleeps, 1);
    CHECK_EQ(t->idle_time - idle_before, 100);
    CHECK_EQ(port_host_lock_depth, 0);                                          // lock and unlock balanced
}

static void test_idle_skips_when_ready(void)
{
    setup();
    port_host_sleeps = 0;
    floret_signal(C, FLORET_USR, 1);
    floret_idle();
    CHECK_EQ(port_host_sleeps, 0);                                              // work waiting: no sleep
    drain();
    floret_tick();                                                              // a tick the timers haven't seen
    floret_idle();
    CHECK_EQ(port_host_sleeps, 0);
    drain();
    floret_idle();
    CHECK_EQ(port_host_sleeps, 1);                                              // now there is nothing to do
}

static void pretend_isr(void)
{
    port_host_now += 30;                                                        // the handler takes 30 us
    floret_signal(A, FLORET_ISR, 1);
}

static void test_idle_excludes_isr_time(void)
{
    setup();
    port_host_sleep_time = 100;
    port_host_on_unlock  = pretend_isr;                                         // pending during the sleep
    const volatile floret_telemetry_t* t = floret_telemetry_get();
    const uint32_t idle_before = t->idle_time;
    const uint32_t now_before  = port_host_now;
    floret_idle();
    CHECK_EQ(port_host_now - now_before, 130);                                  // 100 asleep + 30 in the handler
    CHECK_EQ(t->idle_time - idle_before, 100);                                  // only the sleep is idle
    CHECK(floret_step());                                                       // the handler's event is waiting
    CHECK(expect(0, A, FLORET_ISR, 1));
}

//------------------------------------------------------------------------------
//  Queue
//------------------------------------------------------------------------------
typedef struct {
    uint8_t a, b, c;
} msg_t;                                                                        // 3 bytes: exercises padding

static void test_queue_fifo(void)
{
    static uint8_t storage[FLORET_QUEUE_STORAGE_SIZE(sizeof(msg_t), 4, false)];
    floret_queue_t q;
    CHECK(floret_queue_init(&q, storage, sizeof(msg_t), 4, false));
    msg_t m;
    CHECK(!floret_queue_get(&q, &m, NULL));
    for(uint8_t i = 0; i < 4; i++)
        CHECK(floret_queue_put(&q, &(msg_t){i, (uint8_t)(i + 1), (uint8_t)(i + 2)}));
    CHECK(!floret_queue_put(&q, &(msg_t){9, 9, 9}));                            // full
    CHECK_EQ(floret_queue_dropped(&q), 1);
    CHECK_EQ(floret_queue_count(&q), 4);
    for(uint8_t i = 0; i < 4; i++)
    {
        CHECK(floret_queue_get(&q, &m, NULL));
        CHECK(m.a == i && m.b == i + 1 && m.c == i + 2);
    }
    CHECK_EQ(floret_queue_count(&q), 0);
}

static void test_queue_wrap(void)
{
    static uint8_t storage[FLORET_QUEUE_STORAGE_SIZE(sizeof(uint32_t), 4, false)];
    floret_queue_t q;
    floret_queue_init(&q, storage, sizeof(uint32_t), 4, false);
    uint32_t next_in = 0, next_out = 0, v;
    for(int round = 0; round < 25; round++)                                     // many trips around the ring
    {
        for(int i = 0; i < 3; i++, next_in++)
            CHECK(floret_queue_put(&q, &next_in));
        for(int i = 0; i < 3; i++, next_out++)
        {
            CHECK(floret_queue_get(&q, &v, NULL));
            CHECK_EQ(v, next_out);
        }
    }
    CHECK_EQ(floret_queue_dropped(&q), 0);
}

static void test_queue_timestamps(void)
{
    static uint8_t storage[FLORET_QUEUE_STORAGE_SIZE(sizeof(msg_t), 8, true)];
    CHECK_EQ(sizeof(storage), 8 * (4 + 4));                                     // 3-byte item padded to 4, plus stamp
    floret_queue_t q;
    floret_queue_init(&q, storage, sizeof(msg_t), 8, true);
    port_host_now = 1234;
    floret_queue_put(&q, &(msg_t){1, 2, 3});
    port_host_now = 5678;
    floret_queue_put(&q, &(msg_t){4, 5, 6});
    msg_t    m;
    uint32_t stamp;
    CHECK(floret_queue_get(&q, &m, &stamp));
    CHECK(m.a == 1 && m.c == 3);
    CHECK_EQ(stamp, 1234);
    CHECK(floret_queue_get(&q, &m, &stamp));
    CHECK(m.a == 4 && m.c == 6);
    CHECK_EQ(stamp, 5678);
}

static void test_queue_capacity(void)
{
    static uint8_t storage[64];
    floret_queue_t q;
    CHECK(!floret_queue_init(&q, storage, 4, 3, false));                        // not a power of two
    CHECK(!floret_queue_init(&q, storage, 4, 0, false));
    CHECK(floret_queue_init(&q, storage, 4, 16, false));
}

int main(void)
{
    RUN(test_start);
    RUN(test_priority);
    RUN(test_restart_from_top);
    RUN(test_class_order);
    RUN(test_snapshot);
    RUN(test_starvation);
    RUN(test_masks);
    RUN(test_signal_rules);
    RUN(test_find);
    RUN(test_timer_one_shot);
    RUN(test_timer_periodic);
    RUN(test_telemetry);
    RUN(test_idle_sleeps);
    RUN(test_idle_skips_when_ready);
    RUN(test_idle_excludes_isr_time);
    RUN(test_queue_fifo);
    RUN(test_queue_wrap);
    RUN(test_queue_timestamps);
    RUN(test_queue_capacity);
    return check_report();
}
