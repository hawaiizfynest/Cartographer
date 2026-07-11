"""
test_rompatch.py - tests for IPS/BPS/UPS patch application.

Patches are constructed by hand from each format spec so the applier is checked
against known-correct bytes, including the checksum-verification behaviour.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import rompatch  # noqa: E402


# ----- IPS ----------------------------------------------------------------- #

def _ips(records: list, truncate: int | None = None) -> bytes:
    out = bytearray(b"PATCH")
    for offset, data in records:
        out += offset.to_bytes(3, "big")
        out += len(data).to_bytes(2, "big")
        out += data
    out += b"EOF"
    if truncate is not None:
        out += truncate.to_bytes(3, "big")
    return bytes(out)


def _ips_rle(offset: int, run: int, value: int) -> bytes:
    out = bytearray(b"PATCH")
    out += offset.to_bytes(3, "big")
    out += (0).to_bytes(2, "big")           # size 0 => RLE
    out += run.to_bytes(2, "big")
    out += bytes([value])
    out += b"EOF"
    return bytes(out)


def test_ips_simple_patch():
    src = bytes(range(16))
    patch = _ips([(4, b"\xAA\xBB"), (10, b"\xFF")])
    res = rompatch.apply_patch(src, patch)
    expected = bytearray(src)
    expected[4:6] = b"\xAA\xBB"
    expected[10] = 0xFF
    assert res.data == bytes(expected)
    assert res.patch_format == "ips"


def test_ips_rle_run():
    src = bytes(32)
    patch = _ips_rle(8, 5, 0x77)
    res = rompatch.apply_patch(src, patch)
    expected = bytearray(src)
    for i in range(5):
        expected[8 + i] = 0x77
    assert res.data == bytes(expected)


def test_ips_extends_file():
    src = bytes(4)
    patch = _ips([(8, b"\x01\x02")])   # writes past the end
    res = rompatch.apply_patch(src, patch)
    assert len(res.data) == 10
    assert res.data[8:10] == b"\x01\x02"


def test_ips_truncate():
    src = bytes(range(20))
    patch = _ips([(0, b"\x00")], truncate=8)
    res = rompatch.apply_patch(src, patch)
    assert len(res.data) == 8


# ----- varint helper ------------------------------------------------------- #

def _encode_varint(number: int) -> bytes:
    out = bytearray()
    while True:
        x = number & 0x7F
        number >>= 7
        if number:
            out.append(x)
            number -= 1
        else:
            out.append(0x80 | x)
            return bytes(out)


def test_varint_roundtrip():
    for n in (0, 1, 127, 128, 255, 256, 16384, 1_000_000):
        enc = _encode_varint(n)
        val, pos = rompatch._read_varint(enc, 0)
        assert val == n, f"{n} != {val}"
        assert pos == len(enc)


# ----- BPS ----------------------------------------------------------------- #

def _bps(source: bytes, target: bytes, commands: bytes) -> bytes:
    body = bytearray(b"BPS1")
    body += _encode_varint(len(source))
    body += _encode_varint(len(target))
    body += _encode_varint(0)               # no metadata
    body += commands
    body += (zlib.crc32(source) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(target) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(body)


def _cmd(action: int, length: int) -> bytes:
    return _encode_varint(((length - 1) << 2) | action)


def test_bps_source_read_only():
    # target == source: a single SourceRead of the whole file.
    src = bytes(range(32))
    target = bytes(src)
    commands = _cmd(rompatch._BPS_SOURCE_READ, len(src))
    patch = _bps(src, target, commands)
    res = rompatch.apply_patch(src, patch)
    assert res.data == target
    assert res.source_crc_ok is True
    assert res.target_crc_ok is True


def test_bps_target_read_inserts_bytes():
    # Take first 4 bytes from source, then write 3 new bytes, then rest of source.
    src = bytes(range(10))
    new_bytes = b"\xAA\xBB\xCC"
    target = src[:4] + new_bytes + src[4:]
    commands = bytearray()
    commands += _cmd(rompatch._BPS_SOURCE_READ, 4)
    commands += _cmd(rompatch._BPS_TARGET_READ, 3) + new_bytes
    # after writing 3 target bytes, source read cursor is still at 4; use
    # SourceCopy to jump back to offset 4 and read the remaining 6 bytes.
    commands += _cmd(rompatch._BPS_SOURCE_COPY, 6) + _encode_varint(4 << 1)
    patch = _bps(src, target, bytes(commands))
    res = rompatch.apply_patch(src, patch)
    assert res.data == target
    assert res.target_crc_ok is True


def test_bps_wrong_source_flagged():
    src = bytes(range(32))
    target = bytes(src)
    commands = _cmd(rompatch._BPS_SOURCE_READ, len(src))
    patch = _bps(src, target, commands)
    # apply to a different source of the same length
    wrong = bytes([(b + 1) & 0xFF for b in src])
    res = rompatch.apply_patch(wrong, patch)
    assert res.source_crc_ok is False
    assert "wrong ROM" in res.message


def test_bps_corrupt_patch_rejected():
    src = bytes(range(16))
    patch = bytearray(_bps(src, src, _cmd(rompatch._BPS_SOURCE_READ, 16)))
    patch[-1] ^= 0xFF          # corrupt the patch CRC
    try:
        rompatch.apply_patch(src, bytes(patch))
        assert False, "should have raised"
    except rompatch.PatchError as e:
        assert "corrupt" in str(e)


# ----- UPS ----------------------------------------------------------------- #

def _ups(source: bytes, target: bytes, xor_body: bytes) -> bytes:
    body = bytearray(b"UPS1")
    body += _encode_varint(len(source))
    body += _encode_varint(len(target))
    body += xor_body
    body += (zlib.crc32(source) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(target) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(body)


def test_ups_simple_xor():
    src = bytes(16)
    target = bytearray(16)
    target[3] = 0x40
    target[4] = 0x55
    # UPS body: skip 3, then XOR bytes until a 0x00 terminator.
    # byte0 (offset3) = 0x40, byte1 (offset4) = 0x55, then terminator 0x00.
    xor_body = _encode_varint(3) + bytes([0x40, 0x55, 0x00])
    patch = _ups(src, bytes(target), xor_body)
    res = rompatch.apply_patch(src, patch)
    assert res.data == bytes(target)
    assert res.source_crc_ok is True
    assert res.target_crc_ok is True


# ----- format detection ---------------------------------------------------- #

def test_detect_format():
    assert rompatch.detect_format(b"PATCH....") == "ips"
    assert rompatch.detect_format(b"UPS1....") == "ups"
    assert rompatch.detect_format(b"BPS1....") == "bps"
    try:
        rompatch.detect_format(b"XXXX")
        assert False
    except rompatch.PatchError:
        pass


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} rompatch tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
