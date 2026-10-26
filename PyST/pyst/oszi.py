"""
Reiter „Oszi / Dauermessung“: die Messelektronik als Messwerkzeug (Zündung oder Rolle gestört?).

- Dauermessung unabhaengig vom Leistungslauf: Start/Stopp, unbegrenzt, fortlaufend in Abschnitten auf die
  Platte (recorder.py), Marker mit Text (F2).
- Linienschreiber: frei waehlbare Kanaele live, je Einheit ein Feld, Zeitfenster, Marker als Linien.
- Software-Trigger (alle Geraete, auch alter Mega 3.0.0): Rollensprung, Uebersetzungssprung, Zuendaussetzer,
  Doppelimpuls, eingefrorener Wert, Schwellwert. Modi Auto / Normal / Einzelschuss, Vor- und Nachlauf,
  Ereignisliste (Doppelklick = Stelle anzeigen).
- Flanken-Ansicht (nur Geraete mit PST-Diag 1): Burst anfordern, Logikanalysator-Spuren, Periode je Flanke,
  Pulsbreite (Flanke B), zwei Cursor mit Zeitdifferenz.
- Geraete-Einstellungen (#GET/#SET/#SAVE/#DEFAULTS) ohne Neu-Flashen.
"""
import os
import time
from typing import Callable, Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import diag as dg
from .physics import DynoParams
from .recorder import Recorder

# Kanaele des Linienschreibers: Name -> (Einheit, Farbe)
LIVE_CHANNELS = {
    "Drehzahl Zündung": ("1/min", "#c62828"),
    "Drehzahl Rolle": ("1/min Rolle", "#1f5fbf"),
    "Rolle × Übersetzung": ("1/min", "#2e7d32"),
    "Übersetzung": ("nKW/nR", "#6a1b9a"),
    "Messfrequenz": ("Hz", "#37474f"),
    "AFR": ("AFR", "#ef6c00"),
    "EGT 1": ("°C", "#ad1457"),
    "EGT 2": ("°C", "#4e342e"),
    "Flanken Zündung": ("Flanken", "#c62828"),
    "Flanken Rolle": ("Flanken", "#1f5fbf"),
    "Zündung kürzeste Periode": ("µs Zündung", "#e57373"),
    "Zündung längste Periode": ("µs Zündung", "#8e0000"),
    "Rolle kürzeste Periode": ("µs Rolle", "#64b5f6"),
    "Rolle längste Periode": ("µs Rolle", "#0d3c78"),
    "Zündung verworfen": ("verworfen", "#c62828"),
    "Rolle verworfen": ("verworfen", "#1f5fbf"),
    "Geräte-Flags": ("Flags", "#000000"),
}
DIAG_LIVE = {"Flanken Zündung": "ign_n", "Flanken Rolle": "roll_n", "Zündung kürzeste Periode": "ign_pmin_us",
             "Zündung längste Periode": "ign_pmax_us", "Rolle kürzeste Periode": "roll_pmin_us",
             "Rolle längste Periode": "roll_pmax_us", "Zündung verworfen": "ign_rej", "Rolle verworfen": "roll_rej",
             "Geräte-Flags": "flags"}
DEFAULT_LIVE = ["Drehzahl Zündung", "Rolle × Übersetzung", "Übersetzung"]
WINDOWS = [("10 s", 10.0), ("30 s", 30.0), ("1 min", 60.0), ("5 min", 300.0), ("30 min", 1800.0), ("alles", 0.0)]
KEEP_S = 1800.0                     # so viel bleibt fuer die Anzeige im Speicher (alles steht auf der Platte)
MODES = ["Auto", "Normal", "Einzelschuss"]

# Bekannte Geraete-Einstellungen (PST-Diag 1, §6) -> Erklaerung
SETTING_HELP = {
    "edge_ign": "Flanke Zündung: F fallend, R steigend",
    "edge_roll": "Flanke Rolle: F fallend, R steigend",
    "timeout_ign_ms": "Zündung: so lange ohne Flanke -> 0 und Flag Timeout [ms]",
    "timeout_roll_ms": "Rolle: so lange ohne Flanke -> 0 und Flag Timeout [ms]",
    "pullup": "interne Pull-up-Widerstände an den Eingängen (0/1)",
    "thermo": "Thermoelement vorhanden (0/1) – ändert das normale Telegramm (EGT)",
    "bme_addr": "I²C-Adresse des Klimasensors (hex, 76 oder 77)",
    "ring_ign": "Ringspeicher Zündung: Mittelung über so viele Perioden",
    "ring_roll": "Ringspeicher Rolle: Mittelung über so viele Perioden",
    "ign_lockout_pct": "Doppelimpulsfilter Zündung: Periode kürzer als (100 − x) % der letzten wird verworfen (0 = aus)",
    "ign_min_period_us": "Sperrzeit Zündung: kürzere Perioden werden verworfen [µs]",
    "roll_min_period_us": "Sperrzeit Rolle: kürzere Perioden werden verworfen [µs]",
    "rate_hz": "Telegrammrate [Hz] (Bereich je Gerät, F446 10…100)",
    "egt1": "Thermoelement 1 vorhanden (0/1)",
    "egt2": "Thermoelement 2 vorhanden (0/1)",
    "afr_src": "Quelle AFR (Analogeingang)",
    "hw_filter": "Hardware-Eingangsfilter",
    "roll_lockout_pct": "Störimpulsfilter Rolle: Periode kürzer als (100 − x) % der letzten wird verworfen (0 = aus)",
    "ign_win_lo_pct": "Zündung: Plausibilitätsfenster unten [%]",
    "ign_win_hi_pct": "Zündung: Plausibilitätsfenster oben [%]",
    "ign_min_periods": "Zündung: Mindestanzahl Perioden je Messwert",
    "roll_min_periods": "Rolle: Mindestanzahl Perioden je Messwert",
    "max_avg_span_ms": "längste Mittelungsdauer [ms]",
    "autostop_s": "Autostopp der Ausgabe ohne Signal [s] (nur Modus N)",
    "freq_dec": "Nachkommastellen der Frequenzen im normalen Telegramm",
    "afr_0v": "AFR bei 0 V",
    "afr_fs": "AFR bei Vollausschlag",
    "sim_doppel": "nur Simulator: Doppelimpuls-Störungen je Minute",
    "sim_aussetzer": "nur Simulator: Zündaussetzer je Minute",
    "sim_rollensprung": "nur Simulator: Rollensprünge (Störimpulse) je Minute",
    "sim_wackel": "nur Simulator: Wackelkontakt Rolle je Minute",
}


class Store:
    """Zeitreihen des Linienschreibers (numpy, waechst, alte Daten > KEEP_S werden verworfen)."""

    def __init__(self, names):
        self.names = list(names)
        self.cap = 4096
        self.n = 0
        self.t = np.zeros(self.cap)
        self.v = {k: np.full(self.cap, np.nan) for k in self.names}

    def append(self, t: float, vals: Dict[str, float]):
        if self.n >= self.cap:
            if self.t[self.n - 1] - self.t[0] > KEEP_S:
                cut = int(np.searchsorted(self.t[:self.n], self.t[self.n - 1] - KEEP_S))
                self.t[:self.n - cut] = self.t[cut:self.n]
                for k in self.names:
                    self.v[k][:self.n - cut] = self.v[k][cut:self.n]
                self.n -= cut
            if self.n >= self.cap:
                self.cap *= 2
                self.t = np.resize(self.t, self.cap)
                for k in self.names:
                    a = np.full(self.cap, np.nan)
                    a[:self.n] = self.v[k][:self.n]
                    self.v[k] = a
        self.t[self.n] = t
        for k in self.names:
            self.v[k][self.n] = vals.get(k, np.nan)
        self.n += 1

    def window(self, t0: float, t1: float):
        a = int(np.searchsorted(self.t[:self.n], t0))
        b = int(np.searchsorted(self.t[:self.n], t1, side="right"))
        return a, b


def live_values(f, p: DynoParams) -> Dict[str, float]:
    """Werte eines Telegramms fuer den Linienschreiber und den Software-Trigger."""
    n_roll = f.roll_hz * 60.0 / p.inkr if p.inkr else 0.0
    n_ign = f.ign_hz * 60.0 / p.imp if p.imp else 0.0
    v = {"Drehzahl Zündung": n_ign, "Drehzahl Rolle": n_roll, "Rolle × Übersetzung": n_roll * p.ratio,
         "Übersetzung": n_ign / n_roll if n_roll > 1.0 else np.nan, "Messfrequenz": f.rate,
         "AFR": f.afr if f.afr > 0.5 else np.nan, "EGT 1": f.egt1, "EGT 2": f.egt2,
         "n_roll": n_roll, "n_ign": n_ign, "imp": p.imp}
    if f.diag:
        for name, key in DIAG_LIVE.items():
            v[name] = f.diag.get(key, np.nan)
        v["flags"] = f.diag.get("flags", 0)
        v["ign_pmin_us"] = f.diag.get("ign_pmin_us", 0)
    return v


class DeviceSettingsDialog(QtWidgets.QDialog):
    """Einstellungen der Messelektronik lesen/aendern (#GET/#SET/#SAVE/#DEFAULTS), ohne Neu-Flashen."""

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link
        self.setWindowTitle("Geräte-Einstellungen (PST-Diag 1)")
        self.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(self)
        fw = link.caps.get("fw", "") if link else ""
        lay.addWidget(QtWidgets.QLabel(f"<b>{fw}</b> – Änderungen wirken sofort (flüchtig). "
                                       "„Im Gerät speichern“ legt sie dauerhaft ab."))
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Einstellung", "Wert", "Bedeutung"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bb = QtWidgets.QHBoxLayout()
        self.b_reload = QtWidgets.QPushButton("Neu lesen")
        self.b_apply = QtWidgets.QPushButton("Übernehmen")
        self.b_save = QtWidgets.QPushButton("Im Gerät speichern")
        self.b_def = QtWidgets.QPushButton("Standardwerte")
        self.b_close = QtWidgets.QPushButton("Schließen")
        for b in (self.b_reload, self.b_apply, self.b_save, self.b_def):
            bb.addWidget(b)
        bb.addStretch(1)
        bb.addWidget(self.b_close)
        lay.addLayout(bb)
        self.b_reload.clicked.connect(self.reload)
        self.b_apply.clicked.connect(self.apply)
        self.b_save.clicked.connect(self.save)
        self.b_def.clicked.connect(self.defaults)
        self.b_close.clicked.connect(self.accept)
        self.values: Dict[str, str] = {}
        self.reload()

    def reload(self):
        items = self.link.get_settings() if self.link else []
        self.values = dict(items)
        self.table.setRowCount(len(items))
        for r, (k, v) in enumerate(items):
            a = QtWidgets.QTableWidgetItem(k)
            a.setFlags(a.flags() & ~QtCore.Qt.ItemIsEditable)
            c = QtWidgets.QTableWidgetItem(SETTING_HELP.get(k, ""))
            c.setFlags(c.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(v))
            self.table.setItem(r, 2, c)
        self.table.resizeColumnToContents(0)
        self.msg.setText(f"{len(items)} Einstellungen gelesen." if items else "Keine Antwort auf #GET.")

    def apply(self) -> bool:
        errors, n = [], 0
        for r in range(self.table.rowCount()):
            k = self.table.item(r, 0).text()
            v = self.table.item(r, 1).text().strip()
            if v != self.values.get(k):
                lines = self.link.set_setting(k, v)
                if self.link.ok(lines):
                    self.values[k] = v
                    n += 1
                else:
                    errors.append(f"{k}: {lines[-1] if lines else 'keine Antwort'}")
        self.msg.setText(f"{n} Einstellung(en) übernommen." + ("\nFehler: " + "; ".join(errors) if errors else ""))
        return not errors

    def save(self):
        if self.apply():
            # F446: #SAVE nur bei angehaltener Ausgabe (sonst "#ERR;SAVE;messung laeuft")
            was = self.link.streaming()
            if was:
                self.link.ensure_stopped()
            lines = self.link.command("#SAVE", timeout=3.0)
            if was:
                self.link.start()
            ok = self.link.ok(lines)
            self.msg.setText("Im Gerät gespeichert." if ok else
                             "Speichern fehlgeschlagen: " + (lines[-1] if lines else "keine Antwort"))

    def defaults(self):
        if QtWidgets.QMessageBox.question(self, "Standardwerte", "Alle Einstellungen auf Standardwerte setzen?\n"
                                          "(flüchtig bis „Im Gerät speichern“)") != QtWidgets.QMessageBox.Yes:
            return
        ok = self.link.ok(self.link.command("#DEFAULTS"))
        self.reload()
        self.msg.setText("Standardwerte gesetzt (noch nicht gespeichert)." if ok else "Keine Bestätigung vom Gerät.")


class OsziPage(QtWidgets.QWidget):
    saved = QtCore.Signal(str)              # beendete Dauermessung (Ordner) -> Datenbank

    def __init__(self, settings: dict, save_settings: Callable[[], None], get_link: Callable[[], object],
                 get_params: Callable[[], DynoParams], runs_dir: Callable[[], str], get_vehicle: Callable[[], str]):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.get_link = get_link
        self.get_params = get_params
        self.runs_dir = runs_dir
        self.get_vehicle = get_vehicle
        self.link = None
        self.tap = None
        self.rec: Optional[Recorder] = None
        self.store = Store(LIVE_CHANNELS)
        self.t0: Optional[float] = None
        self.detector = dg.EventDetector(self._trigger_config())
        self.events: List[dg.Event] = []
        self.markers: List[tuple] = []
        self.bursts: List[dg.Burst] = []
        self.follow = True
        self.view_range = None              # (t0, t1) eingefrorene Ansicht (Trigger / Ereignis)
        self.pending = None                 # ausgeloestes Ereignis, Nachlauf laeuft noch
        self.armed = True
        self._last_keepalive = 0.0
        self._last_draw = 0.0
        self.selected = [c for c in settings.get("oszi_kanaele", DEFAULT_LIVE) if c in LIVE_CHANNELS] or DEFAULT_LIVE

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.addLayout(self._build_top())
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        left.addWidget(self._build_recorder())
        left.addWidget(self._build_burst())
        left.setSizes([520, 300])
        split.addWidget(left)
        split.addWidget(self._build_trigger())
        split.setStretchFactor(0, 1)
        split.setSizes([1100, 360])
        self.split = split
        root.addWidget(split, 1)
        self.detector.cfg = self._trigger_config()
        self._show_trig_state()
        self._rebuild_plots()
        self._update_device()

    # ------------------------------------------------------------------ Aufbau
    def _build_top(self):
        top = QtWidgets.QHBoxLayout()
        self.rec_btn = QtWidgets.QPushButton("●  Dauermessung starten")
        self.rec_btn.setCheckable(True)
        self.rec_btn.setFixedHeight(32)
        self.rec_btn.setMinimumWidth(200)
        self.rec_btn.setStyleSheet("QPushButton { font-weight: 700; } QPushButton:checked { background: #e57373; }")
        self.rec_btn.toggled.connect(self.toggle_recording)
        top.addWidget(self.rec_btn)
        self.rec_label = QtWidgets.QLabel("keine Aufzeichnung")
        self.rec_label.setMinimumWidth(260)
        top.addWidget(self.rec_label)
        self.marker_edit = QtWidgets.QLineEdit()
        self.marker_edit.setPlaceholderText("Marker-Text, z.B. „Kabel bewegt“")
        self.marker_edit.returnPressed.connect(self.add_marker)
        top.addWidget(self.marker_edit, 1)
        self.marker_btn = QtWidgets.QPushButton("Marker setzen (F2)")
        self.marker_btn.clicked.connect(self.add_marker)
        top.addWidget(self.marker_btn)
        self.mode_d = QtWidgets.QCheckBox("Diagnose-Telegramm (Modus D)")
        self.mode_d.setToolTip("Gerät sendet zusätzlich Flanken je Telegramm, kürzeste/längste Periode, verworfene "
                               "Flanken und Flags (nur PST-Diag 1). Normale Läufe sind davon unabhängig.")
        self.mode_d.setChecked(True)
        self.mode_d.toggled.connect(self._mode_changed)
        top.addWidget(self.mode_d)
        self.dev_label = QtWidgets.QLabel("")
        top.addWidget(self.dev_label)
        self.dev_btn = QtWidgets.QPushButton("Geräte-Einstellungen …")
        self.dev_btn.clicked.connect(self.device_settings)
        top.addWidget(self.dev_btn)
        QtGui.QShortcut(QtGui.QKeySequence("F2"), self, activated=self.add_marker,
                        context=QtCore.Qt.WidgetWithChildrenShortcut)
        return top

    def _build_recorder(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("<b>Linienschreiber</b>"))
        self.ch_btn = QtWidgets.QToolButton()
        self.ch_btn.setText("Kanäle …")
        self.ch_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.ch_menu = QtWidgets.QMenu(self.ch_btn)
        self.ch_menu.aboutToShow.connect(self._fill_channel_menu)
        self.ch_btn.setMenu(self.ch_menu)
        bar.addWidget(self.ch_btn)
        bar.addWidget(QtWidgets.QLabel("Zeitfenster"))
        self.win_combo = QtWidgets.QComboBox()
        for label, _s in WINDOWS:
            self.win_combo.addItem(label)
        self.win_combo.setCurrentIndex(self.settings.get("oszi_fenster", 1))
        self.win_combo.currentIndexChanged.connect(lambda i: (self.settings.update(oszi_fenster=i), self._go_live()))
        bar.addWidget(self.win_combo)
        self.live_btn = QtWidgets.QPushButton("Live")
        self.live_btn.setToolTip("Zur laufenden Anzeige zurück")
        self.live_btn.clicked.connect(self._go_live)
        bar.addWidget(self.live_btn)
        self.view_label = QtWidgets.QLabel("")
        bar.addWidget(self.view_label, 1)
        lay.addLayout(bar)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("w")
        lay.addWidget(self.glw, 1)
        hint = QtWidgets.QLabel("Mausrad = Zoom · ziehen = verschieben (hält die Anzeige an, „Live“ = weiter) · "
                                "Ereignis doppelklicken = Stelle anzeigen")
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        return w

    def _build_trigger(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        g = QtWidgets.QGroupBox("Software-Trigger (alle Geräte)")
        form = QtWidgets.QGridLayout(g)
        cfg = self.settings.get("oszi_trigger", {})
        self.trig_checks: Dict[str, QtWidgets.QCheckBox] = {}
        self.trig_vals: Dict[str, QtWidgets.QDoubleSpinBox] = {}
        rows = [(dg.EV_ROLL_JUMP, "roll_jump_pct", "> %", 8.0, 0.5, 500),
                (dg.EV_RATIO_JUMP, "ratio_jump_pct", "> %", 15.0, 1, 500),
                (dg.EV_IGN_MISS, "ign_drop_pct", "Einbruch > %", 40.0, 5, 100),
                (dg.EV_DOUBLE, "", "Zündung ≈ 2×", 0, 0, 0),
                (dg.EV_FROZEN, "frozen_s", "länger als s", 1.0, 0.1, 60)]
        for r, (kind, key, unit, default, lo, hi) in enumerate(rows):
            cb = QtWidgets.QCheckBox(kind)
            cb.setChecked(kind in cfg.get("aktiv", dg.EVENT_TYPES[:5]))
            cb.toggled.connect(self._trigger_changed)
            form.addWidget(cb, r, 0)
            self.trig_checks[kind] = cb
            if key:
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setDecimals(1)
                sp.setValue(float(cfg.get(key, default)))
                sp.valueChanged.connect(self._trigger_changed)
                form.addWidget(sp, r, 1)
                self.trig_vals[key] = sp
            form.addWidget(QtWidgets.QLabel(unit), r, 2)
        r = len(rows)
        cb = QtWidgets.QCheckBox(dg.EV_THRESHOLD)
        cb.setChecked(dg.EV_THRESHOLD in cfg.get("aktiv", []))
        cb.toggled.connect(self._trigger_changed)
        self.trig_checks[dg.EV_THRESHOLD] = cb
        form.addWidget(cb, r, 0)
        self.thr_channel = QtWidgets.QComboBox()
        self.thr_channel.addItems(list(LIVE_CHANNELS))
        self.thr_channel.setCurrentText(cfg.get("schwelle_kanal", "Drehzahl Zündung"))
        self.thr_dir = QtWidgets.QComboBox()
        self.thr_dir.addItems([">", "<"])
        self.thr_dir.setCurrentIndex(0 if cfg.get("schwelle_ueber", True) else 1)
        self.thr_val = QtWidgets.QDoubleSpinBox()
        self.thr_val.setRange(-1e6, 1e6)
        self.thr_val.setDecimals(2)
        self.thr_val.setValue(float(cfg.get("schwelle", 10000.0)))
        for x in (self.thr_channel, self.thr_dir):
            x.currentIndexChanged.connect(self._trigger_changed)
        self.thr_val.valueChanged.connect(self._trigger_changed)
        thr = QtWidgets.QHBoxLayout()
        thr.addWidget(self.thr_channel, 1)
        thr.addWidget(self.thr_dir)
        thr.addWidget(self.thr_val)
        form.addLayout(thr, r + 1, 0, 1, 3)
        r += 2
        form.addWidget(QtWidgets.QLabel("Modus"), r, 0)
        self.trig_mode = QtWidgets.QComboBox()
        self.trig_mode.addItems(MODES)
        self.trig_mode.setCurrentText(cfg.get("modus", "Auto"))
        self.trig_mode.setToolTip("Auto: Anzeige läuft weiter, Ereignisse werden nur gelistet.\n"
                                  "Normal: Anzeige springt bei jedem Ereignis auf die Stelle (Vor-/Nachlauf).\n"
                                  "Einzelschuss: wie Normal, danach angehalten bis „Scharf schalten“.")
        self.trig_mode.currentIndexChanged.connect(self._trigger_changed)
        form.addWidget(self.trig_mode, r, 1, 1, 2)
        r += 1
        self.pre_s = QtWidgets.QDoubleSpinBox()
        self.post_s = QtWidgets.QDoubleSpinBox()
        for sp, key, d in ((self.pre_s, "vorlauf", 2.0), (self.post_s, "nachlauf", 2.0)):
            sp.setRange(0.1, 600)
            sp.setDecimals(1)
            sp.setSuffix(" s")
            sp.setValue(float(cfg.get(key, d)))
            sp.valueChanged.connect(self._trigger_changed)
        form.addWidget(QtWidgets.QLabel("Vorlauf / Nachlauf"), r, 0)
        pp = QtWidgets.QHBoxLayout()
        pp.addWidget(self.pre_s)
        pp.addWidget(self.post_s)
        form.addLayout(pp, r, 1, 1, 2)
        r += 1
        self.arm_btn = QtWidgets.QPushButton("Scharf schalten")
        self.arm_btn.clicked.connect(self.arm)
        self.trig_state = QtWidgets.QLabel("")
        form.addWidget(self.arm_btn, r, 0)
        form.addWidget(self.trig_state, r, 1, 1, 2)
        lay.addWidget(g)

        lay.addWidget(QtWidgets.QLabel("<b>Ereignisse</b> (Doppelklick = Stelle anzeigen)"))
        self.ev_table = QtWidgets.QTableWidget(0, 3)
        self.ev_table.setHorizontalHeaderLabels(["Zeit [s]", "Art", "Beschreibung"])
        self.ev_table.verticalHeader().setVisible(False)
        self.ev_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.ev_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.ev_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ev_table.cellDoubleClicked.connect(self._event_clicked)
        lay.addWidget(self.ev_table, 1)
        eb = QtWidgets.QHBoxLayout()
        self.ev_count = QtWidgets.QLabel("")
        eb.addWidget(self.ev_count, 1)
        clr = QtWidgets.QPushButton("Liste leeren")
        clr.clicked.connect(self.clear_events)
        eb.addWidget(clr)
        lay.addLayout(eb)
        return w

    def _build_burst(self):
        w = QtWidgets.QGroupBox("Flanken-Ansicht (Burst, Geräte mit PST-Diag 1)")
        lay = QtWidgets.QVBoxLayout(w)
        bar = QtWidgets.QHBoxLayout()
        self.b_ch = QtWidgets.QComboBox()
        self.b_ch.addItem("Zündung", "I")
        self.b_ch.addItem("Rolle", "R")
        self.b_ch.addItem("beide", "A")
        self.b_n = QtWidgets.QSpinBox()
        self.b_n.setRange(1, 2000)
        self.b_n.setValue(200)
        self.b_edge = QtWidgets.QComboBox()
        self.b_edge.addItem("fallend", "F")
        self.b_edge.addItem("steigend", "R")
        self.b_edge.addItem("beide (Pulsbreite)", "B")
        self.b_trig = QtWidgets.QDoubleSpinBox()
        self.b_trig.setRange(0, 1000)
        self.b_trig.setValue(20)
        self.b_trig.setSuffix(" %")
        self.b_trig.setToolTip("Trigger: Periode weicht mehr als x % vom Mittel der 8 vorigen ab (0 = sofort)")
        self.b_pre = QtWidgets.QSpinBox()
        self.b_pre.setRange(0, 2000)
        self.b_pre.setValue(100)
        for label, x in (("Kanal", self.b_ch), ("Anzahl", self.b_n), ("Flanke", self.b_edge),
                         ("Trigger", self.b_trig), ("Vorlauf", self.b_pre)):
            bar.addWidget(QtWidgets.QLabel(label))
            bar.addWidget(x)
        self.b_go = QtWidgets.QPushButton("Burst anfordern")
        self.b_go.clicked.connect(self.request_burst)
        self.b_stop = QtWidgets.QPushButton("Abbrechen")
        self.b_stop.clicked.connect(self.stop_burst)
        bar.addWidget(self.b_go)
        bar.addWidget(self.b_stop)
        self.b_list = QtWidgets.QComboBox()
        self.b_list.setMinimumWidth(160)
        self.b_list.currentIndexChanged.connect(lambda i: self.show_burst(self.bursts[i]) if 0 <= i < len(self.bursts)
                                                else None)
        bar.addWidget(self.b_list)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.b_state = QtWidgets.QLabel("")
        lay.addWidget(self.b_state)
        self.bglw = pg.GraphicsLayoutWidget()
        self.bglw.setBackground("w")
        lay.addWidget(self.bglw, 1)
        self.p_logic = self.bglw.addPlot(row=0, col=0)
        self.p_logic.setLabel("left", "Pegel")
        self.p_logic.getAxis("left").setTicks([[(0.5, "Rolle"), (2.0, "Zündung")]])
        self.p_logic.setYRange(-0.3, 2.8, padding=0)
        self.p_logic.vb.setMouseEnabled(x=True, y=False)
        self.p_logic.showGrid(x=True, y=False, alpha=0.3)
        self.p_logic.getAxis("bottom").setLabel(units="s")
        self.p_per = self.bglw.addPlot(row=1, col=0)
        self.p_per.setLabel("left", "Periode", units="s")
        self.p_per.setLabel("bottom", "Zeit ab Trigger", units="s")
        self.p_per.setXLink(self.p_logic)
        self.p_per.showGrid(x=True, y=True, alpha=0.3)
        self.p_per.addLegend(offset=(5, 5))
        self.cur = [pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(c, width=1.5)) for c in ("#d32f2f", "#1976d2")]
        for c in self.cur:
            self.p_logic.addItem(c, ignoreBounds=True)
            c.sigPositionChanged.connect(self._cursor_moved)
        self.cur_label = QtWidgets.QLabel("Cursor: rot/blau ziehen")
        lay.addWidget(self.cur_label)
        return w

    # ------------------------------------------------------------------ Geraet
    def _update_device(self):
        link = self.link
        diag = bool(link and link.has_diag)
        if link is None:
            text = "kein Gerät verbunden"
        elif diag:
            text = f"{link.caps.get('fw', 'PST-Diag 1')} · PST-Diag 1"
        else:
            text = "Gerät ohne PST-Diag 1 – nur Software-Trigger"
        self.dev_label.setText(text)
        self.dev_btn.setEnabled(diag)
        self.mode_d.setEnabled(diag)
        for x in (self.b_go, self.b_stop, self.b_ch, self.b_n, self.b_edge, self.b_trig, self.b_pre):
            x.setEnabled(diag)
        if diag:
            mx = int(float(link.caps.get("burst_max", 2000) or 2000))
            self.b_n.setMaximum(mx)
            self.b_pre.setMaximum(mx - 1)
            self.b_state.setText(f"Burst bis {mx} Flanken, Auflösung {link.caps.get('tick_ns', '?')} ns.")
        else:
            self.b_state.setText("Flanken-Ansicht braucht PST-Diag 1 (Mega ab 3.1.0, F446 ab 1.1.0). "
                                 "Der Software-Trigger arbeitet mit jedem Gerät.")

    def _check_link(self):
        link = self.get_link()
        if link is self.link:
            return
        if self.link is not None and self.tap is not None:
            self.link.remove_tap(self.tap)
        self.link, self.tap = link, None
        if link is not None:
            self.tap = link.add_tap()
            if self.rec is not None and link.has_diag and self.mode_d.isChecked():
                link.set_mode("D")
        self._update_device()

    def _mode_changed(self, on: bool):
        if self.link is not None and self.link.has_diag and self.rec is not None:
            self.link.set_mode("D" if on else "N")

    def device_settings(self):
        if self.link is None or not self.link.has_diag:
            return
        DeviceSettingsDialog(self.link, self).exec()

    # ------------------------------------------------------------------ Dauermessung
    def toggle_recording(self, on: bool):
        if on:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        self._check_link()
        link = self.link
        if link is None:
            QtWidgets.QMessageBox.information(self, "Dauermessung", "Bitte zuerst die Messelektronik verbinden.")
            self.rec_btn.blockSignals(True)
            self.rec_btn.setChecked(False)
            self.rec_btn.blockSignals(False)
            return
        diag = link.has_diag and self.mode_d.isChecked()
        if diag:
            link.set_mode("D")
        self.rec = Recorder(self.runs_dir(), self.get_params(), port=link.port,
                            firmware=link.caps.get("fw", "") or "", diag=link.has_diag, vehicle=self.get_vehicle())
        self.clear(keep_settings=True)
        while self.tap is not None and not self.tap.empty():      # Telegramme von vor dem Start verwerfen
            self.tap.get_nowait()
        if not link.streaming():
            link.start()
        self.rec_btn.setText("■  Dauermessung beenden")
        self._last_keepalive = time.time()

    def stop_recording(self):
        if self.rec is None:
            return
        folder = self.rec.close()
        self.rec = None
        self.rec_btn.setText("●  Dauermessung starten")
        if self.link is not None and self.link.has_diag and self.link.mode != "N":
            self.link.set_mode("N")
        self.rec_label.setText(f"gespeichert: {os.path.basename(folder)}")
        self.saved.emit(folder)

    def add_marker(self):
        text = self.marker_edit.text().strip() or f"Marker {len(self.markers) + 1}"
        t = self._now_rel()
        if self.rec is not None:
            self.rec.add_marker(text, t)
        self.markers.append((t, text))
        self.marker_edit.clear()
        self._draw(force=True)

    def _now_rel(self) -> float:
        if self.t0 is None:
            return 0.0
        return self.store.t[self.store.n - 1] if self.store.n else 0.0

    def clear(self, keep_settings: bool = True):
        self.store = Store(LIVE_CHANNELS)
        self.t0 = None
        self.detector = dg.EventDetector(self._trigger_config())
        self.markers = []
        self.clear_events()
        self.view_range = None
        self.pending = None
        self.armed = True
        self._rebuild_plots()

    # ------------------------------------------------------------------ Trigger
    def _trigger_config(self) -> dg.TriggerConfig:
        c = dg.TriggerConfig()
        if not hasattr(self, "trig_vals"):
            return c
        for k, sp in self.trig_vals.items():
            setattr(c, k, sp.value())
        if self.trig_checks[dg.EV_THRESHOLD].isChecked():
            c.threshold_channel = self.thr_channel.currentText()
            c.threshold = self.thr_val.value()
            c.threshold_above = self.thr_dir.currentIndex() == 0
        c.enabled = tuple(dg.EVENT_TYPES)               # erkannt und gelistet wird alles, ausgeloest nur das Gewaehlte
        return c

    def _trigger_changed(self, *_):
        self.detector.cfg = self._trigger_config()
        self.settings["oszi_trigger"] = {
            **{k: sp.value() for k, sp in self.trig_vals.items()},
            "aktiv": [k for k, cb in self.trig_checks.items() if cb.isChecked()],
            "schwelle_kanal": self.thr_channel.currentText(), "schwelle_ueber": self.thr_dir.currentIndex() == 0,
            "schwelle": self.thr_val.value(), "modus": self.trig_mode.currentText(),
            "vorlauf": self.pre_s.value(), "nachlauf": self.post_s.value()}
        self.save_settings()
        self._show_trig_state()

    def arm(self):
        self.armed = True
        self.pending = None
        self._show_trig_state()

    def _show_trig_state(self):
        m = self.trig_mode.currentText()
        if m == "Auto":
            self.trig_state.setText("Auto: Ereignisse werden gelistet")
        elif self.pending is not None:
            self.trig_state.setText(f"ausgelöst ({self.pending.kind}), Nachlauf …")
        elif self.armed:
            self.trig_state.setText("scharf – wartet auf Ereignis")
        else:
            self.trig_state.setText("angehalten (Einzelschuss) – „Scharf schalten“")

    def _on_event(self, ev: dg.Event):
        self.events.append(ev)
        if self.rec is not None:
            self.rec.add_event(ev)
        r = self.ev_table.rowCount()
        if r < 5000:
            self.ev_table.insertRow(r)
            for c, text in enumerate((f"{ev.t:.2f}", ev.kind, ev.text)):
                self.ev_table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        self.ev_count.setText(f"{len(self.events)} Ereignisse")
        mode = self.trig_mode.currentText()
        cb = self.trig_checks.get(ev.kind)
        if mode != "Auto" and self.armed and self.pending is None and cb is not None and cb.isChecked():
            self.pending = ev
            self._show_trig_state()

    def clear_events(self):
        self.events = []
        self.ev_table.setRowCount(0)
        self.ev_count.setText("")

    def _event_clicked(self, row: int, _col: int):
        if 0 <= row < len(self.events):
            self.show_at(self.events[row].t)

    def show_at(self, t: float):
        self.view_range = (t - self.pre_s.value(), t + self.post_s.value())
        self.view_label.setText(f"Stelle {t:.2f} s")
        self._draw(force=True)

    def _go_live(self):
        self.view_range = None
        self.view_label.setText("")
        self._draw(force=True)

    # ------------------------------------------------------------------ Datenfluss
    def poll(self):
        """Regelmaessig vom Hauptfenster aufrufen (auch wenn der Reiter nicht sichtbar ist)."""
        self._check_link()
        if self.tap is None:
            return
        frames = []
        while True:
            try:
                frames.append(self.tap.get_nowait())
            except Exception:
                break
        link = self.link
        # Bursts
        while link is not None:
            try:
                b = link.bursts.get_nowait()
            except Exception:
                break
            self._burst_arrived(b)
        if link is not None:
            link.check_burst_timeout()
        if link is not None and link._burst is not None and link._burst.n:
            self.b_state.setText(f"Burst-Ausgabe: {len(link._burst.edges)} / {link._burst.n} Flanken …")
        if self.rec is not None and link is not None and not link.streaming() \
                and time.time() - self._last_keepalive > 2.0:
            self._last_keepalive = time.time()            # Ausgabe am Laufen halten (Autostopp der Firmware)
            link.start()
        if not frames:
            if self.rec is not None:
                self._show_rec_state()
            return
        p = self.get_params()
        if self.t0 is None:
            self.t0 = frames[0].t
            if self.rec is not None:
                self.rec.t0 = self.t0
        for f in frames:
            v = live_values(f, p)
            t = f.t - self.t0
            v["t"] = t
            self.store.append(t, v)
            for ev in self.detector.feed(v):
                self._on_event(ev)
        if self.rec is not None:
            self.rec.add_frames(frames, link.lost if link else 0)
            self._show_rec_state()
        if self.pending is not None and self.store.n and self.store.t[self.store.n - 1] >= self.pending.t + self.post_s.value():
            ev, self.pending = self.pending, None
            self.show_at(ev.t)
            self.view_label.setText(f"Trigger: {ev.kind} bei {ev.t:.2f} s")
            if self.trig_mode.currentText() == "Einzelschuss":
                self.armed = False
            self._show_trig_state()
        if self.isVisible():
            self._draw()

    def _show_rec_state(self):
        r = self.rec
        dur = self._now_rel()
        h, m, s = int(dur // 3600), int(dur % 3600 // 60), dur % 60
        self.rec_label.setText(f"Aufnahme {h:d}:{m:02d}:{s:04.1f} · {r.n} Telegramme · "
                               f"{r.size_bytes() / 1e6:.1f} MB · {len(self.markers)} Marker"
                               + ("" if self.link and self.link.streaming() else " · keine Daten!"))

    # ------------------------------------------------------------------ Linienschreiber
    def _fill_channel_menu(self):
        m = self.ch_menu
        m.clear()
        diag = bool(self.link and self.link.has_diag)
        for name in LIVE_CHANNELS:
            a = m.addAction(name + ("" if name not in DIAG_LIVE or diag else "  (nur PST-Diag 1)"))
            a.setCheckable(True)
            a.setChecked(name in self.selected)
            a.toggled.connect(lambda on, n=name: self._toggle_channel(n, on))

    def _toggle_channel(self, name: str, on: bool):
        if on and name not in self.selected:
            self.selected.append(name)
        elif not on and name in self.selected:
            self.selected.remove(name)
        self.settings["oszi_kanaele"] = self.selected
        self.save_settings()
        self._rebuild_plots()

    def _rebuild_plots(self):
        self.glw.clear()
        self.plots: Dict[str, pg.PlotItem] = {}
        self.curves: Dict[str, pg.PlotDataItem] = {}
        self.marker_lines: List = []
        units = []
        for n in self.selected:
            u = LIVE_CHANNELS[n][0]
            if u not in units:
                units.append(u)
        prev = None
        for row, u in enumerate(units):
            pi = self.glw.addPlot(row=row, col=0)
            pi.setLabel("left", u)
            pi.showGrid(x=True, y=True, alpha=0.25)
            pi.setDownsampling(auto=True, mode="peak")
            pi.setClipToView(True)
            pi.addLegend(offset=(5, 2), labelTextSize="8pt")
            pi.getAxis("left").setWidth(60)
            pi.vb.sigRangeChangedManually.connect(self._manual_range)
            if prev is not None:
                pi.setXLink(prev)
            if row < len(units) - 1:
                pi.getAxis("bottom").setStyle(showValues=False)
            else:
                pi.setLabel("bottom", "Zeit", units="s")
            prev = pi
            self.plots[u] = pi
        for n in self.selected:
            pi = self.plots[LIVE_CHANNELS[n][0]]
            self.curves[n] = pi.plot([], [], pen=pg.mkPen(LIVE_CHANNELS[n][1], width=1.5), name=n, connect="finite")
        self._draw(force=True)

    def _manual_range(self, *_):
        if self.view_range is None and self.plots:
            r = next(iter(self.plots.values())).vb.viewRange()[0]
            self.view_range = (r[0], r[1])
            self.view_label.setText("angehalten – „Live“ = weiter")

    def _draw(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_draw < 0.2:
            return
        self._last_draw = now
        st = self.store
        if not self.plots:
            return
        t_end = st.t[st.n - 1] if st.n else 0.0
        if self.view_range is not None:
            t0, t1 = self.view_range
        else:
            span = WINDOWS[self.win_combo.currentIndex()][1]
            first = st.t[0] if st.n else 0.0
            t0 = max(first, t_end - span) if span else first
            t1 = t_end if span == 0 else max(t_end, t0 + span)
        a, b = st.window(t0 - 1.0, t1 + 1.0)
        for n, c in self.curves.items():
            c.setData(st.t[a:b], st.v[n][a:b])
        for pi in self.plots.values():
            pi.setXRange(t0, t1, padding=0)            # loest kein sigRangeChangedManually aus
            pi.enableAutoRange(axis="y")
        # Marker und Ereignisse als senkrechte Linien
        for pi, line in self.marker_lines:
            pi.removeItem(line)
        self.marker_lines = []
        first = next(iter(self.plots.values()))
        for t, text in self.markers:
            if t0 <= t <= t1:
                for k, pi in enumerate(self.plots.values()):
                    line = pg.InfiniteLine(t, angle=90, pen=pg.mkPen("#2e7d32", width=1.5),
                                           label=text if pi is first else None,
                                           labelOpts={"position": 0.9, "color": "#2e7d32", "fill": "#ffffffcc"})
                    pi.addItem(line, ignoreBounds=True)
                    self.marker_lines.append((pi, line))
        shown = [e for e in self.events if t0 <= e.t <= t1][-50:]
        for e in shown:
            for pi in self.plots.values():
                line = pg.InfiniteLine(e.t, angle=90, pen=pg.mkPen("#ff9800", width=1, style=QtCore.Qt.DashLine))
                pi.addItem(line, ignoreBounds=True)
                self.marker_lines.append((pi, line))

    # ------------------------------------------------------------------ Burst
    def request_burst(self):
        if self.link is None or not self.link.has_diag:
            return
        trig = self.b_trig.value() or None
        pre = self.b_pre.value() if trig else None
        lines = self.link.request_burst(self.b_ch.currentData(), self.b_n.value(), self.b_edge.currentData(),
                                        trig, pre)
        if self.link.ok(lines):
            self.b_state.setText("Burst scharf – " + ("wartet auf Trigger …" if trig else "Aufnahme läuft …"))
        else:
            self.b_state.setText("Gerät lehnt ab: " + (lines[-1] if lines else "keine Antwort"))
        if not self.link.streaming():
            self.link.start()                                 # Flanken werden nur bei laufender Messung erfasst

    def stop_burst(self):
        if self.link is not None and self.link.has_diag:
            self.b_state.setText("Burst abgebrochen" if self.link.stop_burst() else "keine Bestätigung")

    def _burst_arrived(self, b: dg.Burst):
        if self.t0 is not None:
            b.request["zeit_s"] = f"{b.t_pc - self.t0:.2f}"
        self.bursts.append(b)
        if self.rec is not None:
            self.rec.add_burst(b)
        name = f"{len(self.bursts)}: {b.ch} {len(b.edges)} Fl." + (" Trig" if b.trig_idx >= 0 else "") \
            + ("" if b.complete else " unvollständig")
        self.b_list.blockSignals(True)
        self.b_list.addItem(name)
        self.b_list.setCurrentIndex(self.b_list.count() - 1)
        self.b_list.blockSignals(False)
        self.show_burst(b)
        if b.trig_idx >= 0 and b.request.get("zeit_s"):
            self._on_event(dg.Event(float(b.request["zeit_s"]), dg.EV_DEVICE, "Burst ausgelöst (Gerät)"))

    def show_burst(self, b: dg.Burst):
        self.p_logic.clear()
        self.p_per.clear()
        for c in self.cur:
            self.p_logic.addItem(c, ignoreBounds=True)
        colors = {"I": "#c62828", "R": "#1f5fbf"}
        offs = {"I": 1.5, "R": 0.0}
        info = []
        for ch in ("I", "R"):
            x, y = b.trace(ch)
            if not len(x):
                continue
            self.p_logic.plot(x, y + offs[ch], pen=pg.mkPen(colors[ch], width=1.5))
            t, per = b.periods(ch)
            name = "Zündung" if ch == "I" else "Rolle"
            if len(per):
                self.p_per.plot(t, per, pen=pg.mkPen(colors[ch], width=1), symbol="o", symbolSize=4,
                                symbolBrush=colors[ch], symbolPen=None, name=f"Periode {name}")
                med = float(np.median(per))
                info.append(f"{name}: Periode Median {med * 1e6:.1f} µs ({1 / med:.1f} Hz), "
                            f"min {per.min() * 1e6:.1f} µs, max {per.max() * 1e6:.1f} µs")
            if b.request.get("edge") == "B" or (len(set(y)) > 1 and b.ch == "A"):
                ts, w = b.pulse_widths(ch, 0)
                if len(w):
                    self.p_per.plot(ts, w, pen=None, symbol="t", symbolSize=6, symbolBrush="#ff9800",
                                    name=f"Pulsbreite {name}")
                    info.append(f"Pulsbreite {name}: {np.median(w) * 1e6:.1f} µs")
        self.p_logic.addLine(x=0, pen=pg.mkPen("#ff9800", width=1, style=QtCore.Qt.DashLine)) \
            if b.trig_idx >= 0 else None
        tt = b.times()
        if len(tt):
            self.cur[0].setValue(float(tt[0] + (tt[-1] - tt[0]) * 0.4))
            self.cur[1].setValue(float(tt[0] + (tt[-1] - tt[0]) * 0.6))
            self.p_logic.setXRange(float(tt[0]), float(tt[-1]), padding=0.02)
        trig = "ausgelöst bei Flanke %d" % b.trig_idx if b.trig_idx >= 0 else "ohne Trigger"
        state = "" if b.complete else f"UNVOLLSTÄNDIG (abgebrochen, {len(b.edges)} von {b.n}) · "
        self.b_state.setText(f"{state}Burst {len(b.edges)} Flanken, {trig}, verloren {b.lost} · " + " · ".join(info))
        self._cursor_moved()

    def _cursor_moved(self, *_):
        a, b = self.cur[0].value(), self.cur[1].value()
        d = abs(b - a)
        f = f" = {1 / d:.2f} Hz" if d > 0 else ""
        self.cur_label.setText(f"Cursor rot {a * 1e3:.3f} ms · blau {b * 1e3:.3f} ms · Δt {d * 1e6:.1f} µs{f}")

    def close_page(self):
        if self.rec is not None:
            self.stop_recording()
        if self.link is not None and self.tap is not None:
            self.link.remove_tap(self.tap)
