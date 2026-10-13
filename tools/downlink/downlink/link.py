# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""The downlink itself: an ELF and a probe, put together.

Finds the telemetry block, checks that the ELF matches the running
firmware, names the tasks, and takes snapshots. Also sends uplink commands.
"""
import struct
import time

from . import telemetry
from .elf import ElfImage


class LinkError(Exception):
    pass


class Downlink:
    # How long to wait for Floret to write its telemetry block. Right after
    # --under-reset restarts the target, the application is still setting up
    # the board, and the block is still zero until floret_init() runs.
    START_TIMEOUT = 3.0

    def __init__(self, elf_path, probe):
        self.elf = ElfImage(elf_path)
        self.probe = probe
        self.address, _ = self.elf.telemetry_address()
        self._check_image()
        raw = self._wait_for_start()
        self.clock_hz, self.task_count = telemetry.parse_header(raw)
        self.size = telemetry.block_size(self.task_count)
        first = telemetry.parse(self.probe.read(self.address, self.size))
        self.names = [self._name(t.name_ptr, i) for i, t in enumerate(first.tasks)]

    def _check_image(self):
        """Refuse to read with an ELF that isn't the running program: every
        address in it would be wrong."""
        for address, expected in self.elf.image_samples():
            n = len(expected) & ~3
            if self.probe.read(address, n) != expected[:n]:
                raise LinkError(f"{self.elf.path} doesn't match the firmware on the target "
                                f"(bytes differ at {address:#010x}). Flash this ELF, or pick "
                                "the ELF that is running.")

    def _wait_for_start(self):
        """The block's header, once its magic number is there, or whatever
        is there after START_TIMEOUT (parse_header then says what's wrong)."""
        deadline = time.monotonic() + self.START_TIMEOUT
        while True:
            raw = self.probe.read(self.address, telemetry.HEADER_SIZE)
            magic = struct.unpack_from("<I", raw)[0]
            if magic == telemetry.MAGIC or time.monotonic() >= deadline:
                return raw
            time.sleep(0.05)

    def _name(self, ptr, index):
        name = self.elf.cstring(ptr)
        if name is None:                                    # not constant data: read the target
            raw = self.probe.read(ptr & ~3, 64)
            name = raw[ptr & 3:].split(b"\0", 1)[0].decode("ascii", "replace")
        return name or f"task{index}"

    def snapshot(self):
        snap = telemetry.parse(self.probe.read(self.address, self.size))
        for t, name in zip(snap.tasks, self.names):
            t.name = name
        return snap

    def uplink(self, command, timeout=1.0):
        """Send an uplink command and wait for Floret to acknowledge it (it
        clears the word). An idle Floret sees it at its next wake-up."""
        addr = self.address + telemetry.OFFSET_UPLINK
        self.probe.write32(addr, command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.probe.read32(addr) == 0:
                return True
            time.sleep(0.005)
        return False
