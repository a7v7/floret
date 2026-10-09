# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""The debug-probe link: reads the target's memory while it runs.

Uses pyOCD with its generic Cortex-M target, which is all Downlink needs:
memory reads and writes through the debug port, without halting the core.
"""
import logging
import struct

from pyocd.core.helpers import ConnectHelper

# STM32H7: the debug port's clocks stop while the core sleeps unless the
# DBGMCU "debug in sleep/stop/standby" bits and the D1/D3 debug clock enables
# are set. They survive a reset; only a power cycle clears them.
STM32H7_DBGMCU_IDC = 0x5C001000
STM32H7_DBGMCU_CR = 0x5C001004
STM32H7_DEV_IDS = {0x450}            # STM32H742/743/750/753
STM32H7_DEBUG_IN_SLEEP = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 21) | (1 << 22)


class ProbeError(Exception):
    pass


class Probe:
    """An open connection to the target. Use as a context manager."""

    def __init__(self, unique_id=None, under_reset=False):
        logging.getLogger("pyocd").setLevel(logging.WARNING)
        mode = "under-reset" if under_reset else "attach"
        try:
            self._session = ConnectHelper.session_with_chosen_probe(
                unique_id=unique_id, blocking=False, target_override="cortex_m",
                connect_mode=mode, options={"resume_on_disconnect": True})
        except Exception as e:
            raise ProbeError(f"can't open the debug probe: {e}") from e
        if self._session is None:
            raise ProbeError("no debug probe found (is the ST-Link plugged in, and "
                             "is STM32CubeProgrammer disconnected?)")
        try:
            self._session.open()
        except Exception as e:
            raise ProbeError(
                f"can't reach the target: {e}\n"
                "If the target sleeps (as an idle Floret does), its debug clocks may be "
                "off. Retry with --under-reset: Downlink then resets the target, turns "
                "on debug-in-sleep, and lets it run again.") from e
        self.target = self._session.target
        self.debug_in_sleep_enabled = self._enable_debug_in_sleep()
        if under_reset:
            self.target.reset()                      # run the application again

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    def _enable_debug_in_sleep(self):
        """Keep the debug port alive while the core sleeps (STM32H7 only)."""
        try:
            if self.target.read32(STM32H7_DBGMCU_IDC) & 0xFFF not in STM32H7_DEV_IDS:
                return False
            cr = self.target.read32(STM32H7_DBGMCU_CR)
            if cr & STM32H7_DEBUG_IN_SLEEP != STM32H7_DEBUG_IN_SLEEP:
                self.target.write32(STM32H7_DBGMCU_CR, cr | STM32H7_DEBUG_IN_SLEEP)
            return True
        except Exception:
            return False

    def read(self, address, size):
        """size bytes from the target. address and size must be multiples of 4."""
        try:
            words = self.target.read_memory_block32(address, size // 4)
        except Exception as e:
            raise ProbeError(f"read of {size} bytes at {address:#010x} failed: {e}") from e
        return struct.pack(f"<{len(words)}I", *words)

    def read32(self, address):
        return self.target.read32(address)

    def write32(self, address, value):
        self.target.write32(address, value)
