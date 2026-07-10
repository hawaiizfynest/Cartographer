"""
gbxdump.py - dump a cartridge's ROM and/or save over a connected GBxCart /
Cyclone. Non-destructive: only reads from the cart.

Usage:
    python scripts/gbxdump.py info                 # read + show cart header
    python scripts/gbxdump.py rom  out.gba         # dump ROM
    python scripts/gbxdump.py save out.sav         # dump save (SRAM)
    python scripts/gbxdump.py both name            # -> name.gba + name.sav

    Options:
      --port COM4     serial port (auto-detects a CH340 port otherwise)
      --mode gba|gb   force cart type (else uses the device's toggle position)
      --size N        ROM size in bytes (else auto-detected / from header)

Set the device's physical GBA/GBC voltage switch to match the cart BEFORE
running. GBA carts -> GBA side (3.3V), GB/GBC carts -> GBC side (5V).

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import gb_header as gbh  # noqa: E402
from cartographer import gbxcart as gx  # noqa: E402


def _bar(cur: int, total: int) -> None:
    if total <= 0:
        return
    pct = int(cur * 100 / total)
    width = 32
    fill = int(width * cur / total)
    sys.stdout.write(f"\r  [{'#' * fill}{'.' * (width - fill)}] {pct:3d}%  "
                     f"{cur // 1024}/{total // 1024} KB")
    sys.stdout.flush()
    if cur >= total:
        sys.stdout.write("\n")


def _pick_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ch = [p.device for p in gx.list_serial_ports() if p.is_ch340]
    if ch:
        return ch[0]
    ports = gx.list_serial_ports()
    return ports[0].device if ports else ""


def _describe_gba(hdr: bytes) -> str:
    title = hdr[0xA0:0xAC].decode("ascii", "replace").strip("\x00")
    code = hdr[0xAC:0xB0].decode("ascii", "replace")
    return f"GBA cart: {title} [{code}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dump a cart via GBxCart / Cyclone")
    ap.add_argument("action", choices=["info", "rom", "save", "both"])
    ap.add_argument("output", nargs="?", help="output file (or basename for 'both')")
    ap.add_argument("--port")
    ap.add_argument("--mode", choices=["gba", "gb"])
    ap.add_argument("--size", type=lambda s: int(s, 0))
    args = ap.parse_args(argv)

    if args.action in ("rom", "save") and not args.output:
        print("An output file is required for that action.")
        return 1
    if args.action == "both" and not args.output:
        print("A basename is required for 'both'.")
        return 1

    port = _pick_port(args.port)
    if not port:
        print("No serial port found. Is the device plugged in?")
        return 1

    dev = gx.GBxCart()
    try:
        mode = dev.open(port)
    except gx.GBxCartError as exc:
        print(f"Connect failed on {port}: {exc}")
        return 1

    info = dev.identify()
    fast = dev.check_fast_read()
    print(f"Connected on {port}: firmware R{info.firmware}, PCB {info.pcb_name}, "
          f"toggle reads {info.cart_mode_name}.")
    print(f"Fast read: {'enabled' if fast else 'not available (using safe mode)'}.")

    is_gba = (args.mode == "gba") or (args.mode is None and mode == gx.GBA_MODE)
    print(f"Treating cart as {'GBA' if is_gba else 'GB/GBC'}.\n")

    try:
        if is_gba:
            return _run_gba(dev, args)
        return _run_gb(dev, args)
    except gx.GBxCartError as exc:
        print(f"\nDevice error: {exc}")
        return 1
    finally:
        dev.close()


def _run_gba(dev: gx.GBxCart, args) -> int:
    hdr = dev.read_gba_header()
    print("  " + _describe_gba(hdr))

    if args.action == "info":
        return 0

    if args.action in ("rom", "both"):
        size = args.size
        if not size:
            print("  Detecting ROM size ...")
            size = dev.detect_gba_rom_size()
        print(f"  Dumping {size // (1024*1024)} MB ROM ...")
        out = args.output if args.action == "rom" else args.output + ".gba"
        with open(out, "wb") as f:
            dev.read_gba_rom(f, size, progress=_bar)
        print(f"  Saved {out}")

    if args.action in ("save", "both"):
        out = args.output if args.action == "save" else args.output + ".sav"
        # Detect the save type from the ROM's embedded save-id string.
        save_id = _detect_gba_save_id(dev)
        kind = gx.save_kind_from_id(save_id)
        if kind == gx.SAVE_NONE:
            print("  Could not detect the save type from the ROM. Defaulting to "
                  "32 KB SRAM; use a full dump + known save size if this is wrong.")
            with open(out, "wb") as f:
                dev.read_gba_sram(f, 0x8000, progress=_bar)
        else:
            total = gx.SAVE_LAYOUT[kind][0]
            print(f"  Save type: {save_id} -> {kind} ({total // 1024 or total} "
                  f"{'KB' if total >= 1024 else 'bytes'}). Dumping ...")
            with open(out, "wb") as f:
                dev.read_gba_save(f, kind, progress=_bar)
        print(f"  Saved {out}")
    return 0


def _detect_gba_save_id(dev: gx.GBxCart) -> str:
    """Read a chunk of the ROM and scan for the SDK save-type signature string."""
    import io
    buf = io.BytesIO()
    # 1 MB is plenty to catch the save-id string in the code section.
    try:
        dev.read_gba_rom(buf, 1 * 1024 * 1024)
    except gx.GBxCartError:
        pass
    data = buf.getvalue()
    for tag in (b"EEPROM_V", b"FLASH1M_V", b"FLASH512_V", b"FLASH_V", b"SRAM_V"):
        idx = data.find(tag)
        if idx >= 0:
            end = idx
            while end < len(data) and 32 <= data[end] < 127:
                end += 1
            return data[idx:end].decode("ascii", "replace")
    return ""


def _run_gb(dev: gx.GBxCart, args) -> int:
    hdr = dev.read_gb_header()
    parsed = gbh.parse_header(hdr) if len(hdr) >= 0x150 else None
    if parsed:
        print(f"  GB cart: {parsed.title or '(no title)'}  {parsed.cart_type}  "
              f"ROM {parsed.rom_size}  RAM {parsed.ram_size}")
    ctype = hdr[0x147] if len(hdr) > 0x147 else 0
    title = hdr[0x134:0x143].decode("ascii", "replace").strip("\x00")

    if args.action == "info":
        return 0

    if args.action in ("rom", "both"):
        size = args.size or (parsed.rom_pages * 0x4000 if parsed and parsed.rom_pages
                             else 0x8000)
        print(f"  Dumping {size // 1024} KB ROM ...")
        out = args.output if args.action == "rom" else args.output + ".gb"
        with open(out, "wb") as f:
            dev.read_gb_rom(f, size, cart_type=ctype, title=title, progress=_bar)
        print(f"  Saved {out}")

    if args.action in ("save", "both"):
        ram_bytes = 0
        rsize = 0
        if parsed and parsed.ram_size_byte in gbh.RAM_SIZES:
            ram_bytes = gbh.RAM_SIZES[parsed.ram_size_byte][2]
            rsize = parsed.ram_size_byte
        if ram_bytes == 0:
            print("  No save RAM detected on this cart; skipping save dump.")
            return 0
        out = args.output if args.action == "save" else args.output + ".sav"
        print(f"  Dumping {ram_bytes // 1024 or ram_bytes} "
              f"{'KB' if ram_bytes >= 1024 else 'bytes'} save ...")
        with open(out, "wb") as f:
            dev.read_gb_ram(f, ram_bytes, cart_type=ctype, ram_size_code=rsize,
                            progress=_bar)
        print(f"  Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
