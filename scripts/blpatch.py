"""
blpatch.py - command-line GBA batteryless save preparer.

Usage:
    python scripts/blpatch.py [--keypad] [--no-sram] <rom.gba> [output.gba]

By default the ROM is SRAM-patched first when a Flash/EEPROM save type is
detected, then batteryless-patched. Use --no-sram to skip the SRAM step, and
--keypad for keypad flush mode (L+R+Start+Select) instead of auto.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import bl_patcher as blp  # noqa: E402
from cartographer import pipeline  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GBA batteryless save preparer")
    ap.add_argument("rom", help="input .gba ROM")
    ap.add_argument("output", nargs="?", help="output path (optional)")
    ap.add_argument("--keypad", action="store_true",
                    help="keypad flush mode instead of auto")
    ap.add_argument("--no-sram", action="store_true",
                    help="skip the SRAM patch step")
    args = ap.parse_args(argv)

    try:
        data = open(args.rom, "rb").read()
    except OSError as exc:
        print(f"Cannot read {args.rom}: {exc}")
        return 1

    mode = blp.MODE_KEYPAD if args.keypad else blp.MODE_AUTO
    try:
        result = pipeline.prepare_for_batteryless(
            data, mode, sram_patch=not args.no_sram)
    except Exception as exc:
        print(f"Failed: {exc}")
        return 1

    out = args.output or (os.path.splitext(args.rom)[0] + result.suffix)
    try:
        with open(out, "wb") as f:
            f.write(result.data)
    except OSError as exc:
        print(f"Cannot write {out}: {exc}")
        return 1

    for line in result.log:
        print(line)
    print(f"Done ({'keypad' if mode else 'auto'} mode, "
          f"{result.bl.save_size // 1024} KB save). Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
