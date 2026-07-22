"""
test_savecompare.py - tests for the save file inspection and comparison logic.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import savecompare as sc  # noqa: E402


def _data(n=65536, seed=7):
    return bytes(((i * seed + 3) & 0xFF) for i in range(n))


def test_inspect_blank_ff_is_blank():
    info = sc.inspect_save(b"\xFF" * 8192)
    assert info.is_blank
    assert info.fill_byte == 0xFF
    assert info.size == 8192
    assert "no data" in info.summary()


def test_inspect_blank_zero_is_blank():
    info = sc.inspect_save(b"\x00" * 512)
    assert info.is_blank
    assert info.fill_byte == 0x00
    assert "no data" in info.summary()


def test_inspect_real_data_is_not_blank():
    info = sc.inspect_save(_data())
    assert not info.is_blank
    assert info.distinct_bytes > 1
    assert info.first_data_offset >= 0
    assert "holds data" in info.summary()


def test_inspect_empty_file():
    info = sc.inspect_save(b"")
    assert info.size == 0
    assert "Empty file" in info.summary()


def test_identical_saves_with_data():
    a = _data()
    d = sc.compare_saves(a, a)
    assert d.identical
    assert d.diff_count == 0
    assert "identical" in d.summary()
    assert "kept its save" in d.verdict()


def test_data_then_blank_reads_as_lost_contents():
    # The battery-died case: real data on the first read, blank on the second.
    a = _data()
    b = b"\xFF" * len(a)
    d = sc.compare_saves(a, b)
    assert not d.identical
    assert d.diff_count > 0
    assert d.first_diff >= 0
    v = d.verdict()
    assert "lost its contents" in v
    assert "battery" in v.lower()


def test_both_blank_reads_as_nothing_stored():
    blank = b"\xFF" * 4096
    d = sc.compare_saves(blank, blank)
    assert d.identical
    assert "blank" in d.verdict()


def test_blank_then_data_reads_as_written_between():
    a = b"\xFF" * 4096
    b = _data(4096)
    d = sc.compare_saves(a, b)
    assert not d.identical
    assert "written between" in d.verdict()


def test_partial_difference_is_located():
    a = bytearray(_data(4096))
    b = bytearray(a)
    b[1000:1050] = b"\x00" * 50
    d = sc.compare_saves(bytes(a), bytes(b))
    assert not d.identical
    assert d.diff_count == 50
    assert d.first_diff == 1000
    assert len(d.diff_regions) == 1
    assert d.diff_regions[0] == (1000, 50)
    # One run should read as singular, not "1 separate runs".
    assert "1 separate run." in d.summary()


def test_size_mismatch_is_reported():
    a = _data(4096)
    b = _data(8192)
    d = sc.compare_saves(a, b)
    assert not d.same_size
    assert "Different sizes" in d.summary()


def test_diff_regions_are_capped():
    # Alternating bytes produce many runs; the cap keeps the result readable.
    a = bytes([0x00, 0xFF] * 2048)
    b = bytes([0xFF, 0x00] * 2048)
    d = sc.compare_saves(a, b, max_regions=8)
    assert len(d.diff_regions) <= 8


def test_hex_preview_shows_offset_row():
    data = _data(256)
    out = sc.hex_preview(data, 0x40, 32)
    assert "00000040" in out
    # Two rows of 16 bytes for a 32-byte window.
    assert len(out.splitlines()) == 2


def test_hex_preview_handles_empty():
    assert sc.hex_preview(b"", 0) == ""
    assert sc.hex_preview(b"\x01\x02", -1) == ""


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} save-compare tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
