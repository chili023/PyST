"""Unterreiter „Firmware“: Zange, Basis und Lambda-Controller aus PyST flashen. Pruefungen und Befehlszeilen in flash.py."""
import os
from typing import Callable, Dict

from PySide6 import QtCore, QtGui, QtWidgets

from . import flash, update
from .flash import FlashCancelled, FlashError
from .hw_page import _Bg


class UsbWorker(QtCore.QThread):
    """USB-Update im eigenen Thread, die Oberflaeche bleibt bedienbar."""
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    done = QtCore.Signal(str, str)                # Status (ok, abbruch, fehler), Text

    def __init__(self, update, parent=None):
        super().__init__(parent)
        self.update = update
        self.stop = False

    def run(self):
        f = flash.UsbFlasher(self.update, log=self.log.emit, progress=self.progress.emit, cancelled=lambda: self.stop)
        try:
            self.done.emit("ok", f.run())
        except FlashCancelled as exc:
            self.done.emit("abbruch", str(exc))
        except (FlashError, OSError, ValueError) as exc:
            self.done.emit("fehler", str(exc))


class LambdaWorker(UsbWorker):
    """F042 ueber die Basis per CAN: Fortschritt, Abbrechen und Ergebnis wie beim USB-Update."""

    def run(self):
        f = flash.LambdaFlasher(self.update, log=self.log.emit, progress=self.progress.emit, cancelled=lambda: self.stop)
        try:
            self.done.emit("ok", f.run())
        except FlashCancelled as exc:
            self.done.emit("abbruch", str(exc))
        except (FlashError, OSError, ValueError) as exc:
            self.done.emit("fehler", str(exc))


class FlashRow(QtWidgets.QGroupBox):
    """Ein Flash-Ziel: Ordner, Stand der Firmware, Hinweis, Knopf."""
    start = QtCore.Signal(str)
    load = QtCore.Signal(str)

    def __init__(self, target: flash.Target, folder: str, changed: Callable[[str, str], None]):
        super().__init__(target.name)
        self.target = target
        self.changed = changed
        lay = QtWidgets.QGridLayout(self)
        self.path = QtWidgets.QLineEdit(folder)
        self.is_file = target.key in (flash.BASIS_STM, flash.BASIS_STLINK, flash.LAMBDA_F042)
        self.path.setPlaceholderText("Firmware-Datei wählen" if self.is_file else "Ordner mit dem Build wählen")
        b_pick = QtWidgets.QPushButton("Datei …" if self.is_file else "Ordner …")
        self.b_go = QtWidgets.QPushButton("Flashen")
        self.info = QtWidgets.QLabel("")
        self.info.setWordWrap(True)
        self.versions = QtWidgets.QLabel("")
        self.versions.setStyleSheet("font-weight: 600;")
        self.remote = QtWidgets.QLabel("")
        self.remote.hide()
        self.b_load = QtWidgets.QPushButton("Herunterladen")
        self.b_load.hide()
        self.b_load.clicked.connect(lambda: self.load.emit(target.key))
        self.current, self.stale = "", False
        hint = QtWidgets.QLabel(target.hint)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        lay.addWidget(self.path, 0, 0)
        lay.addWidget(b_pick, 0, 1)
        lay.addWidget(self.b_go, 0, 2)
        lay.addWidget(self.versions, 1, 0, 1, 3)
        lay.addWidget(self.remote, 2, 0, 1, 2)
        lay.addWidget(self.b_load, 2, 2)
        lay.addWidget(self.info, 3, 0, 1, 3)
        lay.addWidget(hint, 4, 0, 1, 3)
        b_pick.clicked.connect(self.pick)
        self.path.editingFinished.connect(self._edited)
        self.b_go.clicked.connect(lambda: self.start.emit(target.key))
        self.refresh()

    def pick(self):
        title = f"Firmware {self.target.name}"
        if self.is_file:
            pattern = {flash.BASIS_STM: "Update-Datei (*.pystfw)",
                       flash.LAMBDA_F042: "Wideband-Abbild (*.bin)"}.get(self.target.key, "Gesamtdatei (*.bin)")
            d = QtWidgets.QFileDialog.getOpenFileName(self, title, self.path.text(), pattern)[0]
        else:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, title, self.path.text())
        if d:
            self.path.setText(d)
            self._edited()

    def _edited(self):
        self.changed(self.target.key, self.path.text().strip())
        self.refresh()

    def refresh(self):
        new = ""
        try:
            image = flash.load_image(self.target.key, self.path.text().strip())
            text, new = image.summary(), flash.image_version(image)
            color = "#2e7d32"
        except (FlashError, OSError, ValueError) as exc:
            text, color = str(exc), "#c62828"
        if self.target.key == flash.LAMBDA_F042:
            self.versions.setText("Version: der F042 meldet nur das Protokoll, geprüft wird nach dem Update (0xA0)")
        else:
            line, state = flash.version_text(self.current, new, self.stale)
            self.versions.setText(line)
            self.versions.setStyleSheet("font-weight: 600; color: %s;" % {
                "neu": "#ef6c00", "gleich": "#2e7d32", "aelter": "#c62828"}.get(state, "#333333"))
        port = flash.target_port(self.target.key)
        if self.target.key != flash.BASIS_STLINK:
            text += f"\nPort: {port or 'nicht gefunden'}"
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")


class FlashPage(QtWidgets.QScrollArea):
    pyst_update = QtCore.Signal(str)         # neue PyST-Version in main

    def __init__(self, settings: dict, save_settings: Callable[[], None], get_link: Callable[[], object],
                 disconnect: Callable[[], None]):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.get_link = get_link
        self.disconnect = disconnect
        self.proc = None
        self.worker = None
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        root = QtWidgets.QVBoxLayout(inner)
        info = QtWidgets.QLabel("Reihenfolge an einer neuen Basis: erst per ST-Link die Gesamtdatei, dann ESP. Danach "
                                "geht das Basis-Update über USB und das Update des Lambda-Controllers über die Basis. Eine bestehende Verbindung wird vor dem Flashen getrennt.")
        info.setWordWrap(True)
        root.addWidget(info)
        folders: Dict[str, str] = settings.setdefault("flash_ordner", {})
        self.rows = {}
        for key, t in flash.TARGETS.items():
            row = FlashRow(t, folders.get(key, ""), self._folder_changed)
            row.start.connect(self.start)
            row.load.connect(self._download)
            root.addWidget(row)
            self.rows[key] = row
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("font-size: 15px; font-weight: 600;")
        root.addWidget(self.status)
        line = QtWidgets.QHBoxLayout()
        self.bar = QtWidgets.QProgressBar()
        self.bar.hide()
        self.b_cancel = QtWidgets.QPushButton("Abbrechen")
        self.b_cancel.hide()
        self.b_cancel.clicked.connect(self._cancel)
        line.addWidget(self.bar, 1)
        line.addWidget(self.b_cancel)
        root.addLayout(line)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.log.setMinimumHeight(180)
        root.addWidget(self.log, 1)
        self.bg = _Bg()
        self.remote: Dict[str, update.Entry] = {}
        self.update_base = update.FIRMWARE_URL
        self.download_dir = os.path.join(os.path.expanduser("~"), "PyST", "firmware_updates")
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2000)

    def check_updates(self, version: str):
        """Beim Programmstart: im Hintergrund nach Neuem suchen, ohne Netz oder Branch bleibt alles still."""
        self.bg.submit("pyst", update.latest_pyst, lambda v, e: self._pyst_seen(v, version))
        self.bg.submit("manifest", lambda: update.fetch_manifest(base=self.update_base), self._manifest)

    def _pyst_seen(self, remote, local: str):
        if remote and update.is_newer(remote, local):
            self.pyst_update.emit(remote)

    def _manifest(self, entries, err):
        self.remote = entries or {}
        self._show_remote()

    def _show_remote(self):
        for key, row in self.rows.items():
            e = self.remote.get(key)
            neu = e is not None and (not row.current or update.is_newer(e.version, row.current))
            row.remote.setVisible(e is not None)
            row.remote.setText(f"Online: {e.version}" + (" (Update verfügbar)" if neu else "") if e else "")
            row.b_load.setVisible(e is not None and row.is_file and key != flash.BASIS_STLINK)

    def _download(self, key: str):
        e = self.remote.get(key)
        if e is None or self.busy:
            return
        self._status(f"Lade {e.file} …", "#ef6c00")
        self.bg.submit("laden", lambda: update.download(e, self.download_dir, self.update_base),
                       lambda path, err: self._downloaded(key, path, err))

    def _downloaded(self, key: str, path, err):
        if err is not None:
            self._status(f"Download fehlgeschlagen: {err}", "#c62828")
            return
        row = self.rows[key]
        row.path.setText(path)
        self._folder_changed(key, path)
        row.refresh()
        self._status(f"{os.path.basename(path)} geladen und geprüft, bereit zum Flashen.", "#2e7d32")

    def _folder_changed(self, key: str, folder: str):
        self.settings.setdefault("flash_ordner", {})[key] = folder
        self.save_settings()

    @property
    def busy(self) -> bool:
        """Solange geflasht wird, darf sich das automatische Verbinden den Port nicht nehmen."""
        return self.proc is not None or self.worker is not None

    def _refresh(self):
        self.bg.poll()
        if self.busy or not self.isVisible():
            return
        link = self.get_link()
        if link is not None and (link.is_basis or link.is_zange_usb):
            self.bg.submit("versionen", lambda: flash.device_versions(link), self._versions)
        else:
            for row in self.rows.values():
                row.stale = bool(row.current)
        for row in self.rows.values():
            row.refresh()

    def _versions(self, found, err):
        if err is not None or found is None:
            return
        for key, row in self.rows.items():
            if key in found:
                row.current, row.stale = found[key], False
            row.refresh()
        self._show_remote()

    def _set_busy(self, busy: bool):
        for row in self.rows.values():
            row.b_go.setEnabled(not busy)

    def _status(self, text: str, color: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {color};")

    def start(self, key: str):
        if self.busy:
            return
        row = self.rows[key]
        t = flash.TARGETS[key]
        if self.get_link() is not None:
            self.disconnect()                     # Port freigeben; danach neu suchen, der Name kann wechseln
        try:
            image, cmd = flash.build_command(key, row.path.text().strip(), flash.target_port(key))
        except (FlashError, OSError, ValueError) as exc:
            self._status(str(exc), "#c62828")
            return
        text = f"{t.name} jetzt flashen?\n\n{image.summary()}\n\n{t.hint}"
        if QtWidgets.QMessageBox.question(self, "Flashen", text) != QtWidgets.QMessageBox.Yes:
            return
        if key in (flash.BASIS_STM, flash.LAMBDA_F042):
            self.run_usb(key, image)
        else:
            self.run(key, cmd)

    def run_usb(self, key: str, image):
        self.log.clear()
        self._status(f"{flash.TARGETS[key].name}: läuft, Basis nicht vom Strom trennen …", "#ef6c00")
        self._set_busy(True)
        is_lambda = key == flash.LAMBDA_F042
        self.bar.setRange(0, len(image.lambda_app) if is_lambda else image.update.image_len)
        self.bar.setValue(0)
        self.bar.show()
        self.b_cancel.setEnabled(True)
        self.b_cancel.show()
        self.worker = LambdaWorker(image.lambda_app, self) if is_lambda else UsbWorker(image.update, self)
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.progress.connect(lambda done, _n: self.bar.setValue(done))
        self.worker.done.connect(lambda state, text: self._usb_done(key, state, text))
        self.worker.start()

    def _cancel(self):
        if self.worker is not None:
            self.worker.stop = True
            self.b_cancel.setEnabled(False)

    def _usb_done(self, key: str, state: str, text: str):
        self.worker.wait()
        self.worker = None
        self.bar.hide()
        self.b_cancel.hide()
        self._set_busy(False)
        t = flash.TARGETS[key]
        if state == "ok" and key == flash.LAMBDA_F042:
            self._status(f"{t.name}: geflasht, 0x190-Telegramme mit Version {text} kamen wieder.", "#2e7d32")
        elif state == "ok":
            self._status(f"{t.name}: Version {text} läuft.", "#2e7d32")
        elif state == "abbruch" and key == flash.LAMBDA_F042:
            self._status(f"{t.name}: {text}", "#ef6c00")
        elif state == "abbruch":
            self._status(f"{t.name}: abgebrochen, alte Firmware bleibt.", "#ef6c00")
        else:
            self.log.appendPlainText(text)
            self._status(f"{t.name}: {text}", "#c62828")

    def run(self, key: str, cmd):
        self.log.clear()
        self.log.appendPlainText(" ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        self._status(f"{flash.TARGETS[key].name}: läuft …", "#ef6c00")
        self._set_busy(True)
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(lambda code, _st: self._finished(key, code))
        self.proc.errorOccurred.connect(lambda _e: self._failed(key))
        self.proc.start(cmd[0], cmd[1:])

    def _read(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf8", "replace")
        for line in text.replace("\r", "\n").splitlines():
            if line.strip():
                self.log.appendPlainText(line.rstrip())

    def _finished(self, key: str, code: int):
        if self.proc is None:
            return
        self._read()
        self.proc = None
        self._set_busy(False)
        t = flash.TARGETS[key]
        if code == 0:
            nach = " 12 V einmal aus und ein." if key == flash.BASIS_STLINK else ""
            self._status(f"{t.name}: geflasht.{nach}", "#2e7d32")
        else:
            self._status(f"{t.name}: Flashen fehlgeschlagen (Code {code}), siehe Ausgabe.", "#c62828")

    def _failed(self, key: str):
        if self.proc is not None and self.proc.error() == QtCore.QProcess.FailedToStart:
            self.proc = None
            self._set_busy(False)
            self._status(f"{flash.TARGETS[key].name}: Werkzeug ließ sich nicht starten.", "#c62828")
