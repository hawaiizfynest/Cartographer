"""
test_bl_patcher.py - Hardware-free tests for the GBA batteryless patcher.

The embedded payload is fixed, so patching a fixed input ROM is fully
deterministic. These tests rebuild known synthetic ROMs and assert both exact
output hashes (verified against metroid-maniac's reference patcher.c) and
structural invariants.

Run:  python scripts/test_bl_patcher.py

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import bl_patcher as blp  # noqa: E402

# SHA-256 of output produced by the reference C patcher (metroid-maniac) for
# the SRAM test ROM below, in each mode.
GOLDEN_AUTO = "f1b1fe264e08cd0110f3bf96dde7265296a529e4163f20f1e2b1c1178a440fcb"
GOLDEN_KEYPAD = "a97bbafdded86673de0faa2fcea524ef7659e7405a9c9620ac2c9dfc0eccc516"


def _sram_rom() -> bytes:
    rom = bytearray(b"\xff" * 0x100000)
    rom[0:4] = bytes((0x2E, 0x00, 0x00, 0xEA))            # b 0x080000c0
    rom[0x100:0x104] = bytes((0xFC, 0x7F, 0x00, 0x03))    # IRQ ref
    rom[0x180:0x184] = bytes((0xFC, 0x7F, 0x00, 0x03))    # IRQ ref
    rom[0x200:0x210] = blp._WRITE_SRAM_SIG
    for i in range(0x300, 0x1000):
        rom[i] = (i * 7) & 0xFF
    return bytes(rom)


def test_payload_loaded():
    assert blp.PAYLOAD_LEN == 2160
    # header offsets baked into the payload
    assert blp._payload_word(blp._PATCHED_ENTRYPOINT) == 0x54
    assert blp._payload_word(blp._WRITE_SRAM_PATCHED) == 0xCD


def test_auto_mode_matches_reference():
    res = blp.patch_rom(_sram_rom(), blp.MODE_AUTO)
    assert hashlib.sha256(res.data).hexdigest() == GOLDEN_AUTO
    assert res.save_size == 0x8000
    assert res.payload_base == 0xBF790
    assert res.irq_refs == 2
    assert res.write_hooks  # at least one hook installed


def test_keypad_mode_matches_reference():
    res = blp.patch_rom(_sram_rom(), blp.MODE_KEYPAD)
    assert hashlib.sha256(res.data).hexdigest() == GOLDEN_KEYPAD
    # keypad mode does not redirect the write function
    assert res.write_hooks == []


def test_already_patched_is_rejected():
    patched = blp.patch_rom(_sram_rom(), blp.MODE_AUTO).data
    try:
        blp.patch_rom(patched, blp.MODE_AUTO)
    except blp.PatchError:
        return
    raise AssertionError("already-patched ROM should have been rejected")


def test_expansion_when_no_free_space():
    # A fully non-uniform 256 KB ROM has no clean region for the payload.
    rom = bytearray((i * 13 + 7) & 0xFF for i in range(0x40000))
    rom[0:4] = bytes((0x2E, 0x00, 0x00, 0xEA))
    rom[0x100:0x104] = bytes((0xFC, 0x7F, 0x00, 0x03))
    rom[0x220:0x230] = blp._WRITE_FLASH3_SIG
    res = blp.patch_rom(bytes(rom), blp.MODE_AUTO)
    assert res.expanded
    assert len(res.data) == 0x40000 + 0x80000
    assert res.save_size == 0x20000  # FLASH1M


def test_missing_save_function_rejected_in_auto():
    rom = bytearray(b"\xff" * 0x100000)
    rom[0:4] = bytes((0x2E, 0x00, 0x00, 0xEA))
    rom[0x100:0x104] = bytes((0xFC, 0x7F, 0x00, 0x03))
    # no save signature present
    try:
        blp.patch_rom(bytes(rom), blp.MODE_AUTO)
    except blp.PatchError:
        return
    raise AssertionError("auto mode without a save function should be rejected")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} batteryless-patcher tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
