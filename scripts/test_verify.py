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


def test_report_contains_hashes_and_metadata():
    rom = _good_gba()
    r = verify.verify_gba(rom)
    meta = verify.DumpMeta(console="Game Boy Advance", title="Test Game",
                           game_code="TSTE", rom_size=len(rom),
                           save_type="SRAM/FRAM 256Kbit (32 KB)")
    text = verify.build_report("Test Game.gba", meta, r,
                               "2026-07-18 12:00:00", app_version="1.0.6")
    assert r.sha1 in text
    assert r.crc32 in text
    assert "File:            Test Game.gba" in text
    assert "Game Boy Advance" in text
    assert "TSTE" in text
    assert "1 KB (1,024 bytes)" in text          # _good_gba() is 0x400 bytes
    assert "Result: PASSED" in text              # valid but not in any DB


def test_report_failed_verify_is_loud():
    rom = bytearray(_good_gba())
    rom[0xBD] ^= 0xFF                            # break the header checksum
    r = verify.verify_gba(bytes(rom))
    text = verify.build_report("bad.gba", verify.DumpMeta(), r,
                               "2026-07-18 12:00:00")
    assert "Result: FAILED" in text
    assert any(line.startswith("Header checksum: FAILED")
               for line in text.splitlines())


def test_report_known_good_verdict():
    rom = _good_gba()
    _crc, sha1 = verify.hashes(rom)
    r = verify.verify_gba(rom, known_db={sha1: {"title": "Test Game (USA)"}})
    text = verify.build_report("t.gba", verify.DumpMeta(), r, "x")
    assert "Result: VERIFIED GOOD" in text
    assert 'Known-good match: YES - matches known-good "Test Game (USA)"' \
        in text


def test_report_read_twice_line_only_when_given():
    r = verify.verify_gba(_good_gba())
    plain = verify.build_report("t.gba", verify.DumpMeta(), r, "x")
    assert "Read-twice" not in plain
    chk = verify.compare_reads(b"same", b"same")
    text = verify.build_report("t.gba", verify.DumpMeta(), r, "x",
                               read_twice=chk)
    assert "Read-twice check: PASS (both reads identical)" in text


def test_write_report_and_sha1_sidecars():
    import tempfile
    rom = _good_gba()
    r = verify.verify_gba(rom)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.gba")
        with open(p, "wb") as f:
            f.write(rom)
        text = verify.build_report("t.gba", verify.DumpMeta(), r, "x")
        rp = verify.write_report(p, text)
        assert rp == p + ".txt"
        with open(rp, encoding="utf-8") as f:
            assert f.read() == text
        sp = verify.write_sha1_file(p, r.sha1)
        assert sp == p + ".sha1"
        with open(sp, encoding="utf-8") as f:
            assert f.read().strip() == f"{r.sha1} *t.gba"


def test_reverify_passes_then_catches_mutation():
    import tempfile
    rom = _good_gba()
    r = verify.verify_gba(rom)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.gba")
        with open(p, "wb") as f:
            f.write(rom)
        verify.write_report(p, verify.build_report("t.gba", verify.DumpMeta(),
                                                   r, "x"))
        assert verify.reverify_against_report(p).passed
        mutated = bytearray(rom)
        mutated[0x200] ^= 0x01                   # single-bit rot mid-file
        with open(p, "wb") as f:
            f.write(mutated)
        check = verify.reverify_against_report(p)
        assert not check.passed
        assert "SHA-1" in check.detail


def test_reverify_missing_report_fails_cleanly():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nothing.gba")
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        check = verify.reverify_against_report(p)
        assert not check.passed
        assert "report" in check.detail


def test_restore_report_verified():
    data = bytes((i * 3 + 5) & 0xFF for i in range(32768))
    crc, sha1 = verify.hashes(data)
    wb = verify.compare_reads(data, data)
    text = verify.build_restore_report(
        "Emerald.sav", "Game Boy Advance", "Flash 1Mbit (128 KB)", len(data),
        crc, sha1, wb, "2026-07-18 12:00:00", app_version="1.0.6")
    assert sha1 in text
    assert crc in text
    assert "Write verify:    PASS (both reads identical)" in text
    assert "Result: RESTORED AND VERIFIED" in text
    assert "32 KB (32,768 bytes)" in text


def test_restore_report_failed_writeback_is_loud():
    data = bytes(range(256))
    mangled = bytearray(data)
    mangled[10] ^= 0xFF
    crc, sha1 = verify.hashes(data)
    wb = verify.compare_reads(data, bytes(mangled))
    text = verify.build_restore_report(
        "save.sav", "Game Boy", "8 KB RAM (MBC1+RAM+BATTERY)", len(data),
        crc, sha1, wb, "2026-07-18 12:00:00")
    assert "Result: FAILED" in text
    assert any(line.startswith("Write verify:    FAILED")
               for line in text.splitlines())


def test_restore_receipt_reverify_roundtrip():
    import tempfile
    data = bytes((i * 11 + 2) & 0xFF for i in range(4096))
    crc, sha1 = verify.hashes(data)
    wb = verify.compare_reads(data, data)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "game.sav")
        with open(p, "wb") as f:
            f.write(data)
        text = verify.build_restore_report("game.sav", "Game Boy Advance",
                                           "SRAM/FRAM 256Kbit (32 KB)",
                                           len(data), crc, sha1, wb, "x")
        rp = verify.write_report(p, text, suffix=".restore.txt")
        assert rp == p + ".restore.txt"
        assert verify.reverify_against_report(p, rp).passed
        with open(p, "ab") as f:
            f.write(b"\x00")               # truncation/append damage
        check = verify.reverify_against_report(p, rp)
        assert not check.passed
        assert "SHA-1" in check.detail


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} verification tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
