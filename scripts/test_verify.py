"""
test_verify.py - tests for dump verification.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import gb_header as gbh  # noqa: E402
from cartographer import verify  # noqa: E402


def _good_gba(size: int = 0x400) -> bytes:
    rom = bytearray((i * 7 + 1) & 0xFF for i in range(size))
    rom[0x04:0x0C] = bytes((0x24, 0xFF, 0xAE, 0x51, 0x69, 0x9A, 0xA2, 0x21))
    rom[0xA0:0xAC] = b"TESTGAME    "
    rom[0xAC:0xB0] = b"TSTE"
    comp, _ = verify.gba_header_checksum(bytes(rom))
    # fix checksum byte so the computed value matches stored
    chk = 0
    for i in range(0xA0, 0xBD):
        chk = (chk - rom[i]) & 0xFF
    chk = (chk - 0x19) & 0xFF
    rom[0xBD] = chk
    return bytes(rom)


def _good_gb() -> bytes:
    rom = bytearray(0x8000)
    for i in range(0x200, 0x8000):
        rom[i] = (i * 5 + 3) & 0xFF
    rom[0x104:0x134] = gbh.NINTENDO_LOGO
    rom[0x134:0x13A] = b"TETRIS"
    chk = 0
    for b in rom[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    rom[0x14D] = chk
    total = 0
    for i, b in enumerate(rom):
        if i not in (0x14E, 0x14F):
            total = (total + b) & 0xFFFF
    rom[0x14E] = (total >> 8) & 0xFF
    rom[0x14F] = total & 0xFF
    return bytes(rom)


def test_gba_good_passes():
    r = verify.verify_gba(_good_gba())
    assert r.all_passed, [c.name for c in r.checks if not c.passed]


def test_gba_bad_logo_fails():
    rom = bytearray(_good_gba())
    rom[0x06] ^= 0xFF          # corrupt the logo
    r = verify.verify_gba(bytes(rom))
    assert not r.all_passed
    assert any(c.name == "Nintendo logo" and not c.passed for c in r.checks)


def test_gba_bad_checksum_fails():
    rom = bytearray(_good_gba())
    rom[0xBD] ^= 0xFF          # corrupt the header checksum byte
    r = verify.verify_gba(bytes(rom))
    assert any(c.name == "header checksum" and not c.passed for c in r.checks)


def test_gba_dead_read_fails():
    rom = bytearray(b"\x00" * 0x400)
    r = verify.verify_gba(bytes(rom))
    assert any(c.name == "data present" and not c.passed for c in r.checks)


def test_gb_good_passes():
    r = verify.verify_gb(_good_gb())
    assert r.all_passed, [c.name for c in r.checks if not c.passed]


def test_gb_global_checksum_detects_corruption():
    rom = bytearray(_good_gb())
    rom[0x2000] ^= 0xFF        # flip a byte somewhere in the ROM body
    r = verify.verify_gb(bytes(rom))
    assert any(c.name == "global checksum" and not c.passed for c in r.checks)


def test_known_good_match():
    rom = _good_gba()
    _crc, sha1 = verify.hashes(rom)
    db = {sha1: {"title": "Test Game (USA)"}}
    r = verify.verify_gba(rom, known_db=db)
    assert r.known_good
    assert r.known_title == "Test Game (USA)"
    assert "Verified good dump" in r.summary()


def test_compare_reads():
    a = bytes((i & 0xFF) for i in range(1000))
    assert verify.compare_reads(a, a).passed
    b = bytearray(a)
    b[500] ^= 1
    c = verify.compare_reads(a, bytes(b))
    assert not c.passed
    assert "0x1F4" in c.detail          # offset 500 == 0x1F4


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} verification tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
