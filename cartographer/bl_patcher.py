"""
bl_patcher.py - GBA automatic batteryless save patcher.

This is a faithful Python port of the host-side patcher from the
"gba-auto-batteryless-patcher" project by metroid-maniac (MIT licensed). It
patches a GBA ROM so a bootleg/flash cartridge that has SRAM but no battery
will persist saves by flushing SRAM to flash. The on-cartridge ARM payload is
metroid-maniac's, compiled unmodified and embedded below; the patching logic
here reproduces the original byte-for-byte.

The ROM must already be SRAM-patched (e.g. with GBATA) before patching, exactly
as the original tool requires.

Modes:
    MODE_AUTO   (0) - save is flushed automatically a few seconds after the
                      in-game save.
    MODE_KEYPAD (1) - save is flushed on demand by pressing L+R+Start+Select.

Original patcher & payload: metroid-maniac
  https://github.com/metroid-maniac/gba-auto-batteryless-patcher
Python port integrated by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field

MODE_AUTO = 0
MODE_KEYPAD = 1

SECTOR = 0x40000          # 256 KB flash sector
ROM_MAX = 0x02000000      # 32 MB max GBA ROM
EXPAND = 0x80000          # 512 KB expansion step

_SIGNATURE = b"<3 from Maniac"

# Payload header word indices (uint32 table at the start of the payload)
_ORIGINAL_ENTRYPOINT_ADDR = 0
_FLUSH_MODE = 1
_SAVE_SIZE = 2
_PATCHED_ENTRYPOINT = 3
_WRITE_SRAM_PATCHED = 4
_WRITE_EEPROM_PATCHED = 5
_WRITE_FLASH_PATCHED = 6
_WRITE_EEPROM_V111_POSTHOOK = 7

# Branch thunks injected over the game's save routines
_THUMB_BRANCH_THUNK = bytes((0x00, 0x4B, 0x18, 0x47))            # ldr r3,[pc,#0]; bx r3
_ARM_BRANCH_THUNK = bytes((0x00, 0x30, 0x9F, 0xE5, 0x13, 0xFF, 0x2F, 0xE1))
_EEPROMV11_EPILOGUE_PATCH = bytes((0x07, 0x49, 0x08, 0x47))

# Known SRAM-patched save-routine signatures
_WRITE_SRAM_SIG = bytes((0x30, 0xB5, 0x05, 0x1C, 0x0C, 0x1C, 0x13, 0x1C,
                         0x0B, 0x4A, 0x10, 0x88, 0x0B, 0x49, 0x08, 0x40))
_WRITE_SRAM2_SIG = bytes((0x80, 0xB5, 0x83, 0xB0, 0x6F, 0x46, 0x38, 0x60,
                          0x79, 0x60, 0xBA, 0x60, 0x09, 0x48, 0x09, 0x49))
_WRITE_SRAM_RAM_SIG = bytes((0x04, 0xC0, 0x90, 0xE4, 0x01, 0xC0, 0xC1, 0xE4,
                             0x2C, 0xC4, 0xA0, 0xE1, 0x01, 0xC0, 0xC1, 0xE4))
_WRITE_EEPROM_SIG = bytes((0x70, 0xB5, 0x00, 0x04, 0x0A, 0x1C, 0x40, 0x0B,
                           0xE0, 0x21, 0x09, 0x05, 0x41, 0x18, 0x07, 0x31,
                           0x00, 0x23, 0x10, 0x78))
_WRITE_FLASH_SIG = bytes((0x70, 0xB5, 0x00, 0x03, 0x0A, 0x1C, 0xE0, 0x21,
                          0x09, 0x05, 0x41, 0x18, 0x01, 0x23, 0x1B, 0x03))
_WRITE_FLASH2_SIG = bytes((0x7C, 0xB5, 0x90, 0xB0, 0x00, 0x03, 0x0A, 0x1C,
                           0xE0, 0x21, 0x09, 0x05, 0x09, 0x18, 0x01, 0x23))
_WRITE_FLASH3_SIG = bytes((0xF0, 0xB5, 0x90, 0xB0, 0x0F, 0x1C, 0x00, 0x04,
                           0x04, 0x0C, 0x03, 0x48, 0x00, 0x68, 0x40, 0x89))
_WRITE_EEPROMV111_SIG = bytes((0x0A, 0x88, 0x80, 0x21, 0x09, 0x06, 0x0A, 0x43,
                               0x02, 0x60, 0x07, 0x48, 0x00, 0x47, 0x00, 0x00))

# metroid-maniac's GBA payload (compiled with arm-none-eabi-gcc, unmodified)
_PAYLOAD_B64 = (
    "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"
)
PAYLOAD = base64.b64decode(_PAYLOAD_B64)
PAYLOAD_LEN = len(PAYLOAD)


class PatchError(Exception):
    """Raised when a ROM cannot be patched."""


@dataclass
class PatchResult:
    data: bytes
    mode: int
    payload_base: int
    original_entrypoint: int
    save_size: int
    expanded: bool
    irq_refs: int
    write_hooks: list = field(default_factory=list)
    log: list = field(default_factory=list)

    @property
    def suffix(self) -> str:
        return "_keypad.gba" if self.mode == MODE_KEYPAD else "_auto.gba"


def _payload_word(idx: int) -> int:
    return struct.unpack_from("<I", PAYLOAD, idx * 4)[0]


def _set_word(buf: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", buf, off, value & 0xFFFFFFFF)


def is_already_patched(data: bytes) -> bool:
    n = len(_SIGNATURE)
    for i in range(0, len(data) - n, 4):
        if data[i:i + n] == _SIGNATURE:
            return True
    return False


def patch_rom(data: bytes, mode: int = MODE_AUTO) -> PatchResult:
    """Patch a GBA ROM for batteryless saving. Returns a PatchResult.

    Mirrors metroid-maniac's patcher.c exactly so output is byte-identical.
    """
    log: list[str] = []

    romsize = len(data)
    if romsize > ROM_MAX:
        raise PatchError("ROM too large - not a GBA ROM?")

    # Pad to 256 KB alignment if trimmed/misaligned
    if romsize & 0x3FFFF:
        log.append("ROM misaligned; padding to 256 KB alignment.")
        romsize = (romsize & ~0x3FFFF) + 0x40000

    rom = bytearray(b"\xff" * romsize)
    rom[:len(data)] = data[:romsize]

    if is_already_patched(rom):
        raise PatchError("Signature found - ROM is already patched.")

    # Patch references to the IRQ handler address (0x03007ffc -> 0x03007ff4)
    old_irq = bytes((0xFC, 0x7F, 0x00, 0x03))
    new_irq = bytes((0xF4, 0x7F, 0x00, 0x03))
    irq_refs = 0
    for i in range(0, romsize, 4):
        if rom[i:i + 4] == old_irq:
            irq_refs += 1
            rom[i:i + 4] = new_irq
    if irq_refs == 0:
        raise PatchError(
            "No reference to the IRQ handler found. The ROM may already be "
            "patched, or it isn't a standard GBA ROM.")
    log.append(f"Patched {irq_refs} IRQ handler reference(s).")

    # Find a clean (all-00 or all-FF) region to host the payload, just before
    # a 256 KB sector boundary, scanning downward.
    expanded = False
    region = SECTOR + PAYLOAD_LEN
    payload_base = romsize - SECTOR - PAYLOAD_LEN
    while payload_base >= 0:
        chunk = rom[payload_base:payload_base + region]
        if chunk.count(0x00) == len(chunk) or chunk.count(0xFF) == len(chunk):
            break
        payload_base -= SECTOR
    if payload_base < 0:
        if romsize + EXPAND > ROM_MAX:
            raise PatchError("ROM already at max size; cannot expand to fit payload.")
        log.append("No free space found; expanding ROM by 512 KB.")
        romsize += EXPAND
        rom.extend(b"\xff" * EXPAND)
        expanded = True
        payload_base = romsize - SECTOR - PAYLOAD_LEN

    log.append(f"Installing payload at 0x{payload_base:x}; "
               f"save stored at 0x{payload_base + PAYLOAD_LEN:x}.")
    rom[payload_base:payload_base + PAYLOAD_LEN] = PAYLOAD

    # Flush mode
    _set_word(rom, payload_base + _FLUSH_MODE * 4, mode)

    # Entry point patch
    if rom[3] != 0xEA:
        raise PatchError("Unexpected entry-point instruction (not a GBA ROM?).")
    oep_off = rom[0] | (rom[1] << 8) | (rom[2] << 16)
    original_entrypoint = 0x08000000 + 8 + (oep_off << 2)
    _set_word(rom, payload_base + _ORIGINAL_ENTRYPOINT_ADDR * 4, original_entrypoint)
    new_entrypoint = 0x08000000 + payload_base + _payload_word(_PATCHED_ENTRYPOINT)
    _set_word(rom, 0, 0xEA000000 | (((new_entrypoint - 0x08000008) >> 2) & 0xFFFFFF))
    log.append(f"Original entry point 0x{original_entrypoint:x}; "
               f"new entry point 0x{new_entrypoint:x}.")

    sram_target = 0x08000000 + payload_base + _payload_word(_WRITE_SRAM_PATCHED)
    eeprom_target = 0x08000000 + payload_base + _payload_word(_WRITE_EEPROM_PATCHED)
    flash_target = 0x08000000 + payload_base + _payload_word(_WRITE_FLASH_PATCHED)
    eepromv111_target = 0x08000000 + payload_base + _payload_word(_WRITE_EEPROM_V111_POSTHOOK)

    write_hooks: list[str] = []

    def thumb_hook(loc: int, target: int) -> None:
        rom[loc:loc + 4] = _THUMB_BRANCH_THUNK
        _set_word(rom, loc + 4, target)

    def arm_hook(loc: int, target: int) -> None:
        rom[loc:loc + 8] = _ARM_BRANCH_THUNK
        _set_word(rom, loc + 8, target)

    found = False
    i = 0
    end = romsize - 64
    while i < end:
        seg = rom[i:i + 20]
        if seg[:16] == _WRITE_SRAM_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, sram_target)
                write_hooks.append(f"WriteSram @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x8000)
        if seg[:16] == _WRITE_SRAM2_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, sram_target)
                write_hooks.append(f"WriteSram2 @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x8000)
        if seg[:16] == _WRITE_SRAM_RAM_SIG:
            found = True
            if mode == MODE_AUTO:
                arm_hook(i, sram_target)
                write_hooks.append(f"WriteSramFast @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x8000)
        if seg[:20] == _WRITE_EEPROM_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, eeprom_target)
                write_hooks.append(f"ProgramEepromDword @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x2000)
        if seg[:16] == _WRITE_FLASH_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, flash_target)
                write_hooks.append(f"Flash write 1 @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x10000)
        if seg[:16] == _WRITE_FLASH2_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, flash_target)
                write_hooks.append(f"Flash write 2 @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x10000)
        if seg[:16] == _WRITE_FLASH3_SIG:
            found = True
            if mode == MODE_AUTO:
                thumb_hook(i, flash_target)
                write_hooks.append(f"Flash write 3 @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x20000)
        if seg[:16] == _WRITE_EEPROMV111_SIG:
            found = True
            if mode == MODE_AUTO:
                rom[i + 12:i + 16] = _EEPROMV11_EPILOGUE_PATCH
                _set_word(rom, i + 44, eepromv111_target)
                write_hooks.append(f"EEPROM_V111 epilogue @ 0x{i:x}")
            _set_word(rom, payload_base + _SAVE_SIZE * 4, 0x2000)
        i += 2

    if not found:
        if mode == MODE_AUTO:
            raise PatchError(
                "No save/write function found to hook. Make sure the game has "
                "saving and has been SRAM-patched (e.g. with GBATA) first.")
        log.append("No known save function found; defaulting to 128 KB save.")

    save_size = struct.unpack_from("<I", rom, payload_base + _SAVE_SIZE * 4)[0]
    return PatchResult(
        data=bytes(rom), mode=mode, payload_base=payload_base,
        original_entrypoint=original_entrypoint, save_size=save_size,
        expanded=expanded, irq_refs=irq_refs, write_hooks=write_hooks, log=log,
    )
