"""
tools_window.py - offline ROM tools: apply IPS/BPS/UPS patches and Game Genie
codes to a ROM file. No device needed.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from . import __app_name__


class ToolsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} - ROM tools")
        self.setMinimumSize(620, 480)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._patch_tab(), "Apply patch (IPS/BPS/UPS)")
        tabs.addTab(self._cheat_tab(), "Game Genie codes")
        v.addWidget(tabs)

    # -- patch tab ---------------------------------------------------------- #
    def _patch_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Apply a ROM hack patch to a clean base ROM. BPS and UPS check the "
            "base ROM's checksum; IPS can't, so make sure the ROM is right."))

        self.ed_rom = QLineEdit()
        self.ed_rom.setPlaceholderText("Base ROM (.gba / .gb / .gbc)")
        b_rom = QPushButton("Browse\u2026")
        b_rom.clicked.connect(lambda: self._pick(self.ed_rom,
                              "Game ROM (*.gba *.gb *.gbc);;All files (*)"))
        r1 = QHBoxLayout(); r1.addWidget(self.ed_rom); r1.addWidget(b_rom)
        v.addLayout(r1)

        self.ed_patch = QLineEdit()
        self.ed_patch.setPlaceholderText("Patch file (.ips / .bps / .ups)")
        b_patch = QPushButton("Browse\u2026")
        b_patch.clicked.connect(lambda: self._pick(self.ed_patch,
                                "Patch (*.ips *.bps *.ups);;All files (*)"))
        r2 = QHBoxLayout(); r2.addWidget(self.ed_patch); r2.addWidget(b_patch)
        v.addLayout(r2)

        apply_btn = QPushButton("Apply patch and save\u2026")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._do_patch)
        v.addWidget(apply_btn)

        self.patch_log = QPlainTextEdit()
        self.patch_log.setReadOnly(True)
        v.addWidget(self.patch_log, stretch=1)
        return w

    def _do_patch(self) -> None:
        from . import rompatch
        rom_path = self.ed_rom.text().strip()
        patch_path = self.ed_patch.text().strip()
        if not (os.path.isfile(rom_path) and os.path.isfile(patch_path)):
            QMessageBox.warning(self, __app_name__,
                                "Pick both a base ROM and a patch file.")
            return
        try:
            rom = open(rom_path, "rb").read()
            patch = open(patch_path, "rb").read()
            result = rompatch.apply_patch(rom, patch)
        except rompatch.PatchError as exc:
            self.patch_log.appendPlainText(f"\u2717 {exc}")
            QMessageBox.critical(self, __app_name__, str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"File error: {exc}")
            return

        self.patch_log.appendPlainText(
            f"Format: {result.patch_format.upper()}. {result.message}")
        # warn but still allow saving if a checksum failed
        base, ext = os.path.splitext(rom_path)
        suggested = f"{base} (patched){ext}"
        out, _ = QFileDialog.getSaveFileName(self, "Save patched ROM", suggested,
                                             "Game ROM (*.gba *.gb *.gbc)")
        if not out:
            return
        try:
            with open(out, "wb") as f:
                f.write(result.data)
            self.patch_log.appendPlainText(f"\u2713 Saved {out}")
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"Couldn't save: {exc}")

    # -- cheat tab ---------------------------------------------------------- #
    def _cheat_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Bake Game Boy Game Genie codes into a ROM permanently. One code per "
            "line (6 or 9 digits, dashes optional). Codes are checked against the "
            "ROM's existing byte and skipped if they don't match, so a wrong-ROM "
            "code won't corrupt anything.\n\nNote: GameShark codes write to RAM at "
            "runtime and can't be baked into a ROM - use an emulator's cheat "
            "engine for those."))

        self.ed_crom = QLineEdit()
        self.ed_crom.setPlaceholderText("ROM to patch (.gb / .gbc)")
        b = QPushButton("Browse\u2026")
        b.clicked.connect(lambda: self._pick(self.ed_crom,
                          "Game Boy ROM (*.gb *.gbc *.gba);;All files (*)"))
        r = QHBoxLayout(); r.addWidget(self.ed_crom); r.addWidget(b)
        v.addLayout(r)

        self.codes = QPlainTextEdit()
        self.codes.setPlaceholderText("FA1-F5A-E61\n00A-17B-C49")
        v.addWidget(self.codes)

        apply_btn = QPushButton("Apply codes and save\u2026")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._do_cheats)
        v.addWidget(apply_btn)

        self.cheat_log = QPlainTextEdit()
        self.cheat_log.setReadOnly(True)
        v.addWidget(self.cheat_log, stretch=1)
        return w

    def _do_cheats(self) -> None:
        from . import cheats
        rom_path = self.ed_crom.text().strip()
        if not os.path.isfile(rom_path):
            QMessageBox.warning(self, __app_name__, "Pick a ROM to patch.")
            return
        code_lines = [ln for ln in self.codes.toPlainText().splitlines()
                      if ln.strip()]
        if not code_lines:
            QMessageBox.warning(self, __app_name__, "Enter at least one code.")
            return
        try:
            rom = open(rom_path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"File error: {exc}")
            return
        report = cheats.apply_game_genie(rom, code_lines)
        self.cheat_log.setPlainText(report.summary())
        if not report.applied:
            return
        base, ext = os.path.splitext(rom_path)
        suggested = f"{base} (cheats){ext}"
        out, _ = QFileDialog.getSaveFileName(self, "Save patched ROM", suggested,
                                             "Game Boy ROM (*.gb *.gbc *.gba)")
        if not out:
            return
        try:
            with open(out, "wb") as f:
                f.write(report.data)
            self.cheat_log.appendPlainText(f"\n\u2713 Saved {out}")
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"Couldn't save: {exc}")

    # -- shared ------------------------------------------------------------- #
    def _pick(self, target: QLineEdit, flt: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", "", flt)
        if path:
            target.setText(path)
