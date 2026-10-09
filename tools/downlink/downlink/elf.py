# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""What Downlink needs from the application's ELF file.

- The address of the telemetry block (symbol floret_telemetry; with LTO GCC
  may rename it floret_telemetry.lto_priv.0, so the name prefix is matched).
- Constant data such as task-name strings, read from the file instead of the
  target.
- Sample bytes of the program image, to check that this ELF is the one
  running on the target.
"""
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

TELEMETRY_SYMBOL = "floret_telemetry"


class ElfError(Exception):
    pass


class ElfImage:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            elf = ELFFile(f)
            self._symbols = {}
            for section in elf.iter_sections():
                if isinstance(section, SymbolTableSection):
                    for sym in section.iter_symbols():
                        if sym.name and sym["st_value"]:
                            self._symbols.setdefault(sym.name, (sym["st_value"], sym["st_size"]))
            # Loadable data, keyed by load address (LMA): what is in flash.
            self._segments = []
            for seg in elf.iter_segments():
                if seg["p_type"] == "PT_LOAD" and seg["p_filesz"] > 0:
                    self._segments.append((seg["p_paddr"], seg["p_vaddr"], seg.data()))

    def telemetry_address(self):
        """Address and size of the telemetry block."""
        matches = [(n, v) for n, v in self._symbols.items()
                   if n == TELEMETRY_SYMBOL or n.startswith(TELEMETRY_SYMBOL + ".")]
        if not matches:
            raise ElfError(f"{self.path} has no {TELEMETRY_SYMBOL} symbol: "
                           "is this a Floret application built with FLORET_FLIGHT_TEST=1?")
        if len(matches) > 1:
            raise ElfError(f"several {TELEMETRY_SYMBOL} symbols: {[n for n, _ in matches]}")
        return matches[0][1]

    def read(self, address, size, by_load_address=False):
        """Bytes at a run-time (or load) address, if the ELF holds them."""
        for lma, vma, data in self._segments:
            base = lma if by_load_address else vma
            if base <= address and address + size <= base + len(data):
                return data[address - base:address - base + size]
        return None

    def cstring(self, address, limit=64):
        """A NUL-terminated string from constant data, or None."""
        for lma, vma, data in self._segments:
            if vma <= address < vma + len(data):
                raw = data[address - vma:address - vma + limit]
                return raw.split(b"\0", 1)[0].decode("ascii", "replace")
        return None

    def image_samples(self, size=256):
        """(load address, bytes) samples of the program image for checking
        against the target: the start of each loadable segment that is
        executed in place (load address == run address), e.g. the vector
        table and the start of the code."""
        samples = []
        for lma, vma, data in self._segments:
            if lma == vma:
                samples.append((lma, data[:size]))
                if len(data) > 4 * size:
                    mid = (len(data) // 2) & ~3
                    samples.append((lma + mid, data[mid:mid + size]))
        return samples
