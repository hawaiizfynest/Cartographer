"""
gbxdetect.py - probe a connected GBxCart RW / Flash Boy Cyclone.

Lists serial ports, connects, and prints the firmware/PCB/cart-mode the device
reports. This is the first thing to run against real hardware - it confirms the
protocol match and tells us exactly which revision the Cyclone clone presents.

Usage:
    python scripts/gbxdetect.py            # auto-pick a CH340 port
    python scripts/gbxdetect.py COM5       # or name the port explicitly

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import gbxcart as gx  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    ports = gx.list_serial_ports()
    print("Serial ports found:")
    if not ports:
        print("  (none) - is the device plugged in and the CH340 driver installed?")
    for p in ports:
        tag = "  <- CH340 (likely)" if p.is_ch340 else ""
        print(f"  {p.device}  {p.description}{tag}")
    print()

    if argv:
        target = argv[0]
    else:
        ch = [p.device for p in ports if p.is_ch340]
        target = ch[0] if ch else (ports[0].device if ports else "")
    if not target:
        print("No port to try. Pass one explicitly: python scripts/gbxdetect.py COM5")
        return 1

    print(f"Connecting to {target} ...")
    dev = gx.GBxCart()
    try:
        mode = dev.open(target)
    except gx.GBxCartError as exc:
        print(f"  FAILED: {exc}")
        return 1

    try:
        info = dev.identify()
    finally:
        pass

    print(f"  Connected at {dev.baud} baud.")
    print(f"  Firmware:  R{info.firmware}")
    print(f"  PCB:       {info.pcb_name}  (raw {info.pcb})")
    print(f"  Cart mode: {info.cart_mode_name}  (raw {info.cart_mode})")
    print()
    if info.looks_like_clone:
        print("  This matches a v1.1/v1.2 board - consistent with the Cyclone clone.")
    print("  Please send this output back so the read/flash paths can be matched "
          "to your exact revision.")
    dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
