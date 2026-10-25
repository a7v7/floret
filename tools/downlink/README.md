# Downlink

The ground station of Floret **Flight Test**. Downlink reads a running Floret application's telemetry block through the debug probe, without stopping it, and shows where the CPU's time goes: per task and handler, interrupts, and idle. See [docs/flight_test.md](../../docs/flight_test.md).

Its main view is a **strip chart**, modeled on the multichannel chart recorders of 1970s flight-test ground stations: a roll of gridded paper moving down past a row of pens, one lane per channel (total CPU, interrupts + kernel, then each task), with time marked down the margin.

- **The grid is printed on the paper,** so it moves with it.
- **The pens are galvanometers:** each is a small mass on a spring, so a sudden change overshoots slightly and settles.
- **Each lane has an attenuator** that sets its full scale. Ink stays where it was written: a change of full scale affects new ink only, and is noted on the paper in red, as an engineer would have written it.
- **The front panel** has, for each lane, a silk-screened control group and, below it, a window with the lane's name and reading, centered over its trace.
- **The attenuator is a bird's-head knob** in the control group, with an FS scale: **A** (auto: picks 1 % to 100 % to fit the signal), then fixed at 1, 2, 5, 10, 20, 50 or 100 %. Click a label to turn straight to it, click the knob to step clockwise, right-click to step back, or use the mouse wheel. Downlink remembers each lane's setting per firmware (by ELF name).
- **The OL lamp** beside the knob lights while the lane is over a fixed full scale. The pen then sits at its stop, and the paper notes "over FS". On auto the attenuator ranges up instead, so the lamp stays dark.
- **Notes also mark** a new longest dispatch for a task, and uplink commands.
- **Toolbar:** paper speed (1 to 25 mm/s), Pause, Reset max (an uplink command), and **Tear off**, which saves the visible paper as a PNG in `Pictures\Downlink`, named `downlink-<firmware>-<date>-<time>.png`, with no dialog.

There is also a text display:

```
Downlink  Floret_Hello.elf  telemetry at 0x2400337c, 200 MHz clock, 2 tasks
interval 1.00 s, 1003 ticks

  handler                load    rate/s     longest
  blink sys             0.00%       0.0     0.34 us
  blink usr             0.00%       2.0     0.22 us
  work sys              0.00%       0.0     0.29 us
  work usr              2.00%      99.7   201.67 us
  -------------------------------------------------
  tasks                 2.00%
  irq + kernel          1.47%
  idle                 96.53%
  CPU                   3.47%
```

## Setup

Python 3.10 or newer. From this folder, on Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

On Linux and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

This installs the dependencies and a `downlink` command into the virtual environment (`.venv/Scripts/downlink`; on Linux and macOS `.venv/bin/downlink`). The activate line puts that folder on the PATH, for this terminal only: run it again in each new terminal, or call `downlink` by its full path. Without installing, `python -m downlink` works from this folder.

## Use

Connect the debug probe (tested with an ST-LINK V3), make sure no other tool such as STM32CubeProgrammer is connected to it, and give Downlink the ELF file of the application that is flashed. In a terminal where the virtual environment is active (see Setup):

```bash
downlink path/to/app.elf
```

| Option | Effect |
|---|---|
| `--text` | A refreshing text table instead of the strip chart |
| `--interval S` | Text mode: seconds between updates (default 1) |
| `--once` | Take one measurement as text and exit |
| `--reset-max` | Reset the "longest" figures first (an uplink command) |
| `--probe ID` | Choose the probe when more than one is connected |
| `--tear-off-dir DIR` | Where Tear off saves the paper (default: `Pictures\Downlink`) |
| `--under-reset` | Connect while holding the target in reset (see below) |

**Downlink checks the ELF first.** It compares sample bytes of the program image with the target's flash, and refuses to run if they differ, rather than reading wrong addresses.

## Reading the numbers

| Line | Meaning |
|---|---|
| **load** | Share of CPU time spent in that handler during the interval |
| **rate/s** | Dispatches per second |
| **longest** | The longest single dispatch since start-up (or `--reset-max`). For a run-to-completion system, the largest of these bounds how long any other task can be kept waiting. |
| **irq + kernel** | Everything that is neither a task nor idle: interrupt handlers (on a Daisy, mostly the audio interrupt) plus the kernel's own work |
| **idle** | Time the CPU spent asleep |

## A sleeping target

An idle Floret sleeps, and an STM32H7 stops its debug port's clocks during sleep unless the "debug in sleep" bits in `DBGMCU_CR` are set. Downlink sets them when it connects, and they stay set until the board is powered off. If Downlink can't reach the target at all, which happens when it's asleep and the bits aren't set yet, run once with `--under-reset`. Downlink then restarts the target, sets the bits, and lets it run.

## Status

Version 0.1: the data link, the strip chart and the text display.
