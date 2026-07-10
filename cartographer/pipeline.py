"""
pipeline.py - Prepare a GBA ROM for a batteryless SRAM repro cartridge.

For Flash/EEPROM save games (e.g. Pokemon Gen 3), the correct order is:
  1. SRAM-patch (fixes the save read path + bank switching to use SRAM), then
  2. batteryless-patch (redirects writes to SRAM and flushes them to flash).

Games that already save to SRAM natively skip step 1.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import bl_patcher, sram_patcher


@dataclass
class PrepareResult:
    data: bytes
    mode: int
    sram_patched: bool
    save_id: str
    bl: bl_patcher.PatchResult
    log: list = field(default_factory=list)

    @property
    def suffix(self) -> str:
        return self.bl.suffix


def needs_sram_patch(rom: bytes) -> str:
    """Return the detected Flash/EEPROM save id if an SRAM patch applies, else ''."""
    return sram_patcher.detect_save_id(rom)


def prepare_for_batteryless(rom: bytes, mode: int = bl_patcher.MODE_AUTO,
                            sram_patch: bool = True) -> PrepareResult:
    """Run the full SRAM->batteryless pipeline and return the patched ROM."""
    log: list[str] = []
    data = rom
    sram_done = False
    save_id = ""

    if sram_patch:
        save_id = sram_patcher.detect_save_id(rom)
        if save_id:
            res = sram_patcher.patch_rom(rom)
            data = res.data
            sram_done = True
            log.append(f"SRAM-patched ({res.save_id}; {res.patches_applied} "
                       f"patches at {', '.join(res.locations)}).")
        else:
            log.append("No Flash/EEPROM save signature found - treating the ROM "
                       "as native SRAM and skipping the SRAM patch.")

    bl = bl_patcher.patch_rom(data, mode)
    log.extend(bl.log)
    return PrepareResult(data=bl.data, mode=mode, sram_patched=sram_done,
                         save_id=save_id, bl=bl, log=log)
