"""
savelayout.py - Work out how a GBA save dump is actually laid out.

metroid-maniac's flash payload does not store an EEPROM save as contiguous
bytes. It spreads them, so that erasing a 4 KB flash sector only disturbs a few
save bytes at once. Save byte n lives at offset n << loadfactor_log2, and the
bytes in between are left erased at 0xFF.

The load factor is chosen at call time from the EEPROM metadata the game leaves
in RAM: 7 for EEPROM 4K, 3 otherwise. Both the read path and the write path
recompute it independently, so if that metadata is not identical on every call
the game writes at one stride and reads at another. A dump showing data at two
different strides is that fault caught in the act.

    python savelayout.py readback.bin

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import sys

BLANK = 0xFF
# stride -> what a save laid out on it means
STRIDES = {
    128: "EEPROM 4K through the flash payload (load factor 7)",
    8: "EEPROM 64K through the flash payload, or 4K written at the wrong "
       "load factor (load factor 3)",
    2: "SRAM through the flash payload (load factor 1)",
    1: "contiguous, so nothing spread it at all",
}


def natural_stride(offsets: list[int]) -> int:
    """The largest spacing that explains every occupied offset.

    Comparing grids one at a time does not work: every multiple of 128 is also
    a multiple of 8, so a healthy spread lights up all the finer grids too and
    looks like several layouts at once. The greatest common divisor of the
    occupied offsets gives the real spacing in one step.
    """
    from math import gcd
    stride = 0
    for off in offsets:
        stride = gcd(stride, off)
        if stride == 1:
            break
    return stride or 1


def analyse(data: bytes) -> list[str]:
    out: list[str] = []
    size = len(data)
    offsets = [i for i, b in enumerate(data) if b != BLANK]
    out.append(f"{size} bytes ({size // 1024} KB), {len(offsets)} of them "
               f"not 0xFF.")

    if not offsets:
        out.append("")
        out.append("NOTHING WAS WRITTEN. The whole area is erased.")
        out.append("The game never got a byte through to the chip, so the "
                   "payload is not running or its writes are failing "
                   "silently.")
        return out

    stride = natural_stride(offsets)
    span_lo, span_hi = offsets[0], offsets[-1]
    on_128 = [o for o in offsets if o % 128 == 0]
    off_128 = [o for o in offsets if o % 128]

    out.append(f"Occupied from 0x{span_lo:05X} to 0x{span_hi:05X}, spacing "
               f"{stride}.")
    if span_hi >= 0x10000:
        above = sum(1 for b in data[0x10000:] if b != BLANK)
        out.append(f"{above} of them sit above 64 KB, which the payload never "
                   f"addresses.")
    out.append("")

    # A mix of spread and packed writes is the failure worth naming, but it
    # only counts when there is a real population on both, not a handful of
    # coincidental hits.
    if len(on_128) > 8 and len(off_128) > 8 and span_hi > 0x2000:
        out.append("TWO LAYOUTS AT ONCE.")
        out.append(f"{len(on_128)} bytes sit on the 128-byte grid and "
                   f"{len(off_128)} do not, spread across more than 8 KB.")
        out.append("That is the load factor differing between calls. Some of "
                   "the save was written spread for EEPROM 4K and some was "
                   "written packed, so whichever stride the game reads with, "
                   "part of what it finds is nonsense. A save in this state "
                   "reports corrupt however many times it is rewritten.")
        return out

    meaning = STRIDES.get(stride)
    if meaning is None:
        out.append(f"UNEXPECTED SPACING of {stride}. The payload only uses "
                   f"1, 2, 8 and 128.")
        return out

    out.append(f"ONE LAYOUT, spacing {stride}. {meaning}.")
    expected = size // stride if stride > 1 else len(offsets)
    if stride == 128:
        if span_hi < 0x8000:
            out.append(f"Only the low {span_hi // 1024 + 1} KB is in use "
                       f"though. A full EEPROM 4K save reaches 0xFF80, so this "
                       f"one is part written.")
        else:
            out.append(f"{len(offsets)} bytes across the full range, which is "
                       f"what a spread EEPROM 4K save looks like. Writes and "
                       f"reads agree on the stride.")
    elif stride == 8:
        out.append("A 4K save written at this spacing is the wrong load "
                   "factor for its size: it should be 128. Written packed and "
                   "read spread, or the other way round, comes out corrupt.")
    else:
        out.append(f"{len(offsets)} bytes occupied.")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    with open(argv[1], "rb") as handle:
        data = handle.read()
    for line in analyse(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
