"""
extract_payload.py - Recover the flash patcher's payload and patch sites.

metroid-maniac's gba-flash-patcher installs a compiled ARM payload into a ROM
and redirects the game's save routines to it. The compiled payload is not in
the source repo, it is generated at build time, so the only trustworthy copy is
one the patcher has already written into a ROM.

Diffing the input against the output recovers all of it: the payload bytes, its
exact length, and every branch thunk the patcher installed. That is enough to
reimplement the whole thing and check the result byte for byte.

    python extract_payload.py SMA2_sram.gba SMA2_sram_flash512.gba

Writes flash_payload.bin next to the inputs. That file is metroid-maniac's MIT
licensed payload, not game code.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import struct
import sys

# A run of changed bytes shorter than this is a patch site; the long one is the
# payload itself.
THUNK_MAX = 64


def changed_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    """Contiguous [start, end) ranges where the two files differ."""
    runs: list[tuple[int, int]] = []
    size = min(len(a), len(b))
    start = None
    for i in range(size):
        if a[i] != b[i]:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, size))
    if len(b) > len(a):
        runs.append((len(a), len(b)))
    return runs


def merge_near(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Join runs separated by less than `gap` untouched bytes.

    The payload is mostly changed bytes but can contain a byte that happened to
    already match whatever the ROM held there, which splits one region into
    several. Merging across small gaps puts it back together.
    """
    if not runs:
        return []
    out = [list(runs[0])]
    for start, end in runs[1:]:
        if start - out[-1][1] < gap:
            out[-1][1] = end
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    with open(argv[1], "rb") as handle:
        original = handle.read()
    with open(argv[2], "rb") as handle:
        patched = handle.read()

    print(f"input   {os.path.basename(argv[1])}: {len(original)} bytes")
    print(f"patched {os.path.basename(argv[2])}: {len(patched)} bytes")
    if len(patched) != len(original):
        print(f"  ROM was expanded by {len(patched) - len(original)} bytes")
    print()

    runs = merge_near(changed_runs(original, patched), 32)
    if not runs:
        print("The two files are identical. Nothing was patched.")
        return 1

    payload = max(runs, key=lambda r: r[1] - r[0])
    thunks = [r for r in runs if r is not payload]

    print(f"{len(runs)} changed regions.")
    print()
    print(f"PAYLOAD at 0x{payload[0]:06X}, {payload[1] - payload[0]} bytes")
    blob = patched[payload[0]:payload[1]]
    if len(blob) >= 28:
        words = struct.unpack_from("<7I", blob, 0)
        names = ("write_sram", "write_eeprom", "read_sram",
                 "read_eeprom", "verify_sram", "verify_eeprom")
        print("  entry table:")
        for name, word in zip(names, words):
            print(f"    {name:<14} 0x{word:04X}")
        print(f"    eeprom_meta    0x{words[6]:04X}"
              f"{'  (populated by the patcher)' if words[6] else ''}")

    print()
    print("PATCH SITES")
    for start, end in thunks:
        size = end - start
        before = " ".join(f"{x:02X}" for x in original[start:start + 8])
        after = " ".join(f"{x:02X}" for x in patched[start:start + 8])
        target = ""
        if size >= 8:
            addr = struct.unpack_from("<I", patched, start + 4)[0]
            target = f"  -> 0x{addr:08X}"
        print(f"  0x{start:06X}  {size:>2} bytes{target}")
        print(f"      was  {before}")
        print(f"      now  {after}")

    out = os.path.join(os.path.dirname(os.path.abspath(argv[2])),
                       "flash_payload.bin")
    with open(out, "wb") as handle:
        handle.write(blob)
    print()
    print(f"Wrote {out} ({len(blob)} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
