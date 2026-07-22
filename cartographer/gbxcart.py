"""
gbxcart.py - Device layer for GBxCart RW (and clones such as the Flash Boy
Cyclone) over a CH340 USB-serial link.

This implements the insideGadgets GBxCart RW serial command protocol used by
v1.1/v1.2/v1.3 boards: 1,000,000 baud 8N1, single-character commands, addresses
sent as "<cmd><hex>\\0", and bulk reads streamed in 64-byte blocks gated by a
'1' (continue) / '0' (stop) handshake.

Scope of this module: connect + identify (firmware/PCB/cart mode), voltage
selection, and the read/dump paths for GB/GBC ROM + save and GBA ROM + SRAM.
Flash writing (the cart-specific flash command database) is a later phase.

Protocol/command set: insideGadgets GBxCart RW (CC BY-NC-SA 4.0). This is an
independent, compatible client implementation of that documented command set;
no insideGadgets code is included. Credit to insideGadgets (Alex).
Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import BinaryIO, Callable, Optional

import serial
from serial.tools import list_ports

BAUD_PRIMARY = 1_000_000
BAUD_FALLBACK = 1_700_000

# Cartridge modes
GB_MODE = 1
GBA_MODE = 2

# Common commands
CART_MODE = "C"
SET_START_ADDRESS = "A"
READ_FIRMWARE_VERSION = "V"
READ_PCB_VERSION = "h"
VOLTAGE_3_3V = "3"
VOLTAGE_5V = "5"
CART_PWR_ON = "/"
CART_PWR_OFF = "."
GB_CART_MODE = "G"
GBA_CART_MODE = "g"
RESET_AVR = "*"
FAST_READ_CHECK = "+"

# GB/GBC
READ_ROM_RAM = "R"
READ_ROM_4000H = "Q"          # GB fast read; device re-armed every 0x4000
WRITE_RAM = "W"
SET_BANK = "B"

# GBA
GBA_READ_ROM = "r"
GBA_READ_ROM_8000H = "Z"      # GBA fast read; device re-armed every 0x8000
GBA_READ_SRAM = "m"
GBA_WRITE_SRAM = "w"
GBA_WRITE_EEPROM = "p"
GBA_WRITE_ONE_BYTE_SRAM = "o"
GBA_FLASH_READ_ID = "i"
GBA_FLASH_WRITE_BYTE = "b"
# Firmware block-write commands: hand a raw data block to the device and it does
# the per-word unlock-and-program internally (fast). 'f' is the plain AMD 256-byte
# block; 't' is the same but for D0/D1-swapped carts. Which one a cart needs
# depends on whether its CFI magic read as "QRY" (not swapped -> 'f') or "RQZ"
# (swapped -> 't').
GBA_FLASH_WRITE_256BYTE = "f"
GBA_FLASH_WRITE_256BYTE_SWAPPED = "t"
# Buffered block-write commands: like the block write above, but the firmware
# uses the chip's hardware write buffer, which can be faster still. 'c' is the
# plain buffered command; 'd' is the D0/D1-swapped variant. Sent identically to
# the block commands (command char + 256 bytes + ack).
GBA_FLASH_WRITE_BUFFERED_256BYTE = "c"
GBA_FLASH_WRITE_BUFFERED_256BYTE_SWAPPED = "d"
GBA_FLASH_WRITE_ATMEL = "a"
GBA_FLASH_4K_SECTOR_ERASE = "s"
GBA_FLASH_CART_WRITE_BYTE = "n"   # write a command byte to the GBA flash bus
GBA_SET_EEPROM_SIZE = "S"
GBA_READ_EEPROM = "e"
GBA_FLASH_SET_BANK = "k"

# EEPROM size selectors (device-side)
EEPROM_NONE = 0
EEPROM_4KBIT = 1
EEPROM_64KBIT = 2

# GBA save kinds
SAVE_NONE = "none"
SAVE_EEPROM_4K = "eeprom_4k"      # 512 bytes
SAVE_EEPROM_64K = "eeprom_64k"    # 8 KB
SAVE_SRAM_256K = "sram_256k"      # 32 KB
SAVE_FLASH_512K = "flash_512k"    # 64 KB
SAVE_FLASH_1M = "flash_1m"        # 128 KB, two banks

# save kind -> (total bytes, banks, bytes per bank)
SAVE_LAYOUT = {
    SAVE_EEPROM_4K: (512, 1, 512),
    SAVE_EEPROM_64K: (8192, 1, 8192),
    SAVE_SRAM_256K: (32768, 1, 32768),
    SAVE_FLASH_512K: (65536, 1, 65536),
    SAVE_FLASH_1M: (131072, 2, 65536),
}


def save_kind_from_id(save_id: str) -> str:
    """Map the save-type string found in a GBA ROM to a save kind."""
    if not save_id:
        return SAVE_NONE
    if save_id.startswith("EEPROM_V"):
        # 4Kbit vs 64Kbit isn't encoded in the string; 64Kbit is the safe default
        # for larger games. Callers may override.
        return SAVE_EEPROM_64K
    if save_id.startswith("SRAM_"):
        return SAVE_SRAM_256K
    if save_id.startswith("FLASH1M_"):
        return SAVE_FLASH_1M
    if save_id.startswith("FLASH512_") or save_id.startswith("FLASH_"):
        return SAVE_FLASH_512K
    return SAVE_NONE


# GB/GBC RAM size code (header 0x149) -> number of 8 KB banks.
_GB_RAM_BANKS = {0: 0, 1: 1, 2: 1, 3: 4, 4: 16, 5: 8}


def gb_ram_layout(cart_type: int, ram_size_code: int,
                  fallback_size: int = 0) -> tuple[int, int]:
    """Return (bank_count, ram_end_address) for a GB/GBC cart, reproducing
    insideGadgets' rules:
      - MBC2 (cart type 6): 1 bank, end 0xA1FF (512 nibble bytes)
      - RAM size 1 (2 KB):  1 bank, end 0xA7FF
      - RAM size >= 2:      N banks, end 0xBFFF (full 8 KB banks)
    fallback_size (bytes) is only used when the RAM size code is unknown/zero.
    """
    if cart_type == 6:                       # MBC2
        return 1, 0xA1FF
    if ram_size_code == 1:                    # 2 KB
        return 1, 0xA7FF
    if ram_size_code >= 2:
        return _GB_RAM_BANKS.get(ram_size_code, 1), 0xBFFF
    # Unknown code: derive banks from a known byte size if we have one.
    if fallback_size >= 0x2000:
        return max(1, fallback_size // 0x2000), 0xBFFF
    if fallback_size > 0:
        return 1, 0xA000 + fallback_size - 1
    return 0, 0xA000


CONT = b"1"   # continue streaming
STOP = b"0"   # stop streaming

BLOCK = 64    # bytes per streamed block

PCB_NAMES = {1: "v1.0", 2: "v1.1", 3: "v1.2", 4: "v1.3", 5: "v1.4",
             90: "GBxMas", 100: "Mini RW"}

# CH340/CH341 USB ids (the GBxCart RW / Cyclone interface chip)
_CH340_VIDPID = {(0x1A86, 0x7523), (0x1A86, 0x5523), (0x1A86, 0x7522)}


ProgressFn = Callable[[int, int], None]
LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def _noop(*_a, **_k):  # pragma: no cover
    return None


def _never() -> bool:  # pragma: no cover
    return False


def format_duration(seconds: float) -> str:
    """Human-friendly elapsed time, e.g. '4 min 48 sec' or '9 sec'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    mins, secs = divmod(seconds, 60)
    if mins < 60:
        return f"{mins} min {secs} sec" if secs else f"{mins} min"
    hrs, mins = divmod(mins, 60)
    return f"{hrs} hr {mins} min"


@dataclass
class PortInfo:
    device: str
    description: str
    is_ch340: bool


def list_serial_ports() -> list[PortInfo]:
    out: list[PortInfo] = []
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        is_ch340 = (vid, pid) in _CH340_VIDPID or (vid == 0x1A86)
        out.append(PortInfo(p.device, p.description or "", is_ch340))
    out.sort(key=lambda x: (not x.is_ch340, x.device))
    return out


@dataclass
class DeviceInfo:
    firmware: int
    pcb: int
    cart_mode: int

    @property
    def pcb_name(self) -> str:
        return PCB_NAMES.get(self.pcb, f"unknown ({self.pcb})")

    @property
    def cart_mode_name(self) -> str:
        return {GB_MODE: "GB/GBC", GBA_MODE: "GBA"}.get(self.cart_mode, "none")

    @property
    def looks_like_clone(self) -> bool:
        # v1.1/v1.2 PCB report is what the Cyclone clones present
        return self.pcb in (2, 3)


class GBxCartError(Exception):
    pass


class GBxCart:
    """Talks to a GBxCart RW / Cyclone over serial."""

    def __init__(self) -> None:
        self.ser: Optional[serial.Serial] = None
        self.baud = BAUD_PRIMARY
        self.fast_read = False

    def check_fast_read(self) -> bool:
        """Ask the device (firmware R19+) whether fast read is supported.
        Sends '+' and expects a short burst of data back within a timeout."""
        if not self.is_open:
            return False
        s = self.ser
        assert s is not None
        s.reset_input_buffer()
        self.set_mode(FAST_READ_CHECK)
        got = 0
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and got < 64:
            chunk = s.read(64 - got)
            if chunk:
                got += len(chunk)
        s.reset_input_buffer()
        self.fast_read = got > 0
        return self.fast_read

    # -- connection --------------------------------------------------------- #

    def open(self, port: str) -> int:
        """Open and handshake. Returns the detected cart mode (GB_MODE/GBA_MODE).
        Tries 1M then 1.7M baud, matching the device's auto-baud behaviour."""
        for baud in (BAUD_PRIMARY, BAUD_FALLBACK):
            self.close()
            self.ser = serial.Serial(port, baud, timeout=0.2, write_timeout=2)
            self.baud = baud
            self.set_mode("0")          # clear any half-finished command
            self.ser.reset_input_buffer()
            # The device answers within milliseconds when present; a short
            # timeout keeps a wrong-baud attempt from stalling the UI.
            mode = self.request_value(CART_MODE, timeout=0.6)
            if mode in (GB_MODE, GBA_MODE):
                return mode
        self.close()
        raise GBxCartError(
            "No valid response on this port at 1M or 1.7M baud. If this is the "
            "Cyclone, confirm the CH340 driver is installed and the cart/voltage "
            "switch are set, then retry.")

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _require(self) -> serial.Serial:
        if not self.is_open:
            raise GBxCartError("Device is not connected.")
        assert self.ser is not None
        return self.ser

    # -- low-level protocol ------------------------------------------------- #

    def set_mode(self, command: str) -> None:
        """Send a single command character."""
        s = self._require()
        s.write(command.encode("latin-1"))
        s.flush()
        time.sleep(0.001)

    def set_number(self, number: int, command: str) -> None:
        """Send '<command><hex-number>\\0'."""
        s = self._require()
        s.write(f"{command}{number:x}".encode("latin-1") + b"\x00")
        s.flush()
        time.sleep(0.001)

    def set_bank(self, address: int, bank: int) -> None:
        """Bank switch: address as hex, then bank as decimal (per protocol)."""
        s = self._require()
        s.write(f"{SET_BANK}{address:x}".encode("latin-1") + b"\x00")
        s.flush()
        time.sleep(0.005)
        s.write(f"{SET_BANK}{bank:d}".encode("latin-1") + b"\x00")
        s.flush()
        time.sleep(0.005)

    def request_value(self, command: str, timeout: float = 2.0) -> int:
        """Send a command and read a single response byte (0 if none)."""
        s = self._require()
        self.set_mode(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            b = s.read(1)
            if b:
                return b[0]
        return 0

    def _read_stream_fast(self, out: BinaryIO, total: int, rearm_cmd: str,
                          rearm_interval: int, progress: ProgressFn,
                          cancel: CancelFn) -> int:
        """Continuous fast read: the device streams without a per-block ack;
        we just re-arm it at each `rearm_interval` boundary. This removes the
        stop/continue round-trip latency and is the safe firmware fast path.
        Bytes on the wire are identical to the slow path - only the flow
        control differs."""
        s = self._require()
        got = 0
        self.set_mode(rearm_cmd)
        idle = 0
        while got < total:
            if cancel():
                s.write(STOP)
                s.flush()
                raise GBxCartError("Canceled.")
            chunk = s.read(min(64, total - got))
            if chunk:
                out.write(chunk)
                got += len(chunk)
                idle = 0
                progress(got, total)
                # re-arm the device at each interval boundary (not at the end)
                if got % rearm_interval == 0 and got != total:
                    self.set_mode(rearm_cmd)
            else:
                idle += 1
                if idle >= 200:      # ~ many seconds with no data -> give up
                    s.write(STOP)
                    s.flush()
                    raise GBxCartError(
                        f"Fast read stalled at {got} of {total} bytes.")
        s.write(STOP)
        s.flush()
        return got

    def write_block(self, command: str, data: bytes) -> None:
        """Send a write command followed by its data payload in one buffer,
        then wait for the device's ack. Mirrors com_write_bytes_from_file."""
        s = self._require()
        s.write(command.encode("latin-1") + data)
        s.flush()
        self._wait_for_ack()

    def _read_stream(self, out: BinaryIO, total: int, progress: ProgressFn,
                     cancel: CancelFn) -> int:
        """Read `total` bytes streamed in 64-byte blocks with continue/stop."""
        s = self._require()
        got = 0
        while got < total:
            if cancel():
                s.write(STOP)
                s.flush()
                raise GBxCartError("Canceled.")
            block = bytearray()
            deadline = time.monotonic() + 2.0
            while len(block) < BLOCK and time.monotonic() < deadline:
                chunk = s.read(BLOCK - len(block))
                if chunk:
                    block += chunk
                    deadline = time.monotonic() + 2.0
            if not block:
                s.write(STOP)
                s.flush()
                raise GBxCartError(
                    f"Read timed out at {got} of {total} bytes.")
            take = min(len(block), total - got)
            out.write(bytes(block[:take]))
            got += take
            if got < total:
                s.write(CONT)
                s.flush()
            else:
                s.write(STOP)
                s.flush()
            progress(got, total)
        return got

    # -- identify ----------------------------------------------------------- #

    def identify(self) -> DeviceInfo:
        fw = self.request_value(READ_FIRMWARE_VERSION, timeout=0.6)
        pcb = self.request_value(READ_PCB_VERSION, timeout=0.6)
        mode = self.request_value(CART_MODE, timeout=0.6)
        return DeviceInfo(firmware=fw, pcb=pcb, cart_mode=mode)

    # -- voltage / mode ----------------------------------------------------- #

    def select_gb(self) -> None:
        self.set_mode(VOLTAGE_5V)
        self.set_mode(GB_CART_MODE)

    def select_gba(self) -> None:
        self.set_mode(VOLTAGE_3_3V)
        self.set_mode(GBA_CART_MODE)

    # -- flash cart identification ------------------------------------------ #

    def _wait_for_ack(self, timeout: float = 2.0) -> bool:
        s = self._require()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            b = s.read(1)
            if b == b"1":
                return True
        return False

    # Settle delay after each flash command write, in seconds. Real hardware
    # needs this (the finicky 5V repro carts miss commands without it); tests
    # against a simulator set it to 0 so the write loop runs quickly.
    flash_settle_s = 0.001
    # Poll interval while waiting for a sector erase to finish, in seconds.
    flash_poll_s = 0.1
    # Poll interval while waiting for a single word to finish programming.
    flash_poll_word_s = 0.0

    def gba_flash_write_address_byte(self, address: int, byte: int) -> None:
        """Write a command byte to the GBA flash cart bus. The address is halved
        (16-bit bus), then address and byte are each sent as an 'n' command.

        A short settle delay follows each write, matching the reference flasher.
        The finicky 5V repro carts need this pause to register the command; without
        it they intermittently miss commands and read back stale ROM data.
        """
        s = self._require()
        addr = address // 2
        s.write(f"{GBA_FLASH_CART_WRITE_BYTE}{addr:x}".encode("latin-1") + b"\x00")
        s.flush()
        if self.flash_settle_s:
            time.sleep(self.flash_settle_s)
        s.write(f"{GBA_FLASH_CART_WRITE_BYTE}{byte:x}".encode("latin-1") + b"\x00")
        s.flush()
        if self.flash_settle_s:
            time.sleep(self.flash_settle_s)
        self._wait_for_ack()

    def _gba_read8(self) -> bytes:
        import io
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(0, SET_START_ADDRESS)
        self.set_mode(GBA_READ_ROM)
        buf = io.BytesIO()
        self._read_stream(buf, 64, _noop, _never)
        return buf.getvalue()[:8]

    def gba_flash_reset(self) -> None:
        """Return the flash chip to normal read mode."""
        self.gba_flash_write_address_byte(0x000, 0xF0)

    def _gba_read_bytes_at(self, address: int, count: int) -> bytes:
        """Read `count` bytes from a given GBA ROM byte address. The address is
        halved for the 16-bit bus, matching how reads are addressed elsewhere."""
        import io
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(address // 2, SET_START_ADDRESS)
        self.set_mode(GBA_READ_ROM)
        buf = io.BytesIO()
        self._read_stream(buf, count, _noop, _never)
        return buf.getvalue()[:count]

    def gba_flash_erase_sector(self, sector_addr: int, unlock_a1: int = 0xAAA,
                               unlock_a2: int = 0x555,
                               verify_len: int = 0x80,
                               timeout_s: float = 12.0,
                               log=None) -> tuple[bool, bytes]:
        """Erase ONE flash sector at `sector_addr` and verify it reads back 0xFF.

        DESTRUCTIVE: this erases the flash contents of one sector. It is the
        atomic building block of a ROM write, kept separate so it can be tested
        on its own. It never touches more than the one sector it is told to.

        Sequence (standard AMD-style sector erase, matching the reference
        flasher): unlock (a1=0xAA, a2=0x55), erase setup (a1=0x80), unlock again
        (a1=0xAA, a2=0x55), then the sector-address command (SA=0x30). The chip
        then erases the sector, which takes up to a second or so. We poll the
        sector address until it reads 0xFFFF (DQ7/data all ones = erase done),
        with a timeout so this can never hang. Finally we read the first
        verify_len bytes back and confirm they are all 0xFF.

        Returns (ok, sample_bytes). ok is True only if the status poll succeeded
        AND the read-back is all 0xFF. sample_bytes is what the sector actually
        contained after the attempt, for diagnosis on failure.

        Assumes the caller has already selected GBA mode and set 5V. Leaves the
        chip in normal read mode.
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)

        # Make sure we start in a clean read state.
        self.gba_flash_reset()

        # AMD-style six-write sector erase command.
        _log(f"Erasing sector at 0x{sector_addr:X} (unlock {unlock_a1:X}/"
             f"{unlock_a2:X})\u2026")
        self.gba_flash_write_address_byte(unlock_a1, 0xAA)
        self.gba_flash_write_address_byte(unlock_a2, 0x55)
        self.gba_flash_write_address_byte(unlock_a1, 0x80)   # erase setup
        self.gba_flash_write_address_byte(unlock_a1, 0xAA)
        self.gba_flash_write_address_byte(unlock_a2, 0x55)
        self.gba_flash_write_address_byte(sector_addr, 0x30)  # sector erase

        # Poll the sector until it reads 0xFFFF (erase complete). The chip
        # returns not-0xFF while busy; once erased every bit is 1.
        import time as _time
        deadline = _time.time() + timeout_s
        done = False
        last = b""
        while _time.time() < deadline:
            if self.flash_poll_s:
                _time.sleep(self.flash_poll_s)
            last = self._gba_read_bytes_at(sector_addr, 2)
            if len(last) >= 2 and last[0] == 0xFF and last[1] == 0xFF:
                done = True
                break

        # Always return to read mode before reading back.
        self.gba_flash_reset()

        if not done:
            _log(f"  Sector erase timed out after {timeout_s:.0f}s "
                 f"(last status: {last.hex() if last else 'none'}).")
            sample = self._gba_read_bytes_at(sector_addr, verify_len)
            return False, sample

        # Verify the sector reads back all 0xFF.
        sample = self._gba_read_bytes_at(sector_addr, verify_len)
        all_ff = len(sample) == verify_len and all(b == 0xFF for b in sample)
        if all_ff:
            _log(f"  Sector erased and verified (read back {verify_len} bytes, "
                 f"all 0xFF).")
        else:
            nonff = next((i for i, b in enumerate(sample) if b != 0xFF), -1)
            _log(f"  Sector erase verify FAILED: byte {nonff} is "
                 f"0x{sample[nonff]:02X}, expected 0xFF.")
        return all_ff, sample

    def gba_flash_program_word(self, byte_addr: int, word: int,
                               unlock_a1: int = 0xAAA,
                               unlock_a2: int = 0x555,
                               poll_timeout_s: float = 0.5) -> bool:
        """Program one 16-bit word at `byte_addr`, then wait for it to commit.

        Sequence: unlock (a1=0xAA, a2=0x55), program setup (a1=0xA0), then write
        the data word at its address. This is the standard S29GL/AMD single-word
        program the reference flasher uses for host-driven writes. The chip must
        already be erased at this address (program can only clear bits).

        After issuing the program, the chip needs a moment to actually store the
        word. We must wait for that before the next command, or the chip - still
        busy programming - drops the next unlock and the word is silently lost.
        We wait by data-polling: read the address back until it reads the value we
        wrote (AMD DQ7/data polling), with a short timeout. Returns True if the
        word read back correct within the timeout, False otherwise.
        """
        self.gba_flash_write_address_byte(unlock_a1, 0xAA)
        self.gba_flash_write_address_byte(unlock_a2, 0x55)
        self.gba_flash_write_address_byte(unlock_a1, 0xA0)   # program setup
        self.gba_flash_write_address_byte(byte_addr, word)   # data word

        # Data-poll: wait until the address reads back the value we wrote. While
        # the chip is still programming, the read differs; once done, it matches.
        import time as _time
        want = bytes([word & 0xFF, (word >> 8) & 0xFF])
        deadline = _time.time() + poll_timeout_s
        while _time.time() < deadline:
            got = self._gba_read_bytes_at(byte_addr, 2)
            if got == want:
                return True
            if self.flash_poll_word_s:
                _time.sleep(self.flash_poll_word_s)
        # One last check before giving up.
        return self._gba_read_bytes_at(byte_addr, 2) == want

    def gba_flash_block_write_probe(self, block_command: str = "f",
                                    test_block: bytes = b"",
                                    log=None) -> tuple:
        """Experiment: erase sector 0, write ONE block via the firmware block
        command, read it back, and report whether it landed correctly.

        This is a diagnostic, not the write feature. It answers one question: does
        the firmware's fast block-write command program this specific chip
        correctly? If the read-back matches, the block command works and a fast
        write loop can be built on it. If it comes back wrong (garbled, swapped,
        or unchanged), the command is not right for this chip.

        Only sector 0 is touched. Assumes GBA mode and 5V are set. Returns
        (ok, readback, message). ok is True only if the read-back equals the block
        we wrote. On mismatch, `readback` is what the chip actually holds, so the
        failure mode (e.g. byte-swapped vs unchanged) is visible.
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)

        if not test_block:
            # A distinctive 256-byte pattern: byte i = (i*7 + 0x11) & 0xFF. No long
            # runs of 0xFF, so it forces real programming and is easy to eyeball.
            test_block = bytes(((i * 7 + 0x11) & 0xFF) for i in range(256))
        block = test_block[:256].ljust(256, b"\x00")

        # Erase sector 0 first (the block must be written to erased flash).
        _log("Erasing sector 0 for the block-write test\u2026")
        ok, _sample = self.gba_flash_erase_sector(0x0, log=None)
        if not ok:
            return False, b"", ("Sector 0 did not erase, so the block-write test "
                                "cannot run. (This is the erase step, not the "
                                "block write.)")

        # Set the address to 0, then stream the block command + 256 bytes.
        _log(f"Writing one 256-byte block with command '{block_command}'\u2026")
        self.set_number(0x0 // 2, SET_START_ADDRESS)
        self.write_block(block_command, block)

        # Give the firmware a moment to program the block, then read it back.
        import time as _time
        _time.sleep(0.05)
        self.gba_flash_reset()
        readback = self._gba_read_bytes_at(0x0, 256)

        if readback == block:
            _log("  Block wrote and read back correct. The fast block-write "
                 "command works on this chip.")
            return True, readback, ("Block-write command "
                                    f"'{block_command}' works: 256 bytes wrote "
                                    f"and verified.")

        # Diagnose the failure mode for the log.
        if all(b == 0xFF for b in readback):
            why = ("the block did not program at all (still erased 0xFF) - this "
                   "command likely is not understood by the firmware for this "
                   "chip")
        else:
            # Check if it looks D0/D1 swapped (a common repro-cart wrinkle).
            def swap01(v):
                b0 = (v & 1); b1 = (v >> 1) & 1
                return (v & ~0b11) | (b1) | (b0 << 1)
            swapped = bytes(swap01(b) for b in block)
            if readback == swapped:
                why = ("the data came back with D0/D1 swapped - this chip needs "
                       "the swapped block command instead")
            else:
                nonmatch = next((i for i in range(256)
                                 if readback[i] != block[i]), 0)
                why = (f"the read-back differs starting at byte {nonmatch} "
                       f"(got 0x{readback[nonmatch]:02X}, expected "
                       f"0x{block[nonmatch]:02X})")
        _log(f"  Block-write test FAILED: {why}.")
        return False, readback, f"Block-write command '{block_command}' failed: {why}."

    def gba_flash_write_rom_fast(self, data: bytes, erase_regions,
                                 block_command: str = "f",
                                 block_size: int = 256,
                                 progress=None, log=None, cancel=None) -> tuple:
        """Write a full ROM using the firmware block-write command (fast path).

        Same safety design as the word-at-a-time write: for each sector, erase
        and verify it, block-write it, then read the whole sector back and confirm
        it matches the file. Any failure stops immediately and reports where.

        The speed comes from block_command (e.g. 'f'): the host sets the address
        once per sector, then streams block_size-byte blocks that the firmware
        programs on the device, instead of one word per serial round-trip. Use
        gba_flash_block_write_probe first to confirm the command works on the
        chip; passing the wrong command would write wrong data (still caught by
        the per-sector verify, but wasteful).

        `erase_regions` is the CFI sector map. Assumes GBA mode and 5V are set.
        Returns (ok, message). Safe to cancel; a partial write is re-flashable.
        """
        def _log(msg: str) -> None:
            if log:
                log(msg)

        def _cancelled() -> bool:
            return bool(cancel and cancel())

        sectors = []
        addr = 0
        for size, count in erase_regions:
            for _ in range(count):
                sectors.append((addr, size))
                addr += size
        total_size = addr
        if not sectors:
            return False, "No sector map available; cannot write safely."
        if len(data) > total_size:
            return False, (f"ROM is {len(data)} bytes but the chip holds "
                           f"{total_size}. Refusing to write past the end.")

        kind = "buffered" if block_command in ("c", "d") else "block"
        _log(f"Writing {len(data)} bytes across up to {len(sectors)} sectors "
             f"using fast {kind} writes ({block_size}-byte blocks).")

        import time as _time
        _start = _time.time()
        written = 0
        try:
            for idx, (sec_addr, sec_size) in enumerate(sectors):
                if sec_addr >= len(data):
                    break
                if _cancelled():
                    self.gba_flash_reset()
                    return False, (f"Cancelled at sector {idx} (0x{sec_addr:X}). "
                                   f"Partial ROM on cart; can be rewritten.")

                chunk = data[sec_addr:sec_addr + sec_size]
                if len(chunk) < sec_size:
                    chunk = chunk + b"\xFF" * (sec_size - len(chunk))

                _log(f"Sector {idx + 1}/{len(sectors)} at 0x{sec_addr:X} "
                     f"({sec_size // 1024} KB)\u2026")

                ok, _sample = self.gba_flash_erase_sector(sec_addr, log=None)
                if not ok:
                    self.gba_flash_reset()
                    return False, (f"Sector {idx} at 0x{sec_addr:X} failed to "
                                   f"erase or verify. Write stopped.")

                # Block-write the sector. Set the address once; the firmware
                # auto-advances across consecutive blocks. After skipping an
                # all-0xFF block (already erased), the address must be re-set.
                self.set_number(sec_addr // 2, SET_START_ADDRESS)
                skipping = False
                for off in range(0, sec_size, block_size):
                    if _cancelled():
                        self.gba_flash_reset()
                        return False, (f"Cancelled while writing sector {idx} "
                                       f"(0x{sec_addr:X}). Partial ROM; can be "
                                       f"rewritten.")
                    block = chunk[off:off + block_size]
                    if len(block) < block_size:
                        block = block + b"\xFF" * (block_size - len(block))
                    if block == b"\xFF" * block_size:
                        skipping = True          # erased already; skip
                        continue
                    if skipping:
                        # Re-set the address after a skip broke auto-advance.
                        self.set_number((sec_addr + off) // 2, SET_START_ADDRESS)
                        skipping = False
                    self.write_block(block_command, block)
                self.gba_flash_reset()

                # Verify the whole sector reads back matching the file.
                readback = self._gba_read_bytes_at(sec_addr, sec_size)
                if readback != chunk:
                    mism = next((i for i in range(min(len(readback), len(chunk)))
                                 if readback[i] != chunk[i]), 0)
                    got = readback[mism] if mism < len(readback) else -1
                    exp = chunk[mism] if mism < len(chunk) else -1
                    self.gba_flash_reset()
                    return False, (f"Sector {idx} at 0x{sec_addr:X} verify "
                                   f"FAILED at byte {mism}: got 0x{got:02X}, "
                                   f"expected 0x{exp:02X}. Write stopped.")

                written += sec_size
                if progress:
                    progress(min(written, len(data)), len(data))
        finally:
            self.gba_flash_reset()

        elapsed = format_duration(_time.time() - _start)
        _log(f"Fast write complete in {elapsed}. All sectors erased, "
             f"{kind}-written and verified.")
        return True, (f"Wrote and verified {len(data)} bytes using fast {kind} "
                      f"writes in {elapsed}. The ROM is on the cart and reads "
                      f"back matching the file.")

    def gba_flash_write_rom(self, data: bytes, erase_regions,
                            unlock_a1: int = 0xAAA, unlock_a2: int = 0x555,
                            write_settle_s: float = 0.0002,
                            progress=None, log=None, cancel=None) -> tuple:
        """Write a full ROM to the flash cart, erasing and verifying as it goes.

        This is the assembled write path: for each flash sector the ROM touches,
        erase-and-verify the sector, program its words, then read the sector back
        and confirm it matches the file. Any failure stops the write immediately
        and reports where. Verification after every sector means a bad write can
        never be reported as good.

        `erase_regions` is the CFI sector map: a sequence of (sector_size_bytes,
        sector_count). `data` is the ROM bytes. Assumes GBA mode and 5V are set.

        `write_settle_s` is the per-command settle delay used during programming.
        It is shorter than the identify-time delay because each word is confirmed
        by read-back and retried if it does not take, so the read-back is the
        safety net rather than a long fixed pause. This roughly halves write time.

        Returns (ok, message). ok is True only if the whole ROM wrote and every
        sector verified. On failure, message says exactly which sector and why.

        Slow by nature: one word per serial round-trip. Safe to cancel; a partial
        write leaves a re-flashable cart, not a brick.
        """
        import time as _time

        def _log(msg: str) -> None:
            if log:
                log(msg)

        def _cancelled() -> bool:
            return bool(cancel and cancel())

        # Build the flat list of sector boundaries from the CFI region map.
        sectors = []          # (start_byte_addr, size_bytes)
        addr = 0
        for size, count in erase_regions:
            for _ in range(count):
                sectors.append((addr, size))
                addr += size
        total_size = addr
        if not sectors:
            return False, "No sector map available; cannot write safely."

        if len(data) > total_size:
            return False, (f"ROM is {len(data)} bytes but the chip holds "
                           f"{total_size}. Refusing to write past the end.")

        _log(f"Writing {len(data)} bytes across up to {len(sectors)} sectors. "
             f"This is slow (one word at a time); progress is per sector.")

        # Use a shorter settle delay during the program loop; the per-word
        # read-back and retry are the safety net. Restored in the finally below.
        saved_settle = self.flash_settle_s
        self.flash_settle_s = write_settle_s
        try:
            return self._write_rom_inner(data, sectors, unlock_a1, unlock_a2,
                                         progress, _log, _cancelled)
        finally:
            self.flash_settle_s = saved_settle
            self.gba_flash_reset()

    def _write_rom_inner(self, data, sectors, unlock_a1, unlock_a2,
                         progress, _log, _cancelled) -> tuple:
        import time as _time
        _start = _time.time()
        written = 0
        for idx, (sec_addr, sec_size) in enumerate(sectors):
            if sec_addr >= len(data):
                break          # ROM shorter than the chip; nothing left to write
            if _cancelled():
                self.gba_flash_reset()
                return False, (f"Cancelled at sector {idx} (0x{sec_addr:X}). "
                               f"The cart holds a partial ROM and can be "
                               f"rewritten.")

            chunk = data[sec_addr:sec_addr + sec_size]
            # Pad the final short chunk with 0xFF (erased value).
            if len(chunk) < sec_size:
                chunk = chunk + b"\xFF" * (sec_size - len(chunk))

            # Skip sectors that are entirely 0xFF: erase already leaves them 0xFF,
            # so there is nothing to program. Still erase them so a previous ROM's
            # data is cleared.
            _log(f"Sector {idx + 1}/{len(sectors)} at 0x{sec_addr:X} "
                 f"({sec_size // 1024} KB)\u2026")

            ok, _sample = self.gba_flash_erase_sector(
                sec_addr, unlock_a1=unlock_a1, unlock_a2=unlock_a2,
                verify_len=min(0x80, sec_size), log=None)
            if not ok:
                self.gba_flash_reset()
                return False, (f"Sector {idx} at 0x{sec_addr:X} failed to erase "
                               f"or verify. Write stopped; nothing past this "
                               f"point was touched.")

            all_ff = all(b == 0xFF for b in chunk)
            if not all_ff:
                # Program the sector one word at a time.
                for off in range(0, sec_size, 2):
                    if _cancelled():
                        self.gba_flash_reset()
                        return False, (f"Cancelled while programming sector "
                                       f"{idx} (0x{sec_addr:X}). Partial ROM on "
                                       f"cart; can be rewritten.")
                    lo = chunk[off]
                    hi = chunk[off + 1] if off + 1 < len(chunk) else 0xFF
                    word = lo | (hi << 8)
                    if word == 0xFFFF:
                        continue      # already erased to 0xFF
                    # Program the word, waiting for it to commit. Retry a couple
                    # of times if it doesn't take, since an occasional word needs
                    # a second attempt; fail cleanly if it truly won't program.
                    committed = False
                    for _try in range(3):
                        if self.gba_flash_program_word(
                                sec_addr + off, word,
                                unlock_a1=unlock_a1, unlock_a2=unlock_a2):
                            committed = True
                            break
                        self.gba_flash_reset()
                    if not committed:
                        self.gba_flash_reset()
                        return False, (f"Word at 0x{sec_addr + off:X} would not "
                                       f"program (wrote 0x{word:04X}, did not "
                                       f"read back) after retries. Write "
                                       f"stopped.")
                self.gba_flash_reset()

                # Verify the whole sector reads back matching the file.
                readback = self._gba_read_bytes_at(sec_addr, sec_size)
                if readback != chunk:
                    mism = next((i for i in range(min(len(readback), len(chunk)))
                                 if readback[i] != chunk[i]), 0)
                    got = readback[mism] if mism < len(readback) else -1
                    exp = chunk[mism] if mism < len(chunk) else -1
                    self.gba_flash_reset()
                    return False, (f"Sector {idx} at 0x{sec_addr:X} verify "
                                   f"FAILED at byte {mism}: got 0x{got:02X}, "
                                   f"expected 0x{exp:02X}. Write stopped.")

            written += sec_size
            if progress:
                progress(min(written, len(data)), len(data))

        self.gba_flash_reset()
        elapsed = format_duration(_time.time() - _start)
        _log(f"Write complete in {elapsed}. All sectors erased, programmed and "
             f"verified.")
        return True, (f"Wrote and verified {len(data)} bytes in {elapsed}. The "
                      f"ROM is on the cart and reads back matching the file.")

    # Flash-ID unlock sequences. Each is (name, (a1,d1), (a2,d2), (a3,d3), reset)
    # where the three writes enter read-ID mode and reset returns to read mode.
    # These are the exact address bases FlashGBX tries, in the same order. The
    # 0xAAAA / 0x5555 and 0x4AAA / 0x7AAA bases are the ones the larger repro
    # carts (S29GL256/512 family, device id 0x227E) actually answer on - the
    # plain 0xAAA / 0x555 bases alone are not enough for those chips.
    FLASH_ID_VARIANTS = (
        ("555/AA",   (0x555, 0xAA),  (0x2AA, 0x55),  (0x555, 0x90),  (0x0, 0xF0)),
        ("5555/AA",  (0x5555, 0xAA), (0x2AAA, 0x55), (0x5555, 0x90), (0x0, 0xF0)),
        ("AAA/AA",   (0xAAA, 0xAA),  (0x555, 0x55),  (0xAAA, 0x90),  (0x0, 0xF0)),
        ("AAAA/AA",  (0xAAAA, 0xAA), (0x5555, 0x55), (0xAAAA, 0x90), (0x0, 0xF0)),
        ("4AAA/AA",  (0x4AAA, 0xAA), (0x4555, 0x55), (0x4AAA, 0x90), (0x4000, 0xF0)),
        ("7AAA/AA",  (0x7AAA, 0xAA), (0x7555, 0x55), (0x7AAA, 0x90), (0x7000, 0xF0)),
    )

    def _gba_read_bytes(self, count: int) -> bytes:
        """Read `count` bytes from GBA ROM address 0 (for CFI/ID buffers)."""
        import io
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(0, SET_START_ADDRESS)
        self.set_mode(GBA_READ_ROM)
        buf = io.BytesIO()
        self._read_stream(buf, count, _noop, _never)
        return buf.getvalue()[:count]

    # Address bases to try for the CFI query (0x98) and identifier (0x90). These
    # match the reference flasher's table: the base that answers tells us where
    # the chip is mapped. 0x555/0x5555/0xAAA/0xAAAA plus the 0x4xxx/0x7xxx bank
    # offsets, then a bare address-0 Intel-style query last.
    # Address bases to try for the CFI query (0x98) and identifier (0x90). Each
    # row is (name, cfi_addr, unlock_a1, unlock_a2, reset_addr). The unlock is
    # a1/a2/a1; a1 == 0 means the Intel-style single-0x90 read at address 0.
    # These match the reference flasher's table.
    _CFI_METHODS = (
        ("555",   0x555,  0x555,  0x2AA,  0x0),
        ("5555",  0x5555, 0x5555, 0x2AAA, 0x0),
        ("AAA",   0xAA,   0xAAA,  0x555,  0x0),
        ("AAAA",  0xAAAA, 0xAAAA, 0x5555, 0x0),
        ("4AAA",  0x4555, 0x4AAA, 0x4555, 0x4000),
        ("7AAA",  0x7555, 0x7AAA, 0x7555, 0x7000),
        ("bare",  0x0,    0x0,    0x0,    0x0),
    )

    def _gba_cfi_query(self) -> tuple[str, bytes, bytes]:
        """Find the chip's CFI table and read its ID from the matching base.

        Mirrors the reference flasher: for each address base, reset the chip,
        send the CFI query command (0x98), read a 0x400 buffer, and look for the
        "QRY" signature at byte offsets 0x20/0x22/0x24. When found, that base is
        the right one; issue its read-identifier command (0x90) and read the
        manufacturer/device ID. Returns (method_name, id_bytes, cfi_buffer) or
        ("", b"", b"").

        Read-only: only enters and exits CFI/ID mode, never erases or programs.
        """
        for name, cfi_addr, a1, a2, reset_addr in self._CFI_METHODS:
            # Reset first so we start from a known state.
            self.gba_flash_write_address_byte(reset_addr, 0xF0)
            # CFI query.
            self.gba_flash_write_address_byte(cfi_addr, 0x98)
            buf = self._gba_read_bytes(0x400)
            if len(buf) < 0x28:
                self.gba_flash_write_address_byte(reset_addr, 0xF0)
                continue
            # CFI "QRY" magic lives at byte offsets 0x20, 0x22, 0x24 on the
            # 16-bit bus (every other byte).
            magic = bytes([buf[0x20], buf[0x22], buf[0x24]])
            if magic != b"QRY":
                self.gba_flash_write_address_byte(reset_addr, 0xF0)
                continue
            # Found the chip. Read its ID at this base - go straight from CFI to
            # the identifier read (no reset in between) so the chip stays awake,
            # the way the reference flasher does.
            if a1 == 0x0:
                self.gba_flash_write_address_byte(0x0, 0x90)      # Intel-style
            else:
                self.gba_flash_write_address_byte(a1, 0xAA)       # AMD unlock
                self.gba_flash_write_address_byte(a2, 0x55)
                self.gba_flash_write_address_byte(a1, 0x90)
            ident = self._gba_read_bytes(8)
            self.gba_flash_write_address_byte(reset_addr, 0xF0)
            return name, ident, buf
        return "", b"", b""

    def gba_flash_intel_reset(self) -> None:
        """Intel chips leave read-ID mode with 0xFF, not 0xF0."""
        self.gba_flash_write_address_byte(0x000, 0xFF)

    def gba_flash_id_probe(self) -> dict:
        """Try several flash-ID methods at 5V and record what the cart returns.

        Non-destructive: this only enters and exits the chip's read-ID mode and
        never issues an erase or program command. A retail (mask ROM) cart
        ignores the command writes and returns its normal ROM bytes.

        Why 5V: many GBA repro carts (the EpicJoy/Gugxiom S29GL256/512 family)
        ignore every flash command at 3.3V and only answer at 5V. The genuine
        GBxCart flasher forces 5V for exactly this reason. We raise to 5V for the
        probe and drop back to 3.3V before returning, so a normal ROM read is
        never left running at the higher voltage.

        Methods tried, in order (all read-only):
          * baseline    - a plain ROM read at 5V, for comparison
          * cfi-<base>  - a Common Flash Interface query (the reliable method);
                          finds the chip's "QRY" table and reads a clean ID
          * 555/AA .. 7AAA/AA - AMD-style unlock sequences (fallback)
          * bare-90     - a single 0x90 write to address 0 (Intel-type detect)

        Returns {'baseline': bytes, '<method>': bytes, ...}. Any method whose
        result differs from the baseline is the one the chip responds to.
        """
        self.select_gba()
        # Force 5V for the probe. select_gba() set 3.3V; raise it now. This is the
        # key that makes 0x227E-class repro carts answer at all.
        self.set_mode(VOLTAGE_5V)
        time.sleep(0.1)
        self.ser.reset_input_buffer()  # type: ignore[union-attr]

        try:
            # Clear the chip out of any stuck mode it may have been left in by a
            # previous operation. These repro carts can answer one run and stay
            # silent the next depending on leftover state, so reset firmly first:
            # both the AMD reset (0xF0) and the Intel reset (0xFF), at a couple of
            # bank bases, before reading a clean baseline.
            for rst_addr in (0x0, 0x4000, 0x7000):
                self.gba_flash_write_address_byte(rst_addr, 0xF0)
                self.gba_flash_write_address_byte(rst_addr, 0xFF)
            time.sleep(0.01)

            results: dict[str, bytes] = {"baseline": self._gba_read8()}
            self.gba_flash_reset()

            # Preferred method: CFI query. This finds the chip's CFI table and
            # reads a clean manufacturer/device ID from the right base, the way
            # the reference flasher does. When it works it is far more reliable
            # than the bare unlock-and-read below, which can read a partial ID.
            # The finicky carts answer intermittently, so try a few times.
            for _attempt in range(3):
                cfi_name, cfi_id, cfi_buf = self._gba_cfi_query()
                if cfi_id and cfi_id[:4] != results["baseline"][:4]:
                    results["cfi-" + cfi_name] = cfi_id
                    if cfi_buf:
                        results["_cfi_buffer"] = cfi_buf
                    break
                self.gba_flash_reset()
                time.sleep(0.01)
            self.gba_flash_reset()

            # AMD-style unlock sequences across all the address bases (fallback
            # for chips that answer the unlock-and-read but not the CFI query).
            for name, c1, c2, c3, rst in self.FLASH_ID_VARIANTS:
                for addr, val in (c1, c2, c3):
                    self.gba_flash_write_address_byte(addr, val)
                results[name] = self._gba_read8()
                # reset using this variant's reset address
                self.gba_flash_write_address_byte(rst[0], rst[1])

            # Intel-type: bare 0x90 to address 0, exit with 0xFF.
            self.gba_flash_write_address_byte(0x00, 0x90)
            time.sleep(0.002)
            results["bare-90"] = self._gba_read8()
            self.gba_flash_intel_reset()
        finally:
            # Always drop back to 3.3V so a subsequent plain read is safe.
            self.set_mode(VOLTAGE_3_3V)
            time.sleep(0.05)

        return results

    # -- cart info ---------------------------------------------------------- #

    def read_gba_header(self) -> bytes:
        """Read the first 192 bytes of a GBA cart (enough for the header)."""
        import io
        self.select_gba()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(0, SET_START_ADDRESS)
        self.set_mode(GBA_READ_ROM)
        buf = io.BytesIO()
        self._read_stream(buf, 192, _noop, _never)
        return buf.getvalue()

    def read_gb_header(self) -> bytes:
        """Read the first 384 bytes of a GB/GBC cart (header at 0x100-0x14F)."""
        import io
        self.select_gb()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(0x0000, SET_START_ADDRESS)
        self.set_mode(READ_ROM_RAM)
        buf = io.BytesIO()
        self._read_stream(buf, 384, _noop, _never)
        return buf.getvalue()

    def detect_gba_rom_size(self, cancel: CancelFn = _never) -> int:
        """Port of insideGadgets' gba_check_rom_size: scan for the all-0x00 tail.
        Returns size in bytes (rounded to 4 MB chunks), capped at 32 MB."""
        import io
        self.select_gba()
        four_mb = 0x3FFFC0
        curr = 0x1FFC0
        zero_total = 0
        size_mb = 0
        for x in range(512):
            if cancel():
                break
            self.set_number(curr // 2, SET_START_ADDRESS)
            self.set_mode(GBA_READ_ROM)
            buf = io.BytesIO()
            try:
                self._read_stream(buf, 64, _noop, _never)
            except GBxCartError:
                break
            data = buf.getvalue()
            if data.count(0) >= 64:
                zero_total += 1
            if curr % four_mb == 0 or curr % four_mb < 512:
                if zero_total >= 30:
                    break
                zero_total = 0
                size_mb += 4
            curr += 0x20000
        return max(size_mb, 4) * 1024 * 1024

    def read_gba_rom(self, out: BinaryIO, size: int,
                     progress: ProgressFn = _noop, log: LogFn = _noop,
                     cancel: CancelFn = _never) -> None:
        self.select_gba()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        # GBA address increments by 1 per 2 bytes on the device side
        self.set_number(0, SET_START_ADDRESS)
        if self.fast_read:
            self._read_stream_fast(out, size, GBA_READ_ROM_8000H, 0x8000,
                                   progress, cancel)
        else:
            self.set_mode(GBA_READ_ROM)
            self._read_stream(out, size, progress, cancel)

    def read_gba_sram(self, out: BinaryIO, size: int,
                      progress: ProgressFn = _noop, log: LogFn = _noop,
                      cancel: CancelFn = _never) -> None:
        """Backwards-compatible plain 32 KB SRAM read (single bank)."""
        self.select_gba()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        self.set_number(0, SET_START_ADDRESS)
        self.set_mode(GBA_READ_SRAM)
        self._read_stream(out, size, progress, cancel)

    def read_gba_save(self, out: BinaryIO, save_kind: str,
                      progress: ProgressFn = _noop, log: LogFn = _noop,
                      cancel: CancelFn = _never) -> None:
        """Read a GBA save of the given kind, using the correct protocol for
        EEPROM (8-byte blocks), SRAM, or Flash (bank-switched)."""
        if save_kind == SAVE_NONE or save_kind not in SAVE_LAYOUT:
            raise GBxCartError(f"Unsupported or unknown save kind: {save_kind}")
        total, banks, per_bank = SAVE_LAYOUT[save_kind]
        self.select_gba()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]

        if save_kind in (SAVE_EEPROM_4K, SAVE_EEPROM_64K):
            self._read_gba_eeprom(out, save_kind, total, progress, cancel)
            return

        is_flash = save_kind in (SAVE_FLASH_512K, SAVE_FLASH_1M)
        written = 0
        for bank in range(banks):
            if bank == 1:
                if is_flash:
                    self.set_number(1, GBA_FLASH_SET_BANK)
                else:  # 1 Mbit SRAM second bank
                    self.gba_flash_write_address_byte(0x1000000, 0x1)
            self.set_number(0, SET_START_ADDRESS)
            self.set_mode(GBA_READ_SRAM)
            import io
            buf = io.BytesIO()
            self._read_stream(buf, per_bank,
                              lambda c, t: progress(written + c, total), cancel)
            out.write(buf.getvalue())
            written += per_bank
            if bank == 1:
                if is_flash:
                    self.set_number(0, GBA_FLASH_SET_BANK)
                else:
                    self.gba_flash_write_address_byte(0x1000000, 0x0)

    def _read_gba_eeprom(self, out: BinaryIO, save_kind: str, total: int,
                         progress: ProgressFn, cancel: CancelFn) -> None:
        size_sel = EEPROM_64KBIT if save_kind == SAVE_EEPROM_64K else EEPROM_4KBIT
        self.set_number(size_sel, GBA_SET_EEPROM_SIZE)
        self.set_number(0, SET_START_ADDRESS)
        self.set_mode(GBA_READ_EEPROM)
        # EEPROM streams in 8-byte blocks
        s = self._require()
        got = 0
        while got < total:
            if cancel():
                s.write(STOP)
                s.flush()
                raise GBxCartError("Canceled.")
            block = bytearray()
            deadline = time.monotonic() + 2.0
            while len(block) < 8 and time.monotonic() < deadline:
                chunk = s.read(8 - len(block))
                if chunk:
                    block += chunk
                    deadline = time.monotonic() + 2.0
            if not block:
                s.write(STOP)
                s.flush()
                raise GBxCartError(f"EEPROM read timed out at {got}/{total}.")
            out.write(bytes(block[:8]))
            got += len(block[:8])
            if got < total:
                s.write(CONT)
                s.flush()
            else:
                s.write(STOP)
                s.flush()
            progress(got, total)

    # -- GBA save writing --------------------------------------------------- #

    def write_gba_save(self, data: bytes, save_kind: str,
                       progress: ProgressFn = _noop, log: LogFn = _noop,
                       cancel: CancelFn = _never) -> None:
        """Write a save back to a GBA cart. Destructive - overwrites the cart's
        existing save. Uses the correct protocol per save type."""
        if save_kind == SAVE_NONE or save_kind not in SAVE_LAYOUT:
            raise GBxCartError(f"Unsupported save kind for writing: {save_kind}")
        total, banks, per_bank = SAVE_LAYOUT[save_kind]
        if len(data) < total:
            data = data + b"\x00" * (total - len(data))

        self.select_gba()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]

        if save_kind in (SAVE_EEPROM_4K, SAVE_EEPROM_64K):
            self._write_gba_eeprom(data, save_kind, total, progress, cancel)
        elif save_kind in (SAVE_FLASH_512K, SAVE_FLASH_1M):
            self._write_gba_flash(data, banks, per_bank, progress, log, cancel)
        else:  # SRAM (incl. 1 Mbit two-bank)
            self._write_gba_sram(data, banks, per_bank, total, progress, cancel)

    def _write_gba_sram(self, data: bytes, banks: int, per_bank: int,
                        total: int, progress: ProgressFn, cancel: CancelFn) -> None:
        written = 0
        for bank in range(banks):
            if bank == 1:
                self.gba_flash_write_address_byte(0x1000000, 0x1)
            self.set_number(0, SET_START_ADDRESS)
            base = bank * per_bank
            addr = 0
            while addr < per_bank:
                if cancel():
                    raise GBxCartError("Canceled.")
                chunk = data[base + addr: base + addr + 64].ljust(64, b"\x00")
                self.write_block(GBA_WRITE_SRAM, chunk)
                addr += 64
                written += 64
                progress(written, total)
            if bank == 1:
                self.gba_flash_write_address_byte(0x1000000, 0x0)

    def _write_gba_eeprom(self, data: bytes, save_kind: str, total: int,
                          progress: ProgressFn, cancel: CancelFn) -> None:
        size_sel = EEPROM_64KBIT if save_kind == SAVE_EEPROM_64K else EEPROM_4KBIT
        self.set_number(size_sel, GBA_SET_EEPROM_SIZE)
        self.set_number(0, SET_START_ADDRESS)
        addr = 0
        while addr < total:
            if cancel():
                raise GBxCartError("Canceled.")
            chunk = data[addr:addr + 8].ljust(8, b"\x00")
            self.write_block(GBA_WRITE_EEPROM, chunk)
            addr += 8
            progress(addr, total)

    def _write_gba_flash(self, data: bytes, banks: int, per_bank: int,
                         progress: ProgressFn, log: LogFn,
                         cancel: CancelFn) -> None:
        """Write a Flash save: erase each 4 KB sector (polling until the first
        byte reads 0xFF) then program 64 bytes at a time. Non-Atmel path."""
        total = banks * per_bank
        written = 0
        for bank in range(banks):
            if bank == 1:
                self.set_number(1, GBA_FLASH_SET_BANK)
            self.set_number(0, SET_START_ADDRESS)
            sector = 0
            addr = 0
            while addr < per_bank:
                if cancel():
                    if bank == 1:
                        self.set_number(0, GBA_FLASH_SET_BANK)
                    raise GBxCartError("Canceled.")
                if addr % 4096 == 0:
                    self._flash_sector_erase_and_wait(sector, addr, log)
                    sector += 1
                chunk = data[bank * per_bank + addr:
                             bank * per_bank + addr + 64].ljust(64, b"\x00")
                self.write_block(GBA_FLASH_WRITE_BYTE, chunk)
                addr += 64
                written += 64
                progress(written, total)
            if bank == 1:
                self.set_number(0, GBA_FLASH_SET_BANK)

    def _flash_sector_erase_and_wait(self, sector: int, addr: int,
                                     log: LogFn) -> None:
        import io
        self.set_number(sector, GBA_FLASH_4K_SECTOR_ERASE)
        self._wait_for_ack()
        # Poll until the first byte of the sector reads 0xFF (erase complete).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            self.set_number(addr, SET_START_ADDRESS)
            self.set_mode(GBA_READ_SRAM)
            buf = io.BytesIO()
            self._read_stream(buf, 64, _noop, _never)
            if buf.getvalue()[:1] == b"\xff":
                break
            time.sleep(0.005)
        else:
            raise GBxCartError(
                f"Flash sector {sector} did not erase (still not 0xFF). The save "
                f"chip may be write-protected or unsupported.")
        self.set_number(addr, SET_START_ADDRESS)

    # -- GB/GBC save writing ------------------------------------------------ #

    def write_gb_ram(self, data: bytes, cart_type: int = 0,
                     ram_size_code: int = 0, progress: ProgressFn = _noop,
                     log: LogFn = _noop, cancel: CancelFn = _never) -> None:
        """Write a save back to a GB/GBC cart's battery RAM. Destructive."""
        self.select_gb()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]

        banks, end_addr = gb_ram_layout(cart_type, ram_size_code, len(data))
        bank_bytes = end_addr - 0xA000 + 1
        if banks == 0:
            raise GBxCartError("This cart has no writable save RAM.")

        self._mbc2_fix()
        if cart_type <= 4:                 # MBC1 RAM mode
            self.set_bank(0x6000, 1)
        self.set_bank(0x0000, 0x0A)        # enable RAM
        total = banks * bank_bytes
        written = 0
        for bank in range(banks):
            self.set_bank(0x4000, bank)
            self.set_number(0xA000, SET_START_ADDRESS)
            base = bank * bank_bytes
            addr = 0
            while addr < bank_bytes:
                if cancel():
                    self.set_bank(0x0000, 0x00)
                    raise GBxCartError("Canceled.")
                chunk = data[base + addr: base + addr + 64]
                n = len(chunk)
                if n == 0:
                    break
                self.write_block(WRITE_RAM, chunk.ljust(64, b"\x00")[:n]
                                 if n < 64 else chunk)
                addr += n
                written += n
                progress(written, total)
        self.set_bank(0x0000, 0x00)        # disable RAM

    def read_gb_rom(self, out: BinaryIO, size: int, cart_type: int = 0,
                    title: str = "",
                    progress: ProgressFn = _noop, log: LogFn = _noop,
                    cancel: CancelFn = _never) -> None:
        self.select_gb()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]
        banks = max(1, size // 0x4000)
        written = 0
        # bank 0 lives at 0x0000; every later bank is read at 0x4000
        for bank in range(banks):
            if bank == 0:
                self.set_number(0x0000, SET_START_ADDRESS)
            else:
                self._select_rom_bank(bank, cart_type, title)
                self.set_number(0x4000, SET_START_ADDRESS)
            import io
            buf = io.BytesIO()
            if self.fast_read:
                # fast path: device streams the 0x4000 bank continuously under 'Q'
                self._read_stream_fast(
                    buf, 0x4000, READ_ROM_4000H, 0x4000,
                    lambda c, t: progress(written + c, size), cancel)
            else:
                self.set_mode(READ_ROM_RAM)
                self._read_stream(buf, 0x4000,
                                  lambda c, t: progress(written + c, size),
                                  cancel)
            out.write(buf.getvalue())
            written += 0x4000

    def _select_rom_bank(self, bank: int, cart_type: int, title: str) -> None:
        """Reproduce insideGadgets' per-MBC bank selection exactly."""
        if cart_type >= 5:                 # MBC2 and above
            self.set_bank(0x3000, 1 if bank >= 256 else 0)
            self.set_bank(0x2100, bank & 0xFF)
        elif cart_type >= 1:               # MBC1
            hudson = title.startswith("MOMOCOL") or title.startswith("BOMCOL")
            if hudson:
                self.set_bank(0x4000, bank >> 4)
                if bank < 10:
                    self.set_bank(0x2000, bank & 0x1F)
                else:
                    self.set_bank(0x2000, 0x10 | (bank & 0x1F))
            else:
                self.set_bank(0x6000, 0)              # ROM mode
                self.set_bank(0x4000, bank >> 5)      # high bits 5-6
                self.set_bank(0x2000, bank & 0x1F)    # low bits 0-4

    def read_gb_ram(self, out: BinaryIO, size: int, cart_type: int = 0,
                    ram_size_code: int = 0,
                    progress: ProgressFn = _noop, log: LogFn = _noop,
                    cancel: CancelFn = _never) -> None:
        """Read GB/GBC cartridge RAM. Honours the per-cart RAM end address so
        MBC2 (512 bytes) and 2 KB carts read the correct amount, matching
        insideGadgets' reference behaviour."""
        self.select_gb()
        self.ser.reset_input_buffer()  # type: ignore[union-attr]

        banks, end_addr = gb_ram_layout(cart_type, ram_size_code, size)
        bank_bytes = end_addr - 0xA000 + 1

        self._mbc2_fix()
        if cart_type <= 4:                 # MBC1 needs RAM-mode select
            self.set_bank(0x6000, 1)
        self.set_bank(0x0000, 0x0A)        # enable RAM
        total = banks * bank_bytes
        written = 0
        for bank in range(banks):
            self.set_bank(0x4000, bank)
            self.set_number(0xA000, SET_START_ADDRESS)
            self.set_mode(READ_ROM_RAM)
            import io
            buf = io.BytesIO()
            self._read_stream(buf, bank_bytes,
                              lambda c, t: progress(written + c, total), cancel)
            out.write(buf.getvalue())
            written += bank_bytes
        self.set_bank(0x0000, 0x00)        # disable RAM

    def _mbc2_fix(self) -> None:
        """Read a little ROM before RAM (matches insideGadgets' MBC2 quirk fix)."""
        import io
        self.set_number(0x0000, SET_START_ADDRESS)
        self.set_mode(READ_ROM_RAM)
        try:
            self._read_stream(io.BytesIO(), 64, _noop, _never)
        except GBxCartError:
            pass
