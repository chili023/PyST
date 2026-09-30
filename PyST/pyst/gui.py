"""
PyST – Oberflaeche (PySide6 + pyqtgraph). Mac, Linux (Raspberry Pi), Windows.

Reiter:  Messen | Fahrzeuge & Setups | Auswertung | ECU | Einstellungen
"""
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Dict, Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__, physics, report, storage
from .channels import lambda_from_afr, run_channels
from .db import SETUP_SUMMARY_KEYS, VEHICLE_FIELDS, Database, base_dir, field_label
from .dialogs import FieldForm, SetupForm
from .link import (DEFAULT_SIM_PORT, SIM_PORT, DynoLink, guess_port, is_pst_firmware, list_serial_ports,
                   pst_board_ports)
from .ratio_dialog import RatioDialog
from .plots import EXTRA_PREFIX, LOWER_DEFAULT, MAX_LOWER, MAX_OVERLAYS, DynoPlot, fmt_value, run_color
from .rusefi import COLUMN_PREFIX
from .runner import ABORTED, DONE, IDLE, RUN, WAIT, RunController
from .rusefi_page import RusefiPage
from .viewer import LogViewer
from .widgets import Gauge

SETTINGS_FILE = os.path.join(base_dir(), "einstellungen.json")
LIVE_KEY = "live"
FILTER_S_DEFAULT = (1.75, 0.75)       # = LabVIEW 35/15 bei 20 Hz

# (Feld, Beschriftung, Min, Max, Nachkommastellen)
PARAM_FIELDS = {
    "Prüfstand": [
        ("inkr", "Rollengeber [Inkr/U]", 1, 10000, 0),
        ("roll_circ", "Rollenumfang [m]", 0.01, 20, 4),
        ("inertia", "Rollenträgheit J [kgm²]", 0.01, 1000, 3),
        ("imp", "Zündung [Imp/U]", 0.25, 16, 2),
    ],
    "Lauf": [
        ("ratio", "Übersetzung nKW/nRolle", 0.01, 100, 3),
        ("n_min", "n vom Gas [1/min]", 0, 30000, 0),
        ("n_stop", "n Stop [1/min] (0 = aus)", 0, 30000, 0),
    ],
    "Filter && Verluste": [
        ("ma_s", "Gleitender Mittelwert [s]", 0.02, 10, 2),
        ("dq_s", "Differenzenquotient ± [s]", 0.02, 5, 2),
        ("ma", "Gleitender Mittelwert [Punkte]", 1, 255, 0),
        ("dq", "Differenzenquotient [Punkte]", 1, 255, 0),
        ("m0", "Verlustmoment M0 [Nm]", -100, 100, 2),
        ("m1", "Verlustmoment M1 [Nm]", -100, 100, 2),
        ("n1", "bei Rollendrehzahl n1 [1/min]", 1, 100000, 0),
    ],
    "Klima": [
        ("temp_c", "Lufttemperatur [°C]", -30, 60, 1),
        ("p_mbar", "Luftdruck [mbar]", 500, 1200, 1),
    ],
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(d: dict):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, ensure_ascii=False)


class AutoHideLabel(QtWidgets.QLabel):
    """Nimmt nur Platz ein, wenn Text drinsteht."""

    def setText(self, text: str):
        super().setText(text)
        self.setVisible(bool(text))


def compact_table(t: QtWidgets.QTableWidget, row_h: int = 20):
    """Duenne Zeilen: feste Zeilenhoehe, flacher Kopf."""
    vh = t.verticalHeader()
    vh.setVisible(False)
    vh.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
    vh.setMinimumSectionSize(row_h)
    vh.setDefaultSectionSize(row_h)
    t.horizontalHeader().setFixedHeight(row_h + 2)
    # eigener Rahmen: der Rahmen des Systems (macOS) ist unterschiedlich breit und wurde unten abgeschnitten
    t.setStyleSheet("QTableView { border: 1px solid #c4c4c4; gridline-color: #d6d6d6; }"
                    "QTableView::item { padding: 0px 4px; }")
    t.setAttribute(QtCore.Qt.WA_MacShowFocusRect, False)
    t.setWordWrap(False)


def color_icon(color: str) -> QtGui.QIcon:
    pm = QtGui.QPixmap(14, 14)
    pm.fill(QtGui.QColor(color))
    return QtGui.QIcon(pm)


# =============================================================================================== Einstellungen
class SettingsPage(QtWidgets.QScrollArea):
    def __init__(self, settings: dict):
        super().__init__()
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        grid = QtWidgets.QGridLayout(inner)

        g = QtWidgets.QGroupBox("Verbindung")
        gl = QtWidgets.QGridLayout(g)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setFixedWidth(32)
        self.connect_btn = QtWidgets.QPushButton("Verbinden")
        self.replay_btn = QtWidgets.QPushButton("Lauf-Datei im Simulator abspielen …")
        self.live_check = QtWidgets.QCheckBox("Live-Anzeige auch ohne Lauf")
        self.live_check.setChecked(settings.get("live", True))
        self.fw_label = QtWidgets.QLabel("nicht verbunden")
        self.fw_label.setWordWrap(True)
        self.fw_label.setStyleSheet("color: gray;")
        gl.addWidget(QtWidgets.QLabel("Port"), 0, 0)
        gl.addWidget(self.port_combo, 0, 1)
        gl.addWidget(self.refresh_btn, 0, 2)
        gl.addWidget(self.connect_btn, 1, 1, 1, 2)
        gl.addWidget(self.replay_btn, 2, 1, 1, 2)
        gl.addWidget(self.live_check, 3, 1, 1, 2)
        self.auto_connect = QtWidgets.QCheckBox("Messboard automatisch verbinden (Prüfung alle 5 s)")
        self.auto_connect.setChecked(settings.get("auto_connect", True))
        gl.addWidget(self.auto_connect, 4, 1, 1, 2)
        gl.addWidget(self.fw_label, 5, 0, 1, 3)
        grid.addWidget(g, 0, 0)

        self.fields: Dict[str, QtWidgets.QDoubleSpinBox] = {}
        defaults = physics.DynoParams(**{k: v for k, v in settings.get("params", {}).items()
                                         if k in physics.DynoParams.__dataclass_fields__})
        self.filter_seconds = settings.get("filter_seconds", True)
        if defaults.ma_s <= 0:
            defaults.ma_s = settings.get("ma_s", FILTER_S_DEFAULT[0])
        if defaults.dq_s <= 0:
            defaults.dq_s = settings.get("dq_s", FILTER_S_DEFAULT[1])
        pos = [(0, 1), (1, 0), (1, 1), (2, 0)]
        for (group, fields), (r, c) in zip(PARAM_FIELDS.items(), pos):
            box = QtWidgets.QGroupBox(group)
            fl = QtWidgets.QFormLayout(box)
            if group == "Filter && Verluste":
                self.filter_form = fl
                self.filter_mode = QtWidgets.QComboBox()
                self.filter_mode.addItems(["Sekunden (passt sich der Messfrequenz an)",
                                           "Messpunkte (wie LabVIEW)"])
                self.filter_mode.setCurrentIndex(0 if self.filter_seconds else 1)
                fl.addRow("Filter in", self.filter_mode)
            for key, label, lo, hi, dec in fields:
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setDecimals(dec)
                if dec == 2 and key.endswith("_s"):
                    sp.setSingleStep(0.05)
                sp.setValue(float(getattr(defaults, key)))
                sp.setKeyboardTracking(False)
                self.fields[key] = sp
                fl.addRow(label, sp)
            if group == "Lauf":
                self.ratio_btn = QtWidgets.QPushButton("Übersetzung einmessen …")
                fl.addRow(self.ratio_btn)
                note = QtWidgets.QLabel("n vom Gas: ab hier wird auf fallende Drehzahl (= Laufende) geprüft. "
                                        "Muss über der Haltedrehzahl liegen.")
                note.setWordWrap(True)
                note.setStyleSheet("color: gray;")
                fl.addRow(note)
            if group == "Filter && Verluste":
                self.filter_label = QtWidgets.QLabel()
                self.filter_label.setWordWrap(True)
                self.filter_label.setStyleSheet("color: gray;")
                fl.addRow(self.filter_label)
            if group == "Klima":
                self.climate_auto = QtWidgets.QCheckBox("vor jedem Lauf vom Sensor lesen")
                self.climate_auto.setChecked(settings.get("climate_auto", True))
                self.climate_btn = QtWidgets.QPushButton("Klima jetzt lesen")
                self.ka_label = QtWidgets.QLabel()
                fl.addRow(self.climate_auto)
                fl.addRow(self.climate_btn, self.ka_label)
            grid.addWidget(box, r, c)

        box = QtWidgets.QGroupBox("Anzeige")
        fl = QtWidgets.QFormLayout(box)
        self.lambda_radio = QtWidgets.QRadioButton("Lambda λ")
        self.afr_radio = QtWidgets.QRadioButton("AFR")
        (self.lambda_radio if settings.get("lambda", True) else self.afr_radio).setChecked(True)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.lambda_radio)
        hb.addWidget(self.afr_radio)
        fl.addRow("Gemisch als", hb)
        self.data_label = QtWidgets.QLabel()
        self.data_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        fl.addRow("Datenbank", self.data_label)
        grid.addWidget(box, 2, 1)
        grid.setRowStretch(3, 1)

        for k in ("temp_c", "p_mbar"):
            self.fields[k].valueChanged.connect(self.update_ka)
        self.update_ka()
        self.filter_mode.currentIndexChanged.connect(lambda i: self.set_filter_seconds(i == 0))
        self.set_filter_seconds(self.filter_seconds)
        self._rate = 60.0

    def set_filter_seconds(self, on: bool):
        self.filter_seconds = on
        self.filter_mode.blockSignals(True)
        self.filter_mode.setCurrentIndex(0 if on else 1)
        self.filter_mode.blockSignals(False)
        for k in ("ma_s", "dq_s"):
            self.filter_form.setRowVisible(self.fields[k], on)
        for k in ("ma", "dq"):
            self.filter_form.setRowVisible(self.fields[k], not on)
        self.update_filter_label()

    def params(self) -> physics.DynoParams:
        d = {k: sp.value() for k, sp in self.fields.items()}
        d["ma"], d["dq"] = int(d["ma"]), int(d["dq"])
        if not self.filter_seconds:
            d["ma_s"] = d["dq_s"] = 0.0
        return physics.DynoParams(**d)

    def set_params(self, p: physics.DynoParams):
        """Filter in Sekunden, wenn p sie hat, sonst Messpunkte (z.B. LabVIEW-Lauf im Simulator)."""
        for k, sp in self.fields.items():
            v = float(getattr(p, k))
            if k in ("ma_s", "dq_s") and v <= 0:
                continue                                # Sekundenwerte behalten
            sp.setValue(v)
        self.set_filter_seconds(p.ma_s > 0 or p.dq_s > 0)

    def update_ka(self):
        ka = physics.din70020(self.fields["temp_c"].value(), self.fields["p_mbar"].value())
        self.ka_label.setText(f"DIN 70020 k = {ka:.3f}")

    def update_filter_label(self, rate: Optional[float] = None):
        if rate:
            self._rate = rate
        rate = getattr(self, "_rate", 60.0)
        if self.filter_seconds:
            q = physics.effective(self.params(), rate)
            self.filter_label.setText(f"Bei {rate:.0f} Hz: MA {q.ma} / dq {q.dq} Messpunkte. "
                                      f"LabVIEW-Standard 35/15 bei 20 Hz = 1,75 s / 0,75 s.")
        else:
            ma, dq = self.fields["ma"].value(), self.fields["dq"].value()
            self.filter_label.setText(f"Bei {rate:.0f} Hz: Mittelwert {ma / rate:.2f} s, Differenz ±{dq / rate:.2f} s. "
                                      f"Messpunkte – 20-Hz-Werte bei 60 Hz ×3 nehmen.")


# =============================================================================================== Datenbank
class DatabasePage(QtWidgets.QWidget):
    changed = QtCore.Signal()
    use_setup = QtCore.Signal(int, int)        # vehicle_id, setup_id

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.vid: Optional[int] = None
        self.sid: Optional[int] = None
        lay = QtWidgets.QHBoxLayout(self)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Fahrzeuge</b>"))
        self.vlist = QtWidgets.QListWidget()
        left.addWidget(self.vlist, 1)
        hb = QtWidgets.QHBoxLayout()
        for text, slot in (("Neu", self.new_vehicle), ("Löschen", self.delete_vehicle)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            hb.addWidget(b)
        left.addLayout(hb)
        left.addWidget(QtWidgets.QLabel("<b>Setups des Fahrzeugs</b>"))
        self.slist = QtWidgets.QListWidget()
        left.addWidget(self.slist, 1)
        hb = QtWidgets.QHBoxLayout()
        for text, slot in (("Neu", self.new_setup), ("Kopieren", self.copy_setup), ("Löschen", self.delete_setup)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            hb.addWidget(b)
        left.addLayout(hb)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(300)
        lay.addWidget(lw)

        right = QtWidgets.QVBoxLayout()
        vbox = QtWidgets.QGroupBox("Fahrzeug")
        vl = QtWidgets.QVBoxLayout(vbox)
        self.vform = FieldForm(VEHICLE_FIELDS[0][1])
        vl.addWidget(self.vform)
        vf = QtWidgets.QFormLayout()
        self.vnotiz = QtWidgets.QLineEdit()
        vf.addRow("Notiz", self.vnotiz)
        vl.addLayout(vf)
        right.addWidget(vbox)

        sbox = QtWidgets.QGroupBox("Setup")
        sl = QtWidgets.QVBoxLayout(sbox)
        top = QtWidgets.QFormLayout()
        self.sname = QtWidgets.QLineEdit()
        self.snotiz = QtWidgets.QPlainTextEdit()
        self.snotiz.setMaximumHeight(50)
        top.addRow("Name", self.sname)
        top.addRow("Notiz", self.snotiz)
        sl.addLayout(top)
        self.sform = SetupForm()
        sl.addWidget(self.sform, 1)
        right.addWidget(sbox, 1)

        hb = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Speichern")
        self.save_btn.clicked.connect(self.save)
        self.use_btn = QtWidgets.QPushButton("Für neue Läufe verwenden")
        self.use_btn.clicked.connect(lambda: self.sid and self.use_setup.emit(self.vid, self.sid))
        hb.addStretch(1)
        hb.addWidget(self.use_btn)
        hb.addWidget(self.save_btn)
        right.addLayout(hb)
        lay.addLayout(right, 1)

        self.vlist.currentItemChanged.connect(self._vehicle_selected)
        self.slist.currentItemChanged.connect(self._setup_selected)
        self.reload()

    def reload(self, select_vid: Optional[int] = None, select_sid: Optional[int] = None):
        self.vlist.blockSignals(True)
        self.vlist.clear()
        for r in self.db.vehicles():
            it = QtWidgets.QListWidgetItem(r["name"])
            it.setData(QtCore.Qt.UserRole, r["id"])
            self.vlist.addItem(it)
            if r["id"] == (select_vid or self.vid):
                self.vlist.setCurrentItem(it)
        self.vlist.blockSignals(False)
        self._vehicle_selected(self.vlist.currentItem(), None, select_sid)

    def _vehicle_selected(self, cur, _prev=None, select_sid=None):
        self.vid = cur.data(QtCore.Qt.UserRole) if cur else None
        v = (self.db.vehicle(self.vid) if self.vid else None) or {}
        self.vform.set_values(v)
        self.vnotiz.setText(v.get("notiz", ""))
        self.slist.blockSignals(True)
        self.slist.clear()
        if self.vid:
            for r in self.db.setups(self.vid):
                it = QtWidgets.QListWidgetItem(r["name"] or "(ohne Namen)")
                it.setData(QtCore.Qt.UserRole, r["id"])
                self.slist.addItem(it)
                if r["id"] == (select_sid or self.sid):
                    self.slist.setCurrentItem(it)
        self.slist.blockSignals(False)
        self._setup_selected(self.slist.currentItem())

    def _setup_selected(self, cur, _prev=None):
        self.sid = cur.data(QtCore.Qt.UserRole) if cur else None
        s = (self.db.setup(self.sid) if self.sid else None) or {}
        self.sname.setText(s.get("name", ""))
        self.snotiz.setPlainText(s.get("notiz", ""))
        self.sform.set_values(s)
        self.use_btn.setEnabled(bool(self.sid))

    def save(self):
        if self.vid is None and not self.vform.has_content():
            QtWidgets.QMessageBox.information(self, "Speichern", "Bitte zuerst ein Fahrzeug anlegen (Neu).")
            return
        vdata = self.vform.values()
        vdata["notiz"] = self.vnotiz.text()
        self.vid = self.db.save_vehicle(vdata, self.vid)
        if self.sid or self.sname.text() or self.sform.has_content():
            self.sid = self.db.save_setup(self.vid, self.sname.text() or "Setup", self.sform.values(),
                                          self.snotiz.toPlainText(), self.sid)
        self.reload(self.vid, self.sid)
        self.changed.emit()

    def new_vehicle(self):
        self.vid = self.db.save_vehicle({"hersteller": "Neu", "modell": ""})
        self.sid = None
        self.reload(self.vid)
        self.changed.emit()

    def delete_vehicle(self):
        if self.vid and QtWidgets.QMessageBox.question(
                self, "Löschen", "Fahrzeug mit allen Setups löschen? Läufe bleiben erhalten.") \
                == QtWidgets.QMessageBox.Yes:
            self.db.delete_vehicle(self.vid)
            self.vid = self.sid = None
            self.reload()
            self.changed.emit()

    def new_setup(self):
        if not self.vid:
            return
        self.sid = self.db.save_setup(self.vid, "Neues Setup", {})
        self.reload(self.vid, self.sid)
        self.changed.emit()

    def copy_setup(self):
        if not self.sid:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Setup kopieren", "Name des neuen Setups:",
                                                  text=self.sname.text() + " (Kopie)")
        if ok:
            self.sid = self.db.copy_setup(self.sid, name)
            self.reload(self.vid, self.sid)
            self.changed.emit()

    def delete_setup(self):
        if self.sid and QtWidgets.QMessageBox.question(self, "Löschen", "Setup löschen? Läufe bleiben erhalten.") \
                == QtWidgets.QMessageBox.Yes:
            self.db.delete_setup(self.sid)
            self.sid = None
            self.reload(self.vid)
            self.changed.emit()


# =============================================================================================== Hauptfenster
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, db: Optional[Database] = None):
        super().__init__()
        self.setWindowTitle(f"PyST {__version__} – WildBugChilGru")
        self.resize(1500, 920)
        self.settings = load_settings()
        self.db = db or Database()
        self.link: Optional[DynoLink] = None
        self.ctrl: Optional[RunController] = None
        self.firmware = ""
        self.cache: Dict[int, tuple] = {}          # run_id -> (run, result, channels, params)
        self.last_keepalive = 0.0
        self._done_handled = True
        self._plot_due = 0.0
        self._shown_rate = 0.0
        self._cursor_n = 0.0
        self._cursor_inside = False
        self._live_vals: Dict[str, str] = {}

        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.page_settings = SettingsPage(self.settings)
        self.page_db = DatabasePage(self.db)
        self.viewer = LogViewer(self.db, self._viewer_channels)
        self.page_rusefi = RusefiPage(self.settings, lambda: save_settings(self.settings))
        self.tabs.addTab(self._build_measure(), "Messen")
        self.tabs.addTab(self.page_db, "Fahrzeuge && Setups")
        self.tabs.addTab(self.viewer, "Auswertung")
        self.tabs.addTab(self.page_rusefi, "ECU")
        self.tabs.addTab(self.page_settings, "Einstellungen")
        self.page_settings.data_label.setText(self.db.path)
        self._build_menu()
        self._wire()
        self._refresh_ports()
        self._reload_vehicle_combo(self.settings.get("vehicle_id"), self.settings.get("setup_id"))
        self._reload_runs()
        self.plot.set_lower_visible(self.settings.get("lower_visible", True))
        self._ecu_changed()
        self.page_settings.update_filter_label(60.0)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)
        self._auto_failed: Dict[str, float] = {}
        self._manual_off = False
        self.auto_timer = QtCore.QTimer(self)
        self.auto_timer.timeout.connect(self._auto_connect)
        self.auto_timer.start(5000)
        QtCore.QTimer.singleShot(800, self._auto_connect)          # beim Start gleich versuchen
        self._update_buttons()
        QtGui.QShortcut(QtGui.QKeySequence("F1"), self, activated=self.on_start)
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.on_abort)

    # ------------------------------------------------------------------ Aufbau Messen
    def _build_measure(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        self.status = QtWidgets.QLabel()
        self.status.setAlignment(QtCore.Qt.AlignCenter)
        self.status.setFixedHeight(34)
        top.addWidget(self.status, 1)
        self.connect_main = QtWidgets.QPushButton("Verbinden")
        self.connect_main.setFixedHeight(34)
        self.connect_main.setMinimumWidth(100)
        top.addWidget(self.connect_main)
        self.start_btn = QtWidgets.QPushButton("START  (F1)")
        self.abort_btn = QtWidgets.QPushButton("Abbrechen  (Esc)")
        for b in (self.start_btn, self.abort_btn):
            b.setFixedHeight(34)
            b.setMinimumWidth(130)
        self.start_btn.setStyleSheet("font-size: 16px; font-weight: 700;")
        top.addWidget(self.start_btn)
        top.addWidget(self.abort_btn)
        lay.addLayout(top)

        info = QtWidgets.QHBoxLayout()
        self.vehicle_combo = QtWidgets.QComboBox()
        self.setup_combo = QtWidgets.QComboBox()
        self.vehicle_combo.setMinimumWidth(220)
        self.setup_combo.setMinimumWidth(220)
        self.run_note = QtWidgets.QLineEdit()
        self.run_note.setPlaceholderText("Notiz zu diesem Lauf (z.B. Änderung gegenüber vorher)")
        self.autosave = QtWidgets.QCheckBox("Auto-Speichern")
        self.autosave.setChecked(self.settings.get("autosave", True))
        for label, wdg in (("Fahrzeug", self.vehicle_combo), ("Setup", self.setup_combo)):
            info.addWidget(QtWidgets.QLabel(label))
            info.addWidget(wdg)
        info.addWidget(self.run_note, 1)
        info.addWidget(self.autosave)
        lay.addLayout(info)

        gauges = QtWidgets.QHBoxLayout()
        self.g_pmax = Gauge("Pmax", "PS")
        self.g_mmax = Gauge("Mmax", "Nm")
        for g in (self.g_pmax, self.g_mmax):
            g.value.setStyleSheet("font-size: 24px; font-weight: 700; color: #b71c1c;")
        self.g_mmax.value.setStyleSheet("font-size: 24px; font-weight: 700; color: #0d47a1;")
        self.g_n = Gauge("Motor (Rolle × i)", "1/min")
        self.g_ign = Gauge("Motor (Zündung)", "1/min")
        self.g_v = Gauge("Rolle", "km/h")
        self.g_egt1 = Gauge("EGT 1", "°C")
        self.g_egt2 = Gauge("EGT 2", "°C")
        self.g_lam = Gauge("Lambda", "λ")
        self.g_ratio = Gauge("Übersetzung Zündung/Rolle", "")
        self.g_ratio_ecu = Gauge("Übersetzung ECU/Rolle", "")
        self.g_rate = Gauge("Messfrequenz", "Hz")
        self.g_lost = Gauge("COM verloren", "")
        # Auswaehlbare Anzeigen: Schluessel -> (Name im Menue, Widget)
        self.gauges = {
            "pmax": ("Pmax / Leistung am Cursor", self.g_pmax), "mmax": ("Mmax / Drehmoment am Cursor", self.g_mmax),
            "n": ("Motor (Rolle × i)", self.g_n), "ign": ("Motor (Zündung)", self.g_ign), "v": ("Rolle km/h", self.g_v),
            "egt1": ("EGT 1", self.g_egt1), "egt2": ("EGT 2", self.g_egt2), "lam": ("Lambda / AFR", self.g_lam),
            "ratio": ("Übersetzung Zündung/Rolle", self.g_ratio), "ratio_ecu": ("Übersetzung ECU/Rolle", self.g_ratio_ecu),
            "rate": ("Messfrequenz", self.g_rate), "lost": ("COM verloren", self.g_lost),
        }
        hidden = set(self.settings.get("gauges_hidden", ["egt2"]))
        # neu hinzugekommene Anzeigen: nur "Übersetzung ECU/Rolle" gleich zeigen, andere erst auf Wunsch
        known = self.settings.get("gauges_known")
        if known is not None:
            hidden |= {k for k in self.gauges if k not in known and k != "ratio_ecu"}
        else:
            hidden.add("ratio")
        self.settings["gauges_known"] = list(self.gauges)
        self.settings["gauges_hidden"] = sorted(hidden)
        for key, (_name, g) in self.gauges.items():
            gauges.addWidget(g)
            g.setVisible(key not in hidden)
        self.gauge_row = gauges
        self.ecu_gauges: Dict[str, Gauge] = {}        # rusEFI-Kanal -> Anzeige (hinter den festen Anzeigen)
        self.gauge_btn = QtWidgets.QToolButton()
        self.gauge_btn.setText("Anzeigen …")
        self.gauge_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.gauge_menu = QtWidgets.QMenu(self.gauge_btn)
        self.gauge_menu.aboutToShow.connect(self._fill_gauge_menu)
        self.gauge_btn.setMenu(self.gauge_menu)
        gauges.addWidget(self.gauge_btn)
        lay.addLayout(gauges)

        split = QtWidgets.QSplitter()
        self.measure_split = split
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.plot = DynoPlot()
        self.plot.set_limits(self.settings.get("plot_limits", {}))
        self.plot.limits_changed.connect(self._limits_changed)
        ll.addWidget(self.plot, 1)
        self.cursor_table = QtWidgets.QTableWidget(0, 8)
        self.cursor_table.setHorizontalHeaderLabels(["", "Lauf", "n [1/min]", "PS", "Nm", "λ", "EGT 1", "EGT 2"])
        self.cursor_table.verticalHeader().setVisible(False)
        self.cursor_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.cursor_table.setColumnWidth(0, 18)
        self.cursor_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        compact_table(self.cursor_table, row_h=24)
        self.cursor_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._fit_cursor_table()
        self.cursor_toggle = QtWidgets.QToolButton()
        self.cursor_toggle.setAutoRaise(True)
        self.cursor_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.cursor_toggle.setCheckable(True)
        self.cursor_toggle.setStyleSheet("QToolButton { font-size: 11px; color: #555; padding: 0px; }")
        self.cursor_toggle.setFixedHeight(16)
        self.cursor_toggle.toggled.connect(self._show_cursor_table)
        self.cursor_cols_btn = QtWidgets.QToolButton()
        self.cursor_cols_btn.setText("Spalten …")
        self.cursor_cols_btn.setAutoRaise(True)
        self.cursor_cols_btn.setStyleSheet("QToolButton { font-size: 11px; color: #555; padding: 0px 4px; }")
        self.cursor_cols_btn.setFixedHeight(16)
        self.cursor_cols_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.cursor_menu = QtWidgets.QMenu(self.cursor_cols_btn)
        self.cursor_menu.aboutToShow.connect(self._fill_cursor_menu)
        self.cursor_cols_btn.setMenu(self.cursor_menu)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(self.cursor_toggle)
        bar.addWidget(self.cursor_cols_btn)
        bar.addStretch(1)
        ll.addLayout(bar)
        ll.addWidget(self.cursor_table)
        self.cursor_toggle.setChecked(self.settings.get("cursor_table", True))
        self._show_cursor_table(self.cursor_toggle.isChecked())
        self.result_label = AutoHideLabel("")
        self.result_label.setStyleSheet("font-size: 13px;")
        self.result_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.result_label.setText("")
        ll.addWidget(self.result_label)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QLabel("<b>Läufe</b>"))
        self.run_filter = QtWidgets.QComboBox()
        self.run_filter.addItems(["alle", "aktuelles Fahrzeug", "aktuelles Setup"])
        self.run_filter.setCurrentIndex(self.settings.get("run_filter", 0))
        hb.addWidget(self.run_filter, 1)
        rl.addLayout(hb)
        self.run_tree = QtWidgets.QTreeWidget()
        self.run_tree.setHeaderLabels(["Lauf", "PS", "bei", "Nm", "Hz", "Notiz"])
        self.run_tree.setRootIsDecorated(False)
        self.run_tree.setAlternatingRowColors(True)
        self.run_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.run_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.run_tree.header().setStretchLastSection(True)
        self.run_tree.setUniformRowHeights(True)
        self.run_tree.setStyleSheet("QTreeView::item { padding: 0px; }")
        rl.addWidget(self.run_tree, 1)
        grid = QtWidgets.QGridLayout()
        self.b_none = QtWidgets.QPushButton("Alle ausblenden")
        self.b_view = QtWidgets.QPushButton("In Auswertung")
        self.b_del = QtWidgets.QPushButton("Löschen")
        self.b_ratio = QtWidgets.QPushButton("Übersetzung einmessen …")
        self.b_ratio.setStyleSheet("font-weight: 600;")
        for i, b in enumerate((self.b_none, self.b_view, self.b_del, self.b_ratio)):
            grid.addWidget(b, i // 2, i % 2)
        rl.addLayout(grid)
        right.setMinimumWidth(300)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        lay.addWidget(split, 1)
        self._set_status("Nicht verbunden – „Verbinden“ drücken", "#dddddd")
        return w

    def _build_menu(self):
        v = self.menuBar().addMenu("Ansicht")
        size = v.addMenu("Größe (wirkt nach Neustart)")
        grp = QtGui.QActionGroup(self)
        current = str(self.settings.get("ui_scale", "auto"))
        for label, val in (("Automatisch (an Bildschirm anpassen)", "auto"), ("100 %", "1.0"), ("90 %", "0.9"),
                           ("80 %", "0.8"), ("70 %", "0.7")):
            a = size.addAction(label)
            a.setCheckable(True)
            a.setChecked(current == val)
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, val=val: self._set_ui_scale(val))
        v.addAction("Jetzt neu starten", self._restart)
        m = self.menuBar().addMenu("Datei")
        for text, slot, key in (("Läufe importieren …", self.on_import, "Ctrl+I"),
                                ("PDF-Bericht der sichtbaren Läufe …", self.on_pdf, "Ctrl+P"),
                                ("CSV-Export des markierten Laufs …", self.on_csv, "Ctrl+E"),
                                ("Beenden", self.close, "Ctrl+Q")):
            a = m.addAction(text)
            a.triggered.connect(slot)
            a.setShortcut(key)

    def _limits_changed(self, lim: dict):
        self.settings["plot_limits"] = lim
        save_settings(self.settings)

    def _wire(self):
        s = self.page_settings
        s.refresh_btn.clicked.connect(self._refresh_ports)
        s.connect_btn.clicked.connect(lambda: self.on_connect())
        self.connect_main.clicked.connect(lambda: self.on_connect())
        s.auto_connect.toggled.connect(lambda on: (self.settings.update(auto_connect=on), save_settings(self.settings)))
        s.replay_btn.clicked.connect(lambda: self.on_replay())
        s.climate_btn.clicked.connect(self.on_climate)
        s.ratio_btn.clicked.connect(self.on_measure_ratio)
        s.lambda_radio.toggled.connect(self._lambda_mode_changed)
        for k in ("ma", "dq", "ma_s", "dq_s"):
            s.fields[k].valueChanged.connect(lambda _=None: s.update_filter_label(self._current_rate()))
        self.start_btn.clicked.connect(self.on_start)
        self.abort_btn.clicked.connect(self.on_abort)
        self.plot.cursor_moved.connect(self._cursor_moved)
        self.plot.cursor_left.connect(self._cursor_left)
        self.run_tree.itemChanged.connect(self._run_item_changed)
        self.run_tree.itemDoubleClicked.connect(lambda it, _c: self._open_in_viewer(it.data(0, QtCore.Qt.UserRole)))
        self.run_tree.customContextMenuRequested.connect(self._run_context_menu)
        self.run_filter.currentIndexChanged.connect(lambda _: self._reload_runs())
        self.vehicle_combo.currentIndexChanged.connect(self._vehicle_combo_changed)
        self.setup_combo.currentIndexChanged.connect(lambda _: self.run_filter.currentIndex() and self._reload_runs())
        self.b_none.clicked.connect(self.on_hide_all)
        self.b_view.clicked.connect(lambda: self._open_in_viewer(self._current_run_id()))
        self.b_del.clicked.connect(self.on_delete)
        self.b_ratio.clicked.connect(self.on_measure_ratio)
        self.viewer.import_btn.clicked.connect(self.on_import)
        self.viewer.pdf_btn.clicked.connect(lambda: self.on_pdf())
        self.viewer.csv_btn.clicked.connect(lambda: self.on_csv(self.viewer.current_run_id()))
        self.page_db.changed.connect(self._db_changed)
        self.viewer.db_changed.connect(self._db_changed)
        self.page_db.use_setup.connect(self._use_setup)
        self.page_rusefi.changed.connect(self._ecu_changed)
        self.plot.top_menu.aboutToShow.connect(self._fill_top_menu)
        self.plot.bottom_menu.aboutToShow.connect(self._fill_lower_menu)
        self._lambda_mode_changed(s.lambda_radio.isChecked())

    # ------------------------------------------------------------------ Hilfen
    def _set_status(self, text: str, color: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"background: {color}; font-size: 18px; font-weight: 700; "
                                  f"border-radius: 6px; padding: 0px;")

    def _refresh_ports(self):
        combo = self.page_settings.port_combo
        current = combo.currentText() or self.settings.get("port", "")
        combo.clear()
        for dev, desc in list_serial_ports():
            combo.addItem(dev, dev)
            combo.setItemData(combo.count() - 1, desc, QtCore.Qt.ToolTipRole)
        pick = current if current and (not current.startswith(SIM_PORT + ":") or current == DEFAULT_SIM_PORT) \
            else (guess_port() or DEFAULT_SIM_PORT)
        i = combo.findText(pick)
        if i >= 0:
            combo.setCurrentIndex(i)
        else:
            combo.setEditText(pick)

    def _current_rate(self) -> float:
        if self.ctrl and self.ctrl.live.rate > 0:
            return self.ctrl.live.rate
        if self.link and self.link.replay:
            return self.link.replay.rate
        return 60.0

    def _run_color(self, row) -> str:
        return run_color(row)

    def _set_ui_scale(self, val: str):
        self.settings["ui_scale"] = val
        save_settings(self.settings)
        if QtWidgets.QMessageBox.question(self, "Größe", "Größe gespeichert. Jetzt neu starten?") \
                == QtWidgets.QMessageBox.Yes:
            self._restart()

    def _restart(self):
        self._persist()
        if self.link:
            self.link.close()
            self.link = None
        os.environ.pop("SIMPLEDYNO_SCALED", None)
        os.environ.pop("QT_SCALE_FACTOR", None)
        os.execv(sys.executable, [sys.executable, "-m", "pyst"] + [a for a in sys.argv[1:]])

    def _toggle_gauge(self, key: str, on: bool):
        self.gauges[key][1].setVisible(on)
        hidden = set(self.settings.get("gauges_hidden", ["egt2"]))
        (hidden.discard if on else hidden.add)(key)
        self.settings["gauges_hidden"] = sorted(hidden)
        save_settings(self.settings)

    # ---- rusEFI-Kanaele als Anzeige und im Diagramm
    def _ecu_available(self):
        """[(Kanal, Bezeichnung, Einheit)] der aufgezeichneten rusEFI-Kanaele."""
        ini = self.page_rusefi.ini
        out = []
        for name in self.page_rusefi.selected:
            c = ini.channels.get(name) if ini else None
            out.append((name, c.title if c else name, c.unit if c else ""))
        return out

    # ---- Auswahlmenues: je eines fuer Anzeigen, Hauptdiagramm, unteres Diagramm, Cursor-Werte
    @staticmethod
    def _check_row(m: QtWidgets.QMenu, text: str, boxes):
        """Menuezeile: Text + Kaestchen [(Beschriftung, an?, Funktion(an))]; Menue bleibt beim Klicken offen."""
        row = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(row)
        hl.setContentsMargins(18, 1, 10, 1)
        hl.addWidget(QtWidgets.QLabel(text), 1)
        out = []
        for label, on, fn in boxes:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(on)
            cb.toggled.connect(fn)
            hl.addWidget(cb)
            out.append(cb)
        act = QtWidgets.QWidgetAction(m)
        act.setDefaultWidget(row)
        m.addAction(act)
        return out

    @staticmethod
    def _limit(boxes, n_max):
        n = sum(cb.isChecked() for cb in boxes)
        for cb in boxes:
            cb.setEnabled(cb.isChecked() or n < n_max)

    def _fill_gauge_menu(self):
        """Anzeigen oben: feste Anzeigen + ECU-Kanaele."""
        m = self.gauge_menu
        m.clear()
        hidden = set(self.settings.get("gauges_hidden", ["egt2"]))
        m.addSection("Messboard / Auswertung")
        for key, (name, _g) in self.gauges.items():
            self._check_row(m, name, [("", key not in hidden, lambda on, k=key: self._toggle_gauge(k, on))])
        avail = self._ecu_available()
        m.addSection("ECU")
        if not avail:
            m.addAction("(keine Kanäle – Reiter ECU)").setEnabled(False)
        shown = self.settings.get("ecu_gauges", [])
        for name, label, unit in avail:
            self._check_row(m, f"{label} [{unit}]" if unit else label,
                            [("", name in shown, lambda on, n=name: self._toggle_ecu(n, on, "ecu_gauges"))])

    def _fill_top_menu(self):
        """Hauptdiagramm: ECU-Kurven ueber der Leistung, unteres Diagramm ein/aus."""
        m = self.plot.top_menu
        m.clear()
        vis = m.addAction("Unteres Diagramm anzeigen")
        vis.setCheckable(True)
        vis.setChecked(self.settings.get("lower_visible", True))
        vis.toggled.connect(self._toggle_lower_visible)
        m.addSection(f"ECU-Kurven über der Leistung (max. {MAX_OVERLAYS})")
        avail = self._ecu_available()
        if not avail:
            m.addAction("(keine Kanäle – Reiter ECU)").setEnabled(False)
            return
        plotted = self.settings.get("ecu_plot", [])
        boxes = []
        for name, label, unit in avail:
            boxes += self._check_row(m, f"{label} [{unit}]" if unit else label, [
                ("", name in plotted,
                 lambda on, n=name: (self._toggle_ecu(n, on, "ecu_plot"), self._limit(boxes, MAX_OVERLAYS)))])
        self._limit(boxes, MAX_OVERLAYS)

    def _fill_lower_menu(self):
        """Unteres Diagramm: Belegung links/rechts."""
        m = self.plot.bottom_menu
        m.clear()
        m.addSection(f"Unteres Diagramm – links / rechts (je max. {MAX_LOWER})")
        left, right = self._lower_sides()
        boxes = {"left": [], "right": []}

        def update():
            for side in boxes:
                self._limit(boxes[side], MAX_LOWER)

        for key, label, unit in self._lower_candidates():
            pair = []

            def toggled(on, k=key, side="left", pair=pair):
                other = pair[1] if side == "left" else pair[0]
                if on and other.isChecked():
                    other.setChecked(False)            # ein Kanal liegt auf genau einer Achse
                self._set_lower(k, side, on)
                update()
            pair += self._check_row(m, f"{label} [{unit}]" if unit else label, [
                ("links", key in left, lambda on, t=toggled: t(on, side="left")),
                ("rechts", key in right, lambda on, t=toggled: t(on, side="right"))])
            boxes["left"].append(pair[0])
            boxes["right"].append(pair[1])
        update()

    def _fill_cursor_menu(self):
        """Spalten der Cursor-Tabelle."""
        m = self.cursor_menu
        m.clear()
        m.addSection("Spalten der Cursor-Tabelle")
        cols = self._cursor_cols()
        for key, label, unit in self._lower_candidates():
            self._check_row(m, f"{label} [{unit}]" if unit else label,
                            [("", key in cols, lambda on, k=key: self._set_cursor_col(k, on))])

    def _cursor_cols(self):
        return self.settings.get("cursor_cols", ["lam", "egt1", "egt2"])

    def _set_cursor_col(self, key: str, on: bool):
        order = [k for k, _l, _u in self._lower_candidates()]
        cur = set(self._cursor_cols()) - {key} | ({key} if on else set())
        self.settings["cursor_cols"] = [k for k in order if k in cur]
        save_settings(self.settings)
        self._ecu_changed()

    # ---- unteres Diagramm: Belegung links/rechts
    def _lower_candidates(self, avail=None):
        """[(Schluessel, Bezeichnung, Einheit)]: Board-Kanaele und aufgezeichnete ECU-Kanaele."""
        out = [("lam", "Lambda / AFR (Board)", ""), ("egt1", "EGT 1 (Board)", "°C"), ("egt2", "EGT 2 (Board)", "°C")]
        for name, label, unit in (avail if avail is not None else self._ecu_available()):
            out.append((EXTRA_PREFIX + name, f"{label} (ECU)", unit))
        return out

    def _lower_sides(self):
        return (self.settings.get("lower_left", LOWER_DEFAULT["left"]),
                self.settings.get("lower_right", LOWER_DEFAULT["right"]))

    def _set_lower(self, key: str, side: str, on: bool):
        cur = [k for k in self.settings.get(f"lower_{side}", LOWER_DEFAULT[side]) if k != key]
        if on:
            cur.append(key)
        self.settings[f"lower_{side}"] = cur
        save_settings(self.settings)
        self._ecu_changed()

    def _toggle_lower_visible(self, on: bool):
        self.settings["lower_visible"] = on
        save_settings(self.settings)
        self.plot.set_lower_visible(on)
        self._cursor_moved(self._cursor_n)

    def _apply_lower(self, avail):
        keys = {k: (lab, unit) for k, lab, unit in self._lower_candidates(avail)}
        left, right = self._lower_sides()
        labels = {k: (lab.replace(" (Board)", "").replace("Lambda / AFR", "Lambda"), unit)
                  for k, (lab, unit) in keys.items()}
        self.plot.set_lower([k for k in left if k in keys], [k for k in right if k in keys], labels)

    def _toggle_ecu(self, name: str, on: bool, key: str):
        cur = [n for n in self.settings.get(key, []) if n != name]
        if on:
            cur.append(name)
        self.settings[key] = cur
        save_settings(self.settings)
        self._ecu_changed()

    def _ecu_changed(self):
        """Anzeigen und Diagramm-Kanaele nach Auswahl/Verbindung neu aufbauen."""
        avail = {n: (lab, unit) for n, lab, unit in self._ecu_available()}
        want = [n for n in self.settings.get("ecu_gauges", []) if n in avail]
        for name in list(self.ecu_gauges):
            if name not in want:
                g = self.ecu_gauges.pop(name)
                self.gauge_row.removeWidget(g)
                g.deleteLater()
        for name in want:
            if name not in self.ecu_gauges:
                g = Gauge(f"{avail[name][0]} (ECU)", avail[name][1])
                self.ecu_gauges[name] = g
                self.gauge_row.insertWidget(self.gauge_row.indexOf(self.gauge_btn), g)
        self.plot.set_overlays([(n, *avail[n]) for n in self.settings.get("ecu_plot", []) if n in avail])
        self._apply_lower([(n, *avail[n]) for n in avail])
        self._redraw_runs()
        if self.ctrl and self.ctrl.frames:
            self._draw_live(final=self.ctrl.state == DONE)

    def _ecu_names(self):
        """ECU-Kanaele, die zu jedem Lauf ins Diagramm-Datenlager kommen (Anzeigen, Kurven, Cursor)."""
        lower = [k[len(EXTRA_PREFIX):] for k in sum(self._lower_sides(), []) + self._cursor_cols()
                 if k.startswith(EXTRA_PREFIX)]
        return list(dict.fromkeys(self.settings.get("ecu_gauges", []) + self.settings.get("ecu_plot", [])
                                  + lower + ["RPMValue"]))

    def _ecu_extra_run(self, run, res) -> Dict[str, np.ndarray]:
        cols = run.get("columns", {})
        return {n: cols[COLUMN_PREFIX + n][res.segment] for n in self._ecu_names() if COLUMN_PREFIX + n in cols}

    def _ecu_extra_live(self, r) -> Dict[str, np.ndarray]:
        from .rusefi import resample
        names = self._ecu_names()
        frames = self.ctrl.frames[r.segment]
        if not names or not frames:
            return {}
        snap = self.ctrl.ecu_data if self.ctrl.state == DONE else None
        if snap is None and self.ctrl.ecu is not None:
            snap = self.ctrl.ecu.snapshot(frames[0].t - 1.0, frames[-1].t + 1.0)
        if not snap:
            return {}
        cols = resample(snap, np.array([f.t for f in frames]))
        return {n: cols[COLUMN_PREFIX + n] for n in names if COLUMN_PREFIX + n in cols}

    def _show_ecu_live(self):
        if self._cursor_inside:
            return
        for name, g in self.ecu_gauges.items():
            g.set(fmt_value(self.page_rusefi.live(name)))

    def _ref_key(self) -> Optional[str]:
        """Lauf fuer die Pmax/Mmax-Anzeige: laufende/letzte Messung, sonst neuester sichtbarer Lauf."""
        if LIVE_KEY in self.plot.data:
            return LIVE_KEY
        for row in self.db.runs():                 # neueste zuerst
            if row.sichtbar and str(row.id) in self.plot.data:
                return str(row.id)
        return None

    def _update_max_display(self):
        key = self._ref_key()
        d = self.plot.data.get(key) if key else None
        if self._cursor_inside and d is not None:
            vals = self._cursor_values(key, self._cursor_n)
            self._show_gauge_texts(vals or {k: "–" for k in ("n", "ign", "v", "egt1", "egt2", "lam", "rate",
                                                             "ratio", "ratio_ecu")})
            for gkey, (_name, g) in self.gauges.items():
                g.set_highlight(True)
            v = self.plot.values_at(key, self._cursor_n) or {}
            for name, g in self.ecu_gauges.items():
                g.set_highlight(True)
                g.set(fmt_value(v.get(EXTRA_PREFIX + name)))
            v = self.plot.values_at(key, self._cursor_n)
            n = f"{self._cursor_n:,.0f}".replace(",", ".")
            self.g_pmax.set_title(f"PS @ {n}")
            self.g_mmax.set_title(f"Nm @ {n}")
            self.g_pmax.set("–" if v is None else f"{v['ps']:.1f}")
            self.g_mmax.set("–" if v is None else f"{v['nm']:.1f}")
            return
        if d is None or not len(d["n"]) or not np.isfinite(d["ps"]).any():
            self.g_pmax.set_title("Pmax [PS]")
            self.g_mmax.set_title("Mmax [Nm]")
            self.g_pmax.set("–")
            self.g_mmax.set("–")
            return
        jp = int(np.nanargmax(d["ps"]))
        jm = int(np.nanargmax(d["nm"]))
        self.g_pmax.set_title(f"Pmax @ {d['n'][jp]:,.0f}".replace(",", "."))
        self.g_mmax.set_title(f"Mmax @ {d['n'][jm]:,.0f}".replace(",", "."))
        self.g_pmax.set(f"{d['ps'][jp]:.1f}")
        self.g_mmax.set(f"{d['nm'][jm]:.1f}")

    def _cursor_left(self):
        self._cursor_inside = False
        self._update_max_display()
        for g in [g for _n, g in self.gauges.values()] + list(self.ecu_gauges.values()):
            g.set_highlight(False)
        self._show_gauge_texts(self._live_vals)
        self._show_ecu_live()

    # ---- Anzeigen: Texte aus Werten, live oder am Cursor
    def _gauge_texts(self, n_calc, n_meas, v_kmh, egt1, egt2, afr, rate, ratio=None, ratio_ecu=None) -> Dict[str, str]:
        lam_mode = self.page_settings.lambda_radio.isChecked()
        fin = lambda x: x is not None and np.isfinite(x)
        return {
            "n": f"{n_calc:,.0f}".replace(",", ".") if fin(n_calc) else "–",
            "ign": f"{n_meas:,.0f}".replace(",", ".") if fin(n_meas) else "–",
            "v": f"{v_kmh:.1f}" if fin(v_kmh) else "–",
            "egt1": f"{egt1:.0f}" if fin(egt1) and egt1 > 0 else "–",
            "egt2": f"{egt2:.0f}" if fin(egt2) and egt2 > 0 else "–",
            "lam": "–" if not fin(afr) or afr <= 0.5 else (f"{afr / 14.7:.3f}" if lam_mode else f"{afr:.2f}"),
            "rate": f"{rate:.2f}" if fin(rate) and rate > 0 else "–",
            "ratio": f"{ratio:.3f}" if fin(ratio) and ratio > 0 else "–",
            "ratio_ecu": f"{ratio_ecu:.3f}" if fin(ratio_ecu) and ratio_ecu > 0 else "–",
        }

    def _show_gauge_texts(self, vals: Dict[str, str]):
        for key, text in (vals or {}).items():
            if key in self.gauges:
                self.gauges[key][1].set(text)

    def _cursor_values(self, key: str, n_rpm: float) -> Optional[Dict[str, str]]:
        """Alle Anzeigewerte des Laufs `key` an der Cursor-Drehzahl."""
        d = self.plot.data.get(key)
        if d is None or not len(d["n"]) or n_rpm < np.nanmin(d["n"]) or n_rpm > np.nanmax(d["n"]):
            return None
        j = int(np.nanargmin(np.abs(d["n"] - n_rpm)))
        n_calc = float(d["n"][j])
        if key == LIVE_KEY:
            r = self.ctrl.result if (self.ctrl.state == DONE and self.ctrl.result) else self.ctrl.live_result
            if r is None:
                return None
            idx = r.start_index + j
            if idx >= len(self.ctrl.frames):
                return None
            f, p = self.ctrl.frames[idx], self.ctrl.params
            n_roll = f.roll_hz * 60.0 / p.inkr
            n_ign = f.ign_hz * 60.0 / p.imp
            rpm = self.plot.data[key].get(EXTRA_PREFIX + "RPMValue")
            ecu_rpm = float(rpm[j]) if rpm is not None and j < len(rpm) else None
            return self._gauge_texts(n_calc, n_ign, n_roll / 60.0 * p.roll_circ * 3.6,
                                     f.egt1, f.egt2, f.afr, f.rate, n_ign / n_roll if n_roll > 1 else None,
                                     ecu_rpm / n_roll if ecu_rpm is not None and n_roll > 1 else None)
        e = self._entry(int(key))
        if e is None or e[1] is None:
            return None
        _run, res, ch, _p = e
        idx = res.start_index + j
        at = lambda name: float(ch[name].values[idx]) if name in ch and idx < len(ch[name].values) else None
        return self._gauge_texts(n_calc, at("Drehzahl Zündung"), at("Geschwindigkeit"), at("EGT 1"), at("EGT 2"),
                                 at("AFR"), at("Messfrequenz"), at("Übersetzung gefiltert"),
                                 at("Übersetzung ECU/Rolle gefiltert"))

    def _lambda_mode_changed(self, lam: bool):
        self.plot.set_lambda_mode(lam)
        self.g_lam.set_title("Lambda [λ]" if lam else "AFR")
        self._redraw_runs()

    def _update_buttons(self):
        busy = self.ctrl is not None and self.ctrl.state in (WAIT, RUN)
        connected = self.link is not None
        self.start_btn.setEnabled(connected and not busy)
        self.abort_btn.setEnabled(busy)
        s = self.page_settings
        s.climate_btn.setEnabled(connected and not busy)
        s.ratio_btn.setEnabled(connected and not busy)
        self.b_ratio.setEnabled(connected and not busy)
        s.connect_btn.setText("Trennen" if connected else "Verbinden")
        self.connect_main.setText("Trennen" if connected else "Verbinden")
        self.connect_main.setToolTip(f"Messboard: {self.link.port}" if connected else
                                     f"Messboard verbinden ({s.port_combo.currentText()})")
        self.connect_main.setEnabled(not busy)
        for sp in s.fields.values():
            sp.setEnabled(not busy)
        self.vehicle_combo.setEnabled(not busy)
        self.setup_combo.setEnabled(not busy)

    def _persist(self):
        s = self.page_settings
        port = s.port_combo.currentText()
        self.settings.update({
            "port": port if not port.startswith(SIM_PORT + ":") or port == DEFAULT_SIM_PORT
            else self.settings.get("port", ""),
            "params": asdict(s.params()), "autosave": self.autosave.isChecked(),
            "filter_seconds": s.filter_seconds, "ma_s": s.fields["ma_s"].value(), "dq_s": s.fields["dq_s"].value(),
            "climate_auto": s.climate_auto.isChecked(), "live": s.live_check.isChecked(),
            "lambda": s.lambda_radio.isChecked(), "vehicle_id": self.vehicle_combo.currentData(),
            "auto_connect": s.auto_connect.isChecked(),
            "setup_id": self.setup_combo.currentData(), "run_filter": self.run_filter.currentIndex(),
        })
        save_settings(self.settings)

    # ------------------------------------------------------------------ Fahrzeug/Setup-Auswahl
    def _reload_vehicle_combo(self, vid=None, sid=None):
        self.vehicle_combo.blockSignals(True)
        self.vehicle_combo.clear()
        self.vehicle_combo.addItem("— kein Fahrzeug —", None)
        for r in self.db.vehicles():
            self.vehicle_combo.addItem(r["name"], r["id"])
        i = self.vehicle_combo.findData(vid)
        self.vehicle_combo.setCurrentIndex(max(0, i))
        self.vehicle_combo.blockSignals(False)
        self._reload_setup_combo(sid)

    def _reload_setup_combo(self, sid=None):
        self.setup_combo.blockSignals(True)
        self.setup_combo.clear()
        self.setup_combo.addItem("— kein Setup —", None)
        vid = self.vehicle_combo.currentData()
        if vid:
            for r in self.db.setups(vid):
                self.setup_combo.addItem(r["name"] or "(ohne Namen)", r["id"])
        i = self.setup_combo.findData(sid)
        self.setup_combo.setCurrentIndex(max(0, i))
        self.setup_combo.blockSignals(False)

    def _vehicle_combo_changed(self, _i):
        self._reload_setup_combo()
        if self.run_filter.currentIndex():
            self._reload_runs()

    def _db_changed(self):
        """Fahrzeug/Setup irgendwo bearbeitet -> alle Ansichten aktualisieren."""
        self._reload_vehicle_combo(self.vehicle_combo.currentData(), self.setup_combo.currentData())
        self.page_db.reload()
        self._reload_runs()

    def _use_setup(self, vid: int, sid: int):
        self._reload_vehicle_combo(vid, sid)
        self.tabs.setCurrentIndex(0)
        self._persist()

    # ------------------------------------------------------------------ Laufdaten
    def _entry(self, rid: int):
        if rid in self.cache:
            return self.cache[rid]
        row = self.db.run(rid)
        if row is None:
            return None
        try:
            run = storage.load_any(row.path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Lauf laden", f"{row.path}:\n{exc}")
            return None
        p = physics.DynoParams(**row.params) if row.params else run["params"]
        res = physics.evaluate(run["n_roll"], run["dt"], p, run["n_meas"], run["afr"], run["egt"],
                               require_end=False)
        ch = run_channels(run, res, p)
        self.cache[rid] = (run, res, ch, p)
        return self.cache[rid]

    def _viewer_channels(self, rid):
        """(Kanaele, Kurvenbereich) fuer den Auswerter"""
        e = self._entry(rid)
        if e is None:
            return None
        _run, res, ch, _p = e
        return ch, (res.segment if res is not None else None)

    def _run_label(self, row) -> str:
        d = row.datum.replace("T", " ")[:16]
        if len(d) == 16 and d[4] == "-":                   # 2026-09-30 12:53 -> 30.09.26 12:53
            d = f"{d[8:10]}.{d[5:7]}.{d[2:4]} {d[11:]}"
        return f"{d}  {row.name}" if row.quelle == "LabVIEW" else d

    def _show_cursor_table(self, on: bool):
        self.cursor_table.setVisible(on)
        if hasattr(self, "cursor_cols_btn"):
            self.cursor_cols_btn.setVisible(on)
        self.cursor_toggle.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
        self.cursor_toggle.setText("Werte am Cursor" + ("" if on else "  (ausgeblendet – klicken zum Einblenden)"))
        if self.settings.get("cursor_table", True) != on:
            self.settings["cursor_table"] = on
            save_settings(self.settings)

    def _fit_cursor_table(self):
        """Cursor-Tabelle genau so hoch wie ihr Inhalt (mind. eine Zeile), Rest bekommt das Diagramm."""
        t = self.cursor_table
        rows = max(1, t.rowCount())
        t.setFixedHeight(t.horizontalHeader().height() + rows * t.verticalHeader().defaultSectionSize()
                         + 2 * t.frameWidth() + 1)

    def _reload_runs(self):
        f = self.run_filter.currentIndex()
        vid = self.vehicle_combo.currentData() if f == 1 else None
        sid = self.setup_combo.currentData() if f == 2 else None
        if f == 0:
            rows = self.db.runs()
        elif vid or sid:
            rows = self.db.runs(vehicle_id=vid, setup_id=sid)
        else:
            rows = []
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        self.run_tree.blockSignals(True)
        self.run_tree.clear()
        for row in rows:
            it = QtWidgets.QTreeWidgetItem([
                self._run_label(row), f"{row.p_max:.1f}", f"{row.n_pmax:.0f}", f"{row.m_max:.1f}",
                f"{row.rate:.0f}", " ".join(x for x in (vehicles.get(row.vehicle_id, ""), row.notiz) if x)])
            it.setData(0, QtCore.Qt.UserRole, row.id)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(0, QtCore.Qt.Checked if row.sichtbar else QtCore.Qt.Unchecked)
            it.setIcon(0, color_icon(self._run_color(row)))
            it.setToolTip(0, f"{self._run_label(row)}\n{row.quelle}: {row.path}")
            self.run_tree.addTopLevelItem(it)
        self.run_tree.blockSignals(False)
        # Spaltenbreiten automatisch – ausser der Benutzer hat seine gespeichert (beim Beenden).
        # Spalte "Lauf" hoechstens so breit wie Datum + Uhrzeit (LabVIEW-Namen abgeschnitten, voll im Tooltip)
        if not (self.settings.get("fenster") or {}).get("laufliste_spalten"):
            for c in range(1, 5):
                self.run_tree.resizeColumnToContents(c)
            self.run_tree.setColumnWidth(0, min(self.run_tree.sizeHintForColumn(0),
                                                self.run_tree.fontMetrics().horizontalAdvance("00.00.00 00:00") + 64))
        self._redraw_runs()
        self.viewer.reload()

    def _redraw_runs(self):
        for key in list(self.plot.items):
            if key != LIVE_KEY:
                self.plot.remove(key)
        for row in self.db.runs():
            if not row.sichtbar:
                continue
            e = self._entry(row.id)
            if e is None or e[1] is None:
                continue
            _run, res, ch, _p = e
            seg = res.segment
            egt2 = ch["EGT 2"].values[seg] if "EGT 2" in ch else None
            self.plot.set_curves(str(row.id), res.n, res.ps, res.nm, lambda_from_afr(res.afr), res.egt, egt2,
                                 color=self._run_color(row), width=2.0, rescale=False,
                                 extra=self._ecu_extra_run(_run, res))
        if not self.plot.frozen and self.plot.data:
            self.plot.auto_scale()
        self._cursor_moved(self._cursor_n)
        self._update_max_display()

    def _run_item_changed(self, it, col):
        if col == 0:
            self.db.update_run(it.data(0, QtCore.Qt.UserRole), sichtbar=it.checkState(0) == QtCore.Qt.Checked)
            self._redraw_runs()

    def _current_run_id(self) -> Optional[int]:
        it = self.run_tree.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def _open_in_viewer(self, rid):
        if rid is None:
            return
        self.tabs.setCurrentWidget(self.viewer)
        self.viewer.show_run(rid)

    def _run_context_menu(self, pos):
        it = self.run_tree.itemAt(pos)
        if it is None:
            return
        rid = it.data(0, QtCore.Qt.UserRole)
        m = QtWidgets.QMenu(self)
        a_view = m.addAction("In Auswertung öffnen")
        a_note = m.addAction("Notiz bearbeiten …")
        a_color = m.addAction("Farbe ändern …")
        a_assign = m.addAction("Aktuellem Fahrzeug/Setup zuordnen")
        a_params = m.addAction("Mit aktuellen Einstellungen neu rechnen")
        m.addSeparator()
        a_csv = m.addAction("CSV-Export …")
        a_del = m.addAction("Löschen …")
        act = m.exec(self.run_tree.viewport().mapToGlobal(pos))
        row = self.db.run(rid)
        if act == a_view:
            self._open_in_viewer(rid)
        elif act == a_note:
            text, ok = QtWidgets.QInputDialog.getText(self, "Notiz", "Notiz zum Lauf:", text=row.notiz)
            if ok:
                self.db.update_run(rid, notiz=text)
                self._reload_runs()
        elif act == a_color:
            c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._run_color(row)), self)
            if c.isValid():
                self.db.update_run(rid, farbe=c.name())
                self._reload_runs()
        elif act == a_assign:
            self.db.update_run(rid, vehicle_id=self.vehicle_combo.currentData(),
                               setup_id=self.setup_combo.currentData())
            self._reload_runs()
        elif act == a_params:
            self._recalc_run(rid)
        elif act == a_csv:
            self.on_csv(rid)
        elif act == a_del:
            self.on_delete()

    def _recalc_run(self, rid: int):
        e = self._entry(rid)
        p = self.page_settings.params()
        if e:
            p = physics.scale_filters(p, self._current_rate(), physics.sample_rate(e[0]["dt"]))   # nur Messpunkt-Modus
            p.temp_c, p.p_mbar = e[3].temp_c, e[3].p_mbar          # Klima des Laufs behalten
        self.db.update_run(rid, params=asdict(p))
        self.cache.pop(rid, None)
        self.viewer.invalidate(rid)
        e = self._entry(rid)
        if e and e[1]:
            r = e[1]
            self.db.update_run(rid, p_max=r.p_max, n_pmax=r.n_pmax, m_max=r.m_max, n_mmax=r.n_mmax, ka=r.ka)
        self._reload_runs()

    # ------------------------------------------------------------------ Cursor
    def _cursor_moved(self, n_rpm: float):
        self._cursor_n = n_rpm
        self._cursor_inside = self.plot._cursor_inside if hasattr(self.plot, "_cursor_inside") else False
        self._update_max_display()
        names = {str(r.id): (self._run_label(r), self._run_color(r)) for r in self.db.runs() if r.sichtbar}
        names[LIVE_KEY] = ("laufende Messung", "#000000")
        lam_mode = self.page_settings.lambda_radio.isChecked()
        # Spalten: n, PS, Nm, dann die gewaehlten (Menue "Spalten …" an der Cursor-Tabelle)
        known = {k for k, _l, _u in self._lower_candidates()}
        cols = [k for k in self._cursor_cols() if k in known]

        def title(k):
            label, unit = self.plot.lower_title(k) if not k.startswith(EXTRA_PREFIX) or k in self.plot.lower_labels \
                else next(((lab + " (ECU)", u) for n, lab, u in self.plot.overlays if EXTRA_PREFIX + n == k), (k, ""))
            return f"{label} [{unit}]" if unit and unit != "λ" else ("λ" if unit == "λ" else label)

        def fmt(k, x):
            if x is None or not np.isfinite(x):
                return ""
            if k == "lam":
                return "" if x <= 0 else (f"{x:.3f}" if lam_mode else f"{x * 14.7:.2f}")
            if k in ("egt1", "egt2"):
                return f"{x:.0f}" if x > 0 else ""
            return fmt_value(x)

        rows = []
        for key in self.plot.data:
            v = self.plot.values_at(key, n_rpm)
            label, color = names.get(key, (key, "#000"))
            if v is None:
                rows.append((color, label, "", "", "") + ("",) * len(cols))
                continue
            rows.append((color, label, f"{v['n']:.0f}", f"{v['ps']:.2f}", f"{v['nm']:.2f}")
                        + tuple(fmt(k, v.get(k)) for k in cols))
        t = self.cursor_table
        if t.columnCount() != 5 + len(cols):
            t.setColumnCount(5 + len(cols))
        for i, text in enumerate(["", "Lauf", "n [1/min]", "PS", "Nm"] + [title(k) for k in cols]):
            t.setHorizontalHeaderItem(i, QtWidgets.QTableWidgetItem(text))
        t.setRowCount(len(rows))
        self._fit_cursor_table()
        for r, vals in enumerate(rows):
            it = QtWidgets.QTableWidgetItem()
            it.setBackground(QtGui.QColor(vals[0]))
            t.setItem(r, 0, it)
            for c, text in enumerate(vals[1:], start=1):
                cell = QtWidgets.QTableWidgetItem(text)
                if c >= 2:
                    cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                t.setItem(r, c, cell)

    # ------------------------------------------------------------------ Verbindung
    def on_connect(self, port: str = "", quiet: bool = False, lost: bool = False) -> bool:
        """Verbinden/Trennen. port: statt der Auswahl in den Einstellungen; quiet: ohne Fehlermeldung
        (automatisches Verbinden); lost: Messboard wurde abgezogen (danach wieder automatisch verbinden)."""
        s = self.page_settings
        if self.link:
            if self.ctrl and self.ctrl.state in (WAIT, RUN):
                self.ctrl.abort()
            try:
                self.link.close()
            except Exception:
                pass
            self.link = self.ctrl = None
            self._manual_off = not lost                    # von Hand getrennt: nicht sofort wieder verbinden
            s.fw_label.setText("nicht verbunden")
            self._set_status("Messboard getrennt (abgezogen?)" if lost else "Nicht verbunden – „Verbinden“ drücken",
                             "#f4b6b6" if lost else "#dddddd")
            self._update_buttons()
            return False
        self._manual_off = False
        port = port or s.port_combo.currentText().strip()
        if port != s.port_combo.currentText().strip():
            s.port_combo.setEditText(port)
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.link = DynoLink(port)
            self.firmware = self.link.info()
        except Exception as exc:
            if self.link:
                try:
                    self.link.close()
                except Exception:
                    pass
            self.link = None
            if not quiet:
                QtWidgets.QMessageBox.critical(self, "Verbindung", f"{port} lässt sich nicht öffnen:\n{exc}")
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if self.link.replay is not None:
            p = physics.DynoParams(**{**self.link.replay.params.__dict__, "n_stop": 0.0})
            s.set_params(p)
            s.climate_auto.setChecked(True)
            self.result_label.setText(f"Simulator spielt {self.link.replay.run['name']} ab – "
                                      f"Parameter aus der Datei übernommen (n Stop aus). START drücken.")
        first = self.firmware.splitlines()[0] if self.firmware else "keine Antwort auf 'v' (Arduino 1.x?)"
        kind = "STM32-Firmware" if self.link.is_stm else "Arduino-Mega-Sketch"
        s.fw_label.setText(f"{port}: {kind} – {first}")
        s.fw_label.setToolTip(self.firmware)
        self.ctrl = RunController(self.link, s.params(), auto_climate=s.climate_auto.isChecked(),
                                  ecu=self.page_rusefi.active_link())
        self._set_status("Verbunden – START drücken (F1)", "#dddddd")
        if s.live_check.isChecked():
            self.link.start()
        self._persist()
        self._update_buttons()
        s.update_filter_label(self._current_rate())
        return True

    def _auto_connect(self):
        """Alle 5 s: abgezogenes Messboard erkennen, angestecktes automatisch verbinden."""
        if self.link is not None:
            if not self.link.port.startswith(SIM_PORT) and self.link.port not in {d for d, _ in list_serial_ports()}:
                self.on_connect(lost=True)
            return
        if not self.page_settings.auto_connect.isChecked() or getattr(self, "_manual_off", False):
            return
        present = set(pst_board_ports())
        # abgezogene Ports duerfen beim naechsten Anstecken wieder probiert werden
        self._auto_failed = {p: t for p, t in self._auto_failed.items() if p in present}
        for port in sorted(present):
            if port in self._auto_failed:
                continue
            if not self.on_connect(port, quiet=True):
                self._auto_failed[port] = time.time()
                continue
            if is_pst_firmware(self.firmware):
                self._set_status("Messboard automatisch verbunden – START drücken (F1)", "#dddddd")
                return
            # anderes Geraet (oder alte Firmware ohne Kennung): wieder trennen, bis zum Abziehen in Ruhe lassen
            self.on_connect(lost=True)
            self._set_status("Nicht verbunden – „Verbinden“ drücken", "#dddddd")
            self._auto_failed[port] = time.time()

    def on_replay(self, path: str = ""):
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Lauf abspielen", os.path.expanduser("~"),
                                                            "Läufe (*.xml *.json);;Alle Dateien (*)")
        if not path:
            return
        if self.link:
            self.on_connect()
        self.page_settings.port_combo.setEditText(f"{SIM_PORT}:{path}")
        self.on_connect()
        self.tabs.setCurrentIndex(0)

    def on_climate(self):
        if not self.link:
            return
        c = self.link.climate()
        if c:
            self.page_settings.fields["temp_c"].setValue(c[0])
            self.page_settings.fields["p_mbar"].setValue(c[1])
            self.result_label.setText(f"Klima: {c[0]:.1f} °C   {c[1]:.1f} mbar   {c[2]:.0f} % rel. Feuchte")
        else:
            self.result_label.setText("Keine Klimadaten vom Sensor")
        if self.page_settings.live_check.isChecked():
            self.link.start()

    def on_measure_ratio(self):
        if not self.ctrl or self.ctrl.state in (WAIT, RUN):
            QtWidgets.QMessageBox.information(self, "Übersetzung", "Erst mit dem Prüfstand verbinden "
                                                                   "(Knopf „Verbinden“).")
            return

        def apply(r: float):
            self.page_settings.fields["ratio"].setValue(r)
            self._persist()
            self._set_status(f"Übersetzung = {r:.3f}", "#b8e6b8")

        dlg = RatioDialog(self, self.ctrl, self.page_rusefi, self.page_settings.params, apply,
                          source=self.settings.get("ratio_source", "board"))
        dlg.finished.connect(lambda _r: (self.settings.update(ratio_source=dlg.source()),
                                         save_settings(self.settings)))
        dlg.exec()

    # ------------------------------------------------------------------ Lauf
    def on_start(self):
        if not self.ctrl or self.ctrl.state in (WAIT, RUN):
            return
        s = self.page_settings
        p = s.params()
        msg = physics.check_params(p)
        if msg:
            QtWidgets.QMessageBox.warning(self, "Einstellungen", msg)
            return
        self._persist()
        self.tabs.setCurrentIndex(0)
        self.ctrl.params = p
        self.ctrl.auto_climate = s.climate_auto.isChecked()
        self.ctrl.ecu = self.page_rusefi.active_link()
        self.plot.remove(LIVE_KEY)
        self.plot.freeze(True)                     # Skalierung wie beim vorherigen Lauf
        self.result_label.setText("")
        self.ctrl.start()
        if self.ctrl.climate:
            s.fields["temp_c"].setValue(self.ctrl.params.temp_c)
            s.fields["p_mbar"].setValue(self.ctrl.params.p_mbar)
        self.result_label.setText(self.ctrl.message)
        self._done_handled = False
        self._update_buttons()

    def on_abort(self):
        if self.ctrl and self.ctrl.state in (WAIT, RUN):
            self.ctrl.abort()
            if self.page_settings.live_check.isChecked():
                self.link.start()
        self._update_buttons()

    def _finish_run(self):
        r = self.ctrl.result
        self.plot.freeze(False)
        if r is None:
            self._set_status("Fertig – keine Auswertung möglich", "#f4b6b6")
            return
        self.result_label.setText(r.summary() + f"\nEnde: {r.end_reason}   |   "
                                  f"{len(self.ctrl.frames)} Telegramme, {self.link.lost} verloren")
        self._set_status(f"{r.p_max:.1f} PS bei {r.n_pmax:.0f} 1/min", "#b8e6b8")
        if self.autosave.isChecked():
            self.save_current_run()
        else:
            self._draw_live(final=True)
            self.plot.auto_scale()

    def save_current_run(self) -> Optional[int]:
        if not self.ctrl or not self.ctrl.result:
            return None
        vid, sid = self.vehicle_combo.currentData(), self.setup_combo.currentData()
        vname = self.vehicle_combo.currentText() if vid else ""
        folder = storage.save_run(self.ctrl, vehicle=vname, firmware=self.firmware, base_dir=self.db.runs_dir())
        r = self.ctrl.result
        rid = self.db.add_run(folder, "lauf.json", "PyST", os.path.basename(folder),
                              QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate), r,
                              params=asdict(self.ctrl.params), rate=self._current_rate(), vehicle_id=vid,
                              setup_id=sid, notiz=self.run_note.text(), farbe="", sichtbar=True)
        self.plot.remove(LIVE_KEY)
        self._reload_runs()
        self.plot.auto_scale()
        self.result_label.setText(self.result_label.text() + "   |   gespeichert ✓")
        self.result_label.setToolTip(folder)
        return rid

    def _draw_live(self, final: bool = False):
        r = self.ctrl.result if final else self.ctrl.live_result
        if r is None:
            return
        egt2 = np.array([f.egt2 for f in self.ctrl.frames])[r.segment] if self.ctrl.frames else None
        # live: die letzten dq Punkte sind noch nicht endgueltig (Differenzenquotient braucht Punkte
        # "aus der Zukunft") -> weglassen, statt einen falschen Knick zu zeigen
        k = len(r.n) if final else max(0, len(r.n) - int(r.dq_used or self.ctrl.params.dq))
        if k < 2:
            return
        cut = lambda a: None if a is None else a[:k]
        extra = {n: cut(v) for n, v in self._ecu_extra_live(r).items()}
        self.plot.set_curves(LIVE_KEY, cut(r.n), cut(r.ps), cut(r.nm), cut(lambda_from_afr(r.afr)), cut(r.egt),
                             cut(egt2), color="#000000", width=3.0, rescale=False, extra=extra)
        self._update_max_display()

    # ------------------------------------------------------------------ Datenverwaltung
    def on_import(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Läufe importieren (LabVIEW-XML oder PyST lauf.json)", os.path.expanduser("~"),
            "Läufe (*.xml *.json);;Alle Dateien (*)")
        if not files:
            return
        vid, sid = self.vehicle_combo.currentData(), self.setup_combo.currentData()
        n_ok, errors = 0, []
        for f in files:
            try:
                self.db.import_file(f, vehicle_id=vid, setup_id=sid)
                n_ok += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(f)}: {exc}")
        self._reload_runs()
        msg = f"{n_ok} Lauf/Läufe importiert" + (f" (Fahrzeug: {self.vehicle_combo.currentText()})" if vid else "")
        if errors:
            msg += "\n\nFehler:\n" + "\n".join(errors)
        QtWidgets.QMessageBox.information(self, "Import", msg + "\n\nIn der Liste anhaken zum Anzeigen.")

    def on_hide_all(self):
        for row in self.db.runs():
            if row.sichtbar:
                self.db.update_run(row.id, sichtbar=False)
        self._reload_runs()

    def on_delete(self):
        items = self.run_tree.selectedItems()
        if not items:
            return
        ans = QtWidgets.QMessageBox.question(
            self, "Löschen", f"{len(items)} Lauf/Läufe aus der Datenbank löschen?\n"
                             "Messdateien im PyST-Datenordner werden mitgelöscht.")
        if ans != QtWidgets.QMessageBox.Yes:
            return
        for it in items:
            rid = it.data(0, QtCore.Qt.UserRole)
            self.db.delete_run(rid, delete_files=True)
            self.cache.pop(rid, None)
        self._reload_runs()

    def on_csv(self, rid: Optional[int] = None):
        rid = rid if rid is not None else self._current_run_id()
        if rid is None:
            QtWidgets.QMessageBox.information(self, "CSV-Export", "Bitte einen Lauf in der Liste markieren.")
            return
        e = self._entry(rid)
        if e is None:
            return
        path, flt = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSV-Export", os.path.join(os.path.expanduser("~"), f"{self.db.run(rid).name}.csv"),
            "CSV für Excel deutsch (*.csv);;CSV international, Dezimalpunkt (*.csv)")
        if path:
            report.export_csv(path, e[2], decimal_comma="deutsch" in flt)

    def pdf_rows(self):
        rows = []
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        for row in self.db.runs():
            if not row.sichtbar:
                continue
            setup = self.db.setup(row.setup_id) if row.setup_id else None
            details = ""
            if setup:
                details = ", ".join(f"{field_label(k).split(' [')[0]}: {setup[k]}" for k in SETUP_SUMMARY_KEYS
                                    if setup.get(k))
            e = self._entry(row.id)
            p = e[3] if e else None
            veh = " / ".join(x for x in (vehicles.get(row.vehicle_id, ""), setup["name"] if setup else "") if x)
            rows.append({
                "farbe": self._run_color(row),
                "name": self._run_label(row) + (f"\n{row.notiz}" if row.notiz else ""),
                "fahrzeug": veh + ("\n" + details if details else ""),
                "ergebnis": f"{row.p_max:.1f} PS bei {row.n_pmax:.0f} 1/min\n"
                            f"{row.m_max:.1f} Nm bei {row.n_mmax:.0f} 1/min",
                "klima": "" if p is None else
                f"{p.temp_c:.1f} °C, {p.p_mbar:.0f} mbar\nk = {physics.din70020(p.temp_c, p.p_mbar):.3f}",
                "details": "" if p is None else
                f"i = {p.ratio:.3f}, J = {p.inertia:.2f}\n{physics.filter_text(p, row.rate)}",
            })
        return rows

    def on_pdf(self, path: str = ""):
        rows = self.pdf_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "PDF-Bericht", "Keine Läufe sichtbar – bitte in der Liste anhaken.")
            return
        title = self.vehicle_combo.currentText() if self.vehicle_combo.currentData() else "Leistungsmessung"
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "PDF-Bericht", os.path.join(os.path.expanduser("~"), "Leistungsmessung.pdf"), "PDF (*.pdf)")
            if not path:
                return
            open_after = True
        else:
            open_after = False
        notes = ["Leistung nach DIN 70020 korrigiert.  Oben: ── Leistung [PS], ╌╌ Drehmoment [Nm].  "
                 "Unten: ── λ, ╌╌ EGT 1, ┈┈ EGT 2."]
        report.export_pdf(path, self.plot.render_image(), title, rows, notes)
        if open_after:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ Zyklus
    def _tick(self):
        self._show_ecu_live()
        if not self.ctrl:
            return
        new = self.ctrl.poll()
        lv = self.ctrl.live
        if new:
            f = new[-1]
            ecu_rpm = self.page_rusefi.live("RPMValue")
            self._live_vals = self._gauge_texts(
                lv.n_calc, lv.n_meas, lv.v_kmh, f.egt1, f.egt2, f.afr, lv.rate,
                lv.n_meas / lv.n_roll if lv.n_roll > 1 else None,
                ecu_rpm / lv.n_roll if ecu_rpm is not None and lv.n_roll > 1 else None)
            if not self._cursor_inside:
                self._show_gauge_texts(self._live_vals)
            if abs(lv.rate - self._shown_rate) > 0.5:
                self._shown_rate = lv.rate
                self.page_settings.update_filter_label(lv.rate)
        self.g_lost.set(str(self.link.lost if self.link else 0))

        st = self.ctrl.state
        if st == WAIT:
            self._set_status("GO – Vollgas!", "#7ed67e")
        elif st == RUN:
            if self.ctrl.gas_off():
                self._set_status("GAS WEG!", "#ff5555")
            else:
                self._set_status(f"Messung … {lv.n_calc:,.0f} 1/min".replace(",", "."), "#ffe08a")
        if st in (WAIT, RUN) and time.time() > self._plot_due:
            self._plot_due = time.time() + 0.05
            self._draw_live()
        elif st in (DONE, ABORTED) and not self._done_handled:
            self._done_handled = True
            if st == DONE:
                self._finish_run()
            else:
                self.plot.freeze(False)
                self._set_status(self.ctrl.message or "Abgebrochen", "#f4b6b6")
            self._update_buttons()
        if st in (WAIT, RUN):
            self._update_buttons()

        # Live-Anzeige: Ausgabe nach Autostopp der Firmware wieder anstossen
        if (self.link and self.page_settings.live_check.isChecked() and st in (IDLE, DONE, ABORTED)
                and not self.link.streaming() and time.time() - self.last_keepalive > 2.0):
            self.last_keepalive = time.time()
            self.link.start()

    # ------------------------------------------------------------------ Fenster-Layout
    def save_window_layout(self):
        """Fenster, Aufteilungen, Spaltenbreiten und Reiter (beim Beenden)."""
        b64 = lambda ba: bytes(ba.toBase64()).decode("ascii")
        self.settings["fenster"] = {
            "geometrie": b64(self.saveGeometry()),
            "messen_teilung": b64(self.measure_split.saveState()),
            "auswertung_teilung": b64(self.viewer.split.saveState()),
            "laufliste_spalten": [self.run_tree.columnWidth(c) for c in range(self.run_tree.columnCount())],
            "reiter": self.tabs.currentIndex(),
        }

    def restore_window_layout(self) -> bool:
        """True, wenn Fenstergroesse/-position wiederhergestellt wurde."""
        f = self.settings.get("fenster") or {}
        ba = lambda text: QtCore.QByteArray.fromBase64(text.encode("ascii"))
        try:
            if f.get("messen_teilung"):
                self.measure_split.restoreState(ba(f["messen_teilung"]))
            if f.get("auswertung_teilung"):
                self.viewer.split.restoreState(ba(f["auswertung_teilung"]))
            for c, wd in enumerate(f.get("laufliste_spalten", [])[:self.run_tree.columnCount()]):
                if wd > 0:
                    self.run_tree.setColumnWidth(c, wd)
            if 0 <= f.get("reiter", -1) < self.tabs.count():
                self.tabs.setCurrentIndex(f["reiter"])
            return bool(f.get("geometrie")) and self.restoreGeometry(ba(f["geometrie"]))
        except (TypeError, ValueError, AttributeError):
            return False

    def closeEvent(self, ev):
        self.save_window_layout()
        self._persist()
        if self.link:
            self.link.close()
        self.page_rusefi.close_link()
        super().closeEvent(ev)


DESIGN_W, DESIGN_H = 1480, 880          # Groesse, fuer die das Layout gedacht ist (logische Punkte)


def _apply_ui_scale(app: QtWidgets.QApplication) -> bool:
    """Skalierung aus Einstellung/Bildschirmgroesse; True = Prozess wurde mit QT_SCALE_FACTOR neu gestartet."""
    if os.environ.get("SIMPLEDYNO_SCALED"):
        return False
    mode = str(load_settings().get("ui_scale", "auto"))
    if mode == "auto":
        g = app.primaryScreen().availableGeometry()
        factor = min(1.0, g.width() / DESIGN_W, g.height() / DESIGN_H)
    else:
        try:
            factor = float(mode)
        except ValueError:
            factor = 1.0
    factor = max(0.6, min(1.5, factor))
    if abs(factor - 1.0) < 0.02:
        return False
    os.environ["QT_SCALE_FACTOR"] = f"{factor:.2f}"
    os.environ["SIMPLEDYNO_SCALED"] = "1"
    os.execv(sys.executable, [sys.executable, "-m", "pyst"] + sys.argv[1:])
    return True


def _fit_window(win: QtWidgets.QMainWindow, app: QtWidgets.QApplication):
    if win.restore_window_layout():          # Groesse/Position vom letzten Mal
        win.show()
        return
    g = app.primaryScreen().availableGeometry()
    if g.width() < DESIGN_W + 20 or g.height() < DESIGN_H + 20:
        win.setGeometry(g)
        win.showMaximized()
    else:
        win.resize(DESIGN_W, DESIGN_H)
        win.show()


def main():
    app = QtWidgets.QApplication(sys.argv)
    _apply_ui_scale(app)
    app.setApplicationName("PyST")
    win = MainWindow()
    files = [a for a in sys.argv[1:] if a.lower().endswith((".xml", ".json"))]
    if "--replay" in sys.argv and files:
        win.on_replay(files.pop(0))
    elif "--sim" in sys.argv:
        win.page_settings.port_combo.setEditText(SIM_PORT if "--synth" in sys.argv else DEFAULT_SIM_PORT)
        win.on_connect()
    _fit_window(win, app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
