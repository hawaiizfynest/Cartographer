"""
settings_window.py - a small preferences dialog.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from . import __app_name__, settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} - Settings")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)

        v.addWidget(QLabel("<b>Dumps</b>"))
        self.ed_out = QLineEdit(settings.get("output_folder") or "")
        self.ed_out.setPlaceholderText("Ask each time")
        b = QPushButton("Browse\u2026")
        b.clicked.connect(self._pick_out)
        row = QHBoxLayout()
        row.addWidget(QLabel("Default save folder:"))
        row.addWidget(self.ed_out)
        row.addWidget(b)
        v.addLayout(row)

        self.chk_verify = QCheckBox("Verify ROM dumps automatically after backup")
        self.chk_verify.setChecked(bool(settings.get("auto_verify")))
        v.addWidget(self.chk_verify)

        self.chk_report = QCheckBox("Write a verification report next to each "
                                    "dump and restore")
        self.chk_report.setChecked(bool(settings.get("write_dump_report")))
        v.addWidget(self.chk_report)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>Library</b>"))
        self.ed_lib = QLineEdit(settings.get("library_folder") or "")
        self.ed_lib.setPlaceholderText("Folder the library view scans")
        b2 = QPushButton("Browse\u2026")
        b2.clicked.connect(self._pick_lib)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Library folder:"))
        row2.addWidget(self.ed_lib)
        row2.addWidget(b2)
        v.addLayout(row2)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>Updates</b>"))
        self.chk_updates = QCheckBox("Check for updates when the app starts")
        self.chk_updates.setChecked(bool(settings.get("check_updates_on_start")))
        v.addWidget(self.chk_updates)
        self.chk_whatsnew = QCheckBox("Show the What's New window after updates")
        self.chk_whatsnew.setChecked(settings.show_whats_new())
        v.addWidget(self.chk_whatsnew)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Default save folder")
        if d:
            self.ed_out.setText(d)

    def _pick_lib(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Library folder")
        if d:
            self.ed_lib.setText(d)

    def _save(self) -> None:
        settings.set("output_folder", self.ed_out.text().strip())
        settings.set("auto_verify", self.chk_verify.isChecked())
        settings.set("write_dump_report", self.chk_report.isChecked())
        settings.set("library_folder", self.ed_lib.text().strip())
        settings.set("check_updates_on_start", self.chk_updates.isChecked())
        settings.set_show_whats_new(self.chk_whatsnew.isChecked())
        self.accept()
