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
import os
import re
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


# ---------------------------------------------------------------------------
# Dump report ("receipt") - a plain-text sidecar written next to each dump so
# the file carries its own re-checkable record of what it hashed to and whether
# it verified on the day it was made. FlashGBX writes one for the same reason.
# ---------------------------------------------------------------------------

@dataclass
class DumpMeta:
    """Cartridge metadata for the report header. Empty fields are omitted."""
    console: str = ""     # "Game Boy Advance", "Game Boy Color", "Game Boy"
    title: str = ""       # resolved full title
    game_code: str = ""   # GBA game code, e.g. "BPEE" (GB carts have none)
    rom_size: int = 0     # dump size in bytes
    save_type: str = ""   # human label, e.g. "Flash 1Mbit (128 KB)"


_COL = 17  # label column width, matches "Header checksum: " with two spaces


def _field(label: str, value: str) -> str:
    return f"{label + ':':<{_COL}}{value}"


def _size_human(n: int) -> str:
    mb = 1024 * 1024
    if n >= mb and n % mb == 0:
        return f"{n // mb} MB ({n:,} bytes)"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024} KB ({n:,} bytes)"
    return f"{n:,} bytes"


def build_report(filename: str, meta: DumpMeta, result: VerifyResult,
                 dumped_at: str, app_version: str = "",
                 read_twice: Check | None = None) -> str:
    """Render the report text. Pure string building - no disk, no clock - so
    it stays unit-testable. `dumped_at` is a preformatted local timestamp."""
    lines = ["Cartographer dump report",
             "========================",
             _field("File", filename),
             _field("Dumped", f"{dumped_at} (local)")]
    if app_version:
        lines.append(_field("Cartographer", f"v{app_version}"))
    lines.append("")

    for label, value in ((("Console", meta.console)),
                         ("Title", meta.title),
                         ("Game code", meta.game_code),
                         ("ROM size", _size_human(meta.rom_size)
                          if meta.rom_size else ""),
                         ("Save type", meta.save_type)):
        if value:
            lines.append(_field(label, value))
    if lines[-1] != "":
        lines.append("")

    lines += ["Integrity", "---------"]
    for c in result.checks:
        verdict = "OK" if c.passed else "FAILED"
        if c.detail and (not c.passed or "checksum" in c.name):
            verdict += f" ({c.detail})"
        lines.append(_field(c.name[:1].upper() + c.name[1:], verdict))
    lines.append(_field("CRC32", result.crc32))
    lines.append(_field("SHA-1", result.sha1))
    lines.append("")

    if result.known_good:
        lines.append("Known-good match: YES - matches known-good "
                     f"\"{result.known_title}\"")
    else:
        lines.append("Known-good match: NO - SHA-1 not in the known-good "
                     "database")
    if read_twice is not None:
        verdict = "PASS" if read_twice.passed else "FAILED"
        lines.append(f"Read-twice check: {verdict} ({read_twice.detail})")
    lines.append("")

    if not result.all_passed:
        lines.append("Result: FAILED - this dump may be corrupt. Re-seat the "
                     "cart, clean the")
        lines.append("        contacts with IPA, and dump again.")
    elif result.known_good:
        lines.append("Result: VERIFIED GOOD")
    else:
        lines.append("Result: PASSED - header and logo are valid. SHA-1 not "
                     "in the known-good")
        lines.append("        database, so there is no reference to compare "
                     "against.")
    lines.append("")
    return "\n".join(lines)


def write_report(dump_path: str, text: str, suffix: str = ".txt") -> str:
    """Write a report next to the file as `<file><suffix>` and return its
    path. Dump receipts use the default `.txt`; restore receipts pass
    `.restore.txt` so the two can never clobber each other."""
    path = dump_path + suffix
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def build_restore_report(filename: str, console: str, save_type: str,
                         save_size: int, crc32: str, sha1: str,
                         writeback: Check | None, restored_at: str,
                         app_version: str = "") -> str:
    """Render the receipt for a save restore: what was written to the cart,
    its hashes, and whether the read-back matched. Pure string building, same
    as build_report, so it stays unit-testable."""
    lines = ["Cartographer restore report",
             "===========================",
             _field("File", filename),
             _field("Restored", f"{restored_at} (local)")]
    if app_version:
        lines.append(_field("Cartographer", f"v{app_version}"))
    lines.append("")

    for label, value in (("Console", console),
                         ("Save type", save_type),
                         ("Save size", _size_human(save_size)
                          if save_size else "")):
        if value:
            lines.append(_field(label, value))
    if lines[-1] != "":
        lines.append("")

    lines += ["Integrity", "---------",
              _field("CRC32", crc32),
              _field("SHA-1", sha1),
              ""]

    if writeback is not None:
        verdict = "PASS" if writeback.passed else "FAILED"
        lines.append(_field("Write verify", f"{verdict} ({writeback.detail})"))
        lines.append("")

    if writeback is not None and writeback.passed:
        lines.append("Result: RESTORED AND VERIFIED - the cartridge save "
                     "matches this file.")
    elif writeback is None:
        lines.append("Result: RESTORED (write not verified by readback)")
    else:
        lines.append("Result: FAILED - the cartridge may not hold this save. "
                     "Re-seat the cart,")
        lines.append("        then restore again.")
    lines.append("")
    return "\n".join(lines)


def write_sha1_file(dump_path: str, sha1: str) -> str:
    """Write `<dump>.sha1` in the classic sha1sum line format
    (`<hash> *<filename>`), so standard tools can check the dump too."""
    path = dump_path + ".sha1"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{sha1} *{os.path.basename(dump_path)}\n")
    return path


_RE_SHA1 = re.compile(r"^SHA-1:\s+([0-9a-fA-F]{40})\s*$", re.MULTILINE)
_RE_CRC32 = re.compile(r"^CRC32:\s+([0-9a-fA-F]{8})\s*$", re.MULTILINE)


def reverify_against_report(dump_path: str,
                            report_path: str | None = None) -> Check:
    """Recompute the file's SHA-1 and CRC32 and compare them against the
    hashes its report recorded. Catches bit rot, truncation and edits."""
    report_path = report_path or (dump_path + ".txt")
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = f.read()
    except OSError as exc:
        return Check("re-verify", False, f"cannot read report: {exc}")

    m_sha, m_crc = _RE_SHA1.search(report), _RE_CRC32.search(report)
    if not m_sha and not m_crc:
        return Check("re-verify", False,
                     "report has no SHA-1 or CRC32 line to check against")
    try:
        with open(dump_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return Check("re-verify", False, f"cannot read ROM: {exc}")

    crc, sha1 = hashes(data)
    if m_sha and m_sha.group(1).lower() != sha1:
        return Check("re-verify", False,
                     f"SHA-1 mismatch: file is {sha1}, report recorded "
                     f"{m_sha.group(1).lower()} - the file has changed since "
                     "it was dumped")
    if m_crc and m_crc.group(1).lower() != crc:
        return Check("re-verify", False,
                     f"CRC32 mismatch: file is {crc}, report recorded "
                     f"{m_crc.group(1).lower()}")
    return Check("re-verify", True,
                 "file still matches the hashes recorded in its report")
