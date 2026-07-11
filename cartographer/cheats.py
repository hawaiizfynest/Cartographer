"""
cheats.py - decode Game Genie and GameShark codes, and bake Game Genie codes
into a ROM permanently.

An important distinction:

  * Game Boy Game Genie codes are ROM patches. Each code says "at this ROM
    address, the original byte is X, change reads of it to Y." Because the target
    is a ROM address with a known original value, these can be applied to a ROM
    file permanently - find the address, check the old value matches, write the
    new value.

  * GameShark codes (GB/GBC) write to RAM while the game runs. They are not ROM
    edits and cannot be baked into a ROM file. We decode them for reference, but
    applying them to a ROM isn't possible - that needs a runtime cheat device or
    an emulator's cheat engine.

  * GBA GameShark/Action Replay codes are also runtime RAM patches, decoded here
    for reference only.

Game Boy Game Genie format (ABC-DEF-GHI, 6 or 9 hex digits):
    AB   = new data
    FCDE = address, XOR 0xF000
    GI   = old data, rotate-right 2 then XOR 0xBA   (9-digit codes only)
    H    = unused (checksum-ish)

Verified against the known code FA1-F5A-E61 (address 0x51F5, 0xC2 -> 0xFA).

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class CheatError(Exception):
    pass


@dataclass
class GameGenieCode:
    raw: str
    address: int
    new_value: int
    old_value: int | None       # None for 6-digit codes

    def describe(self) -> str:
        old = (f", was 0x{self.old_value:02X}" if self.old_value is not None
               else "")
        return (f"ROM 0x{self.address:04X} = 0x{self.new_value:02X}{old}")


@dataclass
class GameSharkCode:
    raw: str
    ram_bank: int
    address: int
    value: int

    def describe(self) -> str:
        return (f"RAM 0x{self.address:04X} = 0x{self.value:02X} "
                f"(bank {self.ram_bank:02X}) - runtime only")


def _ror8(v: int, n: int) -> int:
    return ((v >> n) | (v << (8 - n))) & 0xFF


def decode_game_genie(code: str) -> GameGenieCode:
    """Decode a Game Boy Game Genie code (6 or 9 hex digits, dashes optional)."""
    h = re.sub(r"[^0-9A-Fa-f]", "", code).upper()
    if len(h) not in (6, 9):
        raise CheatError(f"Game Genie codes are 6 or 9 hex digits; got {len(h)}.")
    d = [int(c, 16) for c in h]
    new_value = (d[0] << 4) | d[1]
    addr_enc = (d[5] << 12) | (d[2] << 8) | (d[3] << 4) | d[4]
    address = addr_enc ^ 0xF000
    old_value = None
    if len(h) == 9:
        gi = (d[6] << 4) | d[8]
        old_value = _ror8(gi, 2) ^ 0xBA
    return GameGenieCode(code.strip().upper(), address, new_value, old_value)


def decode_gameshark_gb(code: str) -> GameSharkCode:
    """Decode a Game Boy GameShark code (8 hex digits: TTBBAAAA).

    TT = type/RAM bank, BB = value (note: byte order), AAAA = address (little
    endian). Example 010238CD -> bank 0x01, write 0x02 at 0xCD38.
    """
    h = re.sub(r"[^0-9A-Fa-f]", "", code).upper()
    if len(h) != 8:
        raise CheatError(f"GB GameShark codes are 8 hex digits; got {len(h)}.")
    ram_bank = int(h[0:2], 16)
    value = int(h[2:4], 16)
    # address is stored little-endian: last two bytes are low, high
    low = int(h[4:6], 16)
    high = int(h[6:8], 16)
    address = (high << 8) | low
    return GameSharkCode(code.strip().upper(), ram_bank, address, value)


@dataclass
class GbaCheat:
    raw: str
    address: int
    value: int
    width: int          # 1, 2 or 4 bytes


def decode_gba_raw(code: str) -> GbaCheat:
    """Decode a raw GBA cheat 'XXXXXXXX:YYYYYYYY' (address:value).

    The value's hex length sets the write width: 2 chars = 8-bit, 4 = 16-bit,
    8 = 32-bit. These are runtime RAM writes, decoded for reference.
    """
    m = re.match(r"^\s*([0-9A-Fa-f]{8})\s*:\s*([0-9A-Fa-f]{2,8})\s*$", code)
    if not m:
        raise CheatError("Expected GBA code in the form XXXXXXXX:YYYYYYYY.")
    address = int(m.group(1), 16)
    val_hex = m.group(2)
    if len(val_hex) not in (2, 4, 8):
        raise CheatError("GBA value must be 2, 4 or 8 hex digits.")
    return GbaCheat(code.strip().upper(), address, int(val_hex, 16),
                    len(val_hex) // 2)


@dataclass
class ApplyReport:
    applied: list          # list of GameGenieCode actually written
    skipped: list          # (code, reason) tuples
    data: bytes

    def summary(self) -> str:
        lines = [f"{len(self.applied)} Game Genie code(s) applied."]
        for c in self.applied:
            lines.append(f"  \u2713 {c.raw}  ({c.describe()})")
        for code, reason in self.skipped:
            lines.append(f"  \u2717 {code}: {reason}")
        return "\n".join(lines)


def apply_game_genie(rom: bytes, codes: list) -> ApplyReport:
    """Apply Game Genie codes to a ROM. Codes whose old-value check fails (wrong
    ROM, or the byte is banked out of the visible ROM window) are skipped and
    reported rather than written blindly."""
    out = bytearray(rom)
    applied = []
    skipped = []
    for raw in codes:
        raw = raw.strip()
        if not raw:
            continue
        try:
            gg = decode_game_genie(raw)
        except CheatError as exc:
            skipped.append((raw, str(exc)))
            continue
        if gg.address >= len(out):
            skipped.append((raw, f"address 0x{gg.address:04X} is past the end "
                                 f"of this ROM"))
            continue
        if gg.old_value is not None and out[gg.address] != gg.old_value:
            skipped.append((raw,
                            f"old-value mismatch (ROM has 0x{out[gg.address]:02X}, "
                            f"code expects 0x{gg.old_value:02X}) - wrong ROM "
                            f"version, or the byte lives in a swapped bank"))
            continue
        out[gg.address] = gg.new_value
        applied.append(gg)
    return ApplyReport(applied, skipped, bytes(out))
