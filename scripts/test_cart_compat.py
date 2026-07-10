"""
test_cart_compat.py - tests for the flash cart recommendation logic.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import cart_compat as cc  # noqa: E402


def test_eeprom_recommends_eeprom_cart():
    r = cc.recommend(cc.SAVE_EEPROM_4K)
    assert r.primary
    assert "EEPROM" in r.primary[0].name
    # FRAM listed as an alternative, but only with the patch caveat
    assert any("GBATA" in c.note for c in r.alt)


def test_pokemon_1m_needs_flash1m_and_cannot_shrink():
    r = cc.recommend(cc.SAVE_FLASH_1M)
    assert r.primary
    assert any("1Mbit" in c.name for c in r.primary)
    assert "CANNOT be shrunk" in r.incompatible_note


def test_pokemon_rtc_prefers_rtc_cart():
    r = cc.recommend(cc.SAVE_FLASH_1M, is_pokemon_rtc=True)
    assert "RTC" in r.primary[0].name


def test_sram_recommends_fram():
    r = cc.recommend(cc.SAVE_SRAM_256K)
    assert "FRAM" in r.primary[0].name
    assert "cannot hold Pokemon" in r.incompatible_note


def test_flash512_is_locked():
    r = cc.recommend(cc.SAVE_FLASH_512K)
    assert "512Kbit" in r.primary[0].name
    assert "flash chip ID" in r.incompatible_note


def test_no_save_runs_anywhere():
    r = cc.recommend(cc.SAVE_NONE)
    assert "any flash cart" in r.incompatible_note
    assert len(r.alt) >= 3


def test_summary_is_readable():
    r = cc.recommend(cc.SAVE_EEPROM_64K)
    s = r.summary()
    assert "Save type:" in s
    assert "Recommended cart:" in s


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} cart-compat tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
