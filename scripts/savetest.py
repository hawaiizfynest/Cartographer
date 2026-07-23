"""
savetest.py - Build and check a save memory test pattern.

Proves whether a cart's save memory is as large as its chip id claims. A chip
can report 128 KB while the board only wires 64 KB, in which case bank 1 lands
back on bank 0 and the same storage shows up twice. A game that expects two real
banks writes its second half over its first and reads back nonsense, which is
what a "save data corrupt" screen looks like from the outside.

The pattern is built so that mirroring cannot hide. Every 16-byte record carries
its own absolute offset, a sentinel, and a bank marker, followed by filler that
never repeats. If a record turns up somewhere other than where it was written,
the offset it carries says so.

Build a pattern:
    python scripts/savetest.py make savetest_128k.bin

Check a dump read back off the cart:
    python scripts/savetest.py check savetest_128k.bin readback.bin

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import sys

RECORD = 16          # bytes per self-describing record
SENTINEL = 0xA5      # marks a record this tool wrote
DEFAULT_SIZE = 131072  # 128 KB, a Flash 1M part


# -- pattern ---------------------------------------------------------------- #

def _mix(state: int) -> int:
    """Small deterministic step, so the same pattern comes out every run on
    every machine without depending on a random module's internals."""
    return (state * 1103515245 + 12345) & 0xFFFFFFFF


def build_pattern(size: int = DEFAULT_SIZE) -> bytes:
    """Build a pattern where every record knows its own address."""
    if size % RECORD:
        raise ValueError(f"Size must be a multiple of {RECORD} bytes.")
    out = bytearray(size)
    for off in range(0, size, RECORD):
        rec = bytearray(RECORD)
        rec[0] = (off >> 24) & 0xFF
        rec[1] = (off >> 16) & 0xFF
        rec[2] = (off >> 8) & 0xFF
        rec[3] = off & 0xFF
        rec[4] = SENTINEL
        rec[5] = (0xB0 + off // 65536) & 0xFF   # B0 = bank 0, B1 = bank 1
        state = off ^ 0x5A5A5A5A
        for i in range(6, RECORD):
            state = _mix(state)
            rec[i] = (state >> 16) & 0xFF
        out[off:off + RECORD] = rec
    return bytes(out)


def embedded_offset(data: bytes, at: int) -> int | None:
    """Read the offset a record claims for itself, or None if it isn't ours."""
    base = at - (at % RECORD)
    if base + RECORD > len(data) or data[base + 4] != SENTINEL:
        return None
    return int.from_bytes(data[base:base + 4], "big")


# -- checking --------------------------------------------------------------- #

def smallest_period(data: bytes) -> int | None:
    """Smallest power-of-two period the data repeats on, or None if it doesn't.
    A 128 KB dump that repeats every 64 KB is one bank showing up twice."""
    size = len(data)
    period = 1024
    while period < size:
        if size % period == 0:
            head = data[:period]
            if all(data[i:i + period] == head for i in range(0, size, period)):
                return period
        period *= 2
    return None


def blank_run_at_end(data: bytes) -> int:
    """How many trailing bytes are 0xFF. An erased or absent region reads 0xFF,
    so a whole bank of it means the storage isn't there."""
    count = 0
    for byte in reversed(data):
        if byte != 0xFF:
            break
        count += 1
    return count


def _consistent_fold(dump: bytes, first: int, size: int) -> int | None:
    """Return the fold distance if records past `first` all claim an offset the
    same distance below where they sit, or None.

    One record disagreeing is a bit error, not a wrap. A wrap moves every record
    above the fold point by the same amount, so it only counts as a wrap when a
    run of records agrees on it. Without this, a single flipped byte in an
    offset field looks like a folded address space."""
    deltas = set()
    sampled = 0
    for base in range(first - (first % RECORD), size, RECORD):
        claimed = embedded_offset(dump, base)
        if claimed is None:
            return None            # not our record at all, so not a clean fold
        deltas.add(base - claimed)
        sampled += 1
        if len(deltas) > 1:
            return None            # records disagree, so nothing systematic
        if sampled >= 64:
            break
    if sampled < 8 or not deltas:
        return None
    fold = deltas.pop()
    return fold if fold > 0 else None


def check(pattern: bytes, dump: bytes) -> tuple[bool, list[str]]:
    """Compare a dump against the pattern. Returns (ok, report lines)."""
    lines: list[str] = []
    size = len(pattern)

    if len(dump) != size:
        lines.append(f"Size mismatch: pattern is {size} bytes, dump is "
                     f"{len(dump)} bytes.")
        if len(dump) < size:
            lines.append("A short dump usually means the save type in use is "
                         "smaller than the chip. Check the override is set to "
                         "flash_1m.")
        return False, lines

    if dump == pattern:
        lines.append(f"PASS. All {size} bytes came back exactly as written.")
        lines.append("Both banks hold their own data, so the full capacity is "
                     "real and the bank switch works.")
        return True, lines

    differing = sum(1 for a, b in zip(pattern, dump) if a != b)
    first = next(i for i, (a, b) in enumerate(zip(pattern, dump)) if a != b)
    lines.append(f"FAIL. {differing} of {size} bytes differ. First difference "
                 f"at offset 0x{first:05X}.")

    if all(byte == 0xFF for byte in dump):
        lines.append("")
        lines.append("NOTHING WAS WRITTEN. The whole dump reads 0xFF.")
        lines.append("The write never reached the chip. Check the cart is "
                     "seated and the save type override is still set before "
                     "reading anything into this.")
        return False, lines

    half = size // 2
    period = smallest_period(dump)
    if period is not None:
        lines.append("")
        if period == half:
            lines.append(f"MIRRORED. The second {half // 1024} KB is byte for "
                         f"byte identical to the first.")
            lines.append("Bank 1 is landing on bank 0, so there is one bank of "
                         "storage showing up twice. The chip id claims more "
                         "than the board wires up.")
        else:
            lines.append(f"REPEATING every {period // 1024} KB. The usable "
                         f"storage is that size, and everything above it is "
                         f"the same memory seen again.")
        lines.append("A game that expects two banks will overwrite its own "
                     "save and read back nonsense.")
        return False, lines

    trailing = blank_run_at_end(dump)
    if trailing >= half:
        lines.append("")
        lines.append(f"SECOND BANK IS BLANK. The last {trailing // 1024} KB "
                     f"reads all 0xFF.")
        lines.append("Nothing is answering up there. Either the write never "
                     "reached bank 1 or the storage isn't present.")
        return False, lines

    fold = _consistent_fold(dump, first, size)
    if fold is not None:
        lines.append("")
        lines.append(f"WRAPPED at {fold // 1024} KB. Records past 0x{first:05X} "
                     f"carry offsets {fold // 1024} KB below where they were "
                     f"found, every one of them.")
        lines.append("Addresses above that point fold back down, which is the "
                     "signature of a smaller device than the id claims.")
        return False, lines

    lines.append("")
    lines.append("Not a clean mirror, a blank bank or a wrap. Scattered "
                 "differences point at a flaky chip, a marginal connection, or "
                 "a write that was interrupted. Re-seat the cart and run it "
                 "again before drawing a conclusion.")
    ranges: list[str] = []
    start = None
    for i in range(size):
        bad = pattern[i] != dump[i]
        if bad and start is None:
            start = i
        elif not bad and start is not None:
            ranges.append(f"0x{start:05X}-0x{i - 1:05X}")
            start = None
            if len(ranges) >= 8:
                break
    if start is not None and len(ranges) < 8:
        ranges.append(f"0x{start:05X}-0x{size - 1:05X}")
    if ranges:
        lines.append("First differing ranges: " + ", ".join(ranges))
    return False, lines


# -- entry point ------------------------------------------------------------ #

def _usage() -> int:
    print(__doc__.strip())
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return _usage()
    cmd = argv[1]

    if cmd == "make":
        if len(argv) < 3:
            return _usage()
        size = int(argv[3], 0) if len(argv) > 3 else DEFAULT_SIZE
        data = build_pattern(size)
        with open(argv[2], "wb") as handle:
            handle.write(data)
        print(f"Wrote {argv[2]}, {len(data)} bytes "
              f"({len(data) // 1024} KB, {len(data) // RECORD} records).")
        return 0

    if cmd == "check":
        if len(argv) < 4:
            return _usage()
        with open(argv[2], "rb") as handle:
            pattern = handle.read()
        with open(argv[3], "rb") as handle:
            dump = handle.read()
        ok, lines = check(pattern, dump)
        for line in lines:
            print(line)
        return 0 if ok else 1

    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
