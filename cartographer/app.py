"""
app.py - Application entry point.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import datetime
import os
import sys
import traceback

from PyQt6.QtWidgets import QApplication

from . import __app_name__, __version__
from .settings import _config_dir
from .theme import DARK_QSS


CRASH_LOG = os.path.join(_config_dir(), "crash.log")


def _install_crash_handler() -> None:
    """Report unhandled exceptions instead of letting the app disappear.

    PyQt6 aborts the process when an exception escapes a slot, so a bug that
    would print a traceback from the console closes the window with nothing on
    screen and nothing written down. This catches those, appends them to a log
    next to the settings file, and puts the message where it can be read.
    """
    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as handle:
                handle.write(f"\n===== {stamp}  {__app_name__} "
                             f"{__version__} =====\n{text}")
        except OSError:
            pass
        sys.stderr.write(text)
        try:
            from PyQt6.QtWidgets import QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, __app_name__,
                    f"Something went wrong and the last action stopped.\n\n"
                    f"{exc_type.__name__}: {exc_value}\n\n"
                    f"The details were written to:\n{CRASH_LOG}\n\n"
                    f"The app is still running. Disconnect and reconnect the "
                    f"writer before trying again.")
        except Exception:  # noqa: BLE001 - a failed report must not re-raise
            pass

    sys.excepthook = hook


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setStyleSheet(DARK_QSS)
    _install_crash_handler()

    # Import here so the protocol/header modules can be unit-tested without Qt.
    from .device_window import DeviceWindow

    win = DeviceWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
