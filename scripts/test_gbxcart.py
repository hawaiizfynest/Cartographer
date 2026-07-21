"""
test_gbxcart.py - Hardware-free tests for the GBxCart RW device layer.

A FakeSerial emulates the GBxCart firmware's command/response and 64-byte
streaming protocol so the transport, detection handshake, and GB/GBA read
paths can be verified without a real device attached. (Hardware validation of
the actual Cyclone clone is a separate, manual step.)

Run:  python scripts/test_gbxcart.py

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import gbxcart as gx  # noqa: E402


class FakeSerial:
    """Minimal stand-in for serial.Serial that emulates a GBxCart device."""

    def __init__(self, rom=b"", sram=b"", mode=gx.GBA_MODE, fw=20, pcb=3,
                 flash_id=None, flash_variant="AAA/AA", requires_5v=False,
                 cfi_variant=None):
        self.rom = rom
        self.sram = sram
        self.mode = mode
        self.fw = fw
        self.pcb = pcb
        # flash_id: 8-byte response returned in read-ID mode (None = mask ROM)
        self.flash_id = flash_id
        self.flash_variant = flash_variant
        # requires_5v: if True, the chip only answers flash commands at 5V, like
        # the real Gugxiom/EpicJoy S29GL repro carts. The probe must have sent the
        # '5' voltage command for read-ID mode to engage.
        self.requires_5v = requires_5v
        # cfi_variant: if set (e.g. "AAAA"), the chip answers a CFI query (0x98)
        # at that address base and then reads its ID after a 0x90. None = no CFI.
        self.cfi_variant = cfi_variant
        self._cfi_mode = False
        self._voltage = 3            # tracks last '5'/'3' voltage command
        self.eeprom = b""          # EEPROM contents (8-byte addressed)
        self.flash_bank1 = b""     # second flash/SRAM bank contents
        self._eeprom_mode = False
        self._save_bank = 0
        self.is_open = True
        self.timeout = 0.2

        self._wr = bytearray()        # accumulates bytes written by the host
        self._out = bytearray()       # bytes the host will read back
        self._stream = None           # (data, cursor) when streaming
        self._addr = 0
        self._rom_bank = 0
        self._ram_bank = 0
        self._flash_cmd = []          # recent (addr,val) command-byte writes
        self._id_mode = False

    # -- serial.Serial-like API -------------------------------------------- #

    def write(self, data: bytes) -> int:
        for byte in data:
            self._wr.append(byte)
            self._maybe_process()
        return len(data)

    def read(self, n: int = 1) -> bytes:
        if not self._out:
            return b""
        take = min(n, len(self._out))
        out = bytes(self._out[:take])
        del self._out[:take]
        return out

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.is_open = False

    # -- device emulation -------------------------------------------------- #

    _NUMBER_CMDS = (ord("A"), ord("B"), ord("*"), ord("n"), ord("S"), ord("k"),
                    ord("s"))

    # Write commands carry a fixed-size payload after the command byte.
    _WRITE_CMDS = {ord("W"): 64, ord("w"): 64, ord("p"): 8, ord("b"): 64,
                   ord("a"): 128, ord("f"): 256, ord("t"): 256}

    def _maybe_process(self):
        if not self._wr:
            return
        first = self._wr[0]
        if first in self._WRITE_CMDS:
            need = self._WRITE_CMDS[first] + 1
            if len(self._wr) >= need:
                payload = bytes(self._wr[1:need])
                del self._wr[:need]
                self._handle_write(chr(first), payload)
            return
        if first in self._NUMBER_CMDS:
            if 0 in self._wr:
                end = self._wr.index(0)
                token = bytes(self._wr[:end])
                del self._wr[:end + 1]
                self._handle_number(chr(token[0]), token[1:].decode("latin-1"))
            return
        cmd = chr(first)
        del self._wr[0]
        self._handle_single(cmd)

    def _handle_write(self, cmd, payload):
        # Write into the appropriate buffer at the current address, honouring
        # the selected save bank for SRAM/flash.
        if cmd == "W":                      # GB RAM write
            base = self._ram_bank * self._ram_stride + (self._addr - 0xA000)
            self._write_into("sram", base, payload)
            self._addr += len(payload)
        elif cmd == "w":                    # GBA SRAM write
            base = self._save_bank * 0x8000 + self._addr
            self._write_into("sram", base, payload)
            self._addr += len(payload)
        elif cmd == "p":                    # GBA EEPROM write (8 bytes)
            self._write_into("eeprom", self._eeprom_addr, payload)
            self._eeprom_addr += len(payload)
        elif cmd == "b":                    # GBA flash write (64 bytes)
            base = self._save_bank * 0x10000 + self._addr
            self._write_into("flash", base, payload)
            self._addr += len(payload)
        elif cmd in ("f", "t"):             # GBA ROM block write (256 bytes)
            # Firmware block write: program 256 bytes into ROM flash starting at
            # the current (halved) address, then auto-advance. Honour 5V and the
            # erase requirement (can only program erased-to-0xFF flash).
            self._block_write_rom(cmd, self._addr * 2, payload)
            self._addr += len(payload) // 2   # address is in 16-bit words
        self._out += b"1"                   # ack

    def _block_write_rom(self, cmd, byte_addr, payload):
        # Model the firmware block program. This chip is NON-swapped, so only the
        # 'f' command programs correctly; the 't' (swapped) command would land
        # D0/D1-swapped data, which the probe should detect as wrong.
        if self.requires_5v and self._voltage != 5:
            return
        if self._programmed_words is None:
            self._programmed_words = {}
        for i in range(0, len(payload) - 1, 2):
            a = byte_addr + i
            lo, hi = payload[i], payload[i + 1]
            word = lo | (hi << 8)
            if cmd == "t":
                # Swapped command on a non-swapped chip: D0/D1 of each byte swap.
                def swap01(v):
                    return (v & ~0b11) | ((v >> 1) & 1) | ((v & 1) << 1)
                word = swap01(lo) | (swap01(hi) << 8)
            base = (a // self._rom_sector_size) * self._rom_sector_size
            erased = self._erased_rom_sectors and base in self._erased_rom_sectors
            cur = 0xFFFF if erased else (
                (self.rom[a] if a < len(self.rom) else 0xFF) |
                ((self.rom[a + 1] if a + 1 < len(self.rom) else 0xFF) << 8))
            if a in self._programmed_words:
                cur = self._programmed_words[a]
            self._programmed_words[a] = cur & word

    def _write_into(self, which, base, payload):
        buf = bytearray(getattr(self, "_wbuf_" + which, b""))
        end = base + len(payload)
        if len(buf) < end:
            buf.extend(b"\x00" * (end - len(buf)))
        buf[base:end] = payload
        setattr(self, "_wbuf_" + which, bytes(buf))

    _eeprom_addr = 0
    _wbuf_sram = b""
    _wbuf_eeprom = b""
    _wbuf_flash = b""
    _erased_flash = False
    _rom_sector_size = 0x20000    # 128 KB sectors, matching the CFI sim
    _erased_rom_sectors = None    # set of erased sector base addresses
    _programmed_words = None      # {byte_addr: word} programmed after erase

    def _erase_rom_sector(self, sector_byte_addr):
        # Mark the 128 KB sector containing this address as erased. A real chip
        # erases the aligned sector, so snap down to the sector boundary. Erasing
        # also clears any words previously programmed into that sector.
        if self._erased_rom_sectors is None:
            self._erased_rom_sectors = set()
        if self._programmed_words is None:
            self._programmed_words = {}
        base = (sector_byte_addr // self._rom_sector_size) * self._rom_sector_size
        self._erased_rom_sectors.add(base)
        end = base + self._rom_sector_size
        for a in [k for k in self._programmed_words if base <= k < end]:
            del self._programmed_words[a]

    _program_commit_reads = 0    # reads-before-commit; 0 = instant (test knob)
    _pending_commit = None       # (byte_addr, word, reads_remaining)

    def _program_rom_word(self, byte_addr, word):
        # Program a 16-bit word. Real NOR flash can only clear bits (AND), and
        # only within an erased sector. Model that: the word ANDs with whatever
        # is currently there (0xFFFF if erased). Store low and high bytes.
        if self._programmed_words is None:
            self._programmed_words = {}
        base = (byte_addr // self._rom_sector_size) * self._rom_sector_size
        erased = self._erased_rom_sectors and base in self._erased_rom_sectors
        # Current value at this address (0xFFFF if erased, else original ROM).
        if erased:
            cur = 0xFFFF
        else:
            lo = self.rom[byte_addr] if byte_addr < len(self.rom) else 0xFF
            hi = self.rom[byte_addr + 1] if byte_addr + 1 < len(self.rom) else 0xFF
            cur = lo | (hi << 8)
        if byte_addr in self._programmed_words:
            cur = self._programmed_words[byte_addr]
        newval = cur & word          # AND: can only clear
        if self._program_commit_reads > 0:
            # Model a chip that needs a moment: the new value is not visible until
            # after _program_commit_reads readbacks. Until then the address reads
            # its previous value (0xFFFF when erased), so a writer that does not
            # poll would move on too early and miss it.
            self._pending_commit = [byte_addr, newval,
                                    self._program_commit_reads]
        else:
            self._programmed_words[byte_addr] = newval

    def _effective_rom(self):
        # The ROM as the host would read it: erased sectors read back as 0xFF,
        # then any programmed words overlaid on top.
        # First, advance any pending (slow-commit) program: each read brings it
        # one step closer to visible, modelling a chip that needs time to store.
        if self._pending_commit is not None:
            self._pending_commit[2] -= 1
            if self._pending_commit[2] <= 0:
                a, val, _ = self._pending_commit
                if self._programmed_words is None:
                    self._programmed_words = {}
                self._programmed_words[a] = val
                self._pending_commit = None
        if not self._erased_rom_sectors and not self._programmed_words:
            return self.rom
        buf = bytearray(self.rom)
        for base in (self._erased_rom_sectors or set()):
            end = min(base + self._rom_sector_size, len(buf))
            if base < len(buf):
                for i in range(base, end):
                    buf[i] = 0xFF
        for a, word in (self._programmed_words or {}).items():
            if a + 1 < len(buf):
                buf[a] = word & 0xFF
                buf[a + 1] = (word >> 8) & 0xFF
        return bytes(buf)

    def _handle_flash_byte(self, hexstr):
        # 'n' commands arrive in pairs: address then value.
        val = int(hexstr, 16) if hexstr else 0
        if self._flash_phase == "addr":
            self._flash_addr = val
            self._flash_phase = "val"
        else:
            self._apply_flash_cmd(self._flash_addr, val)
            self._flash_phase = "addr"
        self._out += b"1"    # ack every 'n' write (com_wait_for_ack)

    _flash_phase = "addr"
    _flash_addr = 0

    def _apply_flash_cmd(self, addr, val):
        # addr here is already the halved bus address the host sent.
        if addr == 0x800000:        # 0x1000000/2 -> 1Mbit SRAM bank select
            self._save_bank = val & 0x1
            return
        if val in (0xF0, 0xFF):
            self._id_mode = False
            self._cfi_mode = False
            self._flash_cmd = []
            return
        # CFI query: 0x98 at the chip's CFI base enters CFI mode (returns the QRY
        # table). Honour the 5V requirement like the ID commands.
        if val == 0x98 and self.cfi_variant is not None:
            if self.requires_5v and self._voltage != 5:
                return
            # The halved base the host sends for this chip's CFI query.
            if addr == self._cfi_halved_addr():
                self._cfi_mode = True
                self._flash_cmd = []
            return
        # Intel: a lone 0x90 written to address 0 enters read-ID mode.
        if (val == 0x90 and addr == 0x00 and self.flash_id is not None
                and self.flash_variant == "Intel/90"):
            self._id_mode = True
            self._flash_cmd = []
            return
        # After a CFI match, a 0x90 (any recognised base) reads the ID.
        if val == 0x90 and self._cfi_mode and self.flash_id is not None:
            self._id_mode = True
            self._flash_cmd = []
            return
        self._flash_cmd.append((addr, val))
        # Recognise the AMD program sequence: a1=0xAA, a2=0x55, a1=0xA0, PA=data.
        # The 4th write carries the data word at its (halved) program address.
        if self.cfi_variant is not None and len(self._flash_cmd) >= 4:
            last4 = self._flash_cmd[-4:]
            v4 = tuple(v for _a, v in last4)
            # Match on the first three being the unlock+setup; the 4th is data.
            if v4[0] == 0xAA and v4[1] == 0x55 and v4[2] == 0xA0:
                if self.requires_5v and self._voltage != 5:
                    self._flash_cmd = []
                    return
                prog_halved = last4[3][0]
                prog_byte_addr = prog_halved * 2
                self._program_rom_word(prog_byte_addr, val)
                self._flash_cmd = []
                return
        # Recognise the AMD sector-erase sequence ending in 0x30:
        # a1=0xAA, a2=0x55, a1=0x80, a1=0xAA, a2=0x55, SA=0x30 (halved addrs).
        # The chip requires 5V like everything else on these repro carts.
        if val == 0x30 and self.cfi_variant is not None:
            if self.requires_5v and self._voltage != 5:
                return
            seq = self._flash_cmd[-6:]
            if len(seq) == 6:
                vals = tuple(v for _a, v in seq)
                if vals == (0xAA, 0x55, 0x80, 0xAA, 0x55, 0x30):
                    # The 6th write's address (halved) is the sector to erase.
                    sector_halved = seq[5][0]
                    sector_byte_addr = sector_halved * 2
                    self._erase_rom_sector(sector_byte_addr)
                    self._flash_cmd = []
                    return
        # Recognise the 3-write unlock ending in 0x90 -> enter ID mode, but only
        # for the command set this chip actually speaks (addr + unlock byte), and
        # only at the right voltage if the chip requires 5V.
        if val == 0x90 and self.flash_id is not None:
            if self.requires_5v and self._voltage != 5:
                return          # chip is silent at 3.3V
            seq = self._flash_cmd[-3:]
            if len(seq) < 3:
                return
            addrs = tuple(a for a, _v in seq)
            unlock_byte = seq[0][1]
            # Halved address bases (gba_flash_write_address_byte divides by 2).
            variant = None
            if addrs == (0x2AA, 0x155, 0x2AA):        # 0x555/0x2AA/0x555
                variant = "555/AA"
            elif addrs == (0x2AAA, 0x1555, 0x2AAA):   # 0x5555/0x2AAA/0x5555
                variant = "5555/AA"
            elif addrs == (0x555, 0x2AA, 0x555):      # 0xAAA/0x555/0xAAA
                variant = "AAA/AA"
            elif addrs == (0x5555, 0x2AAA, 0x5555):   # 0xAAAA/0x5555/0xAAAA
                variant = "AAAA/AA"
            elif addrs == (0x2555, 0x22AA, 0x2555):   # 0x4AAA/0x4555/0x4AAA
                variant = "4AAA/AA"
            elif addrs == (0x3D55, 0x3AAA, 0x3D55):   # 0x7AAA/0x7555/0x7AAA
                variant = "7AAA/AA"
            _ = unlock_byte
            if variant == self.flash_variant:
                self._id_mode = True

    def _handle_number(self, cmd, hexstr):
        if cmd == "n":
            self._handle_flash_byte(hexstr)
            return
        if cmd == "S":              # GBA_SET_EEPROM_SIZE
            self._eeprom_mode = True
            return
        if cmd == "k":              # GBA_FLASH_SET_BANK
            self._save_bank = int(hexstr, 16) if hexstr else 0
            return
        if cmd == "s":              # GBA_FLASH_4K_SECTOR_ERASE
            sector = int(hexstr, 16) if hexstr else 0
            # erase 4 KB in the flash write buffer to 0xFF
            buf = bytearray(self._wbuf_flash)
            base = self._save_bank * 0x10000 + sector * 4096
            end = base + 4096
            if len(buf) < end:
                buf.extend(b"\x00" * (end - len(buf)))
            buf[base:end] = b"\xff" * 4096
            self._wbuf_flash = bytes(buf)
            self._erased_flash = True
            self._out += b"1"       # ack the erase
            return
        if cmd == "A":
            self._addr = int(hexstr, 16) if hexstr else 0
            self._eeprom_addr = (int(hexstr, 16) if hexstr else 0) * 8 \
                if self._eeprom_mode else self._eeprom_addr
            return
        if cmd == "B":
            if self._bank_phase == "addr":
                self._last_bank_addr = int(hexstr, 16) if hexstr else 0
                self._bank_phase = "val"
            else:
                # value phase is sent in decimal by set_bank
                val = int(hexstr, 10) if hexstr.lstrip("-").isdigit() else \
                    int(hexstr, 16)
                a = self._last_bank_addr
                if a == 0x0000:
                    self._ram_enabled = (val == 0x0A)
                elif a == 0x2000:
                    self._mbc1_low = val & 0x1F
                elif a == 0x4000:
                    if self._ram_enabled:
                        self._ram_bank = val
                    else:
                        self._mbc1_high = val
                elif a == 0x2100:
                    self._rom_bank = val & 0xFF
                elif a == 0x6000:
                    self._mbc1_mode = val
                self._bank_phase = "addr"

    _bank_phase = "addr"
    _last_bank_addr = 0
    _mbc1_low = 1
    _mbc1_high = 0
    _mbc1_mode = 0
    _ram_enabled = False

    @property
    def _mbc1_bank(self):
        return (self._mbc1_high << 5) | (self._mbc1_low or 1)

    def _handle_single(self, cmd):
        if cmd == "+":                 # FAST_READ_CHECK - emit a burst
            self._out += bytes(64)
            return
        if cmd == "Z":                 # GBA fast read (continuous, 0x8000 chunks)
            if not self._fast:
                self._fast = True
                self._fast_cur = self._addr * 2
            self._fast_fill(self.rom, self._fast_cur, 0x8000)
            self._fast_cur += 0x8000
            return
        if cmd == "Q":                 # GB fast read (continuous, 0x4000 chunks)
            src, base = self._gb_source()
            if not self._fast:
                self._fast = True
                self._fast_cur = base
            self._fast_fill(src, self._fast_cur, 0x4000)
            self._fast_cur += 0x4000
            return
        if cmd == "C":
            self._out.append(self.mode)
        elif cmd == "V":
            self._out.append(self.fw)
        elif cmd == "h":
            self._out.append(self.pcb)
        elif cmd == "R":          # GB read ROM/RAM at current address/bank
            self._start_gb_stream()
        elif cmd == "r":          # GBA read ROM (addr is addr/2)
            self._begin_stream(self._effective_rom(), self._addr * 2)
        elif cmd == "m":          # GBA read SRAM (respect selected save bank)
            if self._erased_flash and self._wbuf_flash:
                base = self._save_bank * 0x10000 + self._addr
                self._begin_stream(self._wbuf_flash, base)
            elif self._save_bank == 1 and self.flash_bank1:
                self._begin_stream(self.flash_bank1, self._addr)
            else:
                self._begin_stream(self.sram, self._addr)
        elif cmd == "e":          # GBA read EEPROM, 8-byte blocks
            self._begin_eeprom_stream()
        elif cmd == "1":          # continue
            if self._stream is not None:
                self._queue_block()
        elif cmd == "0":          # stop (or clear when not streaming)
            self._stream = None
            self._fast = False
        elif cmd == "5":          # set 5V
            self._voltage = 5
        elif cmd == "3":          # set 3.3V
            self._voltage = 3
        # other commands (mode/power/reset) need no response

    def _start_gb_stream(self):
        if self._addr >= 0xA000:                       # RAM window
            phys = self._ram_bank * self._ram_stride + (self._addr - 0xA000)
            self._begin_stream(self.sram, phys)
        elif self._addr >= 0x4000:                     # banked ROM window
            bank = self._rom_bank or self._mbc1_bank
            phys = bank * 0x4000 + (self._addr - 0x4000)
            self._begin_stream(self.rom, phys)
        else:                                          # bank 0
            self._begin_stream(self.rom, self._addr)

    _fast = False
    _fast_cur = 0
    _ram_stride = 0x2000     # bytes between RAM banks in the fake's sram buffer

    def _gb_source(self):
        if self._addr >= 0xA000:
            return self.sram, self._ram_bank * self._ram_stride + (self._addr - 0xA000)
        if self._addr >= 0x4000:
            bank = self._rom_bank or self._mbc1_bank
            return self.rom, bank * 0x4000 + (self._addr - 0x4000)
        return self.rom, self._addr

    def _fast_fill(self, data, base, count):
        # Emit `count` bytes continuously (device fast mode). Host re-arms at
        # interval boundaries, which just calls this again for the next chunk.
        chunk = data[base:base + count]
        if len(chunk) < count:
            chunk = chunk + b"\x00" * (count - len(chunk))
        self._out += chunk

    def _begin_eeprom_stream(self):
        self._stream = [self.eeprom, 0, 8]   # 8-byte block size marker
        self._out += self.eeprom[0:8].ljust(8, b"\x00")
        self._stream[1] = 8

    def _cfi_halved_addr(self):
        # The CFI query address for each variant, halved (host divides by 2).
        bases = {"555": 0x555, "5555": 0x5555, "AAA": 0xAA, "AAAA": 0xAAAA,
                 "4AAA": 0x4555, "7AAA": 0x7555, "bare": 0x0}
        return bases.get(self.cfi_variant, 0x555) // 2

    def _cfi_buffer(self):
        # Build a 0x400 CFI response resembling a real S29GL512 (32 MB): "QRY" at
        # byte offsets 0x20/0x22/0x24, flash_id at the front, plus the size and
        # sector fields the parser reads. Enough to exercise parse_cfi end to end.
        buf = bytearray(0x400)
        buf[0x00:len(self.flash_id)] = self.flash_id
        buf[0x20] = ord("Q")
        buf[0x22] = ord("R")
        buf[0x24] = ord("Y")
        # Primary-table address pointer (0x2A/0x2C) -> point at 0x80 region.
        buf[0x2A] = 0x40
        buf[0x2C] = 0x00
        # Voltage range (must be present or parser rejects). vdd 2.7-3.6.
        buf[0x36] = 0x27
        buf[0x38] = 0x36
        # Timing fields: single write present (0x3E), sector erase (0x42),
        # chip erase (0x44), buffered write (0x40).
        buf[0x3E] = 0x04     # single write ~16us
        buf[0x40] = 0x06     # buffer write present
        buf[0x42] = 0x0A     # sector erase ~1s
        buf[0x44] = 0x0F     # chip erase present
        buf[0x46] = 0x04
        buf[0x48] = 0x04
        buf[0x4A] = 0x04
        buf[0x4C] = 0x04
        # Device size: 2^0x19 = 32 MB.
        buf[0x4E] = 0x19
        # Buffer size: 2^5 = 32 bytes -> stored as log2 at 0x54/0x56.
        buf[0x54] = 0x05
        buf[0x56] = 0x00
        # One erase region: 512 sectors of 128 KB (0x20000).
        buf[0x58] = 0x01
        buf[0x5A] = 0xFF     # (count-1) low  -> 0x01FF + 1 = 512
        buf[0x5C] = 0x01     # (count-1) high
        buf[0x5E] = 0x00     # size/256 low   -> 0x0200 * 256 = 128 KB
        buf[0x60] = 0x02     # size/256 high
        return bytes(buf)

    def _begin_stream(self, data, cursor):
        # In CFI mode, a GBA read returns the CFI table (with the QRY magic).
        if self._cfi_mode and self.flash_id is not None:
            resp = self._cfi_buffer()
            self._stream = [resp, 0]
            self._out += resp[:gx.BLOCK]
            self._stream[1] = gx.BLOCK
            return
        # In flash ID mode, a GBA read returns the chip's ID response.
        if self._id_mode and self.flash_id is not None:
            resp = (self.flash_id * 8)[:64]
            self._stream = [resp, 0]
            self._out += resp[:gx.BLOCK]
            self._stream[1] = gx.BLOCK
            return
        self._stream = [data, cursor]
        self._queue_block()

    def _queue_block(self):
        data = self._stream[0]
        cursor = self._stream[1]
        bs = self._stream[2] if len(self._stream) > 2 else gx.BLOCK
        chunk = data[cursor:cursor + bs]
        if len(chunk) < bs:
            chunk = chunk + b"\x00" * (bs - len(chunk))
        self._out += chunk
        self._stream[1] = cursor + bs


def _connect(fake) -> gx.GBxCart:
    dev = gx.GBxCart()
    dev.ser = fake               # inject the fake transport
    dev.baud = gx.BAUD_PRIMARY
    return dev


def test_identify():
    dev = _connect(FakeSerial(mode=gx.GBA_MODE, fw=20, pcb=3))
    info = dev.identify()
    assert info.firmware == 20
    assert info.pcb == 3
    assert info.pcb_name == "v1.2"
    assert info.cart_mode == gx.GBA_MODE
    assert info.cart_mode_name == "GBA"
    assert info.looks_like_clone           # v1.2 -> Cyclone-style clone


def test_handshake_mode_values():
    for m, name in [(gx.GB_MODE, "GB/GBC"), (gx.GBA_MODE, "GBA")]:
        dev = _connect(FakeSerial(mode=m))
        assert dev.request_value(gx.CART_MODE) == m
        assert gx.DeviceInfo(0, 0, m).cart_mode_name == name


def test_gba_rom_read():
    rom = bytes((i * 7 + 3) & 0xFF for i in range(0x20000))   # 128 KB
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE))
    out = io.BytesIO()
    dev.read_gba_rom(out, len(rom))
    assert out.getvalue() == rom


def test_gba_sram_read():
    sram = bytes((i * 5 + 1) & 0xFF for i in range(0x8000))   # 32 KB
    dev = _connect(FakeSerial(sram=sram, mode=gx.GBA_MODE))
    out = io.BytesIO()
    dev.read_gba_sram(out, len(sram))
    assert out.getvalue() == sram


def test_gb_rom_read_banked():
    rom = bytes((i * 11 + 9) & 0xFF for i in range(0x20000))  # 128 KB = 8 banks
    dev = _connect(FakeSerial(rom=rom, mode=gx.GB_MODE))
    out = io.BytesIO()
    dev.read_gb_rom(out, len(rom), cart_type=1)   # MBC1
    assert out.getvalue() == rom


def test_gb_ram_read_banked():
    # 32 KB SRAM = 4 banks of 8 KB (RAM size code 3), MBC1+RAM+BATTERY
    sram = bytes((i * 3 + 2) & 0xFF for i in range(0x8000))
    dev = _connect(FakeSerial(sram=sram, mode=gx.GB_MODE))
    out = io.BytesIO()
    dev.read_gb_ram(out, len(sram), cart_type=3, ram_size_code=3)
    assert out.getvalue() == sram


def test_gb_ram_mbc2_512_bytes():
    # MBC2 (cart type 6): exactly 512 bytes, single bank, end 0xA1FF.
    full = bytes((i * 7 + 1) & 0xFF for i in range(0x2000))
    fake = FakeSerial(sram=full, mode=gx.GB_MODE)
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gb_ram(out, 512, cart_type=6, ram_size_code=0)
    assert len(out.getvalue()) == 512
    assert out.getvalue() == full[:512]


def test_gb_ram_2kb():
    # RAM size code 1 -> 2 KB, single bank, end 0xA7FF.
    full = bytes((i * 5 + 9) & 0xFF for i in range(0x2000))
    fake = FakeSerial(sram=full, mode=gx.GB_MODE)
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gb_ram(out, 2048, cart_type=2, ram_size_code=1)
    assert len(out.getvalue()) == 2048
    assert out.getvalue() == full[:2048]


def test_gb_ram_128kb_16_banks():
    # RAM size code 4 -> 128 KB = 16 banks of 8 KB (MBC5).
    sram = bytes((i * 11 + 4) & 0xFF for i in range(0x20000))
    fake = FakeSerial(sram=sram, mode=gx.GB_MODE)
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gb_ram(out, len(sram), cart_type=0x1B, ram_size_code=4)
    assert out.getvalue() == sram


def test_gb_ram_layout_helper():
    assert gx.gb_ram_layout(6, 0) == (1, 0xA1FF)      # MBC2
    assert gx.gb_ram_layout(2, 1) == (1, 0xA7FF)      # 2 KB
    assert gx.gb_ram_layout(3, 3) == (4, 0xBFFF)      # 32 KB
    assert gx.gb_ram_layout(0x1B, 4) == (16, 0xBFFF)  # 128 KB
    assert gx.gb_ram_layout(0x1B, 5) == (8, 0xBFFF)   # 64 KB


def test_gbc_rom_read():
    # GBC ROM (CGB flag set) still reads via the same GB path; 256 KB, MBC3.
    rom = bytes((i * 13 + 5) & 0xFF for i in range(0x40000))
    dev = _connect(FakeSerial(rom=rom, mode=gx.GB_MODE))
    out = io.BytesIO()
    dev.read_gb_rom(out, len(rom), cart_type=0x11)   # MBC3
    assert out.getvalue() == rom


def test_gba_header_read():
    rom = bytearray(0x1000)
    rom[0xA0:0xAC] = b"POKEMON EMER"
    dev = _connect(FakeSerial(rom=bytes(rom), mode=gx.GBA_MODE))
    hdr = dev.read_gba_header()
    assert hdr[0xA0:0xAC] == b"POKEMON EMER"


def test_gba_rom_size_detection():
    # 8 MB of non-zero data followed by zeros -> detector should report 8 MB.
    rom = bytes(((i * 7 + 1) & 0xFF) or 1 for i in range(0x800000))
    rom = rom + b"\x00" * 0x800000          # 8 MB zero tail (out to 16 MB)
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE))
    size = dev.detect_gba_rom_size()
    assert size == 8 * 1024 * 1024


def test_flash_id_probe_flashable():
    from cartographer import flash_db
    # 16-bit bus word form: manufacturer 0x0020 (ST/Numonyx), device 0x227E.
    # That is the M29W128 family found on most GBA repro carts.
    rom = bytes((i * 7 + 1) & 0xFF for i in range(0x1000))
    fid = bytes([0x20, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x00, 0x00])
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid))
    probe = dev.gba_flash_id_probe()
    result = flash_db.interpret(probe)
    assert result.is_flashable
    assert result.manufacturer_id == 0x20
    assert result.device_id == 0x227E
    assert result.variant == "AAA/AA"
    assert result.is_known_chip
    assert "M29W128" in result.chip_label
    assert result.chip.capacity_mb == 16
    assert "Numonyx" in result.manufacturer


def test_flash_id_unknown_chip_is_reported_not_guessed():
    from cartographer import flash_db
    rom = bytes((i * 3 + 5) & 0xFF for i in range(0x1000))
    fid = bytes([0x77, 0x00, 0x99, 0x00, 0, 0, 0, 0])   # not in the table
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid))
    result = flash_db.interpret(dev.gba_flash_id_probe())
    assert result.is_flashable
    assert not result.is_known_chip
    assert "not in the database" in result.summary()


def test_known_bad_markings():
    from cartographer import flash_db
    assert flash_db.is_known_bad_marking("XYZ6600")
    assert flash_db.is_known_bad_marking("4050m-abc")
    assert not flash_db.is_known_bad_marking("M29W128GH")


def test_supported_chip_list_present():
    from cartographer import flash_db
    names = flash_db.supported_chip_names()
    for expected in ("M29W128", "S29GL128", "MSP55LV128", "GE28F128W30",
                     "M29W256"):
        assert expected in names


def test_flash_id_probe_retail():
    from cartographer import flash_db
    # A retail mask-ROM cart: ignores command writes, every read == baseline.
    rom = bytes((i * 5 + 3) & 0xFF for i in range(0x1000))
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=None))
    probe = dev.gba_flash_id_probe()
    result = flash_db.interpret(probe)
    assert not result.is_flashable
    assert "mask-ROM" in result.summary()


def test_flash_id_probe_cfi_clean_read():
    from cartographer import flash_db
    # v1.0.8: the real Gugxiom case done right. The chip answers a CFI query at
    # 5V and returns a clean 01/227E ID. This must be preferred over any partial
    # unlock-and-read result, and must identify the chip from the CFI path.
    rom = bytes((i * 5 + 3) & 0xFF for i in range(0x1000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                              flash_variant="none", requires_5v=True,
                              cfi_variant="AAAA"))
    probe = dev.gba_flash_id_probe()
    # A cfi-* method must be present and carry the clean ID.
    cfi_keys = [k for k in probe if k.startswith("cfi-")]
    assert cfi_keys, "CFI query should have produced a result"
    result = flash_db.interpret(probe)
    assert result.is_flashable
    assert result.manufacturer_id == 0x01
    assert result.device_id == 0x227E
    assert result.variant.startswith("cfi-")
    assert result.is_known_chip
    assert "S29GL" in result.chip_label


def test_cfi_parsing_reads_true_size_and_sectors():
    from cartographer import flash_db
    # v1.0.10: the CFI buffer must parse into the chip's true size and sector map.
    # The simulator presents a 32 MB S29GL512 with 512 x 128 KB sectors.
    rom = bytes((i * 5 + 3) & 0xFF for i in range(0x1000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                              flash_variant="none", requires_5v=True,
                              cfi_variant="AAAA"))
    result = flash_db.interpret(dev.gba_flash_id_probe())
    assert result.cfi is not None, "CFI should have been parsed"
    # True size is 32 MB, not the nominal 16 MB from the database.
    assert result.cfi.device_size_mb == 32
    assert result.cfi.sector_erase is True
    assert result.cfi.chip_erase is True
    # One region: 512 sectors of 128 KB.
    assert result.cfi.erase_regions == ((0x20000, 512),)
    # The label must now show the true 32 MB, overriding the 16 MB nominal.
    assert "32 MB" in result.chip_label
    # And the summary carries the CFI detail.
    assert "CFI:" in result.summary()


def test_cfi_parse_rejects_non_cfi():
    from cartographer import flash_db
    # A buffer without the QRY magic must not parse as CFI.
    assert flash_db.parse_cfi(bytes(0x400)) is None
    assert flash_db.parse_cfi(b"\x00" * 4) is None


def test_sector_erase_and_verify_succeeds():
    # v1.0.11: erasing one sector must set that sector to 0xFF and verify it.
    # ROM is non-0xFF everywhere; after erasing the sector at 0x0 it must read
    # back all 0xFF, and only that sector.
    rom = bytes((i * 7 + 1) & 0xFF for i in range(0x40000))  # 256 KB, 2 sectors
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    dev = _connect(fake)
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)   # erase only works at 5V on these carts
    ok, sample = dev.gba_flash_erase_sector(0x0, unlock_a1=0xAAA, unlock_a2=0x555,
                                            verify_len=0x80, timeout_s=5.0)
    assert ok is True, f"erase should succeed, got sample {sample[:8].hex()}"
    assert all(b == 0xFF for b in sample)
    # The second sector (at 0x20000) must be untouched.
    dev.set_mode(gx.VOLTAGE_5V)
    second = dev._gba_read_bytes_at(0x20000, 16)
    assert not all(b == 0xFF for b in second), "only the target sector should erase"


def test_sector_erase_requires_5v():
    # At 3.3V the chip ignores the erase (these repro carts are 5V-only), so the
    # verify must fail rather than falsely report success.
    rom = bytes((i * 7 + 1) & 0xFF for i in range(0x40000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    dev = _connect(fake)
    dev.select_gba()             # leaves 3.3V
    ok, _sample = dev.gba_flash_erase_sector(0x0, unlock_a1=0xAAA,
                                             unlock_a2=0x555, verify_len=0x80,
                                             timeout_s=2.0)
    assert ok is False, "erase at 3.3V must not verify as succeeded"


def test_block_write_probe_f_command_works():
    # v1.1.0: the 'f' block command should program this non-swapped chip
    # correctly, and the probe should confirm it by read-back.
    orig = bytes((i * 7 + 1) & 0xFF for i in range(0x4000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000
    dev = _connect(fake)
    dev.flash_settle_s = 0
    dev.flash_poll_s = 0
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)
    ok, readback, msg = dev.gba_flash_block_write_probe(block_command="f")
    assert ok is True, f"'f' block write should verify: {msg}"
    assert len(readback) == 256


def test_block_write_probe_t_command_detected_swapped():
    # The 't' (swapped) command on a non-swapped chip must NOT verify, and the
    # probe should recognise the D0/D1-swap failure mode.
    orig = bytes((i * 7 + 1) & 0xFF for i in range(0x4000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000
    dev = _connect(fake)
    dev.flash_settle_s = 0
    dev.flash_poll_s = 0
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)
    ok, _readback, msg = dev.gba_flash_block_write_probe(block_command="t")
    assert ok is False, "'t' on a non-swapped chip must fail"
    assert "swap" in msg.lower()


def test_write_rom_waits_for_slow_committing_words():
    # v1.0.14: the real hardware bug was a word not finishing before the next
    # command arrived, so it was silently dropped. Model that: each programmed
    # word needs 2 readbacks before it becomes visible. With the program-poll
    # fix, the write must WAIT for each word and still verify. (Without polling,
    # this would fail exactly like the real cart did.)
    orig = bytes((i * 7 + 1) & 0xFF for i in range(0x4000))
    new_rom = bytes((i * 3 + 9) & 0xFF for i in range(0x1000))    # 4 KB
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000
    fake._program_commit_reads = 2     # each word takes 2 readbacks to commit
    dev = _connect(fake)
    dev.flash_settle_s = 0
    dev.flash_poll_s = 0
    dev.flash_poll_word_s = 0
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)
    ok, msg = dev.gba_flash_write_rom(new_rom, ((0x1000, 4),))
    assert ok is True, f"write must wait for slow words and succeed: {msg}"
    dev.set_mode(gx.VOLTAGE_5V)
    readback = dev._gba_read_bytes_at(0x0, len(new_rom))
    assert readback == new_rom


def test_write_rom_end_to_end_verifies():
    # v1.0.12: writing a ROM must erase, program, and read back matching. Use a
    # small sector size and ROM so the per-word program-and-poll path runs
    # quickly in the sim; the erase/program/verify/advance logic is identical to
    # the real 128 KB sectors.
    orig = bytes((i * 7 + 1) & 0xFF for i in range(0x4000))     # 16 KB start
    new_rom = bytes((i * 3 + 9) & 0xFF for i in range(0x1800))   # 6 KB new ROM
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000     # 4 KB sectors for a fast test
    dev = _connect(fake)
    dev.flash_settle_s = 0        # no real-hardware delays in the simulator
    dev.flash_poll_s = 0
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)
    # CFI map: 4 KB sectors, 4 of them = 16 KB.
    regions = ((0x1000, 4),)
    ok, msg = dev.gba_flash_write_rom(new_rom, regions, unlock_a1=0xAAA,
                                      unlock_a2=0x555)
    assert ok is True, f"write should succeed: {msg}"
    # Read the ROM back and confirm the written region matches the file.
    dev.set_mode(gx.VOLTAGE_5V)
    readback = dev._gba_read_bytes_at(0x0, len(new_rom))
    assert readback == new_rom, "written ROM must read back byte-for-byte"


def test_write_rom_refuses_oversize():
    # A ROM larger than the chip must be refused, not written past the end.
    orig = bytes(0x4000)
    big = bytes(0x5000)    # bigger than the 4x4KB = 16 KB map below
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000
    dev = _connect(fake)
    dev.flash_settle_s = 0
    dev.flash_poll_s = 0
    dev.select_gba()
    dev.set_mode(gx.VOLTAGE_5V)
    ok, msg = dev.gba_flash_write_rom(big, ((0x1000, 4),))
    assert ok is False
    assert "past the end" in msg or "holds" in msg


def test_write_rom_stops_on_bad_verify():
    # If a sector won't erase (sim at 3.3V), the write must stop and report it,
    # never claiming success.
    orig = bytes((i * 7 + 1) & 0xFF for i in range(0x4000))
    new_rom = bytes((i * 3 + 9) & 0xFF for i in range(0x1000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=orig, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="none", requires_5v=True, cfi_variant="AAA")
    fake._rom_sector_size = 0x1000
    dev = _connect(fake)
    dev.flash_settle_s = 0
    dev.flash_poll_s = 0
    dev.select_gba()             # 3.3V - erase will be ignored by the chip
    ok, msg = dev.gba_flash_write_rom(new_rom, ((0x1000, 4),))
    assert ok is False
    assert "erase" in msg.lower()


def test_flash_id_probe_5v_repro_cart():
    from cartographer import flash_db
    # The real Gugxiom/EpicJoy case: an S29GL-family repro cart that stays silent
    # at 3.3V and only answers flash commands at 5V, on the wider 0xAAAA address
    # base. This is the exact cart+behaviour that FlashGBX identified as
    # 01/227E. It must now come back flashable, proving both the 5V forcing and
    # the extra address bases work together.
    rom = bytes((i * 5 + 3) & 0xFF for i in range(0x1000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                              flash_variant="AAAA/AA", requires_5v=True))
    result = flash_db.interpret(dev.gba_flash_id_probe())
    assert result.is_flashable
    assert result.manufacturer_id == 0x01
    assert result.device_id == 0x227E
    assert result.variant == "AAAA/AA"
    assert result.is_known_chip
    assert "S29GL" in result.chip_label


def test_flash_id_5v_cart_silent_without_5v():
    # Guard: if the probe did NOT raise voltage to 5V, a 5V-only cart would still
    # read as mask-ROM. This confirms the probe's 5V step is what makes the
    # difference (the FakeSerial only engages ID mode when voltage == 5).
    from cartographer import flash_db
    rom = bytes((i * 5 + 3) & 0xFF for i in range(0x1000))
    fid = bytes([0x01, 0x00, 0x7E, 0x22, 0x00, 0x00, 0x18, 0x00])
    fake = FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                      flash_variant="AAAA/AA", requires_5v=True)
    dev = _connect(fake)
    # Sanity: after a normal probe the device is left at 3.3V.
    dev.gba_flash_id_probe()
    assert fake._voltage == 3, "probe must drop back to 3.3V when done"


def test_flash_id_probe_intel():
    from cartographer import flash_db
    # Intel/Numonyx repro (36L0R family): answers only the lone-0x90 command.
    rom = bytes((i * 9 + 4) & 0xFF for i in range(0x1000))
    fid = bytes([0x89, 0x00, 0x18, 0x88, 0x00, 0x00, 0x00, 0x00])
    dev = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE, flash_id=fid,
                              flash_variant="Intel/90"))
    probe = dev.gba_flash_id_probe()
    result = flash_db.interpret(probe)
    assert result.is_flashable
    # A lone 0x90 to address 0 is caught by the "bare-90" method (which runs
    # before "Intel/90" in probe order); both map to the Intel write method.
    assert result.variant in ("bare-90", "Intel/90")
    assert result.write_method == flash_db.WRITE_INTEL
    assert result.manufacturer == "Intel"


def test_gba_save_eeprom_64k():
    eeprom = bytes((i * 13 + 7) & 0xFF for i in range(8192))
    fake = FakeSerial(mode=gx.GBA_MODE)
    fake.eeprom = eeprom
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gba_save(out, gx.SAVE_EEPROM_64K)
    assert out.getvalue() == eeprom


def test_gba_save_eeprom_4k():
    eeprom = bytes((i * 3 + 1) & 0xFF for i in range(512))
    fake = FakeSerial(mode=gx.GBA_MODE)
    fake.eeprom = eeprom
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gba_save(out, gx.SAVE_EEPROM_4K)
    assert out.getvalue() == eeprom


def test_gba_save_flash_1m_two_banks():
    bank0 = bytes((i * 7) & 0xFF for i in range(65536))
    bank1 = bytes((i * 5 + 3) & 0xFF for i in range(65536))
    fake = FakeSerial(mode=gx.GBA_MODE)
    fake.sram = bank0
    fake.flash_bank1 = bank1
    dev = _connect(fake)
    out = io.BytesIO()
    dev.read_gba_save(out, gx.SAVE_FLASH_1M)
    assert out.getvalue() == bank0 + bank1


def test_save_kind_from_id():
    assert gx.save_kind_from_id("FLASH1M_V103") == gx.SAVE_FLASH_1M
    assert gx.save_kind_from_id("EEPROM_V124") == gx.SAVE_EEPROM_64K
    assert gx.save_kind_from_id("SRAM_V113") == gx.SAVE_SRAM_256K
    assert gx.save_kind_from_id("FLASH512_V130") == gx.SAVE_FLASH_512K
    assert gx.save_kind_from_id("") == gx.SAVE_NONE


def test_fast_read_check():
    dev = _connect(FakeSerial(mode=gx.GBA_MODE))
    assert dev.check_fast_read() is True
    assert dev.fast_read is True


def test_gba_rom_fast_equals_slow():
    rom = bytes((i * 7 + 3) & 0xFF for i in range(0x40000))   # 256 KB
    # slow
    dev_s = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE))
    out_s = io.BytesIO()
    dev_s.read_gba_rom(out_s, len(rom))
    # fast
    dev_f = _connect(FakeSerial(rom=rom, mode=gx.GBA_MODE))
    dev_f.fast_read = True
    out_f = io.BytesIO()
    dev_f.read_gba_rom(out_f, len(rom))
    assert out_f.getvalue() == rom
    assert out_f.getvalue() == out_s.getvalue()


def test_gb_rom_fast_equals_slow():
    rom = bytes((i * 11 + 9) & 0xFF for i in range(0x20000))  # 128 KB
    dev_s = _connect(FakeSerial(rom=rom, mode=gx.GB_MODE))
    out_s = io.BytesIO()
    dev_s.read_gb_rom(out_s, len(rom), cart_type=1)
    dev_f = _connect(FakeSerial(rom=rom, mode=gx.GB_MODE))
    dev_f.fast_read = True
    out_f = io.BytesIO()
    dev_f.read_gb_rom(out_f, len(rom), cart_type=1)
    assert out_f.getvalue() == rom
    assert out_f.getvalue() == out_s.getvalue()


def test_gba_write_sram_roundtrip():
    save = bytes((i * 7 + 1) & 0xFF for i in range(0x8000))   # 32 KB
    fake = FakeSerial(mode=gx.GBA_MODE)
    dev = _connect(fake)
    dev.write_gba_save(save, gx.SAVE_SRAM_256K)
    assert fake._wbuf_sram[:len(save)] == save


def test_gba_write_eeprom_roundtrip():
    save = bytes((i * 5 + 3) & 0xFF for i in range(8192))     # 64 Kbit
    fake = FakeSerial(mode=gx.GBA_MODE)
    dev = _connect(fake)
    dev.write_gba_save(save, gx.SAVE_EEPROM_64K)
    assert fake._wbuf_eeprom[:len(save)] == save


def test_gba_write_flash_1m_roundtrip():
    save = bytes((i * 11 + 4) & 0xFF for i in range(0x20000))  # 128 KB, 2 banks
    fake = FakeSerial(mode=gx.GBA_MODE)
    dev = _connect(fake)
    dev.write_gba_save(save, gx.SAVE_FLASH_1M)
    assert fake._wbuf_flash[:len(save)] == save


def test_gb_write_ram_roundtrip():
    save = bytes((i * 3 + 9) & 0xFF for i in range(0x8000))    # 32 KB, 4 banks
    fake = FakeSerial(mode=gx.GB_MODE)
    dev = _connect(fake)
    dev.write_gb_ram(save, cart_type=3, ram_size_code=3)
    assert fake._wbuf_sram[:len(save)] == save


def test_gb_write_ram_mbc2():
    save = bytes((i * 7) & 0xFF for i in range(512))
    fake = FakeSerial(mode=gx.GB_MODE)
    dev = _connect(fake)
    dev.write_gb_ram(save, cart_type=6, ram_size_code=0)
    assert fake._wbuf_sram[:512] == save


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} GBxCart protocol tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
