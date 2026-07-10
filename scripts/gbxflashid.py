"""
gbxflashid.py - identify the flash chip on a GBA cart (repro/flash cart).

Runs the non-destructive flash-ID probe: it only enters and exits the chip's
read-ID mode and never erases or writes. A retail (mask ROM) cart is reported
as non-flashable. This is the gate before any flashing: it tells us the chip's
command set / write method.

Usage:
    python scripts/gbxflashid.py            # auto-pick a CH340 port
    python scripts/gbxflashid.py COM4       # or name the port

Set the device's physical switch to the GBA side (3.3V) with the cart inserted
before running.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import flash_db  # noqa: E402
from cartographer import gbxcart as gx  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if argv:
        target = argv[0]
    else:
        ch = [p.device for p in gx.list_serial_ports() if p.is_ch340]
        ports = gx.list_serial_ports()
        target = ch[0] if ch else (ports[0].device if ports else "")
    if not target:
        print("No serial port found. Is the device plugged in?")
        return 1

    dev = gx.GBxCart()
    try:
        mode = dev.open(target)
    except gx.GBxCartError as exc:
        print(f"Connect failed on {target}: {exc}")
        return 1

    info = dev.identify()
    print(f"Connected on {target}: firmware R{info.firmware}, PCB {info.pcb_name}.")
    if mode != gx.GBA_MODE:
        print("\nThe device's toggle is NOT on the GBA side. Set the switch to "
              "GBA (3.3V) with a GBA cart inserted, then re-run.")
        dev.close()
        return 1

    print("Probing flash ID (non-destructive) ...\n")
    try:
        probe = dev.gba_flash_id_probe()
    except gx.GBxCartError as exc:
        print(f"Probe failed: {exc}")
        dev.close()
        return 1
    finally:
        try:
            dev.gba_flash_reset()
        except Exception:
            pass

    for name, data in probe.items():
        hexbytes = " ".join(f"{b:02X}" for b in data[:8])
        print(f"  {name:9}: {hexbytes}")
    print()

    baseline = probe.get("baseline", b"")
    if not baseline or baseline.count(0) == len(baseline):
        print("The cart read back as all zeros, which means no cartridge is "
              "being read at all.\n"
              "  - Is a cartridge fully seated in the slot?\n"
              "  - Is the physical switch on the GBA side (3.3V)?\n"
              "Re-seat the cart, power-cycle the device, and try again.")
        dev.close()
        return 1

    result = flash_db.interpret(probe)
    print(result.summary())
    if result.is_flashable:
        print(f"\n  Manufacturer: {result.manufacturer} "
              f"(0x{result.manufacturer_id:02X})")
        print(f"  Device id:    0x{result.device_id:04X}")
        if result.is_known_chip:
            print(f"  Chip:         {result.chip_label}")
        else:
            print("  Chip:         not in the database.")
            print("\n  Chips known to work with GBxCart-family flashers:")
            names = flash_db.supported_chip_names()
            for i in range(0, len(names), 3):
                print("    " + "  ".join(f"{n:<22}" for n in names[i:i+3]))
            print("  Chips known NOT to work: markings containing "
                  + " or ".join(flash_db.KNOWN_BAD_MARKINGS))
        print(f"\nThis chip can be targeted for flashing once the write path for "
              f"the '{result.write_method}' method is enabled.")
    else:
        print("\nThe cart read valid ROM data, so the device and cart are fine - "
              "the flash chip simply did not respond to any known command set.\n"
              "  - If this is a retail cartridge, that is expected: it is a mask "
              "ROM and cannot be written. Dump-only.\n"
              "  - If this is a repro/flash cart, try re-seating it and power-"
              "cycling the device, then re-run. Some CPLD-based carts need a "
              "reset before they answer.")

    dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
