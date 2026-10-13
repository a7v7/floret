# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""Downlink command line.

    downlink app.elf                  the strip chart
    downlink app.elf --text           a refreshing text table instead
    downlink app.elf --once           one measurement as text, then exit
    downlink app.elf --reset-max      clear the 'longest' figures first
"""
import argparse
import sys

from . import text
from .elf import ElfError
from .link import Downlink, LinkError
from .probe import Probe, ProbeError
from .telemetry import UPLINK_RESET_MAX, TelemetryError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="downlink",
                                 description="Watch a running Floret application over the debug probe.")
    ap.add_argument("elf", help="the application's ELF file (the one that is flashed)")
    ap.add_argument("--text", action="store_true", help="show a text table instead of the strip chart")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="text mode: seconds between updates (default 1)")
    ap.add_argument("--once", action="store_true", help="take one measurement as text and exit")
    ap.add_argument("--reset-max", action="store_true", help="reset the 'longest' figures before measuring")
    ap.add_argument("--probe", metavar="ID", help="debug probe unique ID, if more than one is connected")
    ap.add_argument("--tear-off-dir", metavar="DIR",
                    help="where Tear off saves the paper (default: Pictures\\Downlink)")
    ap.add_argument("--under-reset", action="store_true",
                    help="connect under reset (restarts the target): needed if it sleeps and "
                         "its debug-in-sleep bits aren't set yet")
    args = ap.parse_args(argv)

    try:
        with Probe(args.probe, args.under_reset) as probe:
            link = Downlink(args.elf, probe)
            if args.reset_max and not link.uplink(UPLINK_RESET_MAX):
                print("warning: the target didn't acknowledge the uplink command", file=sys.stderr)
            if args.text or args.once:
                text.run(link, args.interval, args.once)
            else:
                from . import stripchart                        # Qt is only needed here
                return stripchart.run(link, args.tear_off_dir)
    except (ProbeError, ElfError, LinkError, TelemetryError) as e:
        print(f"downlink: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
