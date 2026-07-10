"""
gb_header.py - Game Boy / Game Boy Color cartridge header parsing and the
size tables used to pick page counts for read/write operations.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass

# MBC (memory bank controller) type identifiers.
MBC_AUTO = 0x00
MBC1 = 0x01
MBC2 = 0x02
MBC3 = 0x03
MBC_ROMONLY = 0x04
MBC5 = 0x05
MBC_RUMBLE = 0x06

# Nintendo boot logo (0x104-0x133). A correct logo is how the GB validates
# a cart; we use it as a sanity check on dumps.
NINTENDO_LOGO = bytes((
    0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B, 0x03, 0x73, 0x00, 0x83,
    0x00, 0x0C, 0x00, 0x0D, 0x00, 0x08, 0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E,
    0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99, 0xBB, 0xBB, 0x67, 0x63,
    0x6E, 0x0E, 0xEC, 0xCC, 0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E,
))

# cart-type byte (0x147) -> human label
CART_TYPES = {
    0x00: "ROM ONLY", 0x01: "MBC1", 0x02: "MBC1+RAM",
    0x03: "MBC1+RAM+BATTERY", 0x05: "MBC2", 0x06: "MBC2+BATTERY",
    0x08: "ROM+RAM", 0x09: "ROM+RAM+BATTERY", 0x0B: "MMM01",
    0x0C: "MMM01+RAM", 0x0D: "MMM01+RAM+BATTERY", 0x0F: "MBC3+TIMER+BATTERY",
    0x10: "MBC3+TIMER+RAM+BATTERY", 0x11: "MBC3", 0x12: "MBC3+RAM",
    0x13: "MBC3+RAM+BATTERY", 0x19: "MBC5", 0x1A: "MBC5+RAM",
    0x1B: "MBC5+RAM+BATTERY", 0x1C: "MBC5+RUMBLE",
    0x1D: "MBC5+RUMBLE+RAM", 0x1E: "MBC5+RUMBLE+RAM+BATTERY",
    0x1F: "Pocket Camera", 0xFC: "Pocket Camera",
    0xFD: "Bandai TAMA5", 0xFE: "HuC3", 0xFF: "HuC1+RAM+BATTERY",
}

# ROM size byte (0x148) -> (label, number of 16 KB pages)
ROM_SIZES = {
    0x00: ("32 KB", 2), 0x01: ("64 KB", 4), 0x02: ("128 KB", 8),
    0x03: ("256 KB", 16), 0x04: ("512 KB", 32), 0x05: ("1 MB", 64),
    0x06: ("2 MB", 128), 0x07: ("4 MB", 256), 0x08: ("8 MB", 512),
    0x52: ("1.1 MB", 72), 0x53: ("1.2 MB", 80), 0x54: ("1.5 MB", 96),
}

# RAM size byte (0x149) -> (label, number of 8 KB pages, byte count, is_2k)
RAM_SIZES = {
    0x00: ("None", 0, 0, False), 0x01: ("2 KB", 1, 2048, True),
    0x02: ("8 KB", 1, 8192, False), 0x03: ("32 KB", 4, 32768, False),
    0x04: ("128 KB", 16, 131072, False), 0x05: ("64 KB", 8, 65536, False),
}

# Valid raw file sizes for flashing a ROM (bytes) -> page count.
ROM_FILE_PAGES = {
    32 * 1024: 2, 64 * 1024: 4, 128 * 1024: 8, 256 * 1024: 16,
    512 * 1024: 32, 1024 * 1024: 64, 2048 * 1024: 128,
    4096 * 1024: 256, 8192 * 1024: 512,
}

# Valid raw save file sizes (bytes) -> (page count, is_2k).
RAM_FILE_PAGES = {
    2048: (1, True), 8192: (1, False), 32768: (4, False),
    65536: (8, False), 131072: (16, False),
}


def cart_type_to_mbc(type_byte: int) -> int:
    """Map a 0x147 cart-type byte to the flasher's MBC selector."""
    if type_byte == 0x00 or type_byte in (0x08, 0x09):
        return MBC_ROMONLY
    if type_byte in (0x01, 0x02, 0x03):
        return MBC1
    if type_byte in (0x05, 0x06):
        return MBC2
    if type_byte in (0x0F, 0x10, 0x11, 0x12, 0x13):
        return MBC3
    if type_byte in (0x19, 0x1A, 0x1B):
        return MBC5
    if type_byte in (0x1C, 0x1D, 0x1E):
        return MBC_RUMBLE
    return MBC_AUTO


@dataclass
class GBHeader:
    title: str
    cgb: bool
    sgb: bool
    cart_type_byte: int
    cart_type: str
    rom_size_byte: int
    rom_size: str
    rom_pages: int
    ram_size_byte: int
    ram_size: str
    ram_pages: int
    ram_is_2k: bool
    logo_ok: bool
    header_checksum: int
    header_checksum_ok: bool


def parse_header(rom: bytes) -> GBHeader | None:
    """Parse a Game Boy header from raw ROM bytes (needs >= 0x150 bytes)."""
    if len(rom) < 0x150:
        return None

    logo_ok = rom[0x104:0x134] == NINTENDO_LOGO

    cgb_flag = rom[0x143]
    cgb = cgb_flag in (0x80, 0xC0)
    title_end = 0x143 if cgb else 0x144
    raw = rom[0x134:title_end]
    title = "".join(chr(c) for c in raw if 32 <= c < 127).strip()

    sgb = rom[0x146] == 0x03
    ctype = rom[0x147]
    rsize = rom[0x148]
    asize = rom[0x149]

    rom_label, rom_pages = ROM_SIZES.get(rsize, ("Unknown", 0))
    ram_label, ram_pages, _ram_bytes, ram_2k = RAM_SIZES.get(
        asize, ("Unknown", 0, 0, False))

    # MBC2 has 512 x 4-bit internal RAM and reports 0x00; treat as a 2 KB read.
    if ctype in (0x05, 0x06):
        ram_label, ram_pages, ram_2k = "512 x 4 bit (MBC2)", 1, True

    chk = 0
    for b in rom[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    header_chk = rom[0x14D]

    return GBHeader(
        title=title, cgb=cgb, sgb=sgb,
        cart_type_byte=ctype, cart_type=CART_TYPES.get(ctype, f"0x{ctype:02X}"),
        rom_size_byte=rsize, rom_size=rom_label, rom_pages=rom_pages,
        ram_size_byte=asize, ram_size=ram_label, ram_pages=ram_pages,
        ram_is_2k=ram_2k, logo_ok=logo_ok,
        header_checksum=header_chk, header_checksum_ok=(chk == header_chk),
    )


def safe_filename(title: str, default: str) -> str:
    keep = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    keep = keep.strip().replace(" ", "_")
    return keep or default
