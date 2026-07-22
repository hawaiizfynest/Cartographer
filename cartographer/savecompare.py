"""
savecompare.py - compare and inspect cartridge save files.

Two jobs. First, look at a single save and say whether it actually holds data or
is blank, which is the difference between "the cart saved something" and "the
cart handed back an empty region". Second, compare two saves byte for byte and
describe how they differ, which is how you tell a save that survived a power
cycle from one that did not.

No device needed; this works on .sav files on disk.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A save region that is entirely one byte value holds no real data. 0xFF is
# erased flash/EEPROM, 0x00 is cleared SRAM, and either means "nothing saved".
BLANK_FILLS = (0xFF, 0x00)


@dataclass
class SaveInfo:
    """What a single save file looks like."""
    size: int = 0
    is_blank: bool = False          # entirely one repeated byte
    fill_byte: int = -1             # which byte, when blank
    distinct_bytes: int = 0         # how many different byte values appear
    nonblank_bytes: int = 0         # bytes that are not the dominant fill
    first_data_offset: int = -1     # first byte differing from the fill

    def summary(self) -> str:
        if not self.size:
            return "Empty file (0 bytes)."
        kb = self.size / 1024
        if self.is_blank:
            what = ("erased (all 0xFF)" if self.fill_byte == 0xFF
                    else f"all 0x{self.fill_byte:02X}")
            return (f"{kb:.0f} KB, and it holds no data: every byte is "
                    f"{what}. Nothing was saved, or the save area was not "
                    f"read back.")
        pct = 100.0 * self.nonblank_bytes / self.size
        return (f"{kb:.0f} KB, holds data: {self.distinct_bytes} different byte "
                f"values, {self.nonblank_bytes} bytes ({pct:.1f}%) differ from "
                f"the background, first at offset 0x{self.first_data_offset:X}.")


@dataclass
class SaveDiff:
    """How two save files compare."""
    same_size: bool = True
    size_a: int = 0
    size_b: int = 0
    identical: bool = False
    diff_count: int = 0             # bytes that differ (over the common length)
    first_diff: int = -1
    diff_regions: list = field(default_factory=list)   # (start, length) runs
    info_a: SaveInfo = None
    info_b: SaveInfo = None

    @property
    def diff_percent(self) -> float:
        common = min(self.size_a, self.size_b)
        return (100.0 * self.diff_count / common) if common else 0.0

    def summary(self) -> str:
        lines = []
        if not self.same_size:
            lines.append(f"Different sizes: {self.size_a} bytes vs "
                         f"{self.size_b} bytes. Comparing the first "
                         f"{min(self.size_a, self.size_b)} bytes.")
        if self.identical:
            lines.append("The two saves are byte for byte identical.")
        else:
            n = len(self.diff_regions)
            runs = "run" if n == 1 else "runs"
            lines.append(
                f"The saves differ: {self.diff_count} bytes "
                f"({self.diff_percent:.1f}%) are not the same, first at offset "
                f"0x{self.first_diff:X}, across {n} separate {runs}.")
        return "\n".join(lines)

    def verdict(self) -> str:
        """A plain reading of what the comparison most likely means."""
        a, b = self.info_a, self.info_b
        if a is None or b is None:
            return ""
        if self.identical and not a.is_blank:
            return ("Both saves hold the same data. The cart kept its save "
                    "across the two reads.")
        if self.identical and a.is_blank:
            return ("Both saves are blank. The cart is handing back an empty "
                    "save area, so nothing is being stored (or nothing has "
                    "been saved yet).")
        if a.is_blank and not b.is_blank:
            return ("The first save was blank and the second holds data. "
                    "Something was written between the two reads.")
        if b.is_blank and not a.is_blank:
            return ("The first save held data and the second is blank. The "
                    "save area lost its contents between the two reads. On a "
                    "battery-backed SRAM cart that points at a dead battery.")
        return ("Both saves hold data but they are not the same. Either the "
                "save changed between reads, or the save area is not holding "
                "its contents reliably.")


def inspect_save(data: bytes) -> SaveInfo:
    """Describe a single save: blank, or holding data, and how much."""
    info = SaveInfo(size=len(data))
    if not data:
        return info
    counts = {}
    for b in data:
        counts[b] = counts.get(b, 0) + 1
    info.distinct_bytes = len(counts)
    fill = max(counts, key=lambda k: counts[k])
    info.fill_byte = fill
    info.nonblank_bytes = len(data) - counts[fill]
    if info.distinct_bytes == 1:
        info.is_blank = True
        return info
    # A save that is almost entirely one value with a tiny handful of stray
    # bytes is still effectively blank; treat a very low data ratio as blank
    # only when the fill is a known blank value.
    if fill in BLANK_FILLS and info.nonblank_bytes == 0:
        info.is_blank = True
        return info
    for i, b in enumerate(data):
        if b != fill:
            info.first_data_offset = i
            break
    return info


def compare_saves(a: bytes, b: bytes, max_regions: int = 64) -> SaveDiff:
    """Compare two saves and describe the differences.

    Walks the common length, records which bytes differ and groups consecutive
    differing bytes into runs so the result reads as regions rather than a wall
    of offsets. `max_regions` caps how many runs are recorded.
    """
    diff = SaveDiff(size_a=len(a), size_b=len(b),
                    same_size=(len(a) == len(b)),
                    info_a=inspect_save(a), info_b=inspect_save(b))
    common = min(len(a), len(b))
    run_start = -1
    for i in range(common):
        if a[i] != b[i]:
            diff.diff_count += 1
            if diff.first_diff < 0:
                diff.first_diff = i
            if run_start < 0:
                run_start = i
        else:
            if run_start >= 0:
                if len(diff.diff_regions) < max_regions:
                    diff.diff_regions.append((run_start, i - run_start))
                run_start = -1
    if run_start >= 0 and len(diff.diff_regions) < max_regions:
        diff.diff_regions.append((run_start, common - run_start))
    diff.identical = (diff.diff_count == 0 and diff.same_size)
    return diff


def find_ascii_strings(data: bytes, min_len: int = 4) -> list:
    """Find runs of printable ASCII in a save, as (offset, text).

    Save files are game-specific binary, so nothing here knows what any byte
    means. Readable text is still the most useful landmark there is: player
    names, file labels, game identifiers and menu strings usually sit in plain
    ASCII, and they tell you which parts of the file belong to what.
    """
    out = []
    start = -1
    run = []
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if start < 0:
                start = i
            run.append(chr(b))
        else:
            if start >= 0 and len(run) >= min_len:
                out.append((start, "".join(run)))
            start = -1
            run = []
    if start >= 0 and len(run) >= min_len:
        out.append((start, "".join(run)))
    return out


def region_map(data: bytes, block: int = 512) -> list:
    """Split a save into blocks and label each as blank or holding data.

    Returns a list of (offset, length, kind, fill) where kind is "blank" or
    "data". Consecutive blocks of the same kind are merged, so the result reads
    as a handful of regions rather than hundreds of blocks. This shows the shape
    of a save: where the used areas are and where the untouched space sits.
    """
    if not data:
        return []
    regions = []
    for off in range(0, len(data), block):
        chunk = data[off:off + block]
        first = chunk[0]
        blank = all(b == first for b in chunk) and first in BLANK_FILLS
        kind = "blank" if blank else "data"
        fill = first if blank else -1
        if regions and regions[-1][2] == kind and regions[-1][3] == fill:
            o, ln, k, f = regions[-1]
            regions[-1] = (o, ln + len(chunk), k, f)
        else:
            regions.append((off, len(chunk), kind, fill))
    return [tuple(r) for r in regions]


def structure_report(data: bytes, max_strings: int = 40) -> str:
    """A readable summary of what a save file contains structurally."""
    if not data:
        return "Empty file."
    lines = [inspect_save(data).summary(), ""]

    regions = region_map(data)
    lines.append(f"Layout ({len(regions)} regions):")
    for off, length, kind, fill in regions[:24]:
        if kind == "blank":
            lines.append(f"  0x{off:06X}  {length:>6} bytes  blank "
                         f"(0x{fill:02X})")
        else:
            lines.append(f"  0x{off:06X}  {length:>6} bytes  data")
    if len(regions) > 24:
        lines.append(f"  \u2026and {len(regions) - 24} more")

    strings = find_ascii_strings(data)
    lines.append("")
    if strings:
        lines.append(f"Readable text found ({len(strings)} runs, showing up to "
                     f"{max_strings}):")
        for off, text in strings[:max_strings]:
            shown = text if len(text) <= 60 else text[:57] + "..."
            lines.append(f"  0x{off:06X}  {shown}")
        if len(strings) > max_strings:
            lines.append(f"  \u2026and {len(strings) - max_strings} more")
    else:
        lines.append("No readable text found. The save is entirely binary, "
                     "which is normal for many games.")

    lines.append("")
    lines.append("What the bytes mean is specific to each game. Nothing here "
                 "can label them for you; the layout and any readable text are "
                 "landmarks for finding your way around.")
    return "\n".join(lines)


def hex_preview(data: bytes, offset: int, length: int = 32) -> str:
    """A short hex dump around an offset, for eyeballing what changed."""
    if not data or offset < 0:
        return ""
    start = max(0, (offset // 16) * 16)
    end = min(len(data), start + length)
    lines = []
    for row in range(start, end, 16):
        chunk = data[row:row + 16]
        hexpart = " ".join(f"{c:02X}" for c in chunk)
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        lines.append(f"{row:08X}  {hexpart:<47}  {text}")
    return "\n".join(lines)
