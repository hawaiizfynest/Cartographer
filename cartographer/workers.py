"""
workers.py - Background worker that runs a flasher operation on its own
thread and reports progress / completion back to the GUI via Qt signals.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal


class OperationWorker(QThread):
    """Runs ``fn(progress, log, cancel)`` on a background thread.

    ``fn`` should call ``progress(cur, total)`` and ``log(str)`` as it works
    and check ``cancel()`` periodically. Any exception is reported via sig_done.
    A cancellation is signalled by the function raising an exception whose class
    name contains "Cancel".
    """

    sig_progress = pyqtSignal(int, int)
    sig_log = pyqtSignal(str)
    sig_done = pyqtSignal(bool, str)

    def __init__(self, fn: Callable, *, success_msg: str = "Done.") -> None:
        super().__init__()
        self._fn = fn
        self._success_msg = success_msg
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401  (Qt entry point)
        try:
            self._fn(
                progress=self.sig_progress.emit,
                log=self.sig_log.emit,
                cancel=lambda: self._cancel,
            )
        except Exception as exc:  # noqa: BLE001 - report everything to the UI
            if "Cancel" in type(exc).__name__:
                self.sig_done.emit(False, "Canceled.")
            else:
                self.sig_done.emit(False, str(exc) or type(exc).__name__)
            return
        self.sig_done.emit(True, self._success_msg)
