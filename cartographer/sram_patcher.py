"""
sram_patcher.py - GBA SRAM patcher for reproduction/flash cartridges.

A faithful Python port of bbsan2k's Flash1M_Repro_SRAM_Patcher. It rewrites a
GBA game's save routines (FLASH 512K/1M, EEPROM, or generic FLASH) to use plain
SRAM, including the bank-switching command (write to 0x09000000) that 128 Kbit
FLASH1M games such as Pokemon Gen 3 need on repro carts with 1 Mbit SRAM.

This is the prerequisite step before batteryless patching for flash-save games.

Save-routine pattern sets and patch logic: bbsan2k
  https://github.com/bbsan2k/Flash1M_Repro_SRAM_Patcher  (MIT)
Python port integrated by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SramPatchError(Exception):
    """Raised when a ROM cannot be SRAM-patched."""


@dataclass
class SramPatchResult:
    data: bytes
    save_id: str
    patch_set: str
    patches_applied: int
    locations: list = field(default_factory=list)


# Save-routine patch data ported from Flash1M_Repro_SRAM_Patcher (bbsan2k, MIT)
PATCH_SETS = [
    (['FLASH512'], [
        (bytes([240, 181, 160, 176, 13, 28, 22, 28, 31, 28, 3, 4, 28, 12, 15, 74, 16, 136, 15, 73, 8, 64, 3, 33, 8, 67, 16, 128, 13, 72, 0, 104, 1, 104, 128, 32, 128, 2]), bytes([112, 181, 160, 176, 0, 3, 64, 24, 224, 33, 9, 5, 9, 24, 8, 120, 16, 112, 1, 59, 1, 50, 1, 49, 0, 43, 248, 209, 0, 32, 32, 176, 112, 188, 2, 188, 8, 71]), None),
        (bytes([255, 247, 136, 253, 0, 4, 3, 12]), bytes([27, 35, 27, 2, 50, 32, 3, 67]), None),
        (bytes([112, 181, 144, 176, 21, 77, 41, 136]), bytes([0, 181, 0, 32, 2, 188, 8, 71]), None),
        (bytes([112, 181, 70, 70, 64, 180, 144, 176]), bytes([0, 181, 0, 32, 2, 188, 8, 71]), None),
        (bytes([240, 181, 144, 176, 15, 28, 0, 4, 4, 12, 3, 72, 0, 104, 64, 137, 132, 66, 5, 211, 1, 72, 65, 224]), bytes([124, 181, 144, 176, 0, 3, 10, 28, 224, 33, 9, 5, 9, 24, 1, 35, 27, 3, 16, 120, 8, 112, 1, 59, 1, 50, 1, 49, 0, 43, 248, 209, 0, 32, 16, 176, 124, 188, 2, 188, 8, 71]), None),
    ]),
    (['FLASH1M_V102'], [
        (bytes([5, 75, 170, 33, 25, 112, 5, 74, 85, 33, 17, 112, 176, 33, 25, 112, 224, 33, 9, 5, 8, 112, 112, 71]), bytes([5, 75, 128, 33, 9, 2, 9, 34, 18, 6, 159, 68, 144, 33, 9, 5, 0, 0, 0, 0, 8, 112, 112, 71]), None),
        (bytes([85, 85, 0, 14, 170, 42, 0, 14, 48, 181, 145, 176, 104, 70, 0, 240, 243, 248, 109, 70, 1, 53]), bytes([254, 255, 255, 1, 0, 0, 0, 0, 48, 181, 145, 176, 104, 70, 0, 240, 243, 248, 109, 70, 1, 53]), None),
        (bytes([6, 74, 170, 32, 16, 112, 5, 73, 85, 32, 8, 112, 144, 32, 16, 112, 16, 169, 3, 74, 16, 28, 8, 224, 0, 0, 85, 85, 0, 14, 170, 42, 0, 14, 32, 78, 0, 0, 8, 136, 1, 56, 8, 128, 8, 136, 0, 40, 249, 209, 12, 72]), bytes([6, 74, 170, 32, 0, 0, 5, 73, 85, 32, 0, 0, 144, 32, 0, 0, 16, 169, 3, 74, 16, 28, 8, 224, 0, 0, 85, 85, 0, 14, 170, 42, 0, 14, 32, 78, 0, 0, 8, 136, 1, 56, 8, 128, 8, 136, 0, 40, 249, 209, 12, 72, 19, 32, 19, 32, 0, 6, 4, 12, 224, 32, 0, 5, 98, 32, 98, 32, 0, 6, 0, 14, 4, 67, 7, 73, 170, 32, 0, 0, 7, 74, 85, 32, 0, 0, 240, 32, 0, 0, 0, 0]), None),
        (bytes([20, 73, 170, 36, 12, 112, 19, 75, 85, 34, 26, 112, 128, 32, 8, 112, 12, 112, 26, 112, 16, 32, 8, 112]), bytes([14, 33, 9, 6, 255, 36, 128, 34, 19, 75, 82, 2, 1, 58, 140, 84, 252, 209, 0, 0, 0, 0, 0, 0]), None),
        (bytes([19, 73, 170, 37, 13, 112, 19, 75, 85, 34, 26, 112, 128, 32, 8, 112, 13, 112, 26, 112, 48, 32, 32, 112]), bytes([19, 73, 255, 37, 8, 34, 0, 0, 82, 2, 1, 58, 165, 84, 252, 209, 0, 0, 0, 0, 0, 0, 0, 0]), None),
        (bytes([10, 76, 170, 34, 34, 112, 9, 75, 85, 34, 26, 112, 160, 34, 34, 112, 2, 120, 10, 112]), bytes([10, 76, 170, 34, 0, 0, 9, 75, 85, 34, 0, 0, 160, 34, 0, 0, 2, 120, 10, 112]), None),
    ]),
    (['FLASH1M_V103'], [
        (bytes([5, 75, 170, 33, 25, 112, 5, 74, 85, 33, 17, 112, 176, 33, 25, 112, 224, 33, 9, 5, 8, 112, 112, 71]), bytes([5, 75, 128, 33, 9, 2, 9, 34, 18, 6, 159, 68, 144, 33, 9, 5, 0, 0, 0, 0, 8, 112, 112, 71]), None),
        (bytes([85, 85, 0, 14, 170, 42, 0, 14, 48, 181, 145, 176, 104, 70, 0, 240, 243, 248, 109, 70, 1, 53]), bytes([254, 255, 255, 1, 0, 0, 0, 0, 48, 181, 145, 176, 104, 70, 0, 240, 243, 248, 109, 70, 1, 53]), None),
        (bytes([6, 74, 170, 32, 16, 112, 5, 73, 85, 32, 8, 112, 144, 32, 16, 112, 16, 169, 3, 74, 16, 28, 8, 224, 0, 0, 85, 85, 0, 14, 170, 42, 0, 14, 32, 78, 0, 0, 8, 136, 1, 56, 8, 128, 8, 136, 0, 40, 249, 209, 12, 72]), bytes([6, 74, 170, 32, 0, 0, 5, 73, 85, 32, 0, 0, 144, 32, 0, 0, 16, 169, 3, 74, 16, 28, 8, 224, 0, 0, 85, 85, 0, 14, 170, 42, 0, 14, 32, 78, 0, 0, 8, 136, 1, 56, 8, 128, 8, 136, 0, 40, 249, 209, 12, 72, 19, 32, 19, 32, 0, 6, 4, 12, 224, 32, 0, 5, 98, 32, 98, 32, 0, 6, 0, 14, 4, 67, 7, 73, 170, 32, 0, 0, 7, 74, 85, 32, 0, 0, 240, 32, 0, 0, 0, 0]), None),
        (bytes([20, 73, 170, 36, 12, 112, 19, 75, 85, 34, 26, 112, 128, 32, 8, 112, 12, 112, 26, 112, 16, 32, 8, 112]), bytes([14, 33, 9, 6, 255, 36, 128, 34, 19, 75, 82, 2, 1, 58, 140, 84, 252, 209, 0, 0, 0, 0, 0, 0]), None),
        (bytes([20, 73, 170, 37, 13, 112, 20, 75, 85, 34, 26, 112, 128, 32, 8, 112, 13, 112, 26, 112, 48, 32, 32, 112]), bytes([20, 73, 255, 37, 8, 34, 0, 0, 82, 2, 1, 58, 165, 84, 252, 209, 0, 0, 0, 0, 0, 0, 0, 0]), None),
        (bytes([12, 74, 170, 32, 16, 112, 11, 73, 85, 32, 8, 112, 160, 32, 16, 112, 39, 112, 9, 72]), bytes([12, 74, 170, 32, 0, 0, 11, 73, 85, 32, 0, 0, 160, 32, 0, 0, 39, 112, 9, 72]), None),
        (bytes([10, 76, 170, 34, 34, 112, 9, 75, 85, 34, 26, 112, 160, 34, 34, 112, 2, 120, 10, 112]), bytes([10, 76, 170, 34, 0, 0, 9, 75, 85, 34, 0, 0, 160, 34, 0, 0, 2, 120, 10, 112]), None),
    ]),
    (['EEPROM_V120', 'EEPROM_V121', 'EEPROM_V122'], [
        (bytes([162, 176, 13, 28, 0, 4, 3, 12, 3, 72, 0, 104, 128, 136, 131, 66, 5, 211, 1, 72, 0, 224]), bytes([0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 8, 120, 16, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), [False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, True, False]),
        (bytes([48, 181, 169, 176, 13, 28, 0, 4, 4, 12, 3, 72, 0, 104, 128, 136, 132, 66, 5, 211, 1, 72, 0, 224]), bytes([112, 181, 0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 16, 120, 8, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), [False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, True, False]),
    ]),
    (['EEPROM_V124'], [
        (bytes([162, 176, 13, 28, 0, 4, 3, 12, 3, 72, 0, 104, 128, 136, 131, 66, 5, 211, 1, 72, 0, 224]), bytes([0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 8, 120, 16, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), [False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, False, True, False]),
        (bytes([240, 181, 172, 176, 13, 28, 0, 4, 1, 12, 18, 6, 23, 14, 3, 72, 0, 104, 128, 136, 129, 66, 5, 211]), bytes([112, 181, 0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 16, 120, 8, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), None),
    ]),
    (['EEPROM_V126'], [
        (bytes([162, 176, 13, 28, 0, 4, 3, 12, 3, 72, 0, 104, 128, 136, 131, 66, 5, 211, 1, 72, 74, 224]), bytes([0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 8, 120, 16, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), None),
        (bytes([240, 181, 71, 70, 128, 180, 172, 176, 14, 28, 0, 4, 5, 12, 18, 6, 18, 14, 144, 70, 3, 72, 0, 104]), bytes([112, 181, 0, 4, 10, 28, 64, 11, 224, 33, 9, 5, 65, 24, 7, 49, 0, 35, 16, 120, 8, 112, 1, 51, 1, 50, 1, 57, 7, 43, 248, 217, 0, 32, 112, 188, 2, 188, 8, 71]), None),
    ]),
    (['FLASH_V120', 'FLASH_V121'], [
        (bytes([144, 181, 147, 176, 111, 70, 57, 29, 8, 28, 0, 240]), bytes([0, 181, 61, 32, 0, 2, 31, 33, 8, 67, 2, 188, 8, 71]), None),
        (bytes([128, 181, 148, 176, 111, 70, 57, 28, 8, 128, 56, 28, 1, 136, 15, 41, 4, 217, 1, 72, 86, 224, 0, 0, 255, 128, 0, 0, 35, 72, 35, 73, 10, 136, 35]), bytes([124, 181, 0, 7, 0, 12, 224, 33, 9, 5, 9, 24, 1, 35, 27, 3, 255, 32, 8, 112, 1, 59, 1, 49, 0, 43, 250, 209, 0, 32, 124, 188, 2, 188, 8, 71]), None),
        (bytes([128, 181, 148, 176, 111, 70, 121, 96, 57, 28, 8, 128, 56, 28, 1, 136, 15, 41, 3, 217, 0, 72, 115, 224, 255, 128, 0, 0, 56, 28, 1, 136]), bytes([124, 181, 144, 176, 0, 3, 10, 28, 224, 33, 9, 5, 9, 24, 1, 35, 27, 3, 16, 120, 8, 112, 1, 59, 1, 50, 1, 49, 0, 43, 248, 209, 0, 32, 16, 176, 124, 188, 8, 188, 8, 71]), None),
    ]),
    (['FLASH_V123', 'FLASH_V124'], [
        (bytes([255, 247, 170, 255, 0, 4, 3, 12]), bytes([27, 35, 27, 2, 50, 32, 3, 67]), None),
        (bytes([112, 181, 144, 176, 21, 77]), bytes([0, 32, 112, 71, 21, 77]), None),
        (bytes([112, 181, 70, 70, 64, 180, 144, 176, 0]), bytes([0, 32, 112, 71, 64, 180, 144, 176, 0]), None),
        (bytes([240, 181, 144, 176, 15, 28, 0, 4, 4, 12, 15, 44, 4, 217, 1, 72, 64, 224, 0, 0, 255, 128, 0, 0, 32, 28, 255, 247, 215, 254, 0, 4, 5, 12, 0, 45, 53, 209]), bytes([112, 181, 0, 3, 10, 28, 224, 33, 9, 5, 65, 24, 1, 35, 27, 3, 16, 120, 8, 112, 1, 59, 1, 50, 1, 49, 0, 43, 248, 209, 0, 32, 112, 188, 2, 188, 8, 71]), None),
    ]),
    (['FLASH_V125', 'FLASH_V126'], [
        (bytes([255, 247, 170, 255, 0, 4, 3, 12]), bytes([27, 35, 27, 2, 50, 32, 3, 67]), None),
        (bytes([112, 181, 144, 176, 21, 77]), bytes([0, 32, 112, 71, 21, 77]), None),
        (bytes([112, 181, 70, 70, 64, 180, 144, 176, 0]), bytes([0, 32, 112, 71, 64, 180, 144, 176, 0]), None),
        (bytes([240, 181, 144, 176, 15, 28, 0, 4, 4, 12, 15, 44, 4, 217, 1, 72, 64, 224, 0, 0, 255, 128, 0, 0, 32, 28, 255, 247, 215, 254, 0, 4, 5, 12, 0, 45, 53, 209]), bytes([112, 181, 0, 3, 10, 28, 224, 33, 9, 5, 65, 24, 1, 35, 27, 3, 16, 120, 8, 112, 1, 59, 1, 50, 1, 49, 0, 43, 248, 209, 0, 32, 112, 188, 2, 188, 8, 71]), None),
    ]),
]


def _find_index(data: bytes, marker: bytes, mask) -> int:
    """Leftmost index where marker matches data (mask[i]=True is a wildcard).
    Mirrors bbsan2k's PatchSet::findIndex result (leftmost match) and the
    applyPatches rule that a match must be at index > 0."""
    n = len(marker)
    if len(data) <= n:
        return -1
    if not mask:
        # exact search; honour the > 0 rule
        start = 0
        while True:
            idx = data.find(marker, start)
            if idx < 0:
                return -1
            if idx > 0:
                return idx
            start = idx + 1
    # masked search: anchor on the first concrete (non-wildcard) byte
    anchor = next((k for k in range(n) if not mask[k]), 0)
    anchor_byte = bytes((marker[anchor],))
    start = 0
    limit = len(data) - n
    while start <= limit:
        cand = data.find(anchor_byte, start)
        if cand < 0 or cand - anchor > limit:
            return -1
        base = cand - anchor
        if base > 0:
            ok = True
            for k in range(n):
                if not mask[k] and data[base + k] != marker[k]:
                    ok = False
                    break
            if ok:
                return base
        start = cand + 1
    return -1


def _detect(data: bytes):
    """Return (patch_set_index, matched_identifier) using the dispatch order."""
    for psi, (idents, _patches) in enumerate(PATCH_SETS):
        for ident in idents:
            if ident.encode("ascii") in data:
                return psi, ident
    return -1, ""


def detect_save_id(data: bytes) -> str:
    _psi, ident = _detect(data)
    return ident


def patch_rom(data: bytes) -> SramPatchResult:
    """SRAM-patch a GBA ROM. Output is byte-identical to bbsan2k's tool."""
    psi, ident = _detect(data)
    if psi < 0:
        raise SramPatchError(
            "No supported save-type signature found. This ROM may already be "
            "SRAM-based, or it isn't one of the supported FLASH/EEPROM types.")

    idents, patches = PATCH_SETS[psi]
    set_name = idents[0] if len(idents) == 1 else "/".join(idents)
    buf = bytearray(data)
    locations: list[str] = []

    for marker, replace, mask in patches:
        idx = _find_index(bytes(buf), marker, mask)
        if idx <= 0 or (len(buf) - idx) < len(replace):
            raise SramPatchError(
                f"A required save-routine pattern for {ident} was not found. "
                f"The ROM may be a bad dump, an unsupported revision, or already "
                f"patched.")
        buf[idx:idx + len(replace)] = replace
        locations.append(f"0x{idx:06x}")

    return SramPatchResult(
        data=bytes(buf), save_id=ident, patch_set=set_name,
        patches_applied=len(patches), locations=locations,
    )
