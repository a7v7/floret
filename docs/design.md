# Floret kernel design

**Status:** draft for review · **Language:** C11 · **License:** MIT

## 1. Philosophy

**Wait for the event, and do only what's necessary to process it.**

Most embedded firmware spends nearly all of its time waiting. An RTOS models that waiting with threads: each has its own stack, the scheduler switches between them, and priorities, mutexes and priority inversion follow. That machinery is powerful, but on a small system it costs RAM, cycles and, above all, the ability to reason about what the system is doing.

Floret takes the other path, **run-to-completion (RTC)**:

- **A task is not a thread.** It is a small set of functions, its *handlers*, that the kernel calls when the task has events to process. A handler runs to completion and returns. It never blocks, waits or yields.
- **There is one stack.** Every handler runs on the main stack, so there are no per-task stacks to size and no context switches.
- **Events drive everything.** Interrupts, timers and other tasks wake a task by setting event bits. A task with no events costs nothing.
- **When there's nothing to do, the CPU sleeps.**

The price is a single rule: **a handler must be short.** The longest handler bounds the latency of every other task, because nothing preempts it except interrupts. Floret measures that bound for you (section 9) instead of asking you to guess it.

The public headers are plain C11 and also compile as C++ (`extern "C"`), so C++ applications such as Daisy firmware use them directly.

Floret is the successor to the RTC scheduler in the Trilinear f5c firmware (`kc705_libs/tr_rtos`, tag `v1.11.1`). Section 12 lists what changed.

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Task** | A row in the application's *task table*: a name, an init function, three event handlers, and an argument. |
| **Event** | One bit in a 32-bit word. Each task has three event words, one per *class*. |
| **Class** | Where an event comes from: **isr** (interrupts), **sys** (the kernel itself) or **usr** (the application). |
| **Handler** | A function the kernel calls with the events a task has pending in one class. |
| **Dispatch** | One handler call. |
| **Priority** | A task's row in the table: row 0 is the highest. |
| **Ready set** | One bit per task that has pending events (section 5). |
| **Tick** | The kernel's coarse time unit. Timers count in ticks. |

## 3. Tasks and the task table

Tasks are declared in a constant table, normally generated from a CSV file (section 11), in the table-driven style of the classic UNIX device switches:

```c
typedef uint8_t floret_id_t;                 /* index in the task table */

typedef void (*floret_init_fn)(void* arg, floret_id_t id);
typedef void (*floret_event_fn)(uint32_t events, void* arg);

typedef struct {
    const char*     name;   /* used by floret_find() and by Downlink */
    floret_init_fn  init;   /* called once by floret_start()          */
    floret_event_fn isr;    /* events from interrupt handlers         */
    floret_event_fn sys;    /* events from the kernel                 */
    floret_event_fn usr;    /* events from the application            */
    void*           arg;    /* passed to every handler                */
} floret_task_t;
```

- **The table is `const`** and lives in flash. Per-task run statistics, which the f5c table kept in the table rows, move to the telemetry block.
- **A task's id is its row number.** `floret_find("midi")` looks one up by name, so tasks don't have to share id macros.
- **Any handler may be `NULL`.** A task that only reacts to interrupts provides just `isr`.
- **`FLORET_MAX_TASKS`** (default 16, maximum 32) sizes the kernel's state. The limit of 32 comes from the ready set, a single 32-bit word (section 5).

## 4. Events

Each task has three 32-bit event words, one per class:

| Class | Who sets it | Typical use |
|---|---|---|
| **isr** | Interrupt handlers | "A MIDI byte arrived", "the ADC block is ready", "the encoder moved" |
| **sys** | The kernel | System events only: `FLORET_SYS_START` and future kernel events |
| **usr** | The application | Task-to-task requests and notifications, timer expiry (section 7) |

The **isr** class is new compared with f5c. It gives interrupt-driven work its own handler, separate from task-to-task traffic, so a task's `isr` handler reads like the second half of its interrupt service routine.

**Bit ownership:**
- **sys belongs to Floret.** It carries system events only, and every bit is defined by the kernel. Version 1 defines `FLORET_SYS_START`, sent to every task once all `init()` functions have run. The names `FLORET_SYS_STOP`, `FLORET_SYS_ERROR` and `FLORET_SYS_LOW_POWER` (from f5c) are reserved; their behavior will be defined when they're implemented.
- **isr and usr belong to the task that receives them.** Each task defines its own bit names. Application-level events, including timer expiry, travel in **usr**.

### Signaling

```c
bool floret_signal(floret_id_t task, floret_class_t cls, uint32_t bits);
```

- **Safe from anywhere:** thread code, any handler, or **any interrupt at any priority**.
- **Lock-free:** the bits are ORed in with a C11 atomic (`atomic_fetch_or`, which compiles to an `LDREX`/`STREX` loop on Cortex-M3/M4/M7). No interrupts are ever masked, so signaling can't delay the audio interrupt, unlike libDaisy's PRIMASK-based `ScopedIrqBlocker`.
- **Marks the task ready:** it also sets the task's bit in the ready set (section 5). No separate wake-up call is needed: the way Floret sleeps (section 6) guarantees a signal from an interrupt is never missed.
- **Only `isr` and `usr`.** The `sys` class belongs to the kernel, so `floret_signal()` refuses it.
- **Returns `false`** for an invalid task id or class.

Events are bits, not counts: signaling the same bit twice before the task runs delivers it once. Anything that needs a count, or a payload, goes in a queue that the event announces (section 10).

### Masks

```c
void floret_mask(floret_id_t task, floret_class_t cls, uint32_t enable_bits);
```

A mask gates *dispatch*, not delivery. A masked event stays pending and is dispatched as soon as it is unmasked, so masking never loses an event. (`floret_mask()` marks the task ready again if unmasking exposes pending events.) All bits are enabled by default.

## 5. The run loop

```c
void floret_init(const floret_task_t* table, floret_id_t count);
void floret_start(void);   /* calls each init(), sends FLORET_SYS_START */
void floret_run(void);     /* never returns */
bool floret_step(void);    /* one unit of work; false if nothing was ready */
```

### Strict priority: table order is priority order

**Row 0 is the highest priority.** After every dispatch, the kernel goes back to the top and runs the highest-priority task that has work. A lower task runs only when every task above it is idle.

To make "go back to the top" cheap, the kernel keeps a **ready set**: one 32-bit word with a bit per task. `floret_signal()` sets the task's bit; the dispatcher clears it. Finding the highest-priority ready task is a single instruction (count trailing zeros), however many tasks there are.

```
loop forever:
    process expired timers                          /* section 7 */
    ready = load(ready_set)
    if ready == 0:
        floret_idle()                               /* section 6 */
        continue
    task = count_trailing_zeros(ready)              /* highest priority */
    atomic_fetch_and(&ready_set, ~(1 << task))
    for class in isr, sys, usr:                     /* first class with work */
        old    = atomic_fetch_and(&pending[task][class], ~mask[task][class])
        events = old & mask[task][class]            /* masked bits stay pending */
        if events != 0:
            if other classes of this task still have work:
                atomic_fetch_or(&ready_set, 1 << task)
            call handler(events, arg) if it exists
            break                                   /* back to the top */
```

**One dispatch is one handler call.** Then the kernel starts over from the top, so a higher-priority event that arrived meanwhile is served next.

`floret_run()` is `floret_step()` in a loop, sleeping whenever it returns `false`. Calling `floret_step()` directly runs Floret inside an existing main loop (useful when moving an application over gradually), and it is how the unit tests drive the kernel on a PC.

Events for a task whose handler for that class is `NULL` are cleared and dropped.

**Rules that follow from it:**
- **Snapshot and clear.** A handler receives exactly the events that were pending when its dispatch began. Events that arrive during the handler, including events a task sends to itself, are dispatched on a later dispatch.
- **Within a task, isr first.** Interrupt-originated work is handled before kernel and application events, keeping interrupt follow-up latency low.
- **Higher rows can starve lower rows.** A task that is always busy, for example one that keeps re-signaling itself, keeps every task below it from running. That is the price of strict priority, and the design rule follows from it: **put long-running, chunked work at the bottom of the table.** A storage task that writes an SD card in chunks belongs in the last row, where it soaks up idle time without delaying anything.
- **Latency of a task** is bounded by the longest handler of *any* task (handlers aren't preempted), plus the work of every higher-priority task that becomes ready meanwhile. Flight Test measures both (section 9).
- **Exactly one handler runs at a time.** Handlers never need locks to protect data from *each other*, only from interrupts.

The policy lives in a few lines of the dispatcher, so a different one (round-robin, for example) could replace it later. Version 1 deliberately has exactly one policy.

## 6. Sleeping

```c
void floret_idle(void);   /* sleep until an interrupt, unless there is work */
```

When there is nothing to do, `floret_run()` calls `floret_idle()`. Two problems have to be solved here.

**1. The wake-up race.**
1. The kernel finds no work.
2. An interrupt fires *now*, between the check and the sleep, and signals a task.
3. The CPU goes to sleep anyway, and the new event waits until some unrelated interrupt wakes the CPU.

**2. Honest idle time.** Flight Test measures idle time by reading the clock before and after the sleep. But on Cortex-M, when an interrupt wakes the CPU its handler runs straight away, *before* execution continues after the sleep instruction. Interrupt time would then be counted as idle time, and on a Daisy that hides the audio interrupt, and with it the audio headroom. (This was measured on hardware: the first version of Floret, which slept with `WFE`, reported 0.1 % interrupt time on a Daisy running audio.)

**The solution handles both:** `floret_idle()` holds off interrupt handlers (`floret_port_lock()`, which sets PRIMASK on Cortex-M), then:

```
lock
if no task is ready and every tick has been given to the timers:
    start = now
    sleep                  /* WFI: ends when an interrupt is pending ... */
    end   = now            /* ... but its handler hasn't run yet         */
    idle_time += end - start
unlock                     /* pending handlers run now                   */
```

- **No lost wake-ups.** An interrupt that signals just before the check is seen by the check. One that becomes pending after it ends the sleep at once, because `WFI` wakes on a *pending* interrupt even while interrupts are held off.
- **Exact accounting.** The end time is read before any handler runs, so idle time is pure sleep.
- **Unserviced ticks count as work.** A tick that arrived after the timers were last processed must be processed before sleeping, or a due timer would wait a whole extra tick.
- **What it costs:** interrupts are held off only while the CPU is asleep anyway, plus the few instructions that read the clock after waking (about 0.1 µs on a Daisy). Floret never holds off interrupts while doing work: signaling, dispatching and the queue are all lock-free.

`floret_idle()` is public, the partner of `floret_step()`: to run Floret inside an existing main loop, call `if(!floret_step()) floret_idle();`.

## 7. Time and timers

Floret has two clocks:

| Clock | Source | Used for |
|---|---|---|
| **Tick** | The port calls `floret_tick()` from a periodic interrupt | Timers |
| **Fine time** | `floret_port_now()`: a free-running 32-bit hardware counter, plus `floret_port_now_hz()` | Telemetry, timestamps, measuring |

### The tick

`floret_tick()` is interrupt-safe and tiny: it increments the tick counter and wakes the kernel. All timer processing happens in the dispatcher, in thread context, before the next dispatch. Nothing heavy ever runs in the tick interrupt.

**On the Daisy, the tick comes from the audio callback.** At 48 kHz with 48-sample blocks it fires every 1.000 ms, and it's locked to the codec's sample clock, so timers can never drift relative to the audio. For a musical instrument that matters more than anything else in the kernel. A hardware timer is the fallback for applications without audio.

### Timers

```c
typedef struct floret_timer floret_timer_t;   /* storage owned by the caller */

void floret_timer_start(floret_timer_t* t, floret_id_t task, uint32_t usr_bits,
                        uint32_t delay_ticks, uint32_t period_ticks);
void floret_timer_stop(floret_timer_t* t);
```

- **On expiry,** the timer signals `usr_bits` to its task. Timer expiry is an application event, so it travels in the **usr** class; the task chooses which bits mean which timer.
- **One-shot or periodic:** `period_ticks == 0` makes it one-shot.
- **No catch-up storms:** a periodic timer that falls behind is rescheduled from its last *due* time, and it fires at most once per dispatcher check.
- **Fine timing:** for timing finer than a tick, such as sample-accurate events, read `floret_port_now()` or timestamp events in a queue (section 10).

## 8. The port layer

Everything hardware-specific is in one small interface, `floret_port.h`, of five functions:

| Function | Purpose | Daisy (Cortex-M7) implementation |
|---|---|---|
| `uint32_t floret_port_now(void)` | Free-running counter | TIM2 counter (keeps running while the CPU sleeps) |
| `uint32_t floret_port_now_hz(void)` | Its frequency | TIM2 clock |
| `uint32_t floret_port_lock(void)` | Hold off interrupt handlers | save PRIMASK, `__disable_irq()` |
| `void floret_port_unlock(uint32_t)` | Restore them; pending handlers run | restore PRIMASK |
| `void floret_port_sleep(void)` | Sleep until an interrupt is pending | `__DSB(); __WFI()` |

**Why not the CPU's cycle counter for `now()`:** `DWT->CYCCNT` counts core clock cycles, and the core clock stops while the CPU sleeps. Idle time would read as zero.

The Daisy port lives in meadow, not in Floret. Floret itself contains no vendor code, and a host port (for unit tests on a PC) is a few lines of standard C.

**Atomics on smaller cores:** Cortex-M0/M0+ have no `LDREX`/`STREX`, so GCC turns the C11 atomics into calls to `__atomic_*` helper functions, which bare-metal toolchains don't supply. A port for those parts provides the two helpers Floret needs (fetch-or and fetch-and) with a short critical section. Floret still works there; signaling just isn't lock-free on those parts.

## 9. Flight Test

**Flight Test** is Floret's built-in observability system. It has three parts:

| Part | Where | What it is |
|---|---|---|
| **Telemetry** | On the target | A fixed-layout block recording how the CPU's time is spent |
| **Downlink** | On the host | The tool that reads the telemetry over SWD while the target runs, without halting it, and graphs it |
| **Uplink** | In the telemetry block | The one command word Downlink can write back |

The run loop measures each dispatch with `floret_port_now()`; the cost is two timer reads and a few additions per dispatch, and `FLORET_FLIGHT_TEST=0` removes it.

All three parts are specified in **[flight_test.md](flight_test.md)**.

## 10. Payloads: queues

Event bits carry no data. When data must travel with an event (MIDI bytes, ADC blocks, UI actions), the producer puts the data in a **queue**, then signals the bit that says "the queue has something". Queues are part of version 1.

```c
typedef struct floret_queue floret_queue_t;   /* storage owned by the caller */

bool floret_queue_init(floret_queue_t* q, void* storage, uint32_t item_size,
                       uint32_t capacity, bool timestamped);   /* false: capacity not a power of two */
bool floret_queue_put(floret_queue_t* q, const void* item);              /* producer */
bool floret_queue_get(floret_queue_t* q, void* item, uint32_t* stamp);   /* consumer */
uint32_t floret_queue_count(const floret_queue_t* q);
uint32_t floret_queue_dropped(const floret_queue_t* q);
```

- **Single producer, single consumer, lock-free.** Typically an interrupt puts and a task gets. Each side owns one index, so neither ever waits for or masks the other.
- **Fixed-size items,** with a power-of-two capacity so index wrap-around is a mask, not a division.
- **Timestamps (optional):** with `timestamped` set, `put` records `floret_port_now()` with each item, and `get` returns it. Timestamps are what make sample-accurate MIDI possible: the audio callback can apply an event at the exact sample it arrived, not at the start of the next block.
- **Never blocks.** `put` on a full queue returns `false` and counts the drop, which `floret_queue_dropped()` reports. `get` on an empty queue returns `false`.
- **Signaling stays separate.** The producer calls `floret_queue_put()` and then `floret_signal()`; the consumer's handler drains the queue until `get` returns `false`. Because events are bits, one signal can announce many items.

## 11. Configuration tools

The task table is generated from a CSV file by a Python script, so declaring a task means adding a row:

```csv
name,init,isr,sys,usr,arg
midi,midi_init,midi_isr,,midi_usr,
ui,ui_init,,ui_sys,ui_usr,&ui_state
storage,storage_init,,storage_sys,storage_usr,
```

The tool writes `floret_tasks.c` (the table) and `floret_tasks.h` (an enum of task ids and the handler prototypes). The output is ordinary, readable C, meant to be committed, so a developer can learn the pattern from the generated code as well as from the tool. The state-machine library will use the same CSV conventions.

## 12. What changed from the f5c RTC scheduler

| f5c RTC scheduler | Floret | Why |
|---|---|---|
| `sys` and `usr` handlers | **`isr`**, `sys` and `usr` | Interrupt work gets its own handler |
| Round-robin passes through the table | **Strict priority by table row** | The most important work always runs next; one-instruction ready lookup |
| Event OR inside a critical section | **Lock-free atomic OR** | Signaling never masks interrupts, so audio is never delayed |
| Busy loop when idle | **Sleep with `WFI`, interrupts held off around it** | Saves power, no wake-up race, and idle time that excludes interrupts |
| Fixed 20 ms / 1 s system ticks | **Configurable tick and a timer service** | Musical timing needs about 1 ms; Daisy ticks from the audio clock |
| No payloads | **Lock-free queues with timestamps** | Data travels with events; sample-accurate MIDI |
| Run counters in the task table | **Telemetry block** read by Downlink | The table stays `const` in flash, and the counters become observable |
| Hand-written task table | **Generated from CSV** | Declaring a task is one row |
| Vendor-specific | **Port layer** | Builds on any C11 compiler; the hardware lives in five functions |

## 13. Decisions

Decided in review, 2026-10-09:

| Question | Decision |
|---|---|
| Dispatch policy | **Strict priority by table row**, restarting from the top after every dispatch (section 5). One policy only in version 1; the dispatcher is small enough to change later. |
| Tick source on the Daisy | **The audio callback** (section 7). |
| Queue utility | **In version 1** (section 10). |
| `FLORET_MAX_TASKS` | **Default 16**, maximum 32 (the ready set is one word). |
| Sleeping (after the first hardware test) | **PRIMASK + `WFI`** instead of `WFE`/`SEV`, so interrupt time is never counted as idle (section 6). |
| `sys` class | **System events only**; every bit is defined by Floret. Application events, including timers, use `usr` (section 4). |
| Telemetry statistics per class | **In version 1** ([flight_test.md](flight_test.md)). |
