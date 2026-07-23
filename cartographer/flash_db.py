"""
flash_db.py - interpret GBA flash-cart ID probe results.

The GBxCart flash-ID probe enters each candidate command set's "read ID" mode
and reads back the first bytes. A mask-ROM (retail) cart ignores the command
writes and keeps returning its ROM, so its probe looks identical to baseline.
A flashable cart returns a manufacturer/device id pair, and which command set it
answered tells us the write method needed for programming.

Honesty about the tables below:

  * MANUFACTURERS holds standard JEDEC manufacturer ids. These are well known.
  * CHIP_IDS maps (manufacturer, device) pairs to a chip name. It is deliberately
    SMALL - only entries that are well established. An unknown pair is reported
    with its raw bytes rather than guessed at.
  * SUPPORTED_CHIPS is a name-only reference list of flash chips reported to work
    with GBxCart-family flashers (published by insideGadgets). Use it to
    cross-reference the marking printed on the chip if the id lookup misses.
  * KNOWN_BAD_MARKINGS are chips insideGadgets reports as not flashable at all.

Wrong chip data leads to bricked carts, so this module prefers "unknown" to a
plausible-sounding guess.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass

# Write methods (names mirror insideGadgets' flasher for continuity)
WRITE_AAA = "aaa"                    # standard AMD-style unlock (0xAAA/0x555 etc)
WRITE_555 = "555"                    # unlock at 0x555/0x2AA
WRITE_INTEL = "intel"                # Intel command set (0x90 id, reset 0xFF)
WRITE_UNKNOWN = "unknown"

# A chip that answers a lone 0x90 to address 0 ("bare-90") is Intel-type. The
# AMD-style unlock sequences all use the standard AMD command set; the address
# base that answers just tells us where the chip is mapped, not a different write
# protocol, so they share the AMD write method.
_VARIANT_TO_METHOD = {
    "555/AA": WRITE_555,
    "5555/AA": WRITE_555,
    "AAA/AA": WRITE_AAA,
    "AAAA/AA": WRITE_AAA,
    "4AAA/AA": WRITE_AAA,
    "7AAA/AA": WRITE_AAA,
    "bare-90": WRITE_INTEL,
}

# Probe order: AMD-style sets first, Intel last (matches insideGadgets).
_PROBE_ORDER = ("555/AA", "5555/AA", "AAA/AA", "AAAA/AA",
                "4AAA/AA", "7AAA/AA", "bare-90")


# Standard JEDEC manufacturer ids.
MANUFACTURERS = {
    0x01: "AMD / Spansion",
    0x04: "Fujitsu",
    0x1F: "Atmel",
    0x20: "ST / Numonyx / Micron",
    0x89: "Intel",
    0x98: "Toshiba",
    0xB0: "Sharp",
    0xBF: "SST",
    0xC2: "Macronix",
    0xDA: "Winbond",
    0xEC: "Samsung",
}


@dataclass
class ChipInfo:
    name: str
    capacity_mb: int = 0      # ROM capacity in MByte, 0 if unknown
    note: str = ""


# (manufacturer_id, device_id) -> chip. Kept small and confident.
# 0x227E is the standard device id for 128 Mbit (16 MByte) CFI NOR flash, used
# by S29GL128 / M29W128 / MX29GL128 - the family found on most GBA repro carts.
CHIP_IDS = {
    (0x01, 0x227E): ChipInfo("Spansion S29GL128/256/512 family", 16,
                             "device id 0x227E is shared across this family; "
                             "true size (16-64 MB) comes from CFI. Common on "
                             "EpicJoy/Gugxiom-style 5V repro carts"),
    (0x20, 0x227E): ChipInfo("ST/Numonyx M29W128 (e.g. M29W128GH)", 16,
                             "common on EpicJoy-style RTC/solar repro carts"),
    (0xC2, 0x227E): ChipInfo("Macronix MX29GL128", 16),
    (0xC2, 0x22A8): ChipInfo("Macronix MX29LV320 (bottom boot)", 4),
    (0xC2, 0x22A7): ChipInfo("Macronix MX29LV320 (top boot)", 4),
}


# Name-only reference: chips reported as supported by GBxCart-family flashers.
# Source: insideGadgets' GBxCart RW product page.
SUPPORTED_CHIPS = [
    ChipInfo("MX29LV320", 4),
    ChipInfo("MSP55LV128", 16),
    ChipInfo("MSP55LV128M", 16),
    ChipInfo("29GL128EHMC", 16),
    ChipInfo("29LV128DTMC", 16),
    ChipInfo("MX29GL128ELT", 16),
    ChipInfo("M29W128", 16),
    ChipInfo("S29GL128", 16),
    ChipInfo("M36L0R706", 16),
    ChipInfo("GE28F128W30", 16),
    ChipInfo("256L30B", 32),
    ChipInfo("256M29EWH", 32),
    ChipInfo("M29W256", 32),
    ChipInfo("4455LLZBQ0", 32),
    ChipInfo("4000L0YBQ0", 32),
    ChipInfo("Flash2Advance 256M", 32),
    ChipInfo("Nintendo AGB 128M Flash S (E201850)", 16),
]

# insideGadgets: chips marked with these strings do not work at all.
KNOWN_BAD_MARKINGS = ("6600", "4050M")


# The small flash chips used for GBA saves. The device answers a read-ID with
# two bytes, MANUFACTURER first and device second, so the 16-bit key below is
# (device << 8) | manufacturer, which is the form these parts are written up in
# everywhere. Games check this id before writing a save and drive the chip with
# the command sequence that chip expects, so a chip whose id a game does not
# recognise is one the game will refuse to write to. Capacity is in bytes.
SAVE_FLASH_IDS = {
    0x1B32: ("Panasonic MN63F805MNP", 65536),
    0x3D1F: ("Atmel AT29LV512", 65536),
    0xD4BF: ("SST 39VF512", 65536),
    0x1CC2: ("Macronix MX29L512", 65536),
    0x09C2: ("Macronix MX29L010", 131072),
    0x1362: ("Sanyo LE26FV10N1TS", 131072),
}

# Manufacturer bytes on their own. A known maker with an unfamiliar device byte
# still tells you a real chip is answering, which is a different situation from
# silence, and it narrows down what the part is likely to be.
SAVE_FLASH_MAKERS = {
    0x1F: "Atmel",
    0x32: "Panasonic",
    0x62: "Sanyo",
    0xBF: "SST",
    0xC2: "Macronix",
}


def lookup_save_flash(chip_id: int):
    """Return (name, capacity_bytes) for a save flash id, or None."""
    return SAVE_FLASH_IDS.get(chip_id)


def interpret_save_flash_id(data: bytes) -> str:
    """Describe what a save-flash read-ID response means.

    Games identify the save chip before writing to it. A recognised id means a
    game knows how to drive the chip; an unrecognised one means it very likely
    will not, which shows up in-game as a save that will not write or reads as
    corrupt, even though a flasher can read and write the same chip fine.
    """
    if not data:
        return ("The device did not return a save flash id. Nothing came back "
                "at all, so either this firmware does not implement the read-ID "
                "command or the read was cut short before the chip answered.")
    hexed = " ".join(f"{b:02X}" for b in data[:8])
    if len(data) < 2:
        maker = SAVE_FLASH_MAKERS.get(data[0])
        who = f" That byte is {maker}." if maker else ""
        return (f"Only one byte came back ({hexed}) where a chip id is two.{who} "
                f"Run the check again. If it stays at one byte, something is "
                f"answering but not completing the id.")
    if all(b == 0x00 for b in data[:2]) or all(b == 0xFF for b in data[:2]):
        return (f"Save flash id read back as {hexed}, which is not a real chip "
                f"id. Either there is no flash save chip responding, or it does "
                f"not answer the read-ID command.")
    chip_id = data[0] | (data[1] << 8)
    known = lookup_save_flash(chip_id)
    if known:
        name, cap = known
        return (f"Save flash chip: {name} (id 0x{chip_id:04X}, "
                f"{cap // 1024} KB). This is a chip games know how to write.")
    swapped = data[1] | (data[0] << 8)
    known_swapped = lookup_save_flash(swapped)
    if known_swapped:
        name, cap = known_swapped
        return (f"Save flash chip: {name} (id 0x{swapped:04X}, {cap // 1024} "
                f"KB), reported with its two bytes the other way round (raw "
                f"{hexed}). This is a chip games know how to write.")
    maker = SAVE_FLASH_MAKERS.get(data[0])
    if maker:
        return (f"Save flash id 0x{chip_id:04X} (raw {hexed}). The maker byte "
                f"is {maker}, so a real chip is answering, but device byte "
                f"0x{data[1]:02X} is not one of the parts games check for. A "
                f"game that checks the id before saving will refuse to write, "
                f"which looks like a save that will not stick or reads as "
                f"corrupt.")
    return (f"Save flash id 0x{chip_id:04X} (raw {hexed}) is not one of the "
            f"chips games recognise. A flasher can still read and write it, but "
            f"a game that checks the id before saving will refuse to write, "
            f"which looks like a save that will not stick or reads as corrupt.")


@dataclass
class FlashIdResult:
    is_flashable: bool
    variant: str                 # probe variant that responded ("" if none)
    write_method: str
    manufacturer_id: int
    device_id: int
    manufacturer: str
    chip: object = None          # ChipInfo | None
    raw: dict = None
    cfi: object = None           # CfiData | None (parsed from the CFI buffer)

    @property
    def chip_label(self) -> str:
        # Prefer the true CFI size over the nominal database capacity, since
        # 0x227E-family chips share an id across several sizes.
        if self.chip:
            if self.cfi and self.cfi.device_size_bytes:
                return f"{self.chip.name}, {self.cfi.device_size_mb} MB"
            cap = f", {self.chip.capacity_mb} MB" if self.chip.capacity_mb else ""
            return f"{self.chip.name}{cap}"
        return "Unknown chip"

    @property
    def is_known_chip(self) -> bool:
        return self.chip is not None

    def summary(self) -> str:
        if not self.is_flashable:
            return ("No flash chip responded - this looks like a mask-ROM "
                    "(retail) cartridge, or an unsupported flash chip.")
        ids = f"id 0x{self.manufacturer_id:02X}/0x{self.device_id:04X}"
        who = self.manufacturer if self.manufacturer != "Unknown" else "unknown maker"
        if self.chip:
            base = (f"Flashable cart: {self.chip_label} ({who}, {ids}). "
                    f"Command set {self.variant}, write method "
                    f"'{self.write_method}'.")
            if self.cfi and self.cfi.summary():
                base += f" CFI: {self.cfi.summary()}."
            elif self.chip.note:
                base += f" Note: {self.chip.note}."
            return base
        base = (f"Flashable cart, but this chip is not in the database: {who}, "
                f"{ids}. Command set {self.variant}, write method "
                f"'{self.write_method}'.")
        if self.cfi and self.cfi.summary():
            base += f" CFI: {self.cfi.summary()}."
        base += (" Cross-check the marking printed on the chip against the "
                 "supported list before writing.")
        return base


@dataclass
class CfiData:
    """Parsed Common Flash Interface data - the chip's own description of its
    size, sector layout, and erase/write capabilities. Read straight off the
    chip; this is the ground truth a correct erase needs."""
    device_size_bytes: int = 0          # true capacity in bytes
    sector_erase: bool = False          # supports per-sector erase
    chip_erase: bool = False            # supports whole-chip erase
    single_write: bool = False          # supports single-word program
    buffer_write: bool = False          # supports buffered (fast) program
    buffer_size: int = 0                # buffered write size in bytes (0 = none)
    # Each region is (sector_size_bytes, sector_count). A chip has 1-4 regions.
    erase_regions: tuple = ()
    tb_boot_raw: int = 0                # top/bottom boot flag (0x03 = reversed)

    @property
    def device_size_mb(self) -> int:
        return self.device_size_bytes // (1024 * 1024)

    def summary(self) -> str:
        if not self.device_size_bytes:
            return ""
        parts = [f"{self.device_size_mb} MB"]
        caps = []
        if self.sector_erase:
            caps.append("sector-erase")
        if self.chip_erase:
            caps.append("chip-erase")
        if self.buffer_write:
            caps.append(f"buffered-write({self.buffer_size}B)")
        elif self.single_write:
            caps.append("single-write")
        if caps:
            parts.append(", ".join(caps))
        if self.erase_regions:
            regions = "; ".join(f"{n}x{sz // 1024}KB" for sz, n in
                                self.erase_regions)
            parts.append(f"sectors: {regions}")
        return " | ".join(parts)


def parse_cfi(buffer: bytes) -> "CfiData | None":
    """Parse a 0x400 CFI buffer into a CfiData, or None if it isn't valid CFI.

    Mirrors the reference flasher's parser (offsets are on the 16-bit bus, so the
    CFI bytes land at every other byte: 0x20, 0x22, 0x24 for the "QRY" magic,
    etc). Only reads the buffer - never touches the device.
    """
    if len(buffer) < 0x62:
        return None
    # "QRY" signature at 0x20/0x22/0x24.
    if not (buffer[0x20] == ord("Q") and buffer[0x22] == ord("R")
            and buffer[0x24] == ord("Y")):
        return None
    try:
        # Voltage range must be present, else the CFI is bogus.
        if buffer[0x36] == 0xFF and buffer[0x48] == 0xFF:
            return None

        single_write = 0 < buffer[0x3E] < 0xFF
        buffer_write = 0 < buffer[0x40] < 0xFF
        sector_erase = 0 < buffer[0x42] < 0xFF
        chip_erase = 0 < buffer[0x44] < 0xFF

        device_size = int(2 ** buffer[0x4E])

        buf_size = (buffer[0x56] << 8) | buffer[0x54]
        if buf_size > 1:
            buffer_write = True
            buf_size = int(2 ** buf_size)
        else:
            buf_size = 0
            buffer_write = False

        n_regions = buffer[0x58]
        regions = []
        for i in range(0, min(4, n_regions)):
            count = ((buffer[0x5C + i * 8] << 8) | buffer[0x5A + i * 8]) + 1
            size = ((buffer[0x60 + i * 8] << 8) | buffer[0x5E + i * 8]) * 256
            regions.append((size, count))

        tb_boot_raw = 0
        pri = ((buffer[0x2A] | (buffer[0x2C] << 8)) * 2)
        if (pri + 0x3C) < 0x400:
            if (buffer[pri] == ord("P") and buffer[pri + 2] == ord("R")
                    and buffer[pri + 4] == ord("I")):
                v = buffer[pri + 0x1E]
                if v not in (0, 0xFF):
                    tb_boot_raw = v

        return CfiData(
            device_size_bytes=device_size,
            sector_erase=sector_erase, chip_erase=chip_erase,
            single_write=single_write, buffer_write=buffer_write,
            buffer_size=buf_size, erase_regions=tuple(regions),
            tb_boot_raw=tb_boot_raw)
    except (IndexError, ValueError):
        return None


def _decode_ids(data: bytes) -> tuple[int, int, int, int]:
    """Return (mfr_byte, dev_byte, mfr_word, dev_word) from a read-ID response.

    A 16-bit bus returns each id as a little-endian word, so byte and word forms
    can both be meaningful depending on the chip. Compute both and try each.
    """
    b0 = data[0] if len(data) > 0 else 0
    b1 = data[1] if len(data) > 1 else 0
    b2 = data[2] if len(data) > 2 else 0
    b3 = data[3] if len(data) > 3 else 0
    mfr_word = b0 | (b1 << 8)
    dev_word = b2 | (b3 << 8)
    return b0, b1, mfr_word, dev_word


def lookup_chip(mfr: int, dev: int):
    return CHIP_IDS.get((mfr, dev))


def interpret(probe: dict) -> FlashIdResult:
    """Turn a gba_flash_id_probe() dict into a FlashIdResult."""
    baseline = probe.get("baseline", b"")
    # Parse the CFI buffer once, if the probe captured one.
    cfi = parse_cfi(probe.get("_cfi_buffer", b"")) if probe.get("_cfi_buffer") else None
    # CFI-confirmed results come first: a chip that answered a CFI query is
    # identified far more reliably than one read by a bare unlock sequence. CFI
    # keys look like "cfi-555", "cfi-AAAA", etc. Skip internal "_"-prefixed keys.
    cfi_keys = sorted(k for k in probe if k.startswith("cfi-"))
    for variant in (*cfi_keys, *_PROBE_ORDER):
        data = probe.get(variant, b"")
        if data and data[:4] != baseline[:4]:
            mfr_b, dev_b, mfr_w, dev_w = _decode_ids(data)
            chip_w = lookup_chip(mfr_w, dev_w)
            chip_b = lookup_chip(mfr_b, dev_b)
            if chip_w:
                chip, mfr_id, dev_id = chip_w, mfr_w, dev_w
            elif chip_b:
                chip, mfr_id, dev_id = chip_b, mfr_b, dev_b
            else:
                chip, mfr_id, dev_id = None, mfr_b, (dev_w or dev_b)
            method = (WRITE_AAA if variant.startswith("cfi-")
                      else _VARIANT_TO_METHOD.get(variant, WRITE_UNKNOWN))
            return FlashIdResult(
                is_flashable=True, variant=variant,
                write_method=method,
                manufacturer_id=mfr_id, device_id=dev_id,
                manufacturer=MANUFACTURERS.get(mfr_id & 0xFF, "Unknown"),
                chip=chip, raw=probe, cfi=cfi)
    return FlashIdResult(
        is_flashable=False, variant="", write_method=WRITE_UNKNOWN,
        manufacturer_id=0, device_id=0, manufacturer="Unknown", chip=None,
        raw=probe)


def is_known_bad_marking(marking: str) -> bool:
    """True if a chip marking read off the PCB is on the do-not-flash list."""
    m = (marking or "").upper()
    return any(bad in m for bad in KNOWN_BAD_MARKINGS)


def supported_chip_names() -> list:
    return [c.name for c in SUPPORTED_CHIPS]
