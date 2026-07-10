"""
verify.py - integrity checking for cartridge dumps.

Two independent kinds of check:

1. Internal consistency - header checksums, the Nintendo logo, and (for GB) the
   global checksum. These need no external data: a good dump of a real cart
   should pass them, and a failure points at a bad read (dirty contacts, loose
   cart) or a non-standard ROM.

2. Known-good match - CRC32 / SHA-1 of the full ROM compared against a database
   of verified dumps (No-Intro style). A match means the dump is byte-perfect.

There is also a read-twice consistency helper used by the GUI: dump, read again,
compare. Identical reads mean the connection is stable and the dump is
trustworthy even when the title isn't in any database.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field

# GBA: first 8 bytes of the Nintendo logo at 0x04 (enough to detect a bad read).
_GBA_LOGO_HEAD = bytes((0x24, 0xFF, 0xAE, 0x51, 0x69, 0x9A, 0xA2, 0x21))

# GB/GBC Nintendo logo at 0x104 (48 bytes).
_GB_LOGO = bytes((
    0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B, 0x03, 0x73, 0x00, 0x83,
    0x00, 0x0C, 0x00, 0x0D, 0x00, 0x08, 0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E,
    0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99, 0xBB, 0xBB, 0x67, 0x63,
    0x6E, 0x0E, 0xEC, 0xCC, 0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E,
))


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerifyResult:
    checks: list = field(default_factory=list)
    crc32: str = ""
    sha1: str = ""
    known_good: bool = False
    known_title: str = ""

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        if self.known_good:
            return f"Verified good dump - matches known-good {self.known_title}."
        if self.all_passed:
            return ("Dump passed all internal checks. Not found in the known-good "
                    "database, but the header and logo are valid.")
        failed = [c.name for c in self.checks if not c.passed]
        return "Dump FAILED: " + ", ".join(failed) + ". Re-seat the cart and retry."


def gba_header_checksum(rom: bytes) -> tuple[int, int]:
    """Return (computed, stored) GBA header checksum. Algorithm: subtract bytes
    0xA0..0xBC, then subtract 0x19; low byte is stored at 0xBD."""
    chk = 0
    for i in range(0xA0, 0xBD):
        chk = (chk - rom[i]) & 0xFF
    chk = (chk - 0x19) & 0xFF
    return chk, rom[0xBD]


def gb_header_checksum(rom: bytes) -> tuple[int, int]:
    chk = 0
    for b in rom[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    return chk, rom[0x14D]


def gb_global_checksum(rom: bytes) -> tuple[int, int]:
    """Sum of all bytes except the two checksum bytes at 0x14E/0x14F."""
    total = 0
    for i, b in enumerate(rom):
        if i not in (0x14E, 0x14F):
            total = (total + b) & 0xFFFF
    stored = (rom[0x14E] << 8) | rom[0x14F]
    return total, stored


def hashes(rom: bytes) -> tuple[str, str]:
    crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}"
    sha1 = hashlib.sha1(rom).hexdigest().lower()
    return crc, sha1


def verify_gba(rom: bytes, known_db: dict | None = None) -> VerifyResult:
    r = VerifyResult()
    if len(rom) < 0xC0:
        r.checks.append(Check("size", False, "ROM too small to have a header."))
        return r

    logo_ok = rom[0x04:0x0C] == _GBA_LOGO_HEAD
    r.checks.append(Check("Nintendo logo", logo_ok,
                          "" if logo_ok else "logo bytes wrong - likely a bad read"))

    comp, stored = gba_header_checksum(rom)
    r.checks.append(Check("header checksum", comp == stored,
                          f"computed 0x{comp:02X}, stored 0x{stored:02X}"))

    # A ROM that's all one byte value is a classic dead-read signature.
    nonuniform = len(set(rom[:4096])) > 1
    r.checks.append(Check("data present", nonuniform,
                          "" if nonuniform else "first 4 KB is uniform - dead read"))

    _finish(r, rom, known_db)
    return r


def verify_gb(rom: bytes, known_db: dict | None = None) -> VerifyResult:
    r = VerifyResult()
    if len(rom) < 0x150:
        r.checks.append(Check("size", False, "ROM too small to have a header."))
        return r

    logo_ok = rom[0x104:0x134] == _GB_LOGO
    r.checks.append(Check("Nintendo logo", logo_ok,
                          "" if logo_ok else "logo bytes wrong - likely a bad read"))

    comp, stored = gb_header_checksum(rom)
    r.checks.append(Check("header checksum", comp == stored,
                          f"computed 0x{comp:02X}, stored 0x{stored:02X}"))

    gcomp, gstored = gb_global_checksum(rom)
    # Some prototypes have a wrong global checksum; treat as a soft check but
    # still report it.
    r.checks.append(Check("global checksum", gcomp == gstored,
                          f"computed 0x{gcomp:04X}, stored 0x{gstored:04X}"))

    _finish(r, rom, known_db)
    return r


def _finish(r: VerifyResult, rom: bytes, known_db: dict | None) -> None:
    r.crc32, r.sha1 = hashes(rom)
    if known_db:
        hit = known_db.get(r.sha1) or known_db.get(r.crc32)
        if hit:
            r.known_good = True
            r.known_title = hit.get("title", "release") if isinstance(hit, dict) \
                else str(hit)


def compare_reads(first: bytes, second: bytes) -> Check:
    """Read-twice consistency: identical dumps mean a stable read."""
    if len(first) != len(second):
        return Check("read-twice", False,
                     f"length differs ({len(first)} vs {len(second)})")
    if first != second:
        # locate the first mismatch to help the user
        n = min(len(first), len(second))
        for i in range(n):
            if first[i] != second[i]:
                return Check("read-twice", False,
                             f"first mismatch at offset 0x{i:X}")
    return Check("read-twice", True, "both reads identical")
