"""
test_sram_patcher.py - tests for the SRAM patcher and the full prepare pipeline.

The patch data is fixed, so patching is deterministic. These tests build a
synthetic ROM carrying a FLASH1M_V103 save-routine fingerprint and assert the
patch engine finds and rewrites every pattern, and that the pipeline composes
SRAM + batteryless correctly.

Run:  python scripts/test_sram_patcher.py

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import bl_patcher as blp  # noqa: E402
from cartographer import pipeline  # noqa: E402
from cartographer import sram_patcher as sp  # noqa: E402


def _build_flash1m_rom() -> bytes:
    """Synthetic ROM containing the FLASH1M_V103 id and all its marker blobs."""
    rom = bytearray(b"\xff" * 0x200000)
    rom[0:4] = bytes((0x2E, 0x00, 0x00, 0xEA))
    rom[0x100:0x104] = bytes((0xFC, 0x7F, 0x00, 0x03))   # IRQ ref for bl-patch
    rom[0x9000:0x900C] = b"FLASH1M_V103"                 # save id string

    # lay the markers of the FLASH1M_V103 set into the ROM at spaced offsets
    _idents, patches = sp.PATCH_SETS[2]                  # FLASH1M_V103
    assert _idents == ["FLASH1M_V103"]
    off = 0x20000
    for marker, _replace, _mask in patches:
        rom[off:off + len(marker)] = marker
        off += len(marker) + 0x40
    # also embed a batteryless-recognizable save-write signature (as real
    # Emerald has, separate from the bbsan2k primitives) so the second stage
    # of the pipeline has a routine to hook
    rom[0x40000:0x40000 + len(blp._WRITE_FLASH3_SIG)] = blp._WRITE_FLASH3_SIG
    return bytes(rom)


def test_detect_flash1m():
    rom = _build_flash1m_rom()
    assert sp.detect_save_id(rom) == "FLASH1M_V103"


def test_sram_patch_applies_all_patterns():
    rom = _build_flash1m_rom()
    res = sp.patch_rom(rom)
    assert res.patch_set == "FLASH1M_V103"
    assert res.patches_applied == 7
    assert len(res.locations) == 7
    # output differs from input (patches were written)
    assert res.data != rom


def test_sram_patch_rejects_unknown():
    rom = bytearray(b"\x00" * 0x10000)
    rom[0xA0:0xAC] = b"NOSAVEGAME00"
    try:
        sp.patch_rom(bytes(rom))
    except sp.SramPatchError:
        return
    raise AssertionError("ROM with no save signature should be rejected")


def test_masked_eeprom_set_present():
    # EEPROM set carries wildcard masks; make sure the data survived extraction.
    idents, patches = sp.PATCH_SETS[3]
    assert "EEPROM_V120" in idents
    assert any(mask for _m, _r, mask in patches)


def test_pipeline_sram_then_batteryless():
    rom = _build_flash1m_rom()
    res = pipeline.prepare_for_batteryless(rom, blp.MODE_AUTO, sram_patch=True)
    assert res.sram_patched
    assert res.save_id == "FLASH1M_V103"
    assert blp.is_already_patched(res.data)   # batteryless signature installed
    assert res.bl.write_hooks                  # a save routine was hooked


def test_pipeline_skips_sram_for_native_sram():
    # No Flash/EEPROM id -> SRAM patch skipped, batteryless still requires a hook.
    rom = bytearray(_build_flash1m_rom())
    rom[0x9000:0x900C] = b"XXXXXXXXXXXX"       # wipe the FLASH1M id
    # give it an SRAM write signature so batteryless can hook
    rom[0x180000:0x180000 + len(blp._WRITE_SRAM_SIG)] = blp._WRITE_SRAM_SIG
    res = pipeline.prepare_for_batteryless(bytes(rom), blp.MODE_AUTO, sram_patch=True)
    assert not res.sram_patched
    assert res.save_id == ""


def test_sram_patch_alone_leaves_no_batteryless_payload():
    """The SRAM-only action exists so a ROM can be handed to another tool for
    the next stage. If it dragged the batteryless payload along, the second
    tool would be patching an already-rewritten ROM."""
    rom = bytearray(_build_flash1m_rom())
    alone = sp.patch_rom(bytes(rom))
    chained = pipeline.prepare_for_batteryless(bytes(rom), blp.MODE_AUTO,
                                               sram_patch=True)
    assert alone.data != chained.data, "SRAM-only produced the full chain"
    assert len(alone.data) == len(rom), "SRAM-only should not resize the ROM"
    assert alone.save_id
    assert alone.patches_applied > 0


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} SRAM-patcher / pipeline tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
