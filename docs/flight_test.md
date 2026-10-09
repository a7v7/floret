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
| **Idle time** | The run loop timestamps each `floret_port_idle_wait()` and adds the gap. |
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
    uint32_t          idle_time;  /* ticks spent in idle_wait()            */
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

## 5. Finding and trusting the block

**The block is a `static` variable** inside the kernel, named `floret_telemetry`. Application code gets a read-only pointer from `floret_telemetry_get()`, keeping with the getters-over-globals convention. Downlink doesn't need the symbol to be global: local symbols appear in the ELF symbol table too. (With LTO, GCC may rename it, for example `floret_telemetry.lto_priv.0`, so Downlink matches the name prefix.) A Downlink that can't find the symbol can fall back to scanning RAM for the magic number.

**Downlink checks the ELF before trusting it.** It compares a few hundred bytes of the target's flash (the vector table and the start of `.text`) against the ELF. A mismatch means the ELF isn't the one running, so Downlink stops instead of graphing garbage. This needs nothing from the target.

## 6. Debug access during sleep

When Floret sleeps (`WFE`), an STM32 may stop the clocks its debug port needs, so Downlink's reads would fail exactly while the system is idle. The STM32's `DBGMCU_CR` register has "debug in sleep" bits that keep them running.

**Downlink sets those bits itself when it connects,** rather than Floret setting them at start-up. A product that nobody is monitoring keeps its full power saving. This is what STM32CubeProgrammer's "Debug in Low Power mode" option does.

## 7. Downlink

Downlink lives in this repository, in **`tools/downlink/`**, so the telemetry layout and the tool that reads it always change together, in the same commit.

- **Language:** Python.
- **Debug probe access:** pyOCD, which reads target memory over an ST-Link without halting the core.
- **Symbols:** pyelftools, to find `floret_telemetry`, check the ELF against the target's flash, and read the task names.
- **Main view:** a strip chart in the style of a 1970s flight-test recorder: a wide roll of waxy, gridded paper scrolling down, time marked down the margin, one pen per lane (total load, each task, interrupts, idle), with a selectable paper speed and a "tear off" button that saves the visible stretch as an image.

## 8. Decisions

Decided in review, 2026-10-09:

| Question | Decision |
|---|---|
| Statistics per class | **In version 1:** time, runs and `max_time` for each task's isr, sys and usr handler separately (section 2). |
| Where Downlink lives | **In this repository,** `tools/downlink/` (section 7). |
| Name of the system | **Flight Test**: telemetry (on the target), Downlink (on the host) and the uplink (the command word). |
| Downlink's sample rate | Still open: 10 Hz by default, adjustable in Downlink? |
