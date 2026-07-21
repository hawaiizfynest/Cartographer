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

    @property
    def chip_label(self) -> str:
        if self.chip:
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
            if self.chip.note:
                base += f" Note: {self.chip.note}."
            return base
        return (f"Flashable cart, but this chip is not in the database: {who}, "
                f"{ids}. Command set {self.variant}, write method "
                f"'{self.write_method}'. Cross-check the marking printed on the "
                f"chip against the supported list before writing.")


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
    # CFI-confirmed results come first: a chip that answered a CFI query is
    # identified far more reliably than one read by a bare unlock sequence. CFI
    # keys look like "cfi-555", "cfi-AAAA", etc.
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
                chip=chip, raw=probe)
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
