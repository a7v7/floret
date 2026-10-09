# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""The Floret telemetry block, version 1 (see docs/flight_test.md).

Decodes raw block bytes into a Snapshot, and turns two snapshots into the
numbers Downlink shows: load per handler, idle, interrupt + kernel time.
"""
from dataclasses import dataclass, field
import struct

MAGIC = 0x54524C46              # "FLRT" in memory order
VERSION = 1
HEADER_SIZE = 0x20
TASK_SIZE = 40                  # name pointer + 3 x {time, runs, max_time}
CLASSES = ("isr", "sys", "usr")
OFFSET_UPLINK = 0x1C

UPLINK_RESET_MAX = 1 << 0


class TelemetryError(Exception):
    pass


@dataclass
class ClassStats:
    time: int                   # counter ticks spent in this handler (wraps)
    runs: int                   # dispatches (wraps)
    max_time: int               # longest single dispatch


@dataclass
class TaskStats:
    name_ptr: int
    cls: list                   # three ClassStats, indexed like CLASSES
    name: str = ""


@dataclass
class Snapshot:
    clock_hz: int
    now: int
    idle_time: int
    ticks: int
    uplink: int
    tasks: list = field(default_factory=list)


def parse_header(raw):
    """Check magic and version; return (clock_hz, task_count)."""
    magic, version, clock_hz, task_count = struct.unpack_from("<4I", raw, 0)
    if magic != MAGIC:
        raise TelemetryError(f"no telemetry block here (magic {magic:#010x}, expected {MAGIC:#010x})")
    if version != VERSION:
        raise TelemetryError(f"telemetry version {version}, this Downlink understands {VERSION}")
    if not 1 <= task_count <= 32:
        raise TelemetryError(f"implausible task count {task_count}")
    return clock_hz, task_count


def block_size(task_count):
    return HEADER_SIZE + TASK_SIZE * task_count


def parse(raw):
    """Decode one telemetry block read from the target."""
    clock_hz, task_count = parse_header(raw)
    now, idle_time, ticks, uplink = struct.unpack_from("<4I", raw, 0x10)
    tasks = []
    for i in range(task_count):
        name_ptr, *w = struct.unpack_from("<10I", raw, HEADER_SIZE + TASK_SIZE * i)
        tasks.append(TaskStats(name_ptr, [ClassStats(*w[3 * c:3 * c + 3]) for c in range(3)]))
    return Snapshot(clock_hz, now, idle_time, ticks, uplink, tasks)


def _d(a, b):
    """Difference of two wrapping 32-bit counters."""
    return (b - a) & 0xFFFFFFFF


@dataclass
class HandlerRates:
    task: str
    cls: str
    load: float                 # fraction of CPU time, 0..1
    rate: float                 # dispatches per second
    max_us: float               # longest dispatch since reset (or uplink reset)


@dataclass
class Rates:
    seconds: float              # length of the interval
    ticks: int                  # kernel ticks in the interval
    handlers: list              # HandlerRates for every handler that ran or has run
    idle: float
    tasks: float                # sum of all handler loads
    irq_kernel: float           # 1 - idle - tasks

    @property
    def cpu(self):
        return 1.0 - self.idle

    def task_load(self, name):
        return sum(h.load for h in self.handlers if h.task == name)


def rates(a, b):
    """Turn two snapshots into loads over the interval between them."""
    dt = _d(a.now, b.now)
    if dt == 0:
        raise TelemetryError("no time passed between snapshots")
    hz = b.clock_hz
    handlers = []
    for ta, tb in zip(a.tasks, b.tasks):
        for c, name in enumerate(CLASSES):
            ca, cb = ta.cls[c], tb.cls[c]
            runs = _d(ca.runs, cb.runs)
            if runs == 0 and cb.runs == 0:
                continue                                    # this handler has never run
            handlers.append(HandlerRates(tb.name, name, _d(ca.time, cb.time) / dt,
                                         runs * hz / dt, 1e6 * cb.max_time / hz))
    idle = _d(a.idle_time, b.idle_time) / dt
    tasks = sum(h.load for h in handlers)
    return Rates(dt / hz, _d(a.ticks, b.ticks), handlers, idle, tasks, 1.0 - idle - tasks)
