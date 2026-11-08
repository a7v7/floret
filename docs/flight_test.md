# Floret Flight Test

**Status:** draft for review

**Flight Test** is how you watch a Floret application while it runs. Like a real flight-test program, it has three parts:

| Part | Where | What it is |
|---|---|---|
| **Telemetry** | On the target | The *telemetry block*: a fixed-layout record of where the CPU's time goes, kept up to date by the kernel |
| **Downlink** | On the host | The ground station: reads the telemetry over the debug port (SWD) while the target runs, and graphs it |
| **Uplink** | In the telemetry block | The one word Downlink can write back, to send commands |

The vehicle maintains its telemetry frame; the ground station receives it on the downlink and sends commands on the uplink.

## 1. What it measures

| Measurement | How |
|---|---|
| **Time per task and class** | The dispatcher reads `floret_port_now()` before and after every handler call and adds the difference to that task's counter for the class it dispatched (isr, sys or usr). |
| **Dispatches per task and class** | Counted with each handler call. |
| **Longest dispatch per task and class** | The maximum of those differences. **This is the key number for run-to-completion:** the longest handler bounds the latency of every other task. Splitting by class shows *which* handler is the slow one. |
| **Idle time** | `floret_idle()` reads the clock before and after each sleep, with interrupt handlers held off, so the gap is pure sleep and never includes interrupt time (design.md, section 6). |
| **Interrupts plus kernel overhead** | Not measured, derived: whatever is neither task time nor idle time. No interrupt handler needs instrumenting. On a Daisy this is mostly the audio interrupt, so it shows audio headroom directly. |

All times are in ticks of `floret_port_now()`, a free-running hardware counter that keeps counting while the CPU sleeps. The CPU's own cycle counter (`DWT->CYCCNT`) can't be used, because it stops during sleep.

The cost is two timer reads and a few additions per dispatch. Building with `FLORET_FLIGHT_TEST=0` removes the block and all of the bookkeeping.

## 2. Layout (version 1)

```c
#define FLORET_TELEMETRY_MAGIC   0x54524C46u   /* "FLRT" in memory order */
#define FLORET_TELEMETRY_VERSION 1u

typedef struct {
    uint32_t          time;       /* ticks spent in this handler           */
    uint32_t          runs;       /* dispatches of this handler            */
    uint32_t          max_time;   /* longest single dispatch, in ticks     */
} floret_class_stats_t;           /* 12 bytes                              */

typedef struct {
    const char*          name;    /* task name, from the task table        */
    floret_class_stats_t cls[3];  /* indexed FLORET_ISR, FLORET_SYS, FLORET_USR */
} floret_task_stats_t;            /* 40 bytes                              */

typedef struct {
    uint32_t          magic;      /* FLORET_TELEMETRY_MAGIC                */
    uint32_t          version;    /* FLORET_TELEMETRY_VERSION              */
    uint32_t          clock_hz;   /* floret_port_now_hz()                  */
    uint32_t          task_count; /* valid entries in task[]               */
    uint32_t          now;        /* floret_port_now() at the last update  */
    uint32_t          idle_time;  /* time asleep in floret_idle()          */
    uint32_t          ticks;      /* kernel ticks (section 7 of design.md) */
    volatile uint32_t uplink;     /* commands from Downlink, see section 4 */
    floret_task_stats_t task[FLORET_MAX_TASKS];
} floret_telemetry_t;
```

| Offset | Field | Offset | Field |
|---|---|---|---|
| 0x00 | `magic` | 0x14 | `idle_time` |
| 0x04 | `version` | 0x18 | `ticks` |
| 0x08 | `clock_hz` | 0x1C | `uplink` |
| 0x0C | `task_count` | 0x20 | `task[0]`, then 40 (0x28) bytes per task |
| 0x10 | `now` | | |

Within each 40-byte task entry: `name` at +0x00, then the isr, sys and usr statistics at +0x04, +0x10 and +0x1C, each `{time, runs, max_time}`. With the default 16 tasks the whole block is 672 bytes.

**Rules:**
- **The offsets are part of the contract** for 32-bit targets, where a pointer is 4 bytes. Floret checks them at compile time with `_Static_assert`, so the layout can't drift by accident. (A 64-bit host build for unit tests has a different layout, and Downlink never reads it.)
- **New fields go at the end** of the header, with a version bump. Downlink refuses a version it doesn't know.
- **Counters are 32-bit and wrap.** A 32-bit word reads atomically over SWD, and Downlink computes wrap-aware differences. On a Daisy, TIM2 runs at 200 or 240 MHz (depending on whether the 480 MHz CPU boost is on), so the counters wrap every 18–21 s. Downlink must sample at least every few seconds; it normally samples at 10–30 Hz.
- **Fields may be slightly inconsistent with each other.** A block read takes a few microseconds while the target keeps running, so `now` and a task's counters may differ by a dispatch or two. For load percentages that doesn't matter, and it means the target never has to lock anything for the host.

## 3. How Downlink uses it

From two reads Δ apart in `now`:

| Value | Formula |
|---|---|
| Load of task *i* | `Σ over classes of Δtask[i].cls[c].time / Δnow` |
| Load of one handler | `Δtask[i].cls[c].time / Δnow` |
| Idle | `Δidle_time / Δnow` |
| Interrupts + kernel | `1 − idle − Σ task loads` |
| Total CPU load | `1 − idle` |
| Dispatch rate of a handler | `Δtask[i].cls[c].runs / (Δnow / clock_hz)` per second |
| Longest handler (worst blocking time) | `max over all tasks and classes of max_time / clock_hz` |

Task names come from the `name` pointers: Downlink reads each string from target memory once, so the graph labels itself.

## 4. The uplink

`uplink` is the one word Downlink writes. Floret checks it before each dispatch and clears it after acting. (A fully idle system handles it at the next wake-up; on a Daisy the audio tick wakes it every 1 ms.)

| Bit | Command |
|---|---|
| 0 | Reset every `max_time` (all tasks, all classes) |
| 1–31 | Reserved |

Downlink writes the word, then watches for it to return to 0 as acknowledgement.

## 5. Latency budgets (proposed for version 2)

**Status:** proposal, not implemented. Sections 2–4 describe version 1 as built.

Run-to-completion has one rule: a handler must be short, because the longest handler bounds the latency of every other task. Version 1 *measures* that bound (`max_time`). Version 2 lets each handler *declare* its bound, and counts every dispatch that breaks it. On a project with several developers, that turns "your handler is too long" from an argument in code review into a number the system reports.

The model is SNMP's RMON alarms: the target keeps counters, and the host decides what's an alarm. The kernel's share is one compare and one increment per dispatch. Thresholds, warnings, pass/fail and display all live in Downlink, where they're cheap to change.

### 5.1 Declaring a budget

Each task declares a budget per class, in microseconds, as a new last field of the task table:

```c
typedef struct {
    const char*     name;
    floret_init_fn  init;
    floret_event_fn isr;
    floret_event_fn sys;
    floret_event_fn usr;
    void*           arg;
    uint32_t        budget_us[FLORET_CLASS_COUNT];   /* indexed like the handlers; 0 = no budget */
} floret_task_t;
```

- **Per class,** matching the per-class statistics. A task's `isr` handler and its `usr` handler usually have very different jobs and very different limits.
- **Microseconds in the table,** so a budget means the same thing on every port. `floret_init()` converts each budget to `floret_port_now()` ticks once, rounding up, and clamps a budget too large for 32 bits.
- **0 means no budget.** Because it's the last field, existing tables written with positional initializers still compile, and every budget in them is 0.
- **In the CSV** (design.md, section 11), three optional columns: `isr_budget_us`, `sys_budget_us`, `usr_budget_us`. Blank means no budget.

The budgets live in the task table on purpose: the table is the one file that already describes the whole system, in priority order. A change to a budget shows up in the same diff and review as a change to the tasks.

### 5.2 What counts as an overrun

A dispatch whose measured time is **strictly greater** than its budget. The measured time is the same `elapsed` that feeds `time` and `max_time`: wall-clock time from the handler's call to its return, **including any interrupts that preempted it.** That's deliberate. It's the time every other task actually waited, but it means a budget needs headroom for interrupt load. On a Daisy, the audio interrupt runs inside every handler that's long enough to meet it.

```c
/* in the dispatcher, after the version 1 bookkeeping */
if(s->budget != 0u && elapsed > s->budget)
    s->overruns += 1u;
```

The kernel takes no other action. It doesn't assert, signal or log: what an overrun means is policy, and policy belongs to the host.

### 5.3 Layout (version 2)

Two fields are added to each handler's statistics. Nothing else changes:

```c
#define FLORET_TELEMETRY_VERSION 2u

typedef struct {
    uint32_t          time;       /* ticks spent in this handler                 */
    uint32_t          runs;       /* dispatches of this handler                  */
    uint32_t          max_time;   /* longest single dispatch, in ticks           */
    uint32_t          budget;     /* from the task table, in ticks; 0 = none     */
    uint32_t          overruns;   /* dispatches longer than budget; wraps        */
} floret_class_stats_t;           /* 20 bytes                                    */
```

| Within a task entry | Offset |
|---|---|
| `name` | +0x00 |
| isr statistics | +0x04 |
| sys statistics | +0x18 |
| usr statistics | +0x2C |
| Next task entry | +0x40 (64 bytes per task) |

Within each statistics record: `time` +0x00, `runs` +0x04, `max_time` +0x08, `budget` +0x0C, `overruns` +0x10. The header is unchanged, and `task[0]` is still at 0x20. With the default 16 tasks the block grows from 672 to 1056 bytes.

**Why the records grow, instead of a new array after `task[]`:** an array after `task[]` would keep the version 1 offsets, but its address would depend on `FLORET_MAX_TASKS`, which Downlink can't read from the block. Downlink would need the ELF symbol's size to find it, and the fallback that scans RAM for the magic number would break. Growing the records keeps every offset computable from the version number alone. Downlink keeps a layout for each version and reads both.

**Budgets are in the block** as well as in the table, already converted to ticks. Downlink reads the contract from the running target, in the same units as `max_time`, and needs no CSV and no copy of the table.

**Counters are never reset.** `overruns` wraps like every other counter, and Downlink works with differences between samples, as an SNMP manager does. The uplink's reset command (section 4) clears `max_time` only.

### 5.4 What Downlink does with it

For each handler with a budget, Downlink shows the budget beside `max_time` and gives the handler one of three states:

| State | Condition | Shown as |
|---|---|---|
| **OK** | `max_time` below 80% of the budget | Normal |
| **Near** | `max_time` at or above 80% of the budget, no overruns | Amber. Clears only when `max_time` is reset, because it's a high-water mark |
| **Over** | `overruns` went up within the last 5 seconds | Red, with the count in that window. Falls back to Near after 5 seconds without a new overrun |

The 80% threshold and the 5-second hold are Downlink settings. The hold does the job of RMON's falling threshold: a handler that overruns on and off shows one steady alarm instead of a flicker.

**On the strip chart,** every sample in which a handler's `overruns` went up puts an event mark in the margin, labelled with the handler and the count (for example `midi.isr ×3`), like the event pen on a flight-test recorder. The tear-off then shows *when* the overruns happened, next to the load lanes that explain them.

**For automated runs on hardware,** a check mode:

```
downlink app.elf --check 60                     run 60 s, then report and exit
downlink app.elf --check 60 --require-budgets   also fail if a handler that ran has no budget
```

Check mode resets `max_time` through the uplink, records every `overruns` counter, samples for the given number of seconds, and then prints one row per handler: runs, `max_time`, budget, and overruns during the run. The exit status is 0 if no handler overran, 2 if any did, and 1 for errors (no probe, wrong ELF, unknown version), matching Downlink's existing errors.

`--require-budgets` is for projects that want every handler to declare its bound before it's merged. It fails the run if any handler dispatched during the check has no budget. Handlers that never ran don't count, so a test run doesn't have to exercise every path.

### 5.5 Open questions

| Question | Proposal |
|---|---|
| Per class or per task? | **Per class** (section 5.1), matching the statistics. |
| Should the kernel be able to act on an overrun itself, for example a hook a product could log from, without a probe attached? | **Not in version 2.** A task can already read the block through `floret_telemetry_get()` and watch `overruns`. Revisit if a product needs it. |
| Should budgets be changeable at run time, through an API or the uplink? | **No.** A budget is part of the design, reviewed with the task table. Changing one means changing the table. |
| Near threshold and Over hold time | **80% and 5 seconds,** both adjustable in Downlink. |
| Should `--require-budgets` be the default for `--check`? | **No,** so existing projects can adopt budgets one handler at a time. |

## 6. Finding and trusting the block

**The block is a `static volatile` variable** inside the kernel, named `floret_telemetry`. It must be `volatile` because its real reader, the debug probe, is invisible to the compiler: otherwise the compiler, especially with LTO, may treat the block as write-only and delete the updates. Application code gets a read-only pointer from `floret_telemetry_get()`, keeping with the getters-over-globals convention. Downlink doesn't need the symbol to be global: local symbols appear in the ELF symbol table too. (With LTO, GCC may rename it, for example `floret_telemetry.lto_priv.0`, so Downlink matches the name prefix.) A Downlink that can't find the symbol can fall back to scanning RAM for the magic number.

**The block must be in memory the data cache doesn't hold.** The probe reads RAM directly, past the CPU's data cache. A Cortex-M7 such as the STM32H7 caches its main SRAM with write-back, so the block's latest values can sit in the cache for a long time, and right after a reset the probe sees zeros where the CPU sees the block. Set `FLORET_TELEMETRY_SECTION` to a section in uncached memory. On a Daisy, libDaisy puts it in DTCM (`.dtcmram_bss`), which the cache never holds.

**Downlink checks the ELF before trusting it.** It compares a few hundred bytes of the target's flash (the vector table and the start of `.text`) against the ELF. A mismatch means the ELF isn't the one running, so Downlink stops instead of graphing garbage. This needs nothing from the target.

## 7. Debug access during sleep

When Floret sleeps (`WFI`), an STM32 may stop the clocks its debug port needs, so Downlink's reads would fail exactly while the system is idle. The STM32's `DBGMCU_CR` register has "debug in sleep" bits that keep them running.

**Downlink sets those bits itself when it connects,** rather than Floret setting them at start-up. A product that nobody is monitoring keeps its full power saving. This is what STM32CubeProgrammer's "Debug in Low Power mode" option does.

## 8. Downlink

Downlink lives in this repository, in **`tools/downlink/`**, so the telemetry layout and the tool that reads it always change together, in the same commit.

- **Language:** Python.
- **Debug probe access:** pyOCD, which reads target memory over an ST-Link without halting the core.
- **Symbols:** pyelftools, to find `floret_telemetry`, check the ELF against the target's flash, and read the task names.
- **Main view:** a strip chart in the style of a 1970s flight-test recorder: a wide roll of waxy, gridded paper scrolling down, time marked down the margin, one pen per lane (total load, each task, interrupts, idle), with a selectable paper speed and a "tear off" button that saves the visible stretch as an image.

## 9. Decisions

Decided in review, 2026-10-09:

| Question | Decision |
|---|---|
| Statistics per class | **In version 1:** time, runs and `max_time` for each task's isr, sys and usr handler separately (section 2). |
| Where Downlink lives | **In this repository,** `tools/downlink/` (section 8). |
| Name of the system | **Flight Test**: telemetry (on the target), Downlink (on the host) and the uplink (the command word). |
| Downlink's sample rate | Still open: 10 Hz by default, adjustable in Downlink? |
