"""
library_window.py - a simple view of your dumped ROMs and saves.

Scans a folder, lists each ROM with its size and verification status (checked
against the known-good hash database and internal checks), and flags duplicate
dumps that share the same SHA-1.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import __app_name__, settings

_ROM_EXTS = (".gba", ".gb", ".gbc")
_SAVE_EXTS = (".sav", ".srm")


class LibraryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} - Library")
        self.setMinimumSize(760, 520)
        v = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_folder = QLabel(settings.get("library_folder")
                                 or "No folder chosen")
        b = QPushButton("Choose folder\u2026")
        b.clicked.connect(self._choose)
        b_rescan = QPushButton("Rescan")
        b_rescan.clicked.connect(self._scan)
        top.addWidget(self.lbl_folder, stretch=1)
        top.addWidget(b)
        top.addWidget(b_rescan)
        v.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Type", "Size", "Status"])
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tree, stretch=1)

        self.summary = QLabel("")
        v.addWidget(self.summary)

        folder = settings.get("library_folder")
        if folder and os.path.isdir(folder):
            self._scan()

    def _choose(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Library folder")
        if d:
            settings.set("library_folder", d)
            self.lbl_folder.setText(d)
            self._scan()

    def _scan(self) -> None:
        from . import verify, titles
        folder = settings.get("library_folder")
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, __app_name__, "Choose a folder first.")
            return
        files = []
        for name in sorted(os.listdir(folder)):
            ext = os.path.splitext(name)[1].lower()
            if ext in _ROM_EXTS or ext in _SAVE_EXTS:
                files.append(name)

        self.tree.clear()
        self.bar.setVisible(True)
        self.bar.setMaximum(max(1, len(files)))
        seen_hashes = {}
        verified = 0
        dupes = 0

        for i, name in enumerate(files, 1):
            self.bar.setValue(i)
            path = os.path.join(folder, name)
            ext = os.path.splitext(name)[1].lower()
            try:
                data = open(path, "rb").read()
            except OSError:
                continue
            size = _human(len(data))

            if ext in _ROM_EXTS:
                is_gba = ext == ".gba" or len(data) >= 0x1000000
                result = (verify.verify_gba(data, known_db=titles._SHA1)
                          if is_gba else
                          verify.verify_gb(data, known_db=titles._SHA1))
                sha = result.sha1
                if result.known_good:
                    status = "verified good"
                    verified += 1
                elif result.all_passed:
                    status = "checks pass (not in DB)"
                else:
                    status = "FAILED checks"
                kind = "GBA ROM" if is_gba else "GB/GBC ROM"
            else:
                import hashlib
                sha = hashlib.sha1(data).hexdigest()
                status = "save file"
                kind = "save"

            dupe_of = seen_hashes.get(sha)
            if dupe_of:
                status += f"  \u2022 duplicate of {dupe_of}"
                dupes += 1
            else:
                seen_hashes[sha] = name

            item = QTreeWidgetItem([name, kind, size, status])
            if "FAILED" in status:
                item.setForeground(3, Qt.GlobalColor.red)
            self.tree.addTopLevelItem(item)

        self.bar.setVisible(False)
        self.summary.setText(
            f"{len(files)} file(s)  \u2022  {verified} verified good  \u2022  "
            f"{dupes} duplicate(s)")


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n} {unit}"
        n //= 1024
    return f"{n} GB"
