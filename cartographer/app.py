"""
app.py - Application entry point.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from . import __app_name__
from .theme import DARK_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setStyleSheet(DARK_QSS)

    # Import here so the protocol/header modules can be unit-tested without Qt.
    from .device_window import DeviceWindow

    win = DeviceWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
