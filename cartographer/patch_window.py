"""
patch_window.py - GBA batteryless save patcher dialog.

A self-contained, device-independent tool: pick a GBA ROM, and Cartographer
detects the save type, SRAM-patches it if needed (for Flash/EEPROM repro carts),
then applies the batteryless patch - producing a ROM ready to flash.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QComboBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QVBoxLayout,
)

from . import bl_patcher as blp
from . import pipeline
from . import flash_patcher
from . import sram_patcher


class PatchDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GBA Save Patching")
        self.setMinimumWidth(580)
        self.rom_path: str = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        intro = QLabel(
            "Two jobs live here. Prepare ROM does the full batteryless chain "
            "for a batteryless SRAM repro cart: Flash and EEPROM save games "
            "(Pokemon Gen 3, for instance) are SRAM-patched first, then "
            "batteryless-patched. SRAM patch only stops after the first step, "
            "for a ROM going on to another tool such as a flash-save patcher. "
            "SRAM patch by bbsan2k; batteryless patch and on-cart payload by "
            "metroid-maniac.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        lay.addWidget(intro)

        file_box = QGroupBox("ROM")
        frow = QHBoxLayout(file_box)
        self.lbl_file = QLabel("No ROM selected")
        self.lbl_file.setObjectName("mono")
        frow.addWidget(self.lbl_file, stretch=1)
        btn_browse = QPushButton("Choose .gba\u2026")
        btn_browse.clicked.connect(self.choose_rom)
        frow.addWidget(btn_browse)
        lay.addWidget(file_box)

        self.lbl_detect = QLabel("")
        self.lbl_detect.setObjectName("mono")
        self.lbl_detect.setWordWrap(True)
        lay.addWidget(self.lbl_detect)

        # Both of these feed the batteryless chain and nothing else. SRAM patch
        # only ignores them entirely, so they sit under one heading that says so
        # rather than floating above the buttons looking like settings for
        # whichever one gets pressed.
        bl_box = QGroupBox("Batteryless options (used by Prepare ROM only)")
        blrow = QVBoxLayout(bl_box)
        self.chk_sram = QCheckBox(
            "SRAM-patch first (required for Flash/EEPROM save games)")
        self.chk_sram.setChecked(True)
        blrow.addWidget(self.chk_sram)

        self.rb_auto = QRadioButton(
            "Flush: auto \u2014 save written back a few seconds after each "
            "in-game save")
        self.rb_keypad = QRadioButton(
            "Flush: keypad \u2014 on demand with L+R+Start+Select (more "
            "compatible)")
        self.rb_auto.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_auto)
        grp.addButton(self.rb_keypad)
        blrow.addWidget(self.rb_auto)
        blrow.addWidget(self.rb_keypad)
        lay.addWidget(bl_box)

        fl_box = QGroupBox("Flash save patch (used by Flash 512K patch only)")
        flrow = QVBoxLayout(fl_box)
        self.cmb_lf = QComboBox()
        self.cmb_lf.addItem("Let the game decide (matches gba-flash.exe)", None)
        self.cmb_lf.addItem("Force spread for a 512-byte save (load factor 7)", 7)
        self.cmb_lf.addItem("Force packed for an 8 KB save (load factor 3)", 3)
        self.cmb_lf.setToolTip(
            "How widely the on-cart payload spreads a save across the flash. "
            "The game normally decides from the EEPROM size it reports, which "
            "on a cart with no EEPROM is whatever the SRAM patch left behind.")
        flrow.addWidget(self.cmb_lf)
        lay.addWidget(fl_box)

        arow = QHBoxLayout()
        self.btn_flash = QPushButton("Flash 512K patch\u2026")
        self.btn_flash.setEnabled(False)
        self.btn_flash.setToolTip(
            "Patch the game to save on a flash chip. Run this after an SRAM "
            "patch for EEPROM games.")
        self.btn_flash.clicked.connect(self.do_flash_patch)
        arow.addWidget(self.btn_flash)
        self.btn_sram = QPushButton("SRAM patch only\u2026")
        self.btn_sram.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.btn_sram.setToolTip(
            "Apply just the SRAM step and stop. Use this when another tool is "
            "going to do the next stage, such as a flash-save patcher.")
        self.btn_sram.clicked.connect(self.do_sram_only)
        arow.addWidget(self.btn_sram)
        arow.addStretch(1)
        self.btn_patch = QPushButton("Prepare ROM\u2026")
        self.btn_patch.setObjectName("primary")
        self.btn_patch.setEnabled(False)
        self.btn_sram.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.btn_patch.clicked.connect(self.do_patch)
        arow.addWidget(self.btn_patch)
        lay.addLayout(arow)

        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        lay.addWidget(self.log)

    def choose_rom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose GBA ROM", "", "GBA ROM (*.gba);;All files (*)")
        if not path:
            return
        self.rom_path = path
        self.lbl_file.setText(os.path.basename(path))
        self.btn_patch.setEnabled(True)
        self.btn_sram.setEnabled(True)
        self.btn_flash.setEnabled(True)
        try:
            data = open(path, "rb").read()
        except OSError as exc:
            self.lbl_detect.setText(f"Cannot read file: {exc}")
            return
        title = data[0xA0:0xAC].decode("ascii", "replace").strip("\x00") \
            if len(data) > 0xB0 else "?"
        if blp.is_already_patched(data):
            self.lbl_detect.setText(
                f"{title}: already batteryless-patched. Patching again will fail.")
            return
        save_id = pipeline.needs_sram_patch(data)
        if save_id:
            self.lbl_detect.setText(
                f"{title}: save type {save_id} \u2014 SRAM patch recommended.")
            self.chk_sram.setChecked(True)
        else:
            self.lbl_detect.setText(
                f"{title}: no Flash/EEPROM signature (native SRAM or unknown) "
                f"\u2014 SRAM patch not needed.")
            self.chk_sram.setChecked(False)

    def do_flash_patch(self) -> None:
        """Patch the ROM to save on a flash chip."""
        try:
            data = open(self.rom_path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot read ROM:\n{exc}")
            return
        factor = self.cmb_lf.currentData()
        try:
            result = flash_patcher.patch_rom(data, force_loadfactor=factor)
        except flash_patcher.FlashPatchError as exc:
            self.log.appendPlainText(f"\u2717 {exc}")
            QMessageBox.warning(self, "Flash patch failed", str(exc))
            return

        base, _ = os.path.splitext(self.rom_path)
        out, _ = QFileDialog.getSaveFileName(
            self, "Save flash-patched ROM", base + "_flash512.gba",
            "GBA ROM (*.gba)")
        if not out:
            return
        try:
            with open(out, "wb") as f:
                f.write(result.data)
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot write output:\n{exc}")
            return

        self.log.appendPlainText(
            f"\u2713 Flash 512K patch. Payload installed at "
            f"0x{result.payload_base:06X}, {flash_patcher.PAYLOAD_LEN} bytes.")
        for name, off, target in result.hooks:
            where = f" -> 0x{target:08X}" if target else ""
            self.log.appendPlainText(f"    {name} at 0x{off:X}{where}")
        if result.forced_loadfactor is not None:
            self.log.appendPlainText(
                f"    Load factor forced to {result.forced_loadfactor}, so the "
                f"game's own EEPROM geometry is ignored.")
        elif result.eeprom_meta_addr:
            self.log.appendPlainText(
                f"    EEPROM geometry read from RAM at "
                f"0x{result.eeprom_meta_addr:07X} at run time.")
        if result.expanded:
            self.log.appendPlainText("    ROM was expanded to fit the payload.")
        self.log.appendPlainText(f"  Wrote {os.path.basename(out)}")

    def do_sram_only(self) -> None:
        """Apply the SRAM step by itself and stop.

        The batteryless chain runs this first and then keeps going. A game
        headed for a flash-save patcher needs the SRAM step and nothing after
        it, which previously meant reaching for a separate tool for a patcher
        this app already carries.
        """
        try:
            data = open(self.rom_path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot read ROM:\n{exc}")
            return
        try:
            result = sram_patcher.patch_rom(data)
        except Exception as exc:  # SramPatchError
            self.log.appendPlainText(f"\u2717 {exc}")
            QMessageBox.warning(self, "SRAM patch failed", str(exc))
            return

        base, _ = os.path.splitext(self.rom_path)
        out, _ = QFileDialog.getSaveFileName(
            self, "Save SRAM-patched ROM", base + "_sram.gba",
            "GBA ROM (*.gba)")
        if not out:
            return
        try:
            with open(out, "wb") as f:
                f.write(result.data)
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot write output:\n{exc}")
            return

        self.log.appendPlainText(
            f"\u2713 SRAM patch only. Save type was {result.save_id}, pattern "
            f"set {result.patch_set}, {result.patches_applied} location(s) "
            f"changed at {', '.join(result.locations)}.")
        self.log.appendPlainText(f"  Wrote {os.path.basename(out)}")
        self.log.appendPlainText(
            "  No batteryless patch was applied. Run the next stage on this "
            "file.")

    def do_patch(self) -> None:
        try:
            data = open(self.rom_path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot read ROM:\n{exc}")
            return
        mode = blp.MODE_KEYPAD if self.rb_keypad.isChecked() else blp.MODE_AUTO
        try:
            result = pipeline.prepare_for_batteryless(
                data, mode, sram_patch=self.chk_sram.isChecked())
        except Exception as exc:  # SramPatchError / PatchError
            self.log.appendPlainText(f"\u2717 {exc}")
            QMessageBox.warning(self, "Patch failed", str(exc))
            return

        base, _ = os.path.splitext(self.rom_path)
        out, _ = QFileDialog.getSaveFileName(
            self, "Save patched ROM", base + result.suffix, "GBA ROM (*.gba)")
        if not out:
            return
        try:
            with open(out, "wb") as f:
                f.write(result.data)
        except OSError as exc:
            QMessageBox.critical(self, "Patch", f"Cannot write output:\n{exc}")
            return

        for line in result.log:
            self.log.appendPlainText("  " + line)
        mode_name = "keypad (L+R+Start+Select)" if mode else "auto"
        self.log.appendPlainText(
            f"\u2713 Done ({mode_name}, {result.bl.save_size // 1024} KB save). "
            f"Wrote {os.path.basename(out)}")
        if result.bl.expanded:
            self.log.appendPlainText("  Note: ROM was expanded to fit the payload.")
