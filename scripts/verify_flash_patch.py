"""
verify_flash_patch.py - Check the ported flash patcher against the real tool.

A port of a patcher is only worth anything if it produces exactly what the
original produced. Give this the ROM you fed to gba-flash.exe and the file it
wrote, and it patches the input itself and compares the two byte for byte.

    python scripts/verify_flash_patch.py SMA2_sram.gba SMA2_sram_flash512.gba

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import flash_patcher as fp  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    with open(argv[1], "rb") as handle:
        original = handle.read()
    with open(argv[2], "rb") as handle:
        reference = handle.read()

    try:
        result = fp.patch_rom(original)
    except fp.FlashPatchError as exc:
        print(f"The port refused the ROM: {exc}")
        return 1

    print(f"payload installed at 0x{result.payload_base:06X}, "
          f"{fp.PAYLOAD_LEN} bytes")
    if result.eeprom_meta_addr:
        print(f"eeprom geometry address 0x{result.eeprom_meta_addr:07X}")
    for name, off, target in result.hooks:
        where = f" -> 0x{target:08X}" if target else ""
        print(f"  {name} at 0x{off:X}{where}")
    print()

    mine, theirs = result.data, reference
    print(f"port     {len(mine)} bytes, sha256 "
          f"{hashlib.sha256(mine).hexdigest()}")
    print(f"gba-flash {len(theirs)} bytes, sha256 "
          f"{hashlib.sha256(theirs).hexdigest()}")
    print()

    if mine == theirs:
        print("IDENTICAL. The port produces exactly what the real tool does.")
        return 0

    if len(mine) != len(theirs):
        print(f"SIZE DIFFERS by {len(mine) - len(theirs)} bytes.")
    diffs = [i for i in range(min(len(mine), len(theirs)))
             if mine[i] != theirs[i]]
    print(f"DIFFERENT at {len(diffs)} byte(s).")
    for off in diffs[:24]:
        print(f"  0x{off:06X}  port {mine[off]:02X}  gba-flash {theirs[off]:02X}")
    if len(diffs) > 24:
        print(f"  ... and {len(diffs) - 24} more")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
