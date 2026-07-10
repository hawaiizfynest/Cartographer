"""
test_titles.py - tests for the game-title resolver.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import titles  # noqa: E402


def _gba_header(short: str, code: str) -> bytes:
    h = bytearray(0xC0)
    h[0xA0:0xA0 + len(short)] = short.encode("ascii")[:12]
    h[0xAC:0xAC + len(code)] = code.encode("ascii")[:4]
    return bytes(h)


def test_header_fields():
    hdr = _gba_header("POKEMON EMER", "BPEE")
    short, code = titles.gba_header_fields(hdr)
    assert short == "POKEMON EMER"
    assert code == "BPEE"


def test_resolve_by_code():
    hdr = _gba_header("DORA THE EXPL", "AERE")
    info = titles.resolve_gba(hdr)
    assert info.source == "code"
    assert "Dora the Explorer" in info.full_title
    assert "Pirate Pig" in info.full_title
    assert info.save_type == "eeprom_4k"


def test_save_type_lookup():
    assert titles.save_type_for_code("AERE") == "eeprom_4k"
    assert titles.save_type_for_code("BPEE") == "flash_1m"
    assert titles.save_type_for_code("ZZZZ") == ""


def test_resolve_falls_back_to_header():
    hdr = _gba_header("HOMEBREWGAME", "ZZZZ")
    info = titles.resolve_gba(hdr)
    assert info.source == "header"
    assert info.full_title == "HOMEBREWGAME"
    assert info.save_type == ""


def test_resolve_by_sha1_when_rom_known(tmp_path=None):
    # Build a fake ROM, register its hash, and confirm sha1 wins over code.
    hdr = _gba_header("POKEMON EMER", "BPEE")
    rom = hdr + b"\x00" * 1000
    digest = hashlib.sha1(rom).hexdigest().lower()
    titles._SHA1[digest] = {"title": "Pokemon - Emerald Version (USA, Europe) [verified]"}
    try:
        info = titles.resolve_gba(hdr, rom=rom)
        assert info.source == "sha1"
        assert "verified" in info.full_title
    finally:
        del titles._SHA1[digest]


def test_emerald_code_present():
    hdr = _gba_header("POKEMON EMER", "BPEE")
    info = titles.resolve_gba(hdr)
    assert "Emerald" in info.full_title


def test_filename_from_title():
    # Mirror DeviceWindow._default_filename's sanitization rules without Qt.
    def default_filename(resolved_title, game_code, extension):
        name = resolved_title.strip()
        if not name or name in ("(unknown)", "(none)"):
            name = game_code.strip() or "cartridge"
        illegal = '<>:"/\\|?*\x00'
        cleaned = "".join(" " if c in illegal else c for c in name)
        cleaned = " ".join(cleaned.split()).strip(" .")
        return (cleaned or "cartridge") + extension

    assert default_filename("Pokemon - Emerald Version (USA, Europe)", "", ".gba") \
        == "Pokemon - Emerald Version (USA, Europe).gba"
    # colon (illegal on Windows) removed, spaces preserved
    assert default_filename(
        "Dora the Explorer: The Search for Pirate Pig's Treasure (USA)", "", ".sav"
        ) == "Dora the Explorer The Search for Pirate Pig's Treasure (USA).sav"
    # falls back to code, then generic
    assert default_filename("", "AERE", ".gba") == "AERE.gba"
    assert default_filename("(unknown)", "", ".gba") == "cartridge.gba"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} title-resolver tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
