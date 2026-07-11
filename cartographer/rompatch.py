"""
rompatch.py - apply IPS, BPS and UPS ROM patches.

These formats let you turn a clean base ROM into a ROM hack (translations, fan
edits, bug fixes) without ever distributing copyrighted game data - the patch
holds only the differences.

  IPS  - oldest, simplest. 3-byte offsets (16 MB ceiling), optional RLE runs, no
         checksums, so it patches blindly.
  UPS  - variable-length offsets, CRC32 of source/target/patch for verification.
  BPS  - delta-encoded, smallest patches, CRC32 verification. The modern choice.

Applying a patch never touches the base ROM in place; it returns a new bytes
object. BPS and UPS verify the source and target CRC32; a mismatch means you have
the wrong base ROM, and that's reported rather than producing a broken game.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass


class PatchError(Exception):
    pass


@dataclass
class PatchResult:
    data: bytes
    patch_format: str
    source_crc_ok: bool | None = None    # None if the format has no checksum
    target_crc_ok: bool | None = None
    message: str = ""


def detect_format(patch: bytes) -> str:
    if patch[:5] == b"PATCH":
        return "ips"
    if patch[:4] == b"UPS1":
        return "ups"
    if patch[:4] == b"BPS1":
        return "bps"
    raise PatchError("Unrecognised patch file (not IPS, BPS or UPS).")


def apply_patch(source: bytes, patch: bytes) -> PatchResult:
    """Apply a patch, auto-detecting its format."""
    fmt = detect_format(patch)
    if fmt == "ips":
        return _apply_ips(source, patch)
    if fmt == "ups":
        return _apply_ups(source, patch)
    return _apply_bps(source, patch)


# --------------------------------------------------------------------------- #
# IPS
# --------------------------------------------------------------------------- #

def _apply_ips(source: bytes, patch: bytes) -> PatchResult:
    if patch[:5] != b"PATCH":
        raise PatchError("Not an IPS patch.")
    out = bytearray(source)
    pos = 5
    end = len(patch)
    while pos < end:
        if patch[pos:pos + 3] == b"EOF":
            pos += 3
            # optional truncate extension: 3-byte new length
            if pos + 3 <= end:
                new_len = int.from_bytes(patch[pos:pos + 3], "big")
                del out[new_len:]
            break
        if pos + 5 > end:
            raise PatchError("Truncated IPS record.")
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5
        if size == 0:
            # RLE run: 2-byte run length, 1-byte value
            if pos + 3 > end:
                raise PatchError("Truncated IPS RLE record.")
            run = int.from_bytes(patch[pos:pos + 2], "big")
            value = patch[pos + 2]
            pos += 3
            _ensure_len(out, offset + run)
            for i in range(run):
                out[offset + i] = value
        else:
            if pos + size > end:
                raise PatchError("Truncated IPS data record.")
            _ensure_len(out, offset + size)
            out[offset:offset + size] = patch[pos:pos + size]
            pos += size
    return PatchResult(bytes(out), "ips",
                       message="IPS applied. Note: IPS has no checksum, so it "
                               "can't confirm you used the right base ROM.")


def _ensure_len(buf: bytearray, needed: int) -> None:
    if len(buf) < needed:
        buf.extend(b"\x00" * (needed - len(buf)))


# --------------------------------------------------------------------------- #
# UPS
# --------------------------------------------------------------------------- #

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a UPS/BPS variable-length integer. Returns (value, new_pos)."""
    value = 0
    shift = 1
    while True:
        if pos >= len(data):
            raise PatchError("Truncated variable-length integer.")
        b = data[pos]
        pos += 1
        value += (b & 0x7F) * shift
        if b & 0x80:
            break
        shift <<= 7
        value += shift
    return value, pos


def _apply_ups(source: bytes, patch: bytes) -> PatchResult:
    if patch[:4] != b"UPS1":
        raise PatchError("Not a UPS patch.")
    if len(patch) < 4 + 12:  # header + 3 trailing CRCs
        raise PatchError("UPS patch too short.")
    body_end = len(patch) - 12
    patch_crc_stored = int.from_bytes(patch[len(patch) - 4:], "little")
    if zlib.crc32(patch[:len(patch) - 4]) & 0xFFFFFFFF != patch_crc_stored:
        raise PatchError("UPS patch file is corrupt (patch CRC mismatch).")

    src_crc = int.from_bytes(patch[body_end:body_end + 4], "little")
    dst_crc = int.from_bytes(patch[body_end + 4:body_end + 8], "little")

    pos = 4
    src_size, pos = _read_varint(patch, pos)
    dst_size, pos = _read_varint(patch, pos)

    source_ok = (zlib.crc32(source) & 0xFFFFFFFF) == src_crc
    out = bytearray(source)
    if len(out) < dst_size:
        out.extend(b"\x00" * (dst_size - len(out)))

    out_pos = 0
    while pos < body_end:
        skip, pos = _read_varint(patch, pos)
        out_pos += skip
        # XOR bytes until a terminating 0x00
        while pos < body_end:
            x = patch[pos]
            pos += 1
            if out_pos < len(out):
                out[out_pos] ^= x
            else:
                out.append(x)
            out_pos += 1
            if x == 0:
                break
    del out[dst_size:]

    target_ok = (zlib.crc32(bytes(out)) & 0xFFFFFFFF) == dst_crc
    msg = "UPS applied."
    if not source_ok:
        msg += " Warning: the base ROM's checksum doesn't match what the patch " \
               "expects - you may have the wrong ROM."
    if not target_ok:
        msg += " Warning: the result's checksum doesn't match the patch's target."
    return PatchResult(bytes(out), "ups", source_ok, target_ok, msg)


# --------------------------------------------------------------------------- #
# BPS
# --------------------------------------------------------------------------- #

_BPS_SOURCE_READ = 0
_BPS_TARGET_READ = 1
_BPS_SOURCE_COPY = 2
_BPS_TARGET_COPY = 3


def _apply_bps(source: bytes, patch: bytes) -> PatchResult:
    if patch[:4] != b"BPS1":
        raise PatchError("Not a BPS patch.")
    if len(patch) < 4 + 12:
        raise PatchError("BPS patch too short.")

    # Verify the patch's own CRC before trusting anything in it.
    patch_crc_stored = int.from_bytes(patch[len(patch) - 4:], "little")
    if zlib.crc32(patch[:len(patch) - 4]) & 0xFFFFFFFF != patch_crc_stored:
        raise PatchError("BPS patch file is corrupt (patch CRC mismatch).")

    src_crc = int.from_bytes(patch[len(patch) - 12:len(patch) - 8], "little")
    dst_crc = int.from_bytes(patch[len(patch) - 8:len(patch) - 4], "little")

    pos = 4
    src_size, pos = _read_varint(patch, pos)
    dst_size, pos = _read_varint(patch, pos)
    meta_size, pos = _read_varint(patch, pos)
    pos += meta_size  # skip metadata

    source_ok = (zlib.crc32(source) & 0xFFFFFFFF) == src_crc
    if len(source) != src_size:
        # Not fatal on its own, but usually means the wrong base ROM.
        source_ok = False

    out = bytearray(dst_size)
    out_pos = 0
    src_rel = 0
    tgt_rel = 0
    commands_end = len(patch) - 12

    while pos < commands_end:
        data, pos = _read_varint(patch, pos)
        action = data & 3
        length = (data >> 2) + 1
        if action == _BPS_SOURCE_READ:
            for _ in range(length):
                out[out_pos] = source[out_pos]
                out_pos += 1
        elif action == _BPS_TARGET_READ:
            for _ in range(length):
                out[out_pos] = patch[pos]
                pos += 1
                out_pos += 1
        elif action == _BPS_SOURCE_COPY:
            off, pos = _read_varint(patch, pos)
            src_rel += (-(off >> 1) if (off & 1) else (off >> 1))
            for _ in range(length):
                out[out_pos] = source[src_rel]
                src_rel += 1
                out_pos += 1
        else:  # TARGET_COPY
            off, pos = _read_varint(patch, pos)
            tgt_rel += (-(off >> 1) if (off & 1) else (off >> 1))
            for _ in range(length):
                out[out_pos] = out[tgt_rel]
                tgt_rel += 1
                out_pos += 1

    target_ok = (zlib.crc32(bytes(out)) & 0xFFFFFFFF) == dst_crc
    msg = "BPS applied."
    if not source_ok:
        msg += " Warning: the base ROM doesn't match what this patch was built " \
               "for - you probably have the wrong ROM version."
    if not target_ok:
        msg += " Warning: the patched result failed its checksum."
    return PatchResult(bytes(out), "bps", source_ok, target_ok, msg)
