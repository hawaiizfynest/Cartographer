"""
save_editor.py - view and edit a cartridge save file byte by byte.

A save is game-specific binary data, so nothing here can tell you what a given
byte means. What it can do is show you the whole file, let you change any byte,
and point out the landmarks worth knowing: where the used regions are, and where
readable text sits. Edits happen on a copy in memory and only reach disk when
you save, so the original file is untouched until you say so.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from . import __app_name__

BYTES_PER_ROW = 16


class HexModel(QAbstractTableModel):
    """Presents a bytearray as rows of 16 hex bytes plus an ASCII column."""

    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        self.data_bytes = bytearray(data)
        self.dirty = False

    # -- shape -------------------------------------------------------------- #
    def rowCount(self, _parent=QModelIndex()) -> int:
        return (len(self.data_bytes) + BYTES_PER_ROW - 1) // BYTES_PER_ROW

    def columnCount(self, _parent=QModelIndex()) -> int:
        return BYTES_PER_ROW + 1          # hex bytes, then the ASCII column

    def offset_of(self, row: int, col: int) -> int:
        return row * BYTES_PER_ROW + col

    # -- reading ------------------------------------------------------------ #
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == BYTES_PER_ROW:      # ASCII column
                start = row * BYTES_PER_ROW
                chunk = self.data_bytes[start:start + BYTES_PER_ROW]
                return "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            off = self.offset_of(row, col)
            if off >= len(self.data_bytes):
                return ""
            return f"{self.data_bytes[off]:02X}"
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == BYTES_PER_ROW:
                return int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def headerData(self, section, orientation,
                   role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section == BYTES_PER_ROW:
                return "ASCII"
            return f"{section:X}"
        return f"{section * BYTES_PER_ROW:06X}"

    # -- editing ------------------------------------------------------------ #
    def flags(self, index):
        base = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if index.column() == BYTES_PER_ROW:
            return base                    # ASCII column is read-only
        off = self.offset_of(index.row(), index.column())
        if off >= len(self.data_bytes):
            return Qt.ItemFlag.NoItemFlags
        return base | Qt.ItemFlag.ItemIsEditable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or index.column() == BYTES_PER_ROW:
            return False
        off = self.offset_of(index.row(), index.column())
        if off >= len(self.data_bytes):
            return False
        text = str(value).strip()
        try:
            val = int(text, 16)
        except ValueError:
            return False
        if not 0 <= val <= 0xFF:
            return False
        if self.data_bytes[off] == val:
            return True
        self.data_bytes[off] = val
        self.dirty = True
        # The ASCII column for this row changes too.
        ascii_index = self.index(index.row(), BYTES_PER_ROW)
        self.dataChanged.emit(index, index)
        self.dataChanged.emit(ascii_index, ascii_index)
        return True


class SaveEditorDialog(QDialog):
    """Hex view and editor for a save file, with a structure panel."""

    def __init__(self, path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{__app_name__} - save editor")
        self.setMinimumSize(900, 600)
        self.path = ""
        self.model = None

        v = QVBoxLayout(self)

        # File row.
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("Save file (.sav)")
        b_open = QPushButton("Open\u2026")
        b_open.clicked.connect(self._pick_file)
        row = QHBoxLayout()
        row.addWidget(self.ed_path)
        row.addWidget(b_open)
        v.addLayout(row)

        # Navigation row.
        self.ed_goto = QLineEdit()
        self.ed_goto.setPlaceholderText("Go to offset (hex, e.g. 1F40)")
        self.ed_goto.returnPressed.connect(self._goto)
        b_goto = QPushButton("Go")
        b_goto.clicked.connect(self._goto)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("Find text or hex bytes (e.g. 4A 6F)")
        self.ed_find.returnPressed.connect(self._find)
        b_find = QPushButton("Find")
        b_find.clicked.connect(self._find)
        nav = QHBoxLayout()
        nav.addWidget(self.ed_goto)
        nav.addWidget(b_goto)
        nav.addWidget(self.ed_find, 1)
        nav.addWidget(b_find)
        v.addLayout(nav)

        # Hex table and structure panel, side by side.
        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableView()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.table.setFont(mono)
        self.table.horizontalHeader().setDefaultSectionSize(34)
        split.addWidget(self.table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("What is in this save"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(mono)
        rv.addWidget(self.report, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)

        # Actions.
        self.lbl_status = QLabel("Open a save file to begin.")
        b_save = QPushButton("Save as\u2026")
        b_save.setObjectName("primary")
        b_save.clicked.connect(self._save_as)
        act = QHBoxLayout()
        act.addWidget(self.lbl_status, 1)
        act.addWidget(b_save)
        v.addLayout(act)

        if path:
            self._load(path)

    # -- file handling ------------------------------------------------------ #
    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open save file", "",
            "Save files (*.sav *.srm *.bin);;All files (*)")
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            QMessageBox.critical(self, __app_name__,
                                 f"Couldn't read that file: {exc}")
            return
        self.path = path
        self.ed_path.setText(path)
        self.model = HexModel(data, self)
        self.table.setModel(self.model)
        self.table.resizeColumnsToContents()
        from . import savecompare as sc
        self.report.setPlainText(sc.structure_report(data))
        self.lbl_status.setText(
            f"{os.path.basename(path)} - {len(data)} bytes. Edit a cell by "
            f"typing two hex digits.")

    def _save_as(self) -> None:
        if self.model is None:
            QMessageBox.information(self, __app_name__,
                                    "Open a save file first.")
            return
        suggested = self.path
        if suggested:
            base, ext = os.path.splitext(suggested)
            suggested = f"{base}_edited{ext or '.sav'}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save edited file as", suggested,
            "Save files (*.sav);;All files (*)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(bytes(self.model.data_bytes))
        except OSError as exc:
            QMessageBox.critical(self, __app_name__,
                                 f"Couldn't write that file: {exc}")
            return
        self.model.dirty = False
        self.lbl_status.setText(f"Saved to {os.path.basename(path)}.")

    # -- navigation --------------------------------------------------------- #
    def _goto(self) -> None:
        if self.model is None:
            return
        text = self.ed_goto.text().strip().replace("0x", "").replace("0X", "")
        if not text:
            return
        try:
            off = int(text, 16)
        except ValueError:
            QMessageBox.warning(self, __app_name__,
                                "Enter an offset in hex, for example 1F40.")
            return
        self._scroll_to(off)

    def _scroll_to(self, off: int) -> None:
        if self.model is None or not 0 <= off < len(self.model.data_bytes):
            QMessageBox.warning(self, __app_name__,
                                "That offset is outside this file.")
            return
        row, col = divmod(off, BYTES_PER_ROW)
        idx = self.model.index(row, col)
        self.table.setCurrentIndex(idx)
        self.table.scrollTo(idx)

    def _find(self) -> None:
        if self.model is None:
            return
        needle = _parse_needle(self.ed_find.text())
        if not needle:
            QMessageBox.warning(
                self, __app_name__,
                "Enter text to find, or hex bytes like 4A 6F 68 6E.")
            return
        data = bytes(self.model.data_bytes)
        cur = self.table.currentIndex()
        start = 0
        if cur.isValid():
            start = self.model.offset_of(cur.row(), cur.column()) + 1
        pos = data.find(needle, start)
        if pos < 0:
            pos = data.find(needle)       # wrap around
        if pos < 0:
            self.lbl_status.setText("Not found.")
            return
        self._scroll_to(pos)
        self.lbl_status.setText(f"Found at offset 0x{pos:X}.")


def _parse_needle(text: str) -> bytes:
    """Read a search box as hex bytes if it looks like hex, else as text."""
    s = text.strip()
    if not s:
        return b""
    cleaned = s.replace(" ", "").replace(",", "")
    looks_hex = (len(cleaned) >= 2 and len(cleaned) % 2 == 0
                 and all(c in "0123456789abcdefABCDEF" for c in cleaned))
    if looks_hex:
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            pass
    return s.encode("latin-1", "ignore")
