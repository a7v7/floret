# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""Text display: a refreshing table of where the CPU's time goes."""
import os
import sys
import time

from . import telemetry


def format_rates(r, title=""):
    lines = []
    if title:
        lines.append(title)
    lines.append(f"interval {r.seconds:.2f} s, {r.ticks} ticks")
    lines.append("")
    lines.append(f"  {'handler':<18}{'load':>9}{'rate/s':>10}{'longest':>12}")
    for h in r.handlers:
        label = f"{h.task} {h.cls}"
        lines.append(f"  {label:<18}{100 * h.load:8.2f}%{h.rate:10.1f}{h.max_us:9.2f} us")
    lines.append(f"  {'-' * 49}")
    lines.append(f"  {'tasks':<18}{100 * r.tasks:8.2f}%")
    lines.append(f"  {'irq + kernel':<18}{100 * r.irq_kernel:8.2f}%")
    lines.append(f"  {'idle':<18}{100 * r.idle:8.2f}%")
    lines.append(f"  {'CPU':<18}{100 * r.cpu:8.2f}%")
    return "\n".join(lines)


def run(link, interval=1.0, once=False, out=sys.stdout):
    """Print the load breakdown every interval seconds (Ctrl+C to stop)."""
    title = (f"Downlink  {os.path.basename(link.elf.path)}  telemetry at {link.address:#010x}, "
             f"{link.clock_hz / 1e6:.0f} MHz clock, {link.task_count} tasks")
    clear = out.isatty() and not once
    if clear:
        os.system("")                                       # enable ANSI escapes on Windows consoles
    prev = link.snapshot()
    try:
        while True:
            time.sleep(interval)
            cur = link.snapshot()
            text = format_rates(telemetry.rates(prev, cur), title)
            out.write(("\x1b[H\x1b[J" if clear else "") + text + "\n" + ("" if clear else "\n"))
            out.flush()
            prev = cur
            if once:
                return
    except KeyboardInterrupt:
        pass
