"""
test_flash_patcher.py - tests for the ported GBA flash save patcher.

The port only matters if it matches metroid-maniac's gba-flash.exe exactly, so
these pin the behaviour that makes that true, including one ordering trap that
would otherwise fail silently.

Run:  python scripts/test_flash_patcher.py

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import flash_patcher as fp  # noqa: E402

SIG = {name: sig for name, sig, *_ in fp.SIGNATURES}


def _rom(size=0x400000, blank_from=0x3FF000):
    rom = bytearray(b"\x00" * size)
    for i in range(0, blank_from, 3):
        rom[i] = (i * 131 + 7) & 0xFF
    rom[blank_from:] = b"\x00" * (size - blank_from)
    return rom


def _eeprom_rom():
    """A ROM shaped like an SRAM-patched EEPROM game."""
    rom = _rom()
    rom[0x9E218:0x9E218 + 16] = SIG["SRAM-patched ReadEepromDword"]
    rom[0x9E218 + 16:0x9E218 + 20] = bytes([0xFF, 0xEE, 0xDD, 0xCC])
    rom[0x9E2C8:0x9E2C8 + 20] = SIG["SRAM-patched ProgramEepromDword"]
    rom[0x9E3A4:0x9E3A4 + 16] = SIG["SRAM-patched VerifyEepromDword"]
    rom[0x9E02C:0x9E02C + 16] = fp.IDENTIFY_EEPROM
    struct.pack_into("<I", rom, 0x9E02C + 20, 0x03007BC8)
    return bytes(rom)


def test_payload_is_the_released_one():
    assert fp.PAYLOAD_LEN == 684
    entries = [fp._payload_word(i) for i in range(6)]
    assert entries == [0x1D9, 0x221, 0x1EB, 0x24F, 0x201, 0x27D]
    assert all(e & 1 for e in entries), "entry points must have the Thumb bit"
    assert fp._payload_word(fp.EEPROM_META) == 0


def test_matches_the_real_tool_on_an_eeprom_game():
    """These offsets and targets are what gba-flash.exe 0.0.2 produced for a
    real SRAM-patched Super Mario Advance 2."""
    res = fp.patch_rom(_eeprom_rom())
    assert res.payload_base == 0x3FFD54
    assert res.eeprom_meta_addr == 0x03007BC8
    targets = {off: target for _, off, target in res.hooks if target}
    assert targets == {0x9E218: 0x083FFFA3,
                       0x9E2C8: 0x083FFF75,
                       0x9E3A4: 0x083FFFD1}


def test_read_eeprom_signature_does_not_steal_the_write_site():
    """ReadEepromDword's signature is byte for byte the first 16 bytes of
    ProgramEepromDword's, so both match at a write site. The C tool survives it
    only because the write thunk overwrites those bytes before the read check
    runs. Reorder the two and every EEPROM game silently gets the wrong
    handler on its write routine."""
    res = fp.patch_rom(_eeprom_rom())
    write_site = [t for n, off, t in res.hooks if off == 0x9E2C8]
    assert write_site == [0x08000000 + 0x3FFD54 + fp._payload_word(fp.WRITE_EEPROM)]
    assert len([1 for _, off, t in res.hooks if off == 0x9E2C8 and t]) == 1


def test_identify_is_read_not_patched():
    rom = _eeprom_rom()
    res = fp.patch_rom(rom)
    assert res.data[0x9E02C:0x9E02C + 16] == fp.IDENTIFY_EEPROM
    assert struct.unpack_from("<I", res.data, res.payload_base + 24)[0] == 0x03007BC8


def test_sram_game_hooks_the_sram_routines():
    rom = _rom()
    rom[0x50000:0x50000 + 16] = SIG["WriteSram"]
    rom[0x51000:0x51000 + 16] = SIG["ReadSram"]
    rom[0x52000:0x52000 + 16] = SIG["VerifySram"]
    res = fp.patch_rom(bytes(rom))
    names = {n for n, _, _ in res.hooks}
    assert names == {"WriteSram", "ReadSram", "VerifySram"}
    for _, off, _ in res.hooks:
        assert res.data[off:off + 4] == fp.THUMB_THUNK


def test_arm_thunk_used_for_the_ram_resident_writer():
    rom = _rom()
    rom[0x60000:0x60000 + 16] = SIG["WriteSramFast"]
    res = fp.patch_rom(bytes(rom))
    assert res.data[0x60000:0x60008] == fp.ARM_THUNK
    target = struct.unpack_from("<I", res.data, 0x60000 + 8)[0]
    assert target == 0x08000000 + res.payload_base + fp._payload_word(fp.WRITE_SRAM)


def test_unpatchable_rom_is_refused_not_silently_returned():
    raised = False
    try:
        fp.patch_rom(bytes(_rom()))
    except fp.FlashPatchError:
        raised = True
    assert raised, "a ROM with no save routines must be refused"


def test_forced_loadfactor_points_at_rom_not_ram():
    for factor, addrs in fp.LOADFACTOR_ADDRS.items():
        res = fp.patch_rom(_eeprom_rom(), force_loadfactor=factor)
        meta = struct.unpack_from("<I", res.data, res.payload_base + 24)[0]
        assert meta != 0x03007BC8, "the game's RAM address should be overridden"
        assert meta & 0xFF000000 == 0x08000000, "forced geometry must live in ROM"
        off = meta - 0x08000000
        ptr = struct.unpack_from("<I", res.data, off)[0]
        assert ptr == meta + 4
        got = struct.unpack_from("<H", res.data, ptr - 0x08000000 + 4)[0]
        assert got == addrs, f"load factor {factor} needs addrs 0x{addrs:X}"


def test_default_output_is_untouched_by_the_override_code():
    plain = fp.patch_rom(_eeprom_rom())
    assert plain.forced_loadfactor is None
    assert plain.eeprom_meta_addr == 0x03007BC8
    forced = fp.patch_rom(_eeprom_rom(), force_loadfactor=7)
    assert forced.data != plain.data


def test_bad_loadfactor_refused():
    raised = False
    try:
        fp.patch_rom(_eeprom_rom(), force_loadfactor=5)
    except fp.FlashPatchError:
        raised = True
    assert raised


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} flash-patcher tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
