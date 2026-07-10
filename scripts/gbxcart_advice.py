"""
gbxcart_advice.py - tell me which flash cart a GBA ROM needs.

Reads a ROM's save type and prints the matching insideGadgets flash cart(s),
including the important "you can't shrink Pokemon to SRAM" and "carts are locked
to one save type" caveats.

Usage:
    python scripts/gbxcart_advice.py mygame.gba

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import cart_compat as cc  # noqa: E402
from cartographer import gbxcart as gx  # noqa: E402
from cartographer import titles  # noqa: E402


def _detect(data: bytes):
    short, code = titles.gba_header_fields(data) if len(data) >= 0xB0 else ("", "")
    title = ""
    if code:
        title = titles.resolve_gba(data[:0xC0] if len(data) >= 0xC0 else data
                                   ).full_title
    save_id = ""
    for tag in (b"EEPROM_V", b"FLASH1M_V", b"FLASH512_V", b"FLASH_V", b"SRAM_V"):
        if data.find(tag) >= 0:
            save_id = tag.decode("ascii")
            break
    kind = gx.save_kind_from_id(save_id)
    if kind == "none" and code:
        kind = titles.save_type_for_code(code) or "none"
    return kind, (title or short), code


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python scripts/gbxcart_advice.py <rom.gba>")
        return 1
    try:
        data = open(argv[0], "rb").read()
    except OSError as exc:
        print(f"Cannot read {argv[0]}: {exc}")
        return 1

    kind, title, code = _detect(data)
    is_rtc = any(g in (title or "").lower()
                 for g in ("emerald", "ruby", "sapphire"))
    result = cc.recommend(kind, is_pokemon_rtc=is_rtc)

    print(f"{title or os.path.basename(argv[0])}"
          + (f"  [{code}]" if code else "") + "\n")
    print(result.summary())
    print()
    for c in result.primary + result.alt:
        if c.url:
            print(f"  {c.name}\n    {c.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
