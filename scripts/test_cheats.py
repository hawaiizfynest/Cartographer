"""
test_cheats.py - tests for Game Genie / GameShark decoding and application.

Verified against known real codes.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import cheats  # noqa: E402


def test_game_genie_known_code():
    # FA1-F5A-E61 is documented as: address 0x51F5, old 0xC2, new 0xFA.
    gg = cheats.decode_game_genie("FA1-F5A-E61")
    assert gg.address == 0x51F5
    assert gg.new_value == 0xFA
    assert gg.old_value == 0xC2


def test_game_genie_six_digit():
    gg = cheats.decode_game_genie("00A-178")
    assert gg.old_value is None
    assert gg.address <= 0xFFFF


def test_game_genie_rejects_bad_length():
    try:
        cheats.decode_game_genie("ABCD")
        assert False
    except cheats.CheatError:
        pass


def test_gameshark_gb_decode():
    # Pan Docs example: 010238CD -> bank 0x01, write 0x02 at 0xCD38.
    gs = cheats.decode_gameshark_gb("010238CD")
    assert gs.ram_bank == 0x01
    assert gs.value == 0x02
    assert gs.address == 0xCD38


def test_gba_raw_widths():
    assert cheats.decode_gba_raw("02000000:12").width == 1
    assert cheats.decode_gba_raw("02000000:1234").width == 2
    assert cheats.decode_gba_raw("02000000:12345678").width == 4
    c = cheats.decode_gba_raw("020035AC:000003E7")
    assert c.address == 0x020035AC
    assert c.value == 0x3E7


def test_apply_game_genie_writes_when_old_matches():
    rom = bytearray(0x8000)
    rom[0x51F5] = 0xC2                 # matches the code's expected old value
    report = cheats.apply_game_genie(bytes(rom), ["FA1-F5A-E61"])
    assert len(report.applied) == 1
    assert report.data[0x51F5] == 0xFA


def test_apply_game_genie_skips_on_mismatch():
    rom = bytearray(0x8000)
    rom[0x51F5] = 0x00                 # does NOT match expected 0xC2
    report = cheats.apply_game_genie(bytes(rom), ["FA1-F5A-E61"])
    assert len(report.applied) == 0
    assert len(report.skipped) == 1
    assert "mismatch" in report.skipped[0][1]
    assert report.data[0x51F5] == 0x00  # unchanged


def test_apply_game_genie_six_digit_no_check():
    rom = bytearray(0x8000)
    gg = cheats.decode_game_genie("FA1-F5A")   # 6-digit: no old value
    rom[gg.address] = 0x11
    report = cheats.apply_game_genie(bytes(rom), ["FA1-F5A"])
    assert len(report.applied) == 1
    assert report.data[gg.address] == gg.new_value


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} cheat tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
