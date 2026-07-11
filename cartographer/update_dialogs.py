"""
update_dialogs.py - the What's New window and the update-available prompt.

Two dialogs:

  * UpdateAvailableDialog - shown when a newer release exists. Lists everything
    new (the release notes) so the user can decide whether it's worth it, with an
    Update / Skip choice and a "skip this version" checkbox.

  * WhatsNewDialog - shown after the app updates to a new version, listing what
    changed, with a "don't show this again" checkbox.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QVBoxLayout,
)


def _render_notes(notes: str) -> str:
    """Turn plain-text / lightly-markdown release notes into simple HTML."""
    notes = (notes or "").strip()
    if not notes:
        return "<p>No release notes were provided for this version.</p>"
    lines = notes.splitlines()
    html = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        is_bullet = stripped.startswith(("- ", "* ", "\u2022 "))
        if is_bullet:
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>" + _esc(stripped[2:].strip()) + "</li>")
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            if stripped.startswith("#"):
                html.append("<b>" + _esc(stripped.lstrip("# ").strip()) + "</b>")
            elif stripped:
                html.append("<p>" + _esc(stripped) + "</p>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class UpdateAvailableDialog(QDialog):
    """Asks the user whether to update, and lets them skip this version."""

    UPDATE = 1
    LATER = 2
    SKIP = 3

    def __init__(self, current: str, new_tag: str, notes: str,
                 can_auto: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setMinimumSize(560, 460)
        self._choice = self.LATER

        v = QVBoxLayout(self)

        head = QLabel(f"<b>Cartographer {new_tag}</b> is available "
                      f"(you have {current}).")
        head.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(head)

        sub = QLabel("Here's what's new in this version:")
        v.addWidget(sub)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml("<h4>What's new</h4>" + _render_notes(notes))
        v.addWidget(body, stretch=1)

        if not can_auto:
            note = QLabel("This release isn't a single-file build, so it can't be "
                          "swapped in automatically \u2014 choosing Update will "
                          "download it and open the folder for you.")
            note.setWordWrap(True)
            note.setStyleSheet("color:#a1a1aa; font-size:11px;")
            v.addWidget(note)

        self.chk_skip = QCheckBox(f"Skip this version and don't remind me about "
                                  f"{new_tag} again")
        v.addWidget(self.chk_skip)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_later = QPushButton("Remind me later")
        btn_later.clicked.connect(self._later)
        btn_update = QPushButton("Update now")
        btn_update.setObjectName("primary")
        btn_update.setDefault(True)
        btn_update.clicked.connect(self._update)
        row.addWidget(btn_later)
        row.addWidget(btn_update)
        v.addLayout(row)

    def _update(self):
        self._choice = self.UPDATE
        self.accept()

    def _later(self):
        # If they ticked "skip", honour that even though they clicked later.
        self._choice = self.SKIP if self.chk_skip.isChecked() else self.LATER
        self.accept()

    def closeEvent(self, event):
        if self.chk_skip.isChecked() and self._choice == self.LATER:
            self._choice = self.SKIP
        super().closeEvent(event)

    @property
    def choice(self) -> int:
        # A ticked skip box always means skip, regardless of which button.
        if self.chk_skip.isChecked() and self._choice != self.UPDATE:
            return self.SKIP
        return self._choice

    @property
    def skip_requested(self) -> bool:
        return self.chk_skip.isChecked()


class WhatsNewDialog(QDialog):
    """Shown once after updating: what changed, with a silence checkbox."""

    def __init__(self, version: str, notes: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"What's new in Cartographer {version}")
        self.setMinimumSize(560, 440)

        v = QVBoxLayout(self)
        head = QLabel(f"<b>Welcome to Cartographer {version}</b>")
        head.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(head)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(_render_notes(notes))
        v.addWidget(body, stretch=1)

        self.chk_silence = QCheckBox("Don't show this window after future updates")
        v.addWidget(self.chk_silence)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        v.addWidget(buttons)

    @property
    def silence_requested(self) -> bool:
        return self.chk_silence.isChecked()
