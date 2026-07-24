"""
flash_patcher.py - Patch a GBA ROM to save on Flash 512K.

A faithful port of metroid-maniac's gba-flash-patcher (MIT, Copyright (c) 2023
Metroid Maniac). Output is byte-identical to gba-flash.exe 0.0.2 for the same
input, which is the only standard worth holding a port of a patcher to.

The tool installs a small ARM payload into free space in the ROM and rewrites
the game's save routines to branch into it. The payload drives a flash save chip
directly with AMD-style commands rather than talking to EEPROM or SRAM. Games
whose save routines cannot be found are left alone and reported, since a ROM
that silently comes out unpatched is worse than one that refuses.

The payload does not store a save as contiguous bytes. It spreads them, one byte
every 1 << loadfactor_log2, so that erasing a 4 KB flash sector only disturbs a
few save bytes at a time. The load factor is chosen inside the payload at run
time from the EEPROM geometry the game leaves in RAM.

Original patcher and payload: metroid-maniac
https://github.com/metroid-maniac/gba-flash-patcher

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field


class FlashPatchError(Exception):
    """Raised when a ROM cannot be patched."""


# metroid-maniac's compiled GBA payload, taken unmodified from gba-flash.exe
# 0.0.2 and confirmed byte-identical against a ROM the released tool patched.
_PAYLOAD_B64 = (
    "2QEAACECAADrAQAATwIAAAECAAB9AgAAAAAAAHtGCDsbaBhocEcwtVUhqiSAJQtLC0oc"
    "cBFwHXAccBFwJTkBcMBGAXj/KfzRVTkZcFU5EXDwIhpwMLwBvABHwEZVVQAOqioADqoi"
    "ELVVJApLGnAKShRwSzQccAFwwEYEeIxC/NGqIRlwVTkRcPAiGnAQvAG8AEfARlVVAA6q"
    "KgAO8LWcRgQAACAFngAuAtHwvAK8CEcXeCV47RtrHp1BJ3BAGWQYYkQBPu/nAwDgIItA"
    "AAUYQ3BH8LWAJGQBHEGLsAKv+mFqRnth4x3bCNsA0xqdRgKru2ABI3ppk0D4YLlhe2D7"
    "aQArBNG9Rgmw8LwBvABHumljHr5pmkMeQBMAemmTQOAi/WkSBRpDcxk6YaNCANmlGwCU"
    "ASF7aDppuGj/96//u2iYGQEjAJUZAPpo//en/wAoCdH7aFsZ+2C7aVsZu2H7aVsb+2HL"
    "5zhp//dd/wAmtELv0DAAu2iZXXtpmEA7aRgY//dw/wE28udztRwAASUWAOAiKwChQBIF"
    "AJYKQ6NAKQD/93z/c7wBvABHcLXgJYoYQBotBZFCAtEBIEBCB+AMAJxALENGXCR4pkID"
    "0AgAcLwCvAhHATHt5xC1SQQBI0kM//d3/xC8AbwARwMAELVbBAgAWQwBI//3x/8QvAG8"
    "AEdJBAEjELVJDP/3z/8DAAAgg0IC2+AgAAUYQxC8ArwIR3C1BAANAP/3+f4DAAEgACsK"
    "0JqIAyNAKgDRBDMoAAgi4QD/90b/ACBwvAK8CEdwtQQADQD/9+L+AwABIAArCtCaiAMj"
    "QCoA0QQzKAAIIuEA//eK/wAgcLwCvAhHcLUEAA0A//fL/gMAASAAKwvQmogDI0AqANEE"
    "MwgiKADhAP/3hP/AQ8APcLwCvAhH"
)
PAYLOAD = base64.b64decode(_PAYLOAD_B64)
PAYLOAD_LEN = len(PAYLOAD)

# Word indices into the table at the start of the payload. The first six hold
# entry point offsets with the Thumb bit already set; the last is where the
# patcher records the RAM address of the game's EEPROM geometry.
WRITE_SRAM, WRITE_EEPROM, READ_SRAM, READ_EEPROM, VERIFY_SRAM, VERIFY_EEPROM, \
    EEPROM_META = range(7)

THUMB_THUNK = bytes([0x00, 0x4B, 0x18, 0x47])
ARM_THUNK = bytes([0x00, 0x30, 0x9F, 0xE5, 0x13, 0xFF, 0x2F, 0xE1])

ROM_BASE = 0x08000000
MAX_ROM = 0x2000000
ALIGN = 0x40000                 # ROMs are padded up to 256 KB


# name, signature, payload entry, thunk, word slot after the thunk
SIGNATURES = [
    ("WriteSram", bytes([
        0x30, 0xB5, 0x05, 0x1C, 0x0C, 0x1C, 0x13, 0x1C,
        0x0B, 0x4A, 0x10, 0x88, 0x0B, 0x49, 0x08, 0x40]),
     WRITE_SRAM, THUMB_THUNK, 1),
    ("WriteSram 2", bytes([
        0x80, 0xB5, 0x83, 0xB0, 0x6F, 0x46, 0x38, 0x60,
        0x79, 0x60, 0xBA, 0x60, 0x09, 0x48, 0x09, 0x49]),
     WRITE_SRAM, THUMB_THUNK, 1),
    ("WriteSramFast", bytes([
        0x04, 0xC0, 0x90, 0xE4, 0x01, 0xC0, 0xC1, 0xE4,
        0x2C, 0xC4, 0xA0, 0xE1, 0x01, 0xC0, 0xC1, 0xE4]),
     WRITE_SRAM, ARM_THUNK, 2),
    ("ReadSram", bytes([
        0x70, 0xB5, 0xA0, 0xB0, 0x04, 0x1C, 0x0D, 0x1C,
        0x16, 0x1C, 0x08, 0x4A, 0x10, 0x88, 0x08, 0x49]),
     READ_SRAM, THUMB_THUNK, 1),
    ("VerifySram", bytes([
        0x70, 0xB5, 0xB0, 0xB0, 0x04, 0x1C, 0x0D, 0x1C,
        0x16, 0x1C, 0x08, 0x4A, 0x10, 0x88, 0x08, 0x49]),
     VERIFY_SRAM, THUMB_THUNK, 1),
    ("SRAM-patched ProgramEepromDword", bytes([
        0x70, 0xB5, 0x00, 0x04, 0x0A, 0x1C, 0x40, 0x0B, 0xE0, 0x21,
        0x09, 0x05, 0x41, 0x18, 0x07, 0x31, 0x00, 0x23, 0x10, 0x78]),
     WRITE_EEPROM, THUMB_THUNK, 1),
    ("SRAM-patched ReadEepromDword", bytes([
        0x70, 0xB5, 0x00, 0x04, 0x0A, 0x1C, 0x40, 0x0B,
        0xE0, 0x21, 0x09, 0x05, 0x41, 0x18, 0x07, 0x31]),
     READ_EEPROM, THUMB_THUNK, 1),
    ("SRAM-patched VerifyEepromDword", bytes([
        0x30, 0xB5, 0x82, 0xB0, 0x0C, 0x1C, 0x00, 0x04,
        0x01, 0x0C, 0x00, 0x25, 0x03, 0x48, 0x00, 0x68]),
     VERIFY_EEPROM, THUMB_THUNK, 1),
]

# Not patched, only read: the patcher lifts the RAM address of the game's
# EEPROM geometry out of this routine and hands it to the payload.
IDENTIFY_EEPROM = bytes([
    0x00, 0x04, 0x00, 0x0C, 0x00, 0x22, 0x04, 0x28,
    0x08, 0xD1, 0x02, 0x49, 0x02, 0x48, 0x08, 0x60])

# Space for a forced geometry block appended after the payload: one pointer
# word followed by the structure it points at.
META_BLOCK = 16

# The payload picks its spread from one field: 7 when the EEPROM reports 0x40
# addresses, 3 otherwise. Faking that field is enough to choose the layout.
LOADFACTOR_ADDRS = {7: 0x40, 3: 0x400}


@dataclass
class FlashPatchResult:
    data: bytes
    payload_base: int
    expanded: bool = False
    padded: bool = False
    eeprom_meta_addr: int = 0
    hooks: list = field(default_factory=list)   # (name, offset, target)
    forced_loadfactor: int | None = None


def _payload_word(index: int) -> int:
    return struct.unpack_from("<I", PAYLOAD, index * 4)[0]


def _find_payload_home(rom: bytearray, romsize: int, need: int) -> int:
    """Lowest 4-byte aligned spot holding `need` bytes of blank ROM.

    Blank means all 0x00 or all 0xFF. The search walks down from the end, so
    the payload lands as late in the ROM as the padding allows.
    """
    base = romsize - need
    while base >= 0:
        chunk = rom[base:base + need]
        if not any(chunk) or all(b == 0xFF for b in chunk):
            return base
        base -= 4
    return -1


def patch_rom(data: bytes,
              force_loadfactor: int | None = None) -> FlashPatchResult:
    """Patch a ROM to save on Flash 512K. Raises FlashPatchError if it cannot.

    force_loadfactor overrides how widely the payload spreads a save. Left as
    None the output is byte-identical to gba-flash.exe and the game's own
    reported EEPROM geometry decides, which is the behaviour to keep unless
    there is a reason not to.

    The payload reads that geometry through two indirections: a word it carries
    holds a RAM address, and the word at that address points at the structure.
    Forcing the value means pointing the first word at a structure built here in
    ROM instead of at the game's RAM, so the payload reads a fixed answer
    without a single instruction of it being modified.
    """
    if force_loadfactor is not None and force_loadfactor not in LOADFACTOR_ADDRS:
        raise FlashPatchError(
            f"Load factor must be one of {sorted(LOADFACTOR_ADDRS)}, "
            f"not {force_loadfactor}.")
    romsize = len(data)
    if romsize > MAX_ROM:
        raise FlashPatchError("ROM is larger than 32 MB, so it is not a GBA ROM.")

    padded = bool(romsize & (ALIGN - 1))
    if padded:
        romsize = (romsize & ~(ALIGN - 1)) + ALIGN
    rom = bytearray(data)
    rom.extend(b"\x00" * (romsize - len(rom)))

    need = PAYLOAD_LEN + (META_BLOCK if force_loadfactor is not None else 0)
    expanded = False
    base = _find_payload_home(rom, romsize, need)
    if base < 0:
        if romsize + need > MAX_ROM:
            raise FlashPatchError(
                "ROM is already at the maximum size, so there is nowhere to "
                "put the payload and no room to expand.")
        expanded = True
        base = romsize
        romsize += need
        rom.extend(b"\x00" * need)

    rom[base:base + PAYLOAD_LEN] = PAYLOAD

    hooks = []
    meta_addr = 0
    limit = romsize - 64
    for off in range(0, limit, 2):
        for name, sig, entry, thunk, slot in SIGNATURES:
            if rom[off:off + len(sig)] == sig:
                target = ROM_BASE + base + _payload_word(entry)
                rom[off:off + len(thunk)] = thunk
                struct.pack_into("<I", rom, off + slot * 4, target)
                hooks.append((name, off, target))
        if rom[off:off + len(IDENTIFY_EEPROM)] == IDENTIFY_EEPROM:
            meta_addr = struct.unpack_from("<I", rom, off + 5 * 4)[0]
            struct.pack_into("<I", rom, base + EEPROM_META * 4, meta_addr)
            hooks.append(("SRAM-patched IdentifyEeprom", off, 0))

    if force_loadfactor is not None:
        # Written after the scan so it wins over whatever the game's identify
        # routine reported.
        block = base + PAYLOAD_LEN
        struct.pack_into("<I", rom, block, ROM_BASE + block + 4)
        addrs = LOADFACTOR_ADDRS[force_loadfactor]
        struct.pack_into("<IHHB", rom, block + 4,
                         addrs * 8,          # size in bytes, unused by payload
                         addrs,              # the field that decides the spread
                         0,                  # wait, unused
                         6 if addrs == 0x40 else 14)
        meta_addr = ROM_BASE + block
        struct.pack_into("<I", rom, base + EEPROM_META * 4, meta_addr)

    if not hooks:
        raise FlashPatchError(
            "No save routine could be found to hook. Either the game has no "
            "save functionality, or it needs an SRAM patch applied first.")

    return FlashPatchResult(data=bytes(rom), payload_base=base,
                            expanded=expanded, padded=padded,
                            eeprom_meta_addr=meta_addr, hooks=hooks,
                            forced_loadfactor=force_loadfactor)
