"""
device_window.py - point-and-click GUI for the GBxCart RW / Flash Boy Cyclone.

Connect, identify, read cart info, and back up ROM + save (all save types),
run the non-destructive flash-ID probe, and open the GBA batteryless patcher -
all without touching a command line. Long operations run on a worker thread so
the window stays responsive, with a live progress bar and a cancel button.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from . import __app_name__, __version__
from . import flash_db
from . import gb_header as gbh
from . import gbxcart as gx
from .workers import OperationWorker


class DeviceWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.dev = gx.GBxCart()
        self.info: Optional[gx.DeviceInfo] = None
        self.worker: Optional[OperationWorker] = None
        self.gba_header: bytes = b""
        self.gb_parsed = None
        self.save_id: str = ""
        self.resolved_title: str = ""
        self.game_code: str = ""

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.setMinimumSize(760, 680)

        # Window icon (bundled next to the package, and in assets/ in source).
        import os
        from PyQt6.QtGui import QIcon
        for cand in (
            os.path.join(getattr(sys, "_MEIPASS", ""), "icon.png"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "assets", "icon.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"),
        ):
            if cand and os.path.exists(cand):
                self.setWindowIcon(QIcon(cand))
                break

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(self._build_device_box())
        outer.addWidget(self._build_info_box())
        outer.addWidget(self._build_actions_box())
        outer.addWidget(self._build_progress_box(), stretch=1)
        outer.addWidget(self._build_footer())

        self.refresh_ports()
        self._set_connected(False)
        self.log(f"{__app_name__} {__version__}. Connect your GBxCart / Cyclone, "
                 f"set the voltage switch to match the cart, and click Connect.")
        self._build_menu()

        self._operation_running = False
        # After the window is up: show What's New if we just updated, then do a
        # quiet background update check.
        from PyQt6.QtCore import QTimer
        from . import settings
        QTimer.singleShot(200, self._maybe_show_whats_new)
        if settings.get("check_updates_on_start"):
            QTimer.singleShot(1200, lambda: self.on_check_updates(manual=False))

        # Keep watching while the app stays open, so a release published
        # mid-session gets noticed without a restart. Re-checks every 4 hours.
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(4 * 60 * 60 * 1000)  # 4 hours
        self._update_timer.timeout.connect(
            lambda: self.on_check_updates(manual=False))
        self._update_timer.start()

    def _build_menu(self) -> None:
        from . import settings
        bar = self.menuBar()

        tools_menu = bar.addMenu("Tools")
        act_batch = tools_menu.addAction("Batch dump (multiple carts)\u2026")
        act_batch.triggered.connect(self.on_batch_dump)
        act_rom_tools = tools_menu.addAction("ROM tools (patches, cheats)\u2026")
        act_rom_tools.triggered.connect(self.on_rom_tools)
        act_library = tools_menu.addAction("Library\u2026")
        act_library.triggered.connect(self.on_library)
        act_reverify = tools_menu.addAction(
            "Re-verify a ROM or save against its receipt\u2026")
        act_reverify.triggered.connect(self.on_reverify_report)
        tools_menu.addSeparator()
        act_settings = tools_menu.addAction("Settings\u2026")
        act_settings.triggered.connect(self.on_settings)

        help_menu = bar.addMenu("Help")
        act_update = help_menu.addAction("Check for updates\u2026")
        act_update.triggered.connect(lambda: self.on_check_updates(manual=True))
        act_view_whatsnew = help_menu.addAction("What's new in this version\u2026")
        act_view_whatsnew.triggered.connect(self.on_view_whats_new)
        self.act_whatsnew = help_menu.addAction("Show What's New after updates")
        self.act_whatsnew.setCheckable(True)
        self.act_whatsnew.setChecked(settings.show_whats_new())
        self.act_whatsnew.toggled.connect(self._toggle_whats_new)
        help_menu.addSeparator()
        act_about = help_menu.addAction(f"About {__app_name__}")
        act_about.triggered.connect(self.on_about)

    def on_view_whats_new(self) -> None:
        from . import updater
        from .update_dialogs import WhatsNewDialog
        notes = updater.changelog_section(__version__)
        if not notes:
            notes = "See CHANGELOG.md in the project for the full history."
        WhatsNewDialog(__version__, notes, parent=self).exec()

    def on_rom_tools(self) -> None:
        from .tools_window import ToolsDialog
        ToolsDialog(self).exec()

    def on_library(self) -> None:
        from .library_window import LibraryDialog
        LibraryDialog(self).exec()

    def on_settings(self) -> None:
        from .settings_window import SettingsDialog
        SettingsDialog(self).exec()

    def on_batch_dump(self) -> None:
        from . import settings
        if self.info is None:
            QMessageBox.information(
                self, __app_name__,
                "Connect first, then insert your first cart and run Batch dump. "
                "You'll be prompted to swap carts between each one.")
            return
        folder = settings.get("output_folder")
        if not folder or not os.path.isdir(folder):
            folder = QFileDialog.getExistingDirectory(
                self, "Folder to save all dumps into")
            if not folder:
                return
        self._batch_folder = folder
        self._batch_count = 0
        self.log(f"Batch dump into {folder}. Reading the current cart\u2026")
        self._batch_step()

    def _batch_step(self) -> None:
        """Read info for the seated cart and dump its ROM + save, then ask for
        the next cart."""
        if not self._switch_ok():
            return
        try:
            self.dev.set_mode("0")
            mode = self.dev.request_value(gx.CART_MODE, timeout=0.6)
        except Exception:  # noqa: BLE001
            mode = 0
        if mode == 0:
            QMessageBox.information(self, __app_name__,
                                    "No cart detected. Insert one and try Batch "
                                    "dump again.")
            return
        self.info = gx.DeviceInfo(firmware=self.info.firmware,
                                  pcb=self.info.pcb, cart_mode=mode)
        self.on_read_info()

        folder = self._batch_folder
        is_gba = self._is_gba()
        ext = ".gba" if is_gba else ".gb"
        name = self._default_filename(ext)
        rom_path = os.path.join(folder, name)

        def job(progress, log, cancel):
            if is_gba:
                size = self.dev.detect_gba_rom_size(cancel=cancel)
                with open(rom_path, "wb") as f:
                    self.dev.read_gba_rom(f, size, progress=progress, log=log,
                                          cancel=cancel)
            else:
                parsed = self.gb_parsed
                size = (parsed.rom_pages * 0x4000) if parsed else 0x8000
                with open(rom_path, "wb") as f:
                    self.dev.read_gb_rom(f, size,
                                         cart_type=parsed.cart_type_byte if parsed
                                         else 0,
                                         title=parsed.title if parsed else "",
                                         progress=progress, log=log, cancel=cancel)
            # verify + write the receipt + dump save alongside
            try:
                from . import verify, titles
                rom = open(rom_path, "rb").read()
                result = (verify.verify_gba(rom, known_db=titles._SHA1) if is_gba
                          else verify.verify_gb(rom, known_db=titles._SHA1))
                log(result.summary())
                info = (titles.resolve_gba(self.gba_header, rom=rom) if is_gba
                        else titles.resolve_gb(
                            self.gb_parsed.title if self.gb_parsed else "",
                            rom=rom))
                self._write_dump_report(
                    rom_path, rom, result, is_gba, log,
                    full_title=info.full_title,
                    game_code=info.game_code or self.game_code)
            except Exception:  # noqa: BLE001
                pass
            self._save_alongside(folder, name, is_gba, log, progress, cancel)

        self._batch_count += 1
        self._start(job, f"Dumped {name}", after=self._batch_next_prompt)

    def _save_alongside(self, folder, rom_name, is_gba, log, progress,
                        cancel) -> None:
        """Dump the cart's save next to its ROM, if it has one."""
        import os as _os
        base = _os.path.splitext(rom_name)[0]
        save_path = _os.path.join(folder, base + ".sav")
        try:
            if is_gba:
                kind = self.save_id if self.save_id in gx.SAVE_LAYOUT else \
                    gx.save_kind_from_id(self.save_id)
                if kind in gx.SAVE_LAYOUT:
                    with open(save_path, "wb") as f:
                        self.dev.read_gba_save(f, kind, progress=progress,
                                               cancel=cancel)
                    log(f"Saved {base}.sav")
            else:
                parsed = self.gb_parsed
                if parsed and parsed.ram_size_byte in gbh.RAM_SIZES:
                    ram = gbh.RAM_SIZES[parsed.ram_size_byte][2]
                    if ram:
                        with open(save_path, "wb") as f:
                            self.dev.read_gb_ram(f, ram,
                                                 cart_type=parsed.cart_type_byte,
                                                 ram_size_code=parsed.ram_size_byte,
                                                 progress=progress, cancel=cancel)
                        log(f"Saved {base}.sav")
        except Exception as exc:  # noqa: BLE001
            log(f"(save dump skipped: {exc})")

    def _console_label(self, is_gba) -> str:
        if is_gba:
            return "Game Boy Advance"
        parsed = self.gb_parsed
        return "Game Boy Color" if (parsed and parsed.cgb) else "Game Boy"

    def _save_type_label(self, is_gba) -> str:
        if is_gba:
            from . import cart_compat as cc
            kind = self.save_id if self.save_id in gx.SAVE_LAYOUT else \
                gx.save_kind_from_id(self.save_id)
            return cc._SAVE_LABELS.get(kind, self.save_id or "")
        parsed = self.gb_parsed
        if not parsed:
            return ""
        if parsed.ram_size in ("None", "Unknown"):
            return "no save RAM"
        return f"{parsed.ram_size} RAM ({parsed.cart_type})"

    def _write_dump_report(self, rom_path, rom, result, is_gba, log,
                           full_title="", game_code="") -> None:
        """Write the sidecar receipt (`<dump>.txt` + `<dump>.sha1`) next to a
        finished dump, if the setting is on. Runs inside worker jobs; a report
        failure must never take the dump down with it."""
        from . import settings, verify
        try:
            if not settings.get("write_dump_report"):
                return
            import time as _time
            meta = verify.DumpMeta(console=self._console_label(is_gba),
                                   title=full_title,
                                   game_code=game_code, rom_size=len(rom),
                                   save_type=self._save_type_label(is_gba))
            text = verify.build_report(
                os.path.basename(rom_path), meta, result,
                _time.strftime("%Y-%m-%d %H:%M:%S"), app_version=__version__)
            report = verify.write_report(rom_path, text)
            verify.write_sha1_file(rom_path, result.sha1)
            log(f"Report written: {os.path.basename(report)} (+ .sha1)")
        except Exception as exc:  # noqa: BLE001
            log(f"(report skipped: {exc})")

    def _write_restore_report(self, save_path, data, writeback, is_gba,
                              log) -> None:
        """Write the restore receipt (`<save>.restore.txt` + `<save>.sha1`)
        next to the save file that was written to the cart. Same setting, same
        rule: a receipt failure never takes the restore down with it."""
        from . import settings, verify
        try:
            if not settings.get("write_dump_report"):
                return
            import time as _time
            crc, sha1 = verify.hashes(data)
            text = verify.build_restore_report(
                os.path.basename(save_path), self._console_label(is_gba),
                self._save_type_label(is_gba), len(data), crc, sha1,
                writeback, _time.strftime("%Y-%m-%d %H:%M:%S"),
                app_version=__version__)
            report = verify.write_report(save_path, text,
                                         suffix=".restore.txt")
            verify.write_sha1_file(save_path, sha1)
            log(f"Restore receipt written: {os.path.basename(report)} "
                f"(+ .sha1)")
        except Exception as exc:  # noqa: BLE001
            log(f"(restore receipt skipped: {exc})")

    def _batch_next_prompt(self) -> None:
        ask = QMessageBox.question(
            self, "Batch dump",
            f"Dumped {self._batch_count} cart(s) so far.\n\n"
            f"Swap in the next cart (matching the voltage switch), then click "
            f"Yes to dump it. Click No to finish.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ask == QMessageBox.StandardButton.Yes:
            self._batch_step()
        else:
            self.log(f"Batch dump finished: {self._batch_count} cart(s) into "
                     f"{self._batch_folder}.")

    def _toggle_whats_new(self, on: bool) -> None:
        from . import settings
        settings.set_show_whats_new(on)

    def on_check_updates(self, manual: bool = False) -> None:
        from . import settings, updater
        from .update_dialogs import UpdateAvailableDialog
        import webbrowser
        # Don't let an automatic check interrupt a running operation (a dump,
        # a restore, a batch job). Manual checks always run.
        if not manual and getattr(self, "_operation_running", False):
            return
        self.log("Checking for updates\u2026")
        try:
            rel = updater.fetch_latest()
        except updater.UpdateError as exc:
            self.log(f"Update check failed: {exc}")
            if manual:
                QMessageBox.information(self, __app_name__,
                                        f"Couldn't check for updates:\n{exc}")
            return

        if not updater.is_newer(rel.version, __version__):
            self.log(f"You're up to date (latest is {rel.tag or 'unknown'}).")
            if manual:
                QMessageBox.information(
                    self, __app_name__,
                    f"You're running the latest version ({__version__}).")
            return

        # Respect a previously-skipped version, unless the user asked manually.
        if not manual and settings.skip_version() == rel.tag:
            self.log(f"Update {rel.tag} is available but was skipped.")
            return

        can_auto = updater.is_frozen() and updater.can_self_replace(rel.asset_name)
        notes = updater.best_notes(rel.version, rel.notes)
        dlg = UpdateAvailableDialog(__version__, rel.tag, notes, can_auto,
                                    parent=self)
        dlg.exec()
        choice = dlg.choice

        if choice == UpdateAvailableDialog.SKIP:
            settings.set_skip_version(rel.tag)
            self.log(f"Skipping {rel.tag}. You can still update from Help > "
                     f"Check for updates.")
            return
        if choice != UpdateAvailableDialog.UPDATE:
            self.log("Update postponed.")
            return

        # Clear any prior skip now that the user chose to update.
        if settings.skip_version() == rel.tag:
            settings.set_skip_version("")

        if not rel.asset_url:
            webbrowser.open(rel.html_url or
                            f"https://github.com/{updater.GITHUB_OWNER}/"
                            f"{updater.GITHUB_REPO}/releases")
            return

        import os
        dest_dir = (os.path.dirname(updater.current_executable())
                    if can_auto else
                    os.path.join(os.path.expanduser("~"), "Downloads"))
        if not can_auto and not os.path.isdir(dest_dir):
            dest_dir = os.getcwd()
        self.log(f"Downloading {rel.asset_name}\u2026")
        self._busy(True)
        self._pending_release = rel
        self._pending_can_auto = can_auto

        def job(progress, log, cancel):
            # Download to a temp name in the target dir so the swap is a fast move.
            path = updater.download_asset(rel, dest_dir, progress=progress)
            self._downloaded_update = path

        self._downloaded_update = ""
        self.worker = OperationWorker(job, success_msg="Update downloaded.")
        self.worker.sig_progress.connect(self.on_progress)
        self.worker.sig_log.connect(self.log)
        self.worker.sig_done.connect(self._on_update_downloaded)
        self.worker.start()

    def _on_update_downloaded(self, ok: bool, msg: str) -> None:
        self._busy(False)
        path = getattr(self, "_downloaded_update", "")
        rel = getattr(self, "_pending_release", None)
        can_auto = getattr(self, "_pending_can_auto", False)
        if not (ok and path):
            self.log(f"\u2717 {msg}")
            return
        self.log(f"\u2713 Update downloaded to {path}")

        from . import settings, updater
        import os

        if can_auto:
            # Remember, so the new version shows What's New on first launch.
            settings.set("pending_whats_new_version", rel.tag if rel else "")
            settings.set("pending_whats_new_notes",
                         updater.best_notes(rel.version, rel.notes) if rel else "")
            confirm = QMessageBox.question(
                self, __app_name__,
                f"Cartographer {rel.tag if rel else ''} is ready.\n\n"
                f"The app will close, install the new version, and reopen. "
                f"Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if confirm != QMessageBox.StandardButton.Yes:
                self.log("Install postponed. The download is ready for next time.")
                return
            try:
                updater.apply_update_and_restart(path)
            except updater.UpdateError as exc:
                self.log(f"\u2717 Couldn't apply update automatically: {exc}")
                QMessageBox.warning(self, __app_name__,
                                    f"Couldn't apply the update automatically:\n"
                                    f"{exc}\n\nThe download is at:\n{path}")
                return
            # Hand off to the swapper and quit so it can replace this exe.
            from PyQt6.QtWidgets import QApplication
            self.log("Closing to finish the update\u2026")
            QApplication.instance().quit()
        else:
            # Archive/dmg build: open the folder for a manual swap.
            if QMessageBox.question(
                    self, __app_name__,
                    f"Downloaded to:\n{path}\n\nOpen its folder now? Close "
                    f"Cartographer and run the new version to finish updating."
                    ) == QMessageBox.StandardButton.Yes:
                folder = os.path.dirname(path)
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(folder)  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        os.system(f'open "{folder}"')
                    else:
                        os.system(f'xdg-open "{folder}"')
                except Exception:  # noqa: BLE001
                    pass

    def _maybe_show_whats_new(self) -> None:
        """On first launch after an update, show the What's New window once."""
        from . import settings
        from .update_dialogs import WhatsNewDialog
        pending = settings.get("pending_whats_new_version")
        if not pending:
            return
        # Only show if it matches the version we're now actually running, and the
        # user hasn't silenced it.
        if pending == __version__ and settings.show_whats_new():
            notes = settings.get("pending_whats_new_notes") or ""
            dlg = WhatsNewDialog(__version__, notes, parent=self)
            dlg.exec()
            if dlg.silence_requested:
                settings.set_show_whats_new(False)
        # Clear the pending flag either way so it only shows once.
        settings.set("pending_whats_new_version", "")
        settings.set("pending_whats_new_notes", "")
        settings.set("last_seen_version", __version__)

    def on_about(self) -> None:
        QMessageBox.about(
            self, f"About {__app_name__}",
            f"<b>{__app_name__}</b> v{__version__}<br>"
            f"A reader, writer and flasher for Game Boy, Game Boy Color and "
            f"Game Boy Advance cartridges.<br><br>"
            f"Written by <b>LJ \u201cHawaiizFynest\u201d Eblacas</b>.<br><br>"
            f"<b>Thanks &amp; credits</b><br>"
            f"\u2022 GBA batteryless save patcher \u2014 <b>metroid-maniac</b> "
            f"(gba-auto-batteryless-patcher)<br>"
            f"\u2022 GBA SRAM save patcher \u2014 <b>bbsan2k</b> "
            f"(Flash1M_Repro_SRAM_Patcher)<br>"
            f"\u2022 GBxCart RW device &amp; protocol \u2014 <b>insideGadgets</b><br>"
            f"\u2022 Cartridge/flash reference \u2014 the FlashGBX project "
            f"(Lesserkuma)<br><br>"
            f"This tool bundles ports of the batteryless and SRAM patchers, whose "
            f"original authors deserve the credit for that work. Their licenses "
            f"are included in the <i>licenses/</i> folder.")

    def _build_footer(self) -> QWidget:
        """A slim footer crediting the author and the patcher authors."""
        bar = QLabel(
            f"{__app_name__} v{__version__}  \u2022  Written by "
            f"LJ \u201cHawaiizFynest\u201d Eblacas  \u2022  batteryless patch by "
            f"metroid-maniac \u2022 SRAM patch by bbsan2k \u2022 protocol by "
            f"insideGadgets")
        bar.setObjectName("footer")
        bar.setWordWrap(True)
        return bar

    # ------------------------------------------------------------------ UI -- #

    def _build_device_box(self) -> QGroupBox:
        box = QGroupBox("Device")
        lay = QHBoxLayout(box)
        lay.addWidget(QLabel("Port"))
        self.cmb_port = QComboBox()
        self.cmb_port.setMinimumWidth(280)
        lay.addWidget(self.cmb_port, stretch=1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        lay.addWidget(self.btn_refresh)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(self.on_connect)
        lay.addWidget(self.btn_connect)
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self.on_disconnect)
        lay.addWidget(self.btn_disconnect)
        return box

    def _build_info_box(self) -> QGroupBox:
        box = QGroupBox("Cartridge")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.lbl_fw = QLabel("\u2014")
        self.lbl_mode = QLabel("\u2014")
        self.lbl_title = QLabel("\u2014")
        self.lbl_type = QLabel("\u2014")
        self.lbl_rom = QLabel("\u2014")
        self.lbl_save = QLabel("\u2014")
        for w in (self.lbl_fw, self.lbl_mode, self.lbl_title, self.lbl_type,
                  self.lbl_rom, self.lbl_save):
            w.setObjectName("mono")
        rows = [
            ("Device", self.lbl_fw), ("Cart mode", self.lbl_mode),
            ("Title", self.lbl_title), ("Type", self.lbl_type),
            ("ROM size", self.lbl_rom), ("Save", self.lbl_save),
        ]
        for i, (name, w) in enumerate(rows):
            col = 0 if i < 3 else 2
            row = i if i < 3 else i - 3
            cap = QLabel(name)
            cap.setObjectName("hint")
            grid.addWidget(cap, row, col)
            grid.addWidget(w, row, col + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return box

    def _build_actions_box(self) -> QGroupBox:
        box = QGroupBox("Actions")
        grid = QGridLayout(box)
        grid.setSpacing(8)
        self.btn_readinfo = QPushButton("Read cart info")
        self.btn_readinfo.clicked.connect(self.on_read_info)
        self.btn_backup_rom = QPushButton("Backup ROM \u2192 file")
        self.btn_backup_rom.clicked.connect(self.on_backup_rom)
        self.btn_backup_save = QPushButton("Backup save \u2192 file")
        self.btn_backup_save.clicked.connect(self.on_backup_save)
        self.btn_restore_save = QPushButton("Restore save \u2190 file")
        self.btn_restore_save.setObjectName("danger")
        self.btn_restore_save.clicked.connect(self.on_restore_save)
        self.btn_flashid = QPushButton("Identify flash chip")
        self.btn_flashid.clicked.connect(self.on_flash_id)
        self.btn_verify = QPushButton("Verify a ROM file\u2026")
        self.btn_verify.clicked.connect(self.on_verify_file)
        self.btn_cartadvice = QPushButton("Which flash cart?\u2026")
        self.btn_cartadvice.clicked.connect(self.on_cart_advice)
        self.btn_patch = QPushButton("GBA batteryless patch\u2026")
        self.btn_patch.clicked.connect(self.on_patch)
        self.btn_write_rom = QPushButton("Write ROM to flash cart\u2026")
        self.btn_write_rom.clicked.connect(self.on_write_rom)

        grid.addWidget(self.btn_readinfo, 0, 0)
        grid.addWidget(self.btn_backup_rom, 0, 1)
        grid.addWidget(self.btn_backup_save, 1, 0)
        grid.addWidget(self.btn_restore_save, 1, 1)
        grid.addWidget(self.btn_flashid, 2, 0)
        grid.addWidget(self.btn_verify, 2, 1)
        grid.addWidget(self.btn_cartadvice, 3, 0)
        grid.addWidget(self.btn_patch, 3, 1)
        grid.addWidget(self.btn_write_rom, 4, 0, 1, 2)

        self.action_buttons = [
            self.btn_readinfo, self.btn_backup_rom, self.btn_backup_save,
            self.btn_restore_save, self.btn_flashid, self.btn_write_rom,
        ]
        hint = QLabel("Set the physical GBA/GBC voltage switch to match the cart "
                      "BEFORE connecting. Restore overwrites the cart's save "
                      "(and verifies by reading back). The patcher works offline.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 5, 0, 1, 2)
        return box

    def _build_progress_box(self) -> QGroupBox:
        box = QGroupBox("Progress")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setValue(0)
        row.addWidget(self.bar, stretch=1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_cancel.setEnabled(False)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setObjectName("log")
        self.txt_log.setReadOnly(True)
        lay.addWidget(self.txt_log, stretch=1)
        return box

    # -------------------------------------------------------------- helpers -- #

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{ts}] {msg}")

    def on_progress(self, cur: int, total: int) -> None:
        if total <= 0:
            return
        self.bar.setMaximum(total)
        self.bar.setValue(min(cur, total))

    def refresh_ports(self) -> None:
        prev = self.cmb_port.currentData()
        self.cmb_port.clear()
        ports = gx.list_serial_ports()
        if not ports:
            self.cmb_port.addItem("No serial ports found", None)
            return
        for p in ports:
            tag = "  \u2605 CH340" if p.is_ch340 else ""
            self.cmb_port.addItem(f"{p.device} \u2014 {p.description}{tag}", p.device)
        if prev:
            i = self.cmb_port.findData(prev)
            if i >= 0:
                self.cmb_port.setCurrentIndex(i)

    def _set_connected(self, connected: bool) -> None:
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.cmb_port.setEnabled(not connected)
        self.btn_refresh.setEnabled(not connected)
        for b in self.action_buttons:
            b.setEnabled(connected)
        self.btn_patch.setEnabled(True)  # offline-capable
        self.btn_verify.setEnabled(True)  # offline-capable
        self.btn_cartadvice.setEnabled(True)  # offline-capable

    def _busy(self, busy: bool) -> None:
        self._operation_running = busy
        for b in self.action_buttons:
            b.setEnabled(not busy)
        self.btn_patch.setEnabled(not busy)
        self.btn_verify.setEnabled(not busy)
        self.btn_cartadvice.setEnabled(not busy)
        self.btn_disconnect.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def _is_gba(self) -> bool:
        return self.info is not None and self.info.cart_mode == gx.GBA_MODE

    def _default_filename(self, extension: str) -> str:
        """Build a friendly, filesystem-safe default name from the resolved
        game title, falling back to the game code, then a generic name."""
        name = self.resolved_title.strip()
        if not name or name in ("(unknown)", "(none)"):
            name = self.game_code.strip() or "cartridge"
        # strip characters illegal on Windows/macOS/Linux, keep it readable
        illegal = '<>:"/\\|?*\x00'
        cleaned = "".join(" " if c in illegal else c for c in name)
        cleaned = " ".join(cleaned.split()).strip(" .")  # collapse spaces
        return (cleaned or "cartridge") + extension

    def _switch_ok(self) -> bool:
        """On v1.1/v1.2 boards the physical voltage switch controls GB vs GBA.
        The device reports which side it's on via cart_mode; warn if the current
        cart mode doesn't look right for a seated cart."""
        if self.info is None:
            return False
        if self.info.cart_mode in (gx.GB_MODE, gx.GBA_MODE):
            return True
        QMessageBox.warning(
            self, __app_name__,
            "No cartridge is being detected. On this board the physical GBA/GBC "
            "switch sets the voltage - make sure it matches the inserted cart "
            "(GBA = 3.3V side, GB/GBC = 5V side), re-seat the cart, then click "
            "Read cart info.")
        return False

    # --------------------------------------------------------------- events -- #

    def on_connect(self) -> None:
        port = self.cmb_port.currentData()
        if not port:
            QMessageBox.warning(self, __app_name__, "Select a serial port first.")
            return
        # Immediate feedback: the open + handshake can take a couple of seconds
        # (it tries 1M then 1.7M baud), so show an indeterminate bar right away.
        self.btn_connect.setEnabled(False)
        self.bar.setMaximum(0)          # indeterminate "busy" animation
        self.log(f"Connecting to {port} (trying 1M then 1.7M baud)\u2026")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        try:
            self.dev.open(port)
        except gx.GBxCartError as exc:
            self.bar.setMaximum(100)
            self.bar.setValue(0)
            self.btn_connect.setEnabled(True)
            QMessageBox.critical(self, __app_name__, f"Could not connect:\n{exc}")
            self.log(f"Connect failed: {exc}")
            return
        self.log("Link established. Identifying device\u2026")
        QApplication.processEvents()
        self.info = self.dev.identify()
        fast = self.dev.check_fast_read()
        self.bar.setMaximum(100)
        self.bar.setValue(0)
        self.lbl_fw.setText(f"R{self.info.firmware}  PCB {self.info.pcb_name}"
                            f"{'  fast' if fast else ''}")
        self.lbl_mode.setText(self.info.cart_mode_name)
        self._set_connected(True)
        self.log(f"Connected on {port}: firmware R{self.info.firmware}, "
                 f"PCB {self.info.pcb_name}. Fast read "
                 f"{'enabled' if fast else 'unavailable'}.")
        if self.info.cart_mode == 0:
            self.log("No cartridge detected - insert a cart (switch set to match) "
                     "and click Read cart info.")
        else:
            self.on_read_info()

    def on_disconnect(self) -> None:
        self.dev.close()
        self.info = None
        self.resolved_title = ""
        self.game_code = ""
        self.save_id = ""
        self._set_connected(False)
        for w in (self.lbl_fw, self.lbl_mode, self.lbl_title, self.lbl_type,
                  self.lbl_rom, self.lbl_save):
            w.setText("\u2014")
        self.log("Disconnected.")

    def on_read_info(self) -> None:
        self.info = self.dev.identify()
        self.lbl_mode.setText(self.info.cart_mode_name)
        try:
            if self._is_gba():
                self._read_gba_info()
            else:
                self._read_gb_info()
        except gx.GBxCartError as exc:
            self.log(f"Read info failed: {exc}")
            QMessageBox.warning(self, __app_name__, str(exc))

    def _read_gba_info(self) -> None:
        hdr = self.dev.read_gba_header()
        self.gba_header = hdr
        from . import titles
        info = titles.resolve_gba(hdr)
        short, code = titles.gba_header_fields(hdr)
        self.lbl_title.setText(info.full_title)
        self.lbl_type.setText(f"GBA  [{code}]")
        self.lbl_rom.setText(info.rom_size or "(detect on backup)")
        self.resolved_title = info.full_title
        self.game_code = code
        self.log(f"GBA cart: {info.full_title}"
                 + ("" if info.is_exact else "  (from game code; dump ROM for an "
                    "exact match)"))
        # Save type: prefer the database (authoritative), fall back to a ROM
        # byte-scan only if the code is unknown.
        db_save = titles.save_type_for_code(code)
        if db_save and db_save in gx.SAVE_LAYOUT:
            self.save_id = db_save
            total = gx.SAVE_LAYOUT[db_save][0]
            unit = f"{total // 1024} KB" if total >= 1024 else f"{total} bytes"
            self.lbl_save.setText(f"{db_save} ({unit})")
            self.log(f"Save type (from database): {db_save} ({unit}).")
        else:
            self.lbl_save.setText("scanning\u2026")
            self._detect_save_async()

    def _detect_save_async(self) -> None:
        self._busy(True)
        self.bar.setValue(0)

        def job(progress, log, cancel):
            buf = io.BytesIO()
            try:
                self.dev.read_gba_rom(buf, 2 * 1024 * 1024, progress=progress,
                                      cancel=cancel)
            except Exception:  # noqa: BLE001
                pass
            data = buf.getvalue()
            save_id = ""
            for tag in (b"EEPROM_V", b"FLASH1M_V", b"FLASH512_V", b"FLASH_V",
                        b"SRAM_V"):
                idx = data.find(tag)
                if idx >= 0:
                    end = idx
                    while end < len(data) and 32 <= data[end] < 127:
                        end += 1
                    save_id = data[idx:end].decode("ascii", "replace")
                    break
            self._pending_save_id = save_id

        self._pending_save_id = ""
        self.worker = OperationWorker(job, success_msg="Cart info ready.")
        self.worker.sig_progress.connect(self.on_progress)
        self.worker.sig_log.connect(self.log)
        self.worker.sig_done.connect(self._on_save_detected)
        self.worker.start()

    def _on_save_detected(self, ok: bool, msg: str) -> None:
        self._busy(False)
        raw = getattr(self, "_pending_save_id", "")
        kind = gx.save_kind_from_id(raw)
        if kind != gx.SAVE_NONE:
            self.save_id = kind
            total = gx.SAVE_LAYOUT[kind][0]
            unit = f"{total // 1024} KB" if total >= 1024 else f"{total} bytes"
            self.lbl_save.setText(f"{raw} \u2192 {kind} ({unit})")
            self.log(f"Save type (scanned): {raw} \u2192 {kind} ({unit}).")
        else:
            self.lbl_save.setText("unknown (dump ROM to confirm)")
            self.log("Save type not found by scan. It may be EEPROM (often has "
                     "no signature string) - a full dump can confirm it.")

    def _read_gb_info(self) -> None:
        hdr = self.dev.read_gb_header()
        parsed = gbh.parse_header(hdr) if len(hdr) >= 0x150 else None
        self.gb_parsed = parsed
        if parsed:
            self.lbl_title.setText(parsed.title or "(none)")
            self.resolved_title = parsed.title or ""
            self.lbl_type.setText(parsed.cart_type)
            self.lbl_rom.setText(parsed.rom_size)
            self.lbl_save.setText(parsed.ram_size)
            self.log(f"GB cart: {parsed.title or '(no title)'}, {parsed.cart_type}, "
                     f"ROM {parsed.rom_size}, RAM {parsed.ram_size}.")
        else:
            self.log("Could not parse GB header - re-seat the cart and retry.")

    def _start(self, fn, success_msg: str, after=None) -> None:
        self._busy(True)
        self.bar.setValue(0)
        self._after_done = after
        self.worker = OperationWorker(fn, success_msg=success_msg)
        self.worker.sig_progress.connect(self.on_progress)
        self.worker.sig_log.connect(self.log)
        self.worker.sig_done.connect(self.on_done)
        self.worker.start()

    def on_done(self, ok: bool, msg: str) -> None:
        self._busy(False)
        self.log(("\u2713 " if ok else "\u2717 ") + msg)
        if ok:
            self.bar.setValue(self.bar.maximum())
        exact = getattr(self, "_exact_title", "")
        if ok and exact:
            self.lbl_title.setText(exact)
            self.resolved_title = exact
            self._exact_title = ""
        after = getattr(self, "_after_done", None)
        self._after_done = None
        if ok and after:
            after()

    def on_cancel(self) -> None:
        if self.worker is not None:
            self.log("Canceling\u2026")
            self.worker.cancel()

    def on_backup_rom(self) -> None:
        if not self._switch_ok():
            return
        if self._is_gba():
            default = self._default_filename(".gba")
            flt = "GBA ROM (*.gba)"
        else:
            default = self._default_filename(".gb")
            flt = "Game Boy ROM (*.gb *.gbc)"
        path, _ = QFileDialog.getSaveFileName(self, "Save ROM", default, flt)
        if not path:
            return

        if self._is_gba():
            self.log("Detecting GBA ROM size, then dumping\u2026")

            def job(progress, log, cancel):
                size = self.dev.detect_gba_rom_size(cancel=cancel)
                log(f"ROM size detected: {size // (1024*1024)} MB.")
                with open(path, "wb") as f:
                    self.dev.read_gba_rom(f, size, progress=progress, log=log,
                                          cancel=cancel)
                # Verify the dump: internal checks + known-good hash match.
                try:
                    from . import titles, verify
                    rom = open(path, "rb").read()
                    result = verify.verify_gba(rom, known_db=titles._SHA1)
                    for c in result.checks:
                        mark = "\u2713" if c.passed else "\u2717"
                        log(f"  {mark} {c.name}"
                            + (f" ({c.detail})" if c.detail else ""))
                    log(f"  CRC32 {result.crc32}  SHA-1 {result.sha1}")
                    log(result.summary())
                    # Upgrade title / remember hash as before.
                    info = titles.resolve_gba(self.gba_header, rom=rom)
                    if info.is_exact:
                        self._exact_title = info.full_title
                    else:
                        titles.remember_dump(rom, info.full_title)
                    self._write_dump_report(
                        path, rom, result, True, log,
                        full_title=info.full_title,
                        game_code=info.game_code or self.game_code)
                except Exception as exc:  # noqa: BLE001
                    log(f"  (verification skipped: {exc})")
            self._exact_title = ""
            self._start(job, f"ROM saved to {path}")
        else:
            parsed = self.gb_parsed
            size = parsed.rom_pages * 0x4000 if parsed and parsed.rom_pages else 0x8000
            ctype = parsed.cart_type_byte if parsed else 0
            title = parsed.title if parsed else ""
            self.log(f"Dumping {size // 1024} KB GB ROM\u2026")

            def job(progress, log, cancel):
                with open(path, "wb") as f:
                    self.dev.read_gb_rom(f, size, cart_type=ctype, title=title,
                                         progress=progress, log=log, cancel=cancel)
                try:
                    from . import titles, verify
                    rom = open(path, "rb").read()
                    result = verify.verify_gb(rom, known_db=titles._SHA1)
                    for c in result.checks:
                        mark = "\u2713" if c.passed else "\u2717"
                        log(f"  {mark} {c.name}"
                            + (f" ({c.detail})" if c.detail else ""))
                    log(f"  CRC32 {result.crc32}  SHA-1 {result.sha1}")
                    log(result.summary())
                    info = titles.resolve_gb(title, rom=rom)
                    if info.is_exact:
                        self._exact_title = info.full_title
                    elif title:
                        titles.remember_dump(rom, title)
                    self._write_dump_report(path, rom, result, False, log,
                                            full_title=info.full_title)
                except Exception as exc:  # noqa: BLE001
                    log(f"  (verification skipped: {exc})")
            self._exact_title = ""
            self._start(job, f"ROM saved to {path}")

    def on_backup_save(self) -> None:
        if not self._switch_ok():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save game backup", self._default_filename(".sav"),
            "Save data (*.sav)")
        if not path:
            return
        if self._is_gba():
            kind = self.save_id if self.save_id in gx.SAVE_LAYOUT else \
                gx.save_kind_from_id(self.save_id)
            if kind not in gx.SAVE_LAYOUT:
                if QMessageBox.question(
                        self, __app_name__,
                        "Save type could not be detected. Try a 32 KB SRAM read?"
                        ) != QMessageBox.StandardButton.Yes:
                    return

                def job(progress, log, cancel):
                    with open(path, "wb") as f:
                        self.dev.read_gba_sram(f, 0x8000, progress=progress,
                                               log=log, cancel=cancel)
                self._start(job, f"Save saved to {path}")
                return
            total = gx.SAVE_LAYOUT[kind][0]
            self.log(f"Dumping {self.save_id} save ({total // 1024 or total} "
                     f"{'KB' if total >= 1024 else 'bytes'})\u2026")

            def job(progress, log, cancel):
                with open(path, "wb") as f:
                    self.dev.read_gba_save(f, kind, progress=progress, log=log,
                                           cancel=cancel)
            self._start(job, f"Save saved to {path}")
        else:
            parsed = self.gb_parsed
            if not parsed or parsed.ram_size_byte not in gbh.RAM_SIZES:
                QMessageBox.information(self, __app_name__,
                                        "No save RAM detected on this cart.")
                return
            ram_bytes = gbh.RAM_SIZES[parsed.ram_size_byte][2]
            if ram_bytes == 0:
                QMessageBox.information(self, __app_name__,
                                        "This cart has no battery save.")
                return
            ctype = parsed.cart_type_byte
            rsize = parsed.ram_size_byte
            self.log(f"Dumping {ram_bytes // 1024 or ram_bytes} "
                     f"{'KB' if ram_bytes >= 1024 else 'bytes'} GB save\u2026")

            def job(progress, log, cancel):
                with open(path, "wb") as f:
                    self.dev.read_gb_ram(f, ram_bytes, cart_type=ctype,
                                         ram_size_code=rsize,
                                         progress=progress, log=log, cancel=cancel)
            self._start(job, f"Save saved to {path}")

    def on_restore_save(self) -> None:
        if not self._switch_ok():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose save file to restore", "",
            "Save data (*.sav *.srm);;All files (*)")
        if not path:
            return
        try:
            data = open(path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"Cannot read file:\n{exc}")
            return

        import os as _os
        if self._is_gba():
            self._restore_gba(path, data)
        else:
            self._restore_gb(path, data)

    def _restore_gba(self, path, data) -> None:
        import os as _os
        kind = self.save_id if self.save_id in gx.SAVE_LAYOUT else \
            gx.save_kind_from_id(self.save_id)
        if kind not in gx.SAVE_LAYOUT:
            QMessageBox.warning(
                self, __app_name__,
                "The cart's save type isn't known yet. Click Read cart info "
                "first so the correct write method is used.")
            return
        expected = gx.SAVE_LAYOUT[kind][0]
        if len(data) != expected:
            if QMessageBox.question(
                    self, "Size mismatch",
                    f"The save file is {len(data)} bytes but this cart's "
                    f"{kind} save is {expected} bytes. Writing a mismatched save "
                    f"can corrupt it.\n\nContinue anyway?"
                    ) != QMessageBox.StandardButton.Yes:
                return
        if QMessageBox.warning(
                self, "Overwrite cartridge save",
                f"This will OVERWRITE the save on the cartridge with "
                f"{_os.path.basename(path)}.\n\nThe cart's current save will be "
                f"lost unless you have backed it up. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
            return
        self.log(f"Restoring {kind} save from {_os.path.basename(path)}\u2026")

        def job(progress, log, cancel):
            self.dev.write_gba_save(data, kind, progress=progress, log=log,
                                    cancel=cancel)
            log("Write complete. Verifying by reading back\u2026")
            import io as _io
            buf = _io.BytesIO()
            self.dev.read_gba_save(buf, kind, progress=progress, cancel=cancel)
            self._verify_writeback(buf.getvalue(), data, kind, log,
                                   save_path=path, is_gba=True)

        self._start(job, "Save restored.")

    def _restore_gb(self, path, data) -> None:
        import os as _os
        parsed = self.gb_parsed
        if not parsed or parsed.ram_size_byte not in gbh.RAM_SIZES:
            QMessageBox.warning(self, __app_name__,
                                "No writable save RAM detected. Click Read cart "
                                "info first.")
            return
        ram_bytes = gbh.RAM_SIZES[parsed.ram_size_byte][2]
        if ram_bytes == 0:
            QMessageBox.information(self, __app_name__,
                                    "This cart has no battery save to write.")
            return
        if len(data) != ram_bytes:
            if QMessageBox.question(
                    self, "Size mismatch",
                    f"The save file is {len(data)} bytes but this cart's save is "
                    f"{ram_bytes} bytes. Continue anyway?"
                    ) != QMessageBox.StandardButton.Yes:
                return
        if QMessageBox.warning(
                self, "Overwrite cartridge save",
                f"This will OVERWRITE the save on the cartridge with "
                f"{_os.path.basename(path)}. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
            return
        ctype = parsed.cart_type_byte
        rsize = parsed.ram_size_byte
        self.log(f"Restoring GB save from {_os.path.basename(path)}\u2026")

        def job(progress, log, cancel):
            self.dev.write_gb_ram(data, cart_type=ctype, ram_size_code=rsize,
                                  progress=progress, log=log, cancel=cancel)
            log("Write complete. Verifying by reading back\u2026")
            import io as _io
            buf = _io.BytesIO()
            self.dev.read_gb_ram(buf, ram_bytes, cart_type=ctype,
                                 ram_size_code=rsize, progress=progress,
                                 cancel=cancel)
            self._verify_writeback(buf.getvalue(), data, "GB RAM", log,
                                   save_path=path, is_gba=False)

        self._start(job, "Save restored.")

    def _verify_writeback(self, read_back: bytes, written: bytes, kind: str,
                          log, save_path: str = "", is_gba: bool = True
                          ) -> None:
        from . import verify
        n = min(len(read_back), len(written))
        check = verify.compare_reads(written[:n], read_back[:n])
        # Receipt first, verdict second: a failed write still gets a loud
        # FAILED receipt on disk before this raises.
        if save_path:
            self._write_restore_report(save_path, written, check, is_gba, log)
        if check.passed:
            log(f"\u2713 Verified: the cartridge now matches the save file.")
        else:
            log(f"\u2717 Verification FAILED ({check.detail}). The save may not "
                f"have written correctly - re-seat the cart and try again.")
            raise gx.GBxCartError(
                "Write-back verification failed; the save on the cart does not "
                "match the file.")

    def on_flash_id(self) -> None:
        if not self._is_gba():
            QMessageBox.information(
                self, __app_name__,
                "Flash-ID probing here is for GBA flash carts. Set the switch to "
                "GBA with the cart inserted.")
            return
        self.log("Probing flash ID (non-destructive, forces 5V briefly)\u2026")

        def job(progress, log, cancel):
            probe = self.dev.gba_flash_id_probe()
            for name, data in probe.items():
                if name.startswith("_"):
                    continue   # internal buffers (e.g. raw CFI), not for display
                log(f"  {name}: " + " ".join(f"{b:02X}" for b in data[:8]))
            result = flash_db.interpret(probe)
            baseline = probe.get("baseline", b"")
            if not baseline or baseline.count(0) == len(baseline):
                log("All zeros - no cart seated or wrong switch position.")
            else:
                log(result.summary())
                if result.is_flashable and not result.is_known_chip:
                    log("  Chip not in the database. Check the marking printed "
                        "on the flash chip against the supported list; markings "
                        "containing 6600 or 4050M are known not to work.")

        self._start(job, "Flash-ID probe complete.")

    def on_write_rom(self) -> None:
        """Write a ROM file to the flash cart. Gated: identifies the chip first,
        needs a known CFI sector map, warns clearly, and requires the person to
        type the word ERASE to confirm, since writing wipes the cart."""
        if not self._is_gba():
            QMessageBox.information(
                self, __app_name__,
                "Writing here is for GBA flash carts. Set the switch to GBA "
                "with the flash cart inserted.")
            return

        # Pick the ROM file.
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a .gba ROM to write", "",
            "GBA ROMs (*.gba *.bin);;All files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"Couldn't read file: {exc}")
            return
        if not data:
            QMessageBox.warning(self, __app_name__, "That file is empty.")
            return

        # Identify the chip and read its CFI map first. We refuse to write
        # without a confirmed flashable chip and a real sector map.
        self.log("Checking the flash cart before writing\u2026")

        def check_job(progress, log, cancel):
            probe = self.dev.gba_flash_id_probe()
            # The worker does not pass return values to the after-callback, so
            # stash the result for after_check to read.
            self._write_check_result = flash_db.interpret(probe)

        def after_check() -> None:
            result = getattr(self, "_write_check_result", None)
            self._write_check_result = None
            if result is None or not result.is_flashable:
                QMessageBox.critical(
                    self, __app_name__,
                    "No writable flash chip was identified on this cart. Writing "
                    "is cancelled. Run Identify flash chip to see what the cart "
                    "reports.")
                return
            if result.cfi is None or not result.cfi.erase_regions:
                QMessageBox.critical(
                    self, __app_name__,
                    "The chip was identified, but its sector map could not be "
                    "read from CFI. Writing needs the sector map to erase "
                    "safely, so it is cancelled.")
                return

            regions = result.cfi.erase_regions
            chip_bytes = sum(sz * n for sz, n in regions)
            if len(data) > chip_bytes:
                QMessageBox.critical(
                    self, __app_name__,
                    f"This ROM is {len(data) // (1024*1024)} MB but the chip "
                    f"holds {chip_bytes // (1024*1024)} MB. Writing is "
                    f"cancelled.")
                return

            # Clear, honest warning + typed confirmation.
            warn = (
                f"About to ERASE the cart and write a new ROM.\n\n"
                f"Chip: {result.chip_label}\n"
                f"ROM file: {len(data) // 1024} KB\n\n"
                f"This permanently erases whatever is currently on the cart "
                f"(the current game and its save area). Writing is slow on this "
                f"hardware - it can take a long time - and can be cancelled; a "
                f"partial write is not damage and can be redone.\n\n"
                f"Type the word ERASE to confirm.")
            text, ok = QInputDialog.getText(self, __app_name__, warn)
            if not ok or text.strip().upper() != "ERASE":
                self.log("Write cancelled (not confirmed).")
                return

            a1, a2 = self._unlock_addrs_for(result.variant)

            def write_job(progress, log, cancel):
                # 5V is required; the probe left the device at 3.3V.
                self.dev.select_gba()
                self.dev.set_mode(gx.VOLTAGE_5V)
                import time as _t
                _t.sleep(0.1)
                try:
                    ok2, msg = self.dev.gba_flash_write_rom(
                        data, regions, unlock_a1=a1, unlock_a2=a2,
                        progress=progress, log=log, cancel=cancel)
                finally:
                    self.dev.set_mode(gx.VOLTAGE_3_3V)
                if not ok2:
                    raise RuntimeError(msg)
                log(msg)

            self._start(write_job, "ROM write complete.")

        self._start(check_job, "Flash cart checked.", after=after_check)

    @staticmethod
    def _unlock_addrs_for(variant: str) -> tuple:
        """Map the probe's winning command-set variant to the unlock addresses
        the erase/program sequences should use. Defaults to the standard
        0xAAA/0x555 base, which is what the S29GL-family carts use."""
        v = variant.replace("cfi-", "")
        table = {
            "555": (0x555, 0x2AA), "5555": (0x5555, 0x2AAA),
            "AAA": (0xAAA, 0x555), "AAAA": (0xAAAA, 0x5555),
            "4AAA": (0x4AAA, 0x4555), "7AAA": (0x7AAA, 0x7555),
            "555/AA": (0x555, 0x2AA), "5555/AA": (0x5555, 0x2AAA),
            "AAA/AA": (0xAAA, 0x555), "AAAA/AA": (0xAAAA, 0x5555),
            "4AAA/AA": (0x4AAA, 0x4555), "7AAA/AA": (0x7AAA, 0x7555),
        }
        return table.get(v, (0xAAA, 0x555))

    def on_patch(self) -> None:
        from .patch_window import PatchDialog
        PatchDialog(self).exec()

    def on_cart_advice(self) -> None:
        from . import cart_compat as cc
        # Prefer the connected GBA cart's detected save type; else ask for a ROM.
        save_kind = ""
        title = ""
        if self._is_gba() and self.save_id:
            save_kind = (self.save_id if self.save_id in cc._SAVE_LABELS
                         else gx.save_kind_from_id(self.save_id))
            title = self.resolved_title
        if not save_kind:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a GBA ROM to advise on", "",
                "GBA ROM (*.gba);;All files (*)")
            if not path:
                return
            try:
                data = open(path, "rb").read()
            except OSError as exc:
                QMessageBox.critical(self, __app_name__, f"Cannot read file:\n{exc}")
                return
            save_kind, title = self._save_kind_from_rom(data, path)

        if not save_kind or save_kind == "none":
            # still give the no-save answer if that's what we found
            save_kind = save_kind or ""
        is_rtc = self._looks_like_rtc_pokemon(title)
        result = cc.recommend(save_kind, is_pokemon_rtc=is_rtc)
        self.log(f"Flash cart advice{(' for ' + title) if title else ''}:")
        for line in result.summary().split("\n"):
            self.log("  " + line)
        # also surface the buy links
        for c in (result.primary + result.alt):
            if c.url:
                self.log(f"  {c.name}: {c.url}")

    def _save_kind_from_rom(self, data: bytes, path: str):
        from . import titles
        short, code = titles.gba_header_fields(data) if len(data) >= 0xB0 \
            else ("", "")
        title = ""
        if code:
            info = titles.resolve_gba(data[:0xC0] if len(data) >= 0xC0 else data)
            title = info.full_title
        # scan for the save-type signature string
        save_id = ""
        for tag in (b"EEPROM_V", b"FLASH1M_V", b"FLASH512_V", b"FLASH_V",
                    b"SRAM_V"):
            idx = data.find(tag)
            if idx >= 0:
                save_id = tag.decode("ascii").rstrip("_V")
                # map to kind
                break
        kind = gx.save_kind_from_id(
            {"EEPROM": "EEPROM_V", "FLASH1M": "FLASH1M_V",
             "FLASH512": "FLASH512_V", "FLASH": "FLASH_V",
             "SRAM": "SRAM_V"}.get(save_id, ""))
        # fall back to code database
        if kind == "none" and code:
            db_save = titles.save_type_for_code(code)
            if db_save:
                kind = db_save
        return kind, (title or short)

    @staticmethod
    def _looks_like_rtc_pokemon(title: str) -> bool:
        t = (title or "").lower()
        return any(g in t for g in ("emerald", "ruby", "sapphire"))

    def on_verify_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Verify a ROM file", "",
            "Game ROM (*.gba *.gb *.gbc);;All files (*)")
        if not path:
            return
        try:
            rom = open(path, "rb").read()
        except OSError as exc:
            QMessageBox.critical(self, __app_name__, f"Cannot read file:\n{exc}")
            return
        from . import titles, verify
        is_gba = path.lower().endswith(".gba") or len(rom) >= 0x1000000
        result = (verify.verify_gba(rom, known_db=titles._SHA1) if is_gba
                  else verify.verify_gb(rom, known_db=titles._SHA1))
        import os as _os
        self.log(f"Verifying {_os.path.basename(path)} "
                 f"({'GBA' if is_gba else 'GB/GBC'}, {len(rom)} bytes):")
        for c in result.checks:
            mark = "\u2713" if c.passed else "\u2717"
            self.log(f"  {mark} {c.name}" + (f" ({c.detail})" if c.detail else ""))
        self.log(f"  CRC32 {result.crc32}  SHA-1 {result.sha1}")
        self.log(result.summary())

    def on_reverify_report(self) -> None:
        """Re-check a ROM or save file against the receipt written when it was
        dumped or restored. Offline; catches bit rot, truncation and edits."""
        from . import verify
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the ROM or save to re-verify", "",
            "ROM or save (*.gba *.gb *.gbc *.sav *.srm);;All files (*)")
        if not path:
            return
        report = ""
        for candidate in (path + ".txt", path + ".restore.txt"):
            if os.path.isfile(candidate):
                report = candidate
                break
        if not report:
            report, _ = QFileDialog.getOpenFileName(
                self, "Pick its receipt", os.path.dirname(path),
                "Receipt (*.txt);;All files (*)")
            if not report:
                return
        check = verify.reverify_against_report(path, report)
        mark = "\u2713" if check.passed else "\u2717"
        self.log(f"{mark} re-verify {os.path.basename(path)}: {check.detail}")
        if check.passed:
            QMessageBox.information(
                self, __app_name__,
                f"Still good.\n\n{os.path.basename(path)} {check.detail}.")
        else:
            QMessageBox.warning(
                self, __app_name__,
                f"Mismatch.\n\n{check.detail}.\n\nIf the file was moved or "
                "copied, the copy may be damaged. If it hasn't been touched, "
                "the drive may be failing.")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        self.dev.close()
        super().closeEvent(event)
