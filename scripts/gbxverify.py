"""
gbxverify.py - check the integrity of a dumped ROM file.

Runs the internal checks (Nintendo logo, header checksum, GB global checksum)
and reports CRC32 / SHA-1. If a known-good database is present it also reports
whether the dump matches a verified release.

Usage:
    python scripts/gbxverify.py mygame.gba
    python scripts/gbxverify.py mygame.gb

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import titles, verify  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python scripts/gbxverify.py <rom file>")
        return 1
    path = argv[0]
    try:
        rom = open(path, "rb").read()
    except OSError as exc:
        print(f"Cannot read {path}: {exc}")
        return 1

    is_gba = path.lower().endswith(".gba") or len(rom) >= 0x1000000
    result = (verify.verify_gba(rom, known_db=titles._SHA1) if is_gba
              else verify.verify_gb(rom, known_db=titles._SHA1))

    print(f"{os.path.basename(path)}  ({len(rom)} bytes, "
          f"{'GBA' if is_gba else 'GB/GBC'})\n")
    for c in result.checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.name}" + (f"  - {c.detail}" if c.detail else ""))
    print(f"\n  CRC32 {result.crc32}   SHA-1 {result.sha1}\n")
    print(result.summary())
    return 0 if result.all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
