"""
cart_compat.py - recommend which flash cart a game needs.

GBA flash carts are locked to a single save type - a game checks the save chip's
ID, so (for example) a 1Mbit-flash game will not save on a 512Kbit-flash cart.
This module maps a detected save type to the flash cart(s) that will actually
work, following insideGadgets' own "Choosing a Flash Cart" guidance.

Key facts encoded here (from the manufacturer):
  - 256Kbit FRAM/SRAM cart: SRAM 256Kbit saves, and EEPROM games AFTER a GBATA
    SRAM patch. Cannot hold Pokemon (1Mbit) or 512Kbit-flash saves.
  - 512Kbit flash cart: only 512Kbit flash saves.
  - 1Mbit flash cart: only 1Mbit flash saves (all Pokemon Gen 3).
  - 4Kbit/64Kbit EEPROM cart: only EEPROM saves (native, no patch).
  - No-save games run on any cart.

This is guidance, not a guarantee; always confirm against the specific game.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass, field

# save "kind" strings match cartographer.gbxcart.SAVE_* values
SAVE_NONE = "none"
SAVE_EEPROM_4K = "eeprom_4k"
SAVE_EEPROM_64K = "eeprom_64k"
SAVE_SRAM_256K = "sram_256k"
SAVE_FLASH_512K = "flash_512k"
SAVE_FLASH_1M = "flash_1m"


@dataclass
class CartOption:
    name: str
    url: str
    note: str = ""


@dataclass
class CompatResult:
    save_kind: str
    save_label: str
    primary: list = field(default_factory=list)   # best-fit carts
    alt: list = field(default_factory=list)        # workable with a caveat
    incompatible_note: str = ""

    def summary(self) -> str:
        if not self.primary and not self.alt:
            return f"Save type {self.save_label}: no matching cart found."
        lines = [f"Save type: {self.save_label}"]
        if self.primary:
            lines.append("Recommended cart:")
            for c in self.primary:
                lines.append(f"  - {c.name}"
                             + (f" ({c.note})" if c.note else ""))
        if self.alt:
            lines.append("Also works (with a caveat):")
            for c in self.alt:
                lines.append(f"  - {c.name}"
                             + (f" ({c.note})" if c.note else ""))
        if self.incompatible_note:
            lines.append(self.incompatible_note)
        return "\n".join(lines)


# insideGadgets cart catalogue (names + product URLs)
_FRAM = CartOption(
    "insideGadgets GBA 32MB, 256Kbit FRAM Save Flash Cart",
    "https://shop.insidegadgets.com/product/gba-32mb-256kbit-fram-save-flash-cart/")
_EEPROM = CartOption(
    "insideGadgets GBA 32MB, 4Kbit/64Kbit EEPROM Save Flash Cart",
    "https://shop.insidegadgets.com/product/gba-32mb-4kbit-64kbit-eeprom-save-flash-cart/")
_FLASH512 = CartOption(
    "insideGadgets GBA 32MB, 512Kbit Flash Save Flash Cart",
    "https://shop.insidegadgets.com/product/gba-32mb-512kbit-flash-save-flash-cart/")
_FLASH1M = CartOption(
    "insideGadgets GBA 32MB, 1Mbit Flash Save Flash Cart (Pokemon)",
    "https://shop.insidegadgets.com/product/gba-32mb-1mbit-flash-save-no-rtc-flash-cart-low-cost/")
_FLASH1M_RTC = CartOption(
    "insideGadgets GBA 32MB, 1Mbit Flash Save WITH RTC Flash Cart",
    "https://shop.insidegadgets.com/product/gba-32mb-1mbit-flash-save-with-rtc-flash-cart-works-with-pokemon-games/")
_ROMONLY = CartOption(
    "insideGadgets GBA ROM-only Flash Cart (any no-save game)",
    "https://shop.insidegadgets.com/product/gba-16-32mb-rom-only-flash-cart/")

_SAVE_LABELS = {
    SAVE_NONE: "no save",
    SAVE_EEPROM_4K: "EEPROM 4Kbit (512 bytes)",
    SAVE_EEPROM_64K: "EEPROM 64Kbit (8 KB)",
    SAVE_SRAM_256K: "SRAM/FRAM 256Kbit (32 KB)",
    SAVE_FLASH_512K: "Flash 512Kbit (64 KB)",
    SAVE_FLASH_1M: "Flash 1Mbit (128 KB)",
}


def recommend(save_kind: str, is_pokemon_rtc: bool = False) -> CompatResult:
    """Return cart recommendations for a detected save kind."""
    label = _SAVE_LABELS.get(save_kind, save_kind or "unknown")
    r = CompatResult(save_kind=save_kind, save_label=label)

    if save_kind == SAVE_NONE:
        r.primary = [_ROMONLY]
        r.alt = [_FRAM, _EEPROM, _FLASH512, _FLASH1M]
        r.incompatible_note = "A no-save game runs on any flash cart."
        return r

    if save_kind in (SAVE_EEPROM_4K, SAVE_EEPROM_64K):
        r.primary = [CartOption(_EEPROM.name, _EEPROM.url, "native EEPROM, no patch")]
        r.alt = [CartOption(_FRAM.name, _FRAM.url,
                            "only after patching the ROM to SRAM with GBATA")]
        return r

    if save_kind == SAVE_SRAM_256K:
        r.primary = [_FRAM]
        r.incompatible_note = ("Note: the FRAM/SRAM cart cannot hold Pokemon "
                               "(1Mbit) or 512Kbit-flash saves.")
        return r

    if save_kind == SAVE_FLASH_512K:
        r.primary = [_FLASH512]
        r.incompatible_note = ("512Kbit-flash games only work on the 512Kbit cart "
                               "- the game checks the flash chip ID.")
        return r

    if save_kind == SAVE_FLASH_1M:
        primary = [_FLASH1M_RTC, _FLASH1M] if is_pokemon_rtc else [_FLASH1M,
                                                                   _FLASH1M_RTC]
        r.primary = primary
        r.incompatible_note = (
            "Pokemon Gen 3 (Emerald/Ruby/Sapphire/FireRed/LeafGreen) needs the "
            "1Mbit cart. It CANNOT be shrunk with an SRAM patch (still needs "
            "1Mbit of SRAM). For Ruby/Sapphire/Emerald pick the RTC version so "
            "berry growth and time events work.")
        return r

    # unknown save type
    r.incompatible_note = ("Save type not recognised - dump the ROM and check "
                           "with GBATA, or verify the save size in an emulator.")
    return r
