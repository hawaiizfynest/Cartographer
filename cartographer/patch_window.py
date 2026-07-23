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
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout,
)

from . import bl_patcher as blp
from . import pipeline
from . import sram_patcher


class PatchDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GBA Batteryless Save Patch")
        self.setMinimumWidth(580)
        self.rom_path: str = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        intro = QLabel(
            "Prepares a GBA ROM for a batteryless SRAM repro cart. For Flash/"
            "EEPROM save games (e.g. Pokemon Gen 3) the ROM is SRAM-patched first, "
            "then batteryless-patched. SRAM patch by bbsan2k; batteryless patch "
            "and on-cart payload by metroid-maniac.")
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

        self.chk_sram = QCheckBox(
            "SRAM-patch first (required for Flash/EEPROM save games)")
        self.chk_sram.setChecked(True)
        lay.addWidget(self.chk_sram)

        mode_box = QGroupBox("Flush mode")
        mrow = QVBoxLayout(mode_box)
        self.rb_auto = QRadioButton(
            "Auto \u2014 save written back a few seconds after each in-game save")
        self.rb_keypad = QRadioButton(
            "Keypad \u2014 flush on demand with L+R+Start+Select (more compatible)")
        self.rb_auto.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_auto)
        grp.addButton(self.rb_keypad)
        mrow.addWidget(self.rb_auto)
        mrow.addWidget(self.rb_keypad)
        lay.addWidget(mode_box)

        arow = QHBoxLayout()
        self.btn_sram = QPushButton("SRAM patch only\u2026")
        self.btn_sram.setEnabled(False)
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
