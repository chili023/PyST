"""
PyST – Oberflaeche (PySide6 + pyqtgraph). Mac, Linux (Raspberry Pi), Windows.

Reiter:  Messen | Fahrzeuge & Setups | Auswertung | Einstellungen
"""
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Dict, Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import physics, report, storage
from .channels import lambda_from_afr, run_channels
from .db import SETUP_SUMMARY_KEYS, VEHICLE_FIELDS, Database, base_dir, field_label
from .dialogs import FieldForm, SetupForm
from .link import DEFAULT_SIM_PORT, SIM_PORT, DynoLink, guess_port, list_serial_ports
from .plots import DynoPlot, run_color
from .runner import ABORTED, DONE, IDLE, RUN, WAIT, RunController
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
        gl.addWidget(self.fw_label, 4, 0, 1, 3)
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
                self.ratio_btn = QtWidgets.QPushButton("Übersetzung aus Zündung messen (5 s)")
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
        self.setWindowTitle("PyST – WildBugChilGru")
        self.resize(1500, 920)
        self.settings = load_settings()
        self.db = db or Database()
        self.link: Optional[DynoLink] = None
        self.ctrl: Optional[RunController] = None
        self.firmware = ""
        self.cache: Dict[int, tuple] = {}          # run_id -> (run, result, channels, params)
        self.ratio_samples = None
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
        self.tabs.addTab(self._build_measure(), "Messen")
        self.tabs.addTab(self.page_db, "Fahrzeuge && Setups")
        self.tabs.addTab(self.viewer, "Auswertung")
        self.tabs.addTab(self.page_settings, "Einstellungen")
        self.page_settings.data_label.setText(self.db.path)
        self._build_menu()
        self._wire()
        self._refresh_ports()
        self._reload_vehicle_combo(self.settings.get("vehicle_id"), self.settings.get("setup_id"))
        self._reload_runs()
        self.page_settings.update_filter_label(60.0)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)
        self._update_buttons()
        QtGui.QShortcut(QtGui.QKeySequence("F1"), self, activated=self.on_start)
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.on_abort)

    # ------------------------------------------------------------------ Aufbau Messen
    def _build_measure(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        top = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel()
        self.status.setAlignment(QtCore.Qt.AlignCenter)
        self.status.setMinimumHeight(40)
        top.addWidget(self.status, 1)
        self.start_btn = QtWidgets.QPushButton("START  (F1)")
        self.abort_btn = QtWidgets.QPushButton("Abbrechen  (Esc)")
        for b in (self.start_btn, self.abort_btn):
            b.setMinimumHeight(40)
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
        self.g_tps = Gauge("TPS (rusEFI)", "%")
        self.g_rate = Gauge("Messfrequenz", "Hz")
        self.g_lost = Gauge("COM verloren", "")
        # Auswaehlbare Anzeigen: Schluessel -> (Name im Menue, Widget)
        self.gauges = {
            "pmax": ("Pmax / Leistung am Cursor", self.g_pmax), "mmax": ("Mmax / Drehmoment am Cursor", self.g_mmax),
            "n": ("Motor (Rolle × i)", self.g_n), "ign": ("Motor (Zündung)", self.g_ign), "v": ("Rolle km/h", self.g_v),
            "egt1": ("EGT 1", self.g_egt1), "egt2": ("EGT 2", self.g_egt2), "lam": ("Lambda / AFR", self.g_lam),
            "tps": ("TPS (rusEFI)", self.g_tps), "rate": ("Messfrequenz", self.g_rate), "lost": ("COM verloren", self.g_lost),
        }
        hidden = set(self.settings.get("gauges_hidden", ["egt2", "tps"]))
        for key, (_name, g) in self.gauges.items():
            gauges.addWidget(g)
            g.setVisible(key not in hidden)
        self.gauge_btn = QtWidgets.QToolButton()
        self.gauge_btn.setText("Anzeigen …")
        self.gauge_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self.gauge_btn)
        for key, (name, g) in self.gauges.items():
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(key not in hidden)
            act.toggled.connect(lambda on, k=key: self._toggle_gauge(k, on))
        self.gauge_btn.setMenu(menu)
        gauges.addWidget(self.gauge_btn)
        lay.addLayout(gauges)

        split = QtWidgets.QSplitter()
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.plot = DynoPlot()
        ll.addLayout(self._build_limits())
        ll.addWidget(self.plot, 1)
        legend = QtWidgets.QLabel("oben: ── Leistung [PS]   ╌╌ Drehmoment [Nm]      "
                                  "unten: ── Lambda   ╌╌ EGT 1   ┈┈ EGT 2      "
                                  "Farbe = Lauf (Liste rechts), schwarz = laufende Messung")
        legend.setStyleSheet("color: #555;")
        ll.addWidget(legend)
        self.cursor_table = QtWidgets.QTableWidget(0, 8)
        self.cursor_table.setHorizontalHeaderLabels(["", "Lauf", "n [1/min]", "PS", "Nm", "λ", "EGT 1", "EGT 2"])
        self.cursor_table.verticalHeader().setVisible(False)
        self.cursor_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.cursor_table.setColumnWidth(0, 18)
        self.cursor_table.setMaximumHeight(100)
        self.cursor_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        ll.addWidget(self.cursor_table)
        self.result_label = QtWidgets.QLabel("")
        self.result_label.setStyleSheet("font-size: 13px;")
        self.result_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
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
        rl.addWidget(self.run_tree, 1)
        grid = QtWidgets.QGridLayout()
        self.b_import = QtWidgets.QPushButton("Importieren …")
        self.b_none = QtWidgets.QPushButton("Alle ausblenden")
        self.b_view = QtWidgets.QPushButton("In Auswertung")
        self.b_pdf = QtWidgets.QPushButton("PDF-Bericht …")
        self.b_csv = QtWidgets.QPushButton("CSV-Export …")
        self.b_del = QtWidgets.QPushButton("Löschen")
        for i, b in enumerate((self.b_import, self.b_none, self.b_view, self.b_pdf, self.b_csv, self.b_del)):
            grid.addWidget(b, i // 2, i % 2)
        rl.addLayout(grid)
        right.setMinimumWidth(300)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        lay.addWidget(split, 1)
        self._set_status("Nicht verbunden – Einstellungen → Verbinden", "#dddddd")
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

    def _build_limits(self) -> QtWidgets.QHBoxLayout:
        """Zeile ueber dem Diagramm: feste Achsgrenzen (auto = aus den Kurven)."""
        hb = QtWidgets.QHBoxLayout()
        hb.setContentsMargins(4, 0, 4, 0)
        saved = self.settings.get("plot_limits", {})
        self.limit_fields: Dict[str, QtWidgets.QSpinBox] = {}
        for label, lo_key, hi_key, top, step in (("Drehzahl", "n_lo", "n_hi", 30000, 500),
                                                 ("Leistung PS", "ps_lo", "ps_hi", 2000, 5),
                                                 ("Drehmoment Nm", "nm_lo", "nm_hi", 5000, 5)):
            hb.addWidget(QtWidgets.QLabel(label))
            for key, pre in ((lo_key, "von "), (hi_key, "bis ")):
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, top)
                sp.setSingleStep(step)
                sp.setPrefix(pre)
                sp.setSpecialValueText(pre + "auto")
                sp.setValue(int(saved.get(key, 0) or 0))
                sp.setKeyboardTracking(False)
                sp.setToolTip("0 = automatisch aus den Kurven")
                sp.valueChanged.connect(self._limits_changed)
                self.limit_fields[key] = sp
                hb.addWidget(sp)
            hb.addSpacing(12)
        b = QtWidgets.QPushButton("Achsen auto")
        b.setToolTip("Alle Grenzen zurück auf automatisch")
        b.clicked.connect(self._limits_reset)
        hb.addWidget(b)
        hb.addStretch(1)
        self.plot.set_limits({k: sp.value() for k, sp in self.limit_fields.items()})
        return hb

    def _limits_changed(self, _=None):
        lim = {k: sp.value() for k, sp in self.limit_fields.items()}
        self.plot.set_limits(lim)
        self.settings["plot_limits"] = lim
        save_settings(self.settings)

    def _limits_reset(self):
        for sp in self.limit_fields.values():
            sp.blockSignals(True)
            sp.setValue(0)
            sp.blockSignals(False)
        self._limits_changed()

    def _wire(self):
        s = self.page_settings
        s.refresh_btn.clicked.connect(self._refresh_ports)
        s.connect_btn.clicked.connect(self.on_connect)
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
        self.b_import.clicked.connect(self.on_import)
        self.b_none.clicked.connect(self.on_hide_all)
        self.b_view.clicked.connect(lambda: self._open_in_viewer(self._current_run_id()))
        self.b_pdf.clicked.connect(self.on_pdf)
        self.b_csv.clicked.connect(lambda: self.on_csv())
        self.b_del.clicked.connect(self.on_delete)
        self.page_db.changed.connect(self._db_changed)
        self.viewer.db_changed.connect(self._db_changed)
        self.page_db.use_setup.connect(self._use_setup)
        self._lambda_mode_changed(s.lambda_radio.isChecked())

    # ------------------------------------------------------------------ Hilfen
    def _set_status(self, text: str, color: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"background: {color}; font-size: 20px; font-weight: 700; "
                                  f"border-radius: 6px; padding: 4px;")

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
        hidden = set(self.settings.get("gauges_hidden", ["egt2", "tps"]))
        (hidden.discard if on else hidden.add)(key)
        self.settings["gauges_hidden"] = sorted(hidden)
        save_settings(self.settings)

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
            self._show_gauge_texts(vals or {k: "–" for k in ("n", "ign", "v", "egt1", "egt2", "lam", "tps", "rate")})
            for gkey, (_name, g) in self.gauges.items():
                g.set_highlight(True)
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
        for key, (_name, g) in self.gauges.items():
            g.set_highlight(False)
        self._show_gauge_texts(self._live_vals)

    # ---- Anzeigen: Texte aus Werten, live oder am Cursor
    def _gauge_texts(self, n_calc, n_meas, v_kmh, egt1, egt2, afr, tps, rate) -> Dict[str, str]:
        lam_mode = self.page_settings.lambda_radio.isChecked()
        fin = lambda x: x is not None and np.isfinite(x)
        return {
            "n": f"{n_calc:,.0f}".replace(",", ".") if fin(n_calc) else "–",
            "ign": f"{n_meas:,.0f}".replace(",", ".") if fin(n_meas) else "–",
            "v": f"{v_kmh:.1f}" if fin(v_kmh) else "–",
            "egt1": f"{egt1:.0f}" if fin(egt1) and egt1 > 0 else "–",
            "egt2": f"{egt2:.0f}" if fin(egt2) and egt2 > 0 else "–",
            "lam": "–" if not fin(afr) or afr <= 0.5 else (f"{afr / 14.7:.3f}" if lam_mode else f"{afr:.2f}"),
            "tps": f"{tps:.1f}" if fin(tps) else "–",
            "rate": f"{rate:.2f}" if fin(rate) and rate > 0 else "–",
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
            return self._gauge_texts(n_calc, f.ign_hz * 60.0 / p.imp, n_roll / 60.0 * p.roll_circ * 3.6,
                                     f.egt1, f.egt2, f.afr, None, f.rate)
        e = self._entry(int(key))
        if e is None or e[1] is None:
            return None
        _run, res, ch, _p = e
        idx = res.start_index + j
        at = lambda name: float(ch[name].values[idx]) if name in ch and idx < len(ch[name].values) else None
        return self._gauge_texts(n_calc, at("Drehzahl Zündung"), at("Geschwindigkeit"), at("EGT 1"), at("EGT 2"),
                                 at("AFR"), at("tps") if "tps" in ch else at("TPS"), at("Messfrequenz"))

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
        s.connect_btn.setText("Trennen" if connected else "Verbinden")
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
        return f"{d}  {row.name}" if row.quelle == "LabVIEW" else d

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
            it.setToolTip(0, f"{row.quelle}: {row.path}")
            self.run_tree.addTopLevelItem(it)
        self.run_tree.blockSignals(False)
        for c in range(1, 5):
            self.run_tree.resizeColumnToContents(c)
        self.run_tree.setColumnWidth(0, 190)
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
                                 color=self._run_color(row), width=2.0, rescale=False)
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
        rows = []
        for key in self.plot.data:
            v = self.plot.values_at(key, n_rpm)
            label, color = names.get(key, (key, "#000"))
            if v is None:
                rows.append((color, label, "", "", "", "", "", ""))
                continue
            lam = v.get("lam", float("nan"))
            lam_s = "" if not np.isfinite(lam) else (f"{lam:.3f}" if lam_mode else f"{lam * 14.7:.2f}")

            def egt(k):
                x = v.get(k, float("nan"))
                return f"{x:.0f}" if np.isfinite(x) and x > 0 else ""
            rows.append((color, label, f"{v['n']:.0f}", f"{v['ps']:.2f}", f"{v['nm']:.2f}", lam_s,
                         egt("egt1"), egt("egt2")))
        self.cursor_table.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            it = QtWidgets.QTableWidgetItem()
            it.setBackground(QtGui.QColor(vals[0]))
            self.cursor_table.setItem(r, 0, it)
            for c, text in enumerate(vals[1:], start=1):
                cell = QtWidgets.QTableWidgetItem(text)
                if c >= 2:
                    cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.cursor_table.setItem(r, c, cell)
        self.cursor_table.setHorizontalHeaderItem(5, QtWidgets.QTableWidgetItem("λ" if lam_mode else "AFR"))

    # ------------------------------------------------------------------ Verbindung
    def on_connect(self):
        s = self.page_settings
        if self.link:
            self.link.close()
            self.link = self.ctrl = None
            s.fw_label.setText("nicht verbunden")
            self._set_status("Nicht verbunden – Einstellungen → Verbinden", "#dddddd")
            self._update_buttons()
            return
        port = s.port_combo.currentText().strip()
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.link = DynoLink(port)
            self.firmware = self.link.info()
        except Exception as exc:
            self.link = None
            QtWidgets.QMessageBox.critical(self, "Verbindung", f"{port} lässt sich nicht öffnen:\n{exc}")
            return
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
        self.ctrl = RunController(self.link, s.params(), auto_climate=s.climate_auto.isChecked())
        self._set_status("Verbunden – START drücken (F1)", "#dddddd")
        if s.live_check.isChecked():
            self.link.start()
        self._persist()
        self._update_buttons()
        s.update_filter_label(self._current_rate())

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
        if not self.link:
            return
        if not self.link.streaming():
            self.link.start()
        self.ratio_samples = (time.time() + 5.0, [], [])
        self._set_status("Übersetzung wird gemessen – Drehzahl konstant halten", "#ffe08a")

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
        self.plot.set_curves(LIVE_KEY, cut(r.n), cut(r.ps), cut(r.nm), cut(lambda_from_afr(r.afr)), cut(r.egt),
                             cut(egt2), color="#000000", width=3.0, rescale=False)
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
        report.export_pdf(path, report.render_plot(self.plot.scene()), title, rows, notes)
        if open_after:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ Zyklus
    def _tick(self):
        if not self.ctrl:
            return
        new = self.ctrl.poll()
        lv = self.ctrl.live
        if new:
            f = new[-1]
            self._live_vals = self._gauge_texts(lv.n_calc, lv.n_meas, lv.v_kmh, f.egt1, f.egt2, f.afr, None, lv.rate)
            if not self._cursor_inside:
                self._show_gauge_texts(self._live_vals)
            if abs(lv.rate - self._shown_rate) > 0.5:
                self._shown_rate = lv.rate
                self.page_settings.update_filter_label(lv.rate)
        self.g_lost.set(str(self.link.lost if self.link else 0))

        if self.ratio_samples and new:
            end, nm, nr = self.ratio_samples
            p = self.page_settings.params()
            for f in new:
                nm.append(f.ign_hz * 60.0 / p.imp)
                nr.append(f.roll_hz * 60.0 / p.inkr)
            if time.time() > end:
                r = physics.trimmed_ratio(np.array(nm), np.array(nr))
                self.ratio_samples = None
                if r:
                    self.page_settings.fields["ratio"].setValue(r)
                    self._set_status(f"Übersetzung = {r:.3f}", "#b8e6b8")
                else:
                    self._set_status("Übersetzung: zu wenige Daten (laufen Motor und Rolle?)", "#f4b6b6")

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

    def closeEvent(self, ev):
        self._persist()
        if self.link:
            self.link.close()
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
