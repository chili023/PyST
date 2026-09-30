"""
Reiter "rusEFI": Verbindung zum Steuergeraet, .ini, Auswahl der aufzuzeichnenden Kanaele.

Gezeigt und aufgezeichnet werden nur die gewaehlten Kanaele. Die komplette Kanalliste der .ini
erscheint nur im Dialog "Kanaele hinzufuegen".
Kanal-Setups (Name -> Kanalliste + Abtastrate) liegen in einstellungen.json unter "rusefi" und lassen
sich als Datei exportieren/importieren.
"""
import json
import os
import time
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtWidgets

from . import rusefi

DEFAULT_PROFILE = "Standard"
FILE_TYPE = "pyst-rusefi-kanalsetup"


class ChannelPicker(QtWidgets.QDialog):
    """Alle Kanaele der .ini mit Suche; angehakt = aufzeichnen."""

    def __init__(self, ini: rusefi.IniDef, selected: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("rusEFI-Kanäle auswählen")
        self.resize(720, 640)
        lay = QtWidgets.QVBoxLayout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Suchen (Bezeichnung, Kanalname oder Einheit) …")
        self.search.setClearButtonEnabled(True)
        self.only_sel = QtWidgets.QCheckBox("nur angehakte")
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.only_sel)
        lay.addLayout(top)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Bezeichnung", "Kanal (.ini)", "Einheit"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        sel = set(selected)
        chans = list(ini.channels.values())
        self.table.setRowCount(len(chans))
        for i, c in enumerate(chans):
            it = QtWidgets.QTableWidgetItem(c.title)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if c.name in sel else QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, c.name)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(c.name))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(c.unit))
        self.table.resizeColumnToContents(1)
        lay.addWidget(self.table, 1)
        self.count = QtWidgets.QLabel()
        lay.addWidget(self.count)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.search.textChanged.connect(self._filter)
        self.only_sel.toggled.connect(self._filter)
        self.table.itemChanged.connect(lambda _it: self._update_count())
        self.table.cellDoubleClicked.connect(self._toggle_row)
        self._update_count()
        self.search.setFocus()

    def _toggle_row(self, row, _col):
        it = self.table.item(row, 0)
        it.setCheckState(QtCore.Qt.Unchecked if it.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)

    def _filter(self):
        words = self.search.text().lower().split()
        only = self.only_sel.isChecked()
        for i in range(self.table.rowCount()):
            text = " ".join(self.table.item(i, k).text() for k in range(3)).lower()
            hide = any(w not in text for w in words)
            hide |= only and self.table.item(i, 0).checkState() != QtCore.Qt.Checked
            self.table.setRowHidden(i, hide)

    def _update_count(self):
        self.count.setText(f"{len(self.selected())} Kanäle angehakt")

    def selected(self) -> List[str]:
        return [self.table.item(i, 0).data(QtCore.Qt.UserRole) for i in range(self.table.rowCount())
                if self.table.item(i, 0).checkState() == QtCore.Qt.Checked]


class RusefiPage(QtWidgets.QWidget):
    changed = QtCore.Signal()            # Verbindung oder Kanalauswahl geaendert

    def __init__(self, settings: dict, save: Callable[[], None]):
        super().__init__()
        self.settings = settings
        self._save_settings = save
        cfg = self.cfg
        cfg.setdefault("profiles", {DEFAULT_PROFILE: {"kanaele": list(rusefi.DEFAULT_CHANNELS),
                                                      "rate_hz": rusefi.DEFAULT_RATE_HZ}})
        cfg.setdefault("profile", DEFAULT_PROFILE)
        if cfg["profile"] not in cfg["profiles"]:
            cfg["profile"] = next(iter(cfg["profiles"]))
        prof = cfg["profiles"][cfg["profile"]]
        self.selected: List[str] = list(cfg.get("kanaele", prof["kanaele"]))
        self.link: Optional[rusefi.RusefiLink] = None
        self.ini: Optional[rusefi.IniDef] = None
        self._last_auto = 0.0

        lay = QtWidgets.QVBoxLayout(self)
        # ---- Verbindung
        g = QtWidgets.QGroupBox("Steuergerät")
        gl = QtWidgets.QGridLayout(g)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(260)
        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setFixedWidth(32)
        self.connect_btn = QtWidgets.QPushButton("Verbinden")
        self.auto_check = QtWidgets.QCheckBox("automatisch verbinden, sobald ein rusEFI angesteckt ist")
        self.auto_check.setChecked(cfg.get("auto", True))
        self.rate_spin = QtWidgets.QSpinBox()
        self.rate_spin.setRange(5, 200)
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setValue(int(cfg.get("rate_hz", prof.get("rate_hz", rusefi.DEFAULT_RATE_HZ))))
        self.status = QtWidgets.QLabel("nicht verbunden")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        gl.addWidget(QtWidgets.QLabel("Port"), 0, 0)
        gl.addWidget(self.port_combo, 0, 1)
        gl.addWidget(self.refresh_btn, 0, 2)
        gl.addWidget(self.connect_btn, 0, 3)
        gl.addWidget(QtWidgets.QLabel("Abtastrate"), 1, 0)
        gl.addWidget(self.rate_spin, 1, 1)
        gl.addWidget(self.auto_check, 2, 1, 1, 3)
        gl.addWidget(self.status, 3, 0, 1, 5)
        gl.setColumnStretch(4, 1)
        lay.addWidget(g)

        # ---- .ini
        g = QtWidgets.QGroupBox("TunerStudio-.ini (Kanalbeschreibung)")
        gl = QtWidgets.QGridLayout(g)
        self.ini_label = QtWidgets.QLabel("keine .ini geladen")
        self.ini_label.setWordWrap(True)
        self.ini_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.ini_file_btn = QtWidgets.QPushButton("Datei wählen …")
        self.ini_drive_btn = QtWidgets.QPushButton("Vom Steuergerät (USB-Laufwerk) holen")
        hint = QtWidgets.QLabel("Die .ini hängt an der Firmware-Version des Steuergeräts, nicht am Tune. "
                                "Normalerweise holt PyST sie automatisch vom USB-Laufwerk „RUSEFI“ "
                                "und merkt sie sich.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        gl.addWidget(self.ini_label, 0, 0, 1, 3)
        gl.addWidget(self.ini_file_btn, 1, 0)
        gl.addWidget(self.ini_drive_btn, 1, 1)
        gl.addWidget(hint, 2, 0, 1, 3)
        gl.setColumnStretch(2, 1)
        lay.addWidget(g)

        # ---- Kanaele
        g = QtWidgets.QGroupBox("Aufgezeichnete Kanäle")
        gl = QtWidgets.QVBoxLayout(g)
        pr = QtWidgets.QHBoxLayout()
        pr.addWidget(QtWidgets.QLabel("Kanal-Setup"))
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setMinimumWidth(200)
        pr.addWidget(self.profile_combo)
        self.save_btn = QtWidgets.QPushButton("Speichern")
        self.save_as_btn = QtWidgets.QPushButton("Speichern unter …")
        self.del_btn = QtWidgets.QPushButton("Löschen")
        self.export_btn = QtWidgets.QPushButton("Exportieren …")
        self.import_btn = QtWidgets.QPushButton("Importieren …")
        for b in (self.save_btn, self.save_as_btn, self.del_btn, self.export_btn, self.import_btn):
            pr.addWidget(b)
        pr.addStretch(1)
        gl.addLayout(pr)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Bezeichnung", "Kanal (.ini)", "Einheit", "Live"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        gl.addWidget(self.table, 1)
        bt = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Kanäle hinzufügen …")
        self.remove_btn = QtWidgets.QPushButton("Markierte entfernen")
        self.up_btn = QtWidgets.QPushButton("▲")
        self.down_btn = QtWidgets.QPushButton("▼")
        for b in (self.up_btn, self.down_btn):
            b.setFixedWidth(36)
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            bt.addWidget(b)
        bt.addStretch(1)
        self.dirty_label = QtWidgets.QLabel("")
        self.dirty_label.setStyleSheet("color: #b26a00;")
        bt.addWidget(self.dirty_label)
        gl.addLayout(bt)
        lay.addWidget(g, 1)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.on_connect)
        self.auto_check.toggled.connect(lambda on: self._set_cfg(auto=on))
        self.rate_spin.valueChanged.connect(self._rate_changed)
        self.ini_file_btn.clicked.connect(self.on_ini_file)
        self.ini_drive_btn.clicked.connect(self.on_ini_drive)
        self.profile_combo.activated.connect(self._profile_chosen)
        self.save_btn.clicked.connect(self.on_save_profile)
        self.save_as_btn.clicked.connect(self.on_save_profile_as)
        self.del_btn.clicked.connect(self.on_delete_profile)
        self.export_btn.clicked.connect(self.on_export)
        self.import_btn.clicked.connect(self.on_import)
        self.add_btn.clicked.connect(self.on_add)
        self.remove_btn.clicked.connect(self.on_remove)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))

        self.refresh_ports()
        ini_path = cfg.get("ini", "")
        if ini_path and os.path.exists(ini_path):
            self._load_ini(ini_path, quiet=True)
        self._fill_profiles()
        self._fill_table()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)

    # ------------------------------------------------------------------ Einstellungen
    @property
    def cfg(self) -> dict:
        return self.settings.setdefault("rusefi", {})

    def _set_cfg(self, **kw):
        self.cfg.update(kw)
        self._save_settings()

    def _profile(self) -> dict:
        return self.cfg["profiles"][self.cfg["profile"]]

    def _selection_changed(self):
        self._set_cfg(kanaele=list(self.selected))
        if self.link and self.link.ini:
            self.link.set_channels(self.selected)
        self._fill_table()
        self.changed.emit()

    def _rate_changed(self, hz):
        self._set_cfg(rate_hz=hz)
        if self.link:
            self.link.set_rate(hz)
        self._update_dirty()

    # ------------------------------------------------------------------ Anzeige
    def _fill_profiles(self):
        self.profile_combo.clear()
        for name in sorted(self.cfg["profiles"], key=str.lower):
            self.profile_combo.addItem(name)
        self.profile_combo.setCurrentText(self.cfg["profile"])
        self._update_dirty()

    def _update_dirty(self):
        p = self._profile()
        dirty = p.get("kanaele") != self.selected or int(p.get("rate_hz", 0)) != self.rate_spin.value()
        self.dirty_label.setText("Änderungen nicht im Kanal-Setup gespeichert" if dirty else "")

    def _fill_table(self):
        self.table.setRowCount(len(self.selected))
        for i, name in enumerate(self.selected):
            c = self.ini.channels.get(name) if self.ini else None
            cells = [c.title if c else name, name, c.unit if c else "", "–"]
            for k, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if k == 0 and self.ini and c is None:
                    it.setForeground(QtCore.Qt.red)
                    it.setToolTip("Kanal gibt es in dieser .ini nicht – wird nicht aufgezeichnet")
                if k == 3:
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.table.setItem(i, k, it)
        self.table.resizeColumnToContents(1)
        self._update_dirty()

    def _tick(self):
        if self.link:
            for i, name in enumerate(self.selected):
                v = self.link.value(name)
                it = self.table.item(i, 3)
                if it is not None:
                    it.setText("–" if v is None else f"{v:.6g}")
            self._show_status()
        elif self.auto_check.isChecked() and time.time() - self._last_auto > 3.0:
            self._last_auto = time.time()
            ports = rusefi.list_rusefi_ports()
            if ports:
                self.refresh_ports()
                self.connect(ports[0][0], quiet=True)

    def _show_status(self):
        L = self.link
        if L is None:
            self.status.setText("nicht verbunden")
            self.status.setStyleSheet("")
            return
        if L.ini is None:
            text, color = f"{L.port}: {L.signature}\nKeine passende .ini – bitte unten wählen.", "#b71c1c"
        elif L.error:
            text, color = f"{L.port}: Fehler – {L.error}", "#b71c1c"
        else:
            warn = "" if L.signature_ok else "\nACHTUNG: .ini passt nicht zur Firmware – Werte können falsch sein!"
            text = (f"{L.port}: {L.signature}\n{len(L.channel_names())} Kanäle, "
                    f"{L.measured_hz:.0f} Hz gelesen{warn}")
            color = "#b71c1c" if warn else "#1b5e20"
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def refresh_ports(self):
        current = self.port_combo.currentText() or self.cfg.get("port", "")
        self.port_combo.clear()
        for dev, desc in rusefi.list_rusefi_ports():
            self.port_combo.addItem(dev)
            self.port_combo.setItemData(self.port_combo.count() - 1, desc, QtCore.Qt.ToolTipRole)
        if current:
            i = self.port_combo.findText(current)
            if i >= 0:
                self.port_combo.setCurrentIndex(i)
            elif not self.port_combo.count():
                self.port_combo.setEditText(current)

    # ------------------------------------------------------------------ Verbindung
    def on_connect(self):
        if self.link:
            self.disconnect()
            return
        port = self.port_combo.currentText().strip()
        if not port:
            QtWidgets.QMessageBox.information(self, "rusEFI", "Kein rusEFI-Steuergerät gefunden (USB-Kabel?).")
            return
        self.connect(port)

    def connect(self, port: str, quiet: bool = False) -> bool:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            link = rusefi.RusefiLink(port, rate_hz=self.rate_spin.value())
        except Exception as exc:
            if not quiet:
                QtWidgets.QMessageBox.critical(self, "rusEFI", f"{port} lässt sich nicht öffnen:\n{exc}")
            self._last_auto = time.time() + 10.0          # nicht sofort wieder probieren
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.link = link
        self._set_cfg(port=port)
        if self.ini is None or self.ini.signature != link.signature:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                path, how = rusefi.resolve_ini(link.signature, self.cfg.get("ini", ""))
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            if path:
                self._load_ini(path, quiet=True, how=how)
        if self.ini is not None and self.ini.signature == link.signature:
            link.set_ini(self.ini, self.selected)
        self.connect_btn.setText("Trennen")
        self._show_status()
        self.changed.emit()
        return True

    def disconnect(self):
        if self.link:
            self.link.close()
        self.link = None
        self.connect_btn.setText("Verbinden")
        self._last_auto = time.time() + 5.0
        self._show_status()
        self.changed.emit()

    def active_link(self) -> Optional[rusefi.RusefiLink]:
        """Verbindung, die Daten liefert (fuer die Laeufe)."""
        return self.link if self.link and self.link.ini is not None else None

    def live(self, name: str) -> Optional[float]:
        return self.link.value(name) if self.link else None

    # ------------------------------------------------------------------ .ini
    def _load_ini(self, path: str, quiet: bool = False, how: str = "") -> bool:
        try:
            ini = rusefi.IniDef(path)
            err = ini.check()
        except Exception as exc:
            err = str(exc)
        if err:
            if not quiet:
                QtWidgets.QMessageBox.warning(self, "rusEFI", f"{os.path.basename(path)}:\n{err}")
            return False
        self.ini = ini
        self._set_cfg(ini=path)
        self.ini_label.setText(f"{ini.signature}\n{len(ini.channels)} Kanäle in der .ini"
                               + (f" – {how}" if how else "") + f"\n{path}")
        self._fill_table()
        return True

    def _use_ini(self, path: str):
        sig = rusefi.read_signature(path)
        if self.link and sig != self.link.signature:
            ans = QtWidgets.QMessageBox.question(
                self, "rusEFI", f"Die .ini gehört zu\n  {sig}\ndas Steuergerät meldet\n  {self.link.signature}\n\n"
                                "Kanäle können an anderer Stelle liegen – Werte wären dann falsch.\n"
                                "Trotzdem verwenden?")
            if ans != QtWidgets.QMessageBox.Yes:
                return
        try:
            path = rusefi.store_in_cache(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "rusEFI", str(exc))
            return
        if self._load_ini(path) and self.link:
            self.link.set_ini(self.ini, self.selected)
        self.changed.emit()

    def on_ini_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "TunerStudio-.ini wählen", os.path.expanduser("~"), "INI (*.ini *.7z);;Alle Dateien (*)")
        if not path:
            return
        if path.lower().endswith(".7z"):
            import tempfile
            tmp = tempfile.mkdtemp(prefix="pyst_ini_")
            try:
                inis = rusefi.extract_7z(path, tmp)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "rusEFI", str(exc))
                return
            if not inis:
                QtWidgets.QMessageBox.warning(self, "rusEFI", "Keine .ini im Archiv.")
                return
            path = inis[0]
        self._use_ini(path)

    def on_ini_drive(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            path = rusefi.ini_from_drive(self.link.signature if self.link else "")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not path:
            QtWidgets.QMessageBox.information(
                self, "rusEFI", "Kein USB-Laufwerk „RUSEFI“ mit passender .ini gefunden.\n"
                                "(Steuergerät angesteckt? Laufwerk im Finder/Explorer sichtbar?)")
            return
        self._use_ini(path)

    # ------------------------------------------------------------------ Kanalauswahl
    def on_add(self):
        if self.ini is None:
            QtWidgets.QMessageBox.information(self, "rusEFI", "Zuerst eine .ini laden (oder Steuergerät verbinden).")
            return
        dlg = ChannelPicker(self.ini, self.selected, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        chosen = dlg.selected()
        # bisherige Reihenfolge behalten, neue hinten anhaengen
        self.selected = [n for n in self.selected if n in chosen or n not in self.ini.channels] \
            + [n for n in chosen if n not in self.selected]
        self._selection_changed()

    def _marked_rows(self) -> List[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def on_remove(self):
        rows = set(self._marked_rows())
        if rows:
            self.selected = [n for i, n in enumerate(self.selected) if i not in rows]
            self._selection_changed()

    def _move(self, d: int):
        rows = self._marked_rows()
        if len(rows) != 1:
            return
        i, j = rows[0], rows[0] + d
        if 0 <= j < len(self.selected):
            self.selected[i], self.selected[j] = self.selected[j], self.selected[i]
            self._selection_changed()
            self.table.selectRow(j)

    # ------------------------------------------------------------------ Kanal-Setups
    def _apply_profile(self, name: str):
        self.cfg["profile"] = name
        p = self._profile()
        self.selected = list(p.get("kanaele", []))
        self.rate_spin.blockSignals(True)
        self.rate_spin.setValue(int(p.get("rate_hz", rusefi.DEFAULT_RATE_HZ)))
        self.rate_spin.blockSignals(False)
        self._set_cfg(rate_hz=self.rate_spin.value())
        if self.link:
            self.link.set_rate(self.rate_spin.value())
        self._selection_changed()
        self._fill_profiles()

    def _profile_chosen(self, _i):
        name = self.profile_combo.currentText()
        if name == self.cfg["profile"]:
            return
        if self.dirty_label.text():
            ans = QtWidgets.QMessageBox.question(
                self, "Kanal-Setup", f"Änderungen an „{self.cfg['profile']}“ verwerfen?")
            if ans != QtWidgets.QMessageBox.Yes:
                self.profile_combo.setCurrentText(self.cfg["profile"])
                return
        self._apply_profile(name)

    def _store_profile(self, name: str):
        self.cfg["profiles"][name] = {"kanaele": list(self.selected), "rate_hz": self.rate_spin.value()}
        self.cfg["profile"] = name
        self._save_settings()
        self._fill_profiles()

    def on_save_profile(self):
        self._store_profile(self.cfg["profile"])

    def on_save_profile_as(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Kanal-Setup speichern", "Name:",
                                                  text=self.cfg["profile"])
        name = name.strip()
        if not ok or not name:
            return
        if name in self.cfg["profiles"] and name != self.cfg["profile"]:
            if QtWidgets.QMessageBox.question(self, "Kanal-Setup", f"„{name}“ überschreiben?") \
                    != QtWidgets.QMessageBox.Yes:
                return
        self._store_profile(name)

    def on_delete_profile(self):
        if len(self.cfg["profiles"]) <= 1:
            QtWidgets.QMessageBox.information(self, "Kanal-Setup", "Das letzte Kanal-Setup kann nicht gelöscht werden.")
            return
        name = self.cfg["profile"]
        if QtWidgets.QMessageBox.question(self, "Kanal-Setup", f"„{name}“ löschen?") != QtWidgets.QMessageBox.Yes:
            return
        del self.cfg["profiles"][name]
        self._apply_profile(sorted(self.cfg["profiles"], key=str.lower)[0])

    def on_export(self):
        name = self.cfg["profile"]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Kanal-Setup exportieren", os.path.join(os.path.expanduser("~"), f"rusEFI_{name}.json"),
            "Kanal-Setup (*.json)")
        if not path:
            return
        data = {"typ": FILE_TYPE, "name": name, "kanaele": self.selected, "rate_hz": self.rate_spin.value(),
                "signatur": self.ini.signature if self.ini else ""}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    def on_import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Kanal-Setup importieren", os.path.expanduser("~"),
                                                        "Kanal-Setup (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("typ") != FILE_TYPE or not isinstance(data.get("kanaele"), list):
                raise ValueError("keine PyST-rusEFI-Kanal-Setup-Datei")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Kanal-Setup", f"{os.path.basename(path)}:\n{exc}")
            return
        name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
        while name in self.cfg["profiles"]:
            name += " (importiert)"
        self.cfg["profiles"][name] = {"kanaele": [str(x) for x in data["kanaele"]],
                                      "rate_hz": int(data.get("rate_hz", rusefi.DEFAULT_RATE_HZ))}
        self._apply_profile(name)
        missing = [n for n in self.selected if self.ini and n not in self.ini.channels]
        if missing:
            QtWidgets.QMessageBox.information(self, "Kanal-Setup",
                                              "Diese Kanäle gibt es in der aktuellen .ini nicht:\n" + ", ".join(missing))

    def close_link(self):
        if self.link:
            self.link.close()
            self.link = None
