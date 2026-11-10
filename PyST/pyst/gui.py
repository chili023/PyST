"""
PyST – Oberflaeche (PySide6 + pyqtgraph). Mac, Linux (Raspberry Pi), Windows.

Reiter:  Messen | Fahrzeuge & Setups | Auswertung | Oszi / Dauermessung | Einstellungen
Einstellungen:  Rolle/Lauf | Verbindung | Zange | Lambda | ECU | Software & Firmware | WiFi
  Rolle/Lauf: Allgemein (Pruefstand, Lauf, Filter, Klima, Anzeige) | Verluste (Ausrollversuch, frueher Reiter Kalibrierung)
  Verbindung: Messboard (Port, Geraete-Einstellungen PST-Diag) | Prüfen
  Zange: Zange (Zustand, Kopplung, Log) | Tasten;  Lambda: Lambda-Heizung;  ECU: rusEFI (frueher Hauptreiter ECU)
  WiFi: Netzwerk der Basis, Steckdosen
Tag/Nacht: theme.py (Menue Ansicht › Darstellung, Umschalter in der Statusleiste), Statusleiste: statusleiste.py
Messen: Aufbau und Messlogik in messen_page.py (MessenMixin), Einstellungen messen_page.START_NACHFRAGE,
  NACH_LAUF_FENSTER (Fenster „Lauf fertig“), SETUP_EINGEKLAPPT; Lauf speichern: save_current_run / lauf_speichern
PDF-Bericht: bericht.py (was im Diagramm steht; Knopf unten rechts: on_pdf_aktiv), Ordner-Import: ordner_import.py
"""
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict
from typing import Dict, Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import farben
from . import theme
from .statusleiste import Eingang, Statusleiste, zustaende
from .umbruch import UmbruchLayout
from . import __version__, analog, ausrollen, bericht, htmlexport, ordner_import, physics, report, storage, tasten
from .analog_page import AnalogPage
from .channels import lambda_from_afr, run_channels, run_faults
from .db import VEHICLE_FIELDS, Database, base_dir
from .dialogs import FieldForm, SetupForm
from .basis import ZANGE_USB_WARNUNG
from .flash_page import FlashPage
from .pruef_page import PruefPage
from .hw_page import HardwarePage
from .ausroll_assistent import AusrollAssistent
from .kalib_page import KalibPage
from .link import (DEFAULT_SIM_PORT, SIM_PORT, DynoLink, guess_port, is_pst_firmware, list_serial_ports,
                   pst_board_ports, zange_usb_ports)
from .oszi import OsziPage
from .ratio_dialog import RatioDialog
from .share_ui import FORMAT_FOLDER, FORMAT_HTML, ExportDialog, ImportParamsDialog
from .auth import AuthError, AuthNoReply
from .bg import Bg, wait_in_thread, waiting
from .plots import EXTRA_PREFIX, LOWER_DEFAULT, MAX_LOWER, MAX_OVERLAYS, DynoPlot, fmt_value, run_color
from .rusefi import COLUMN_PREFIX
from .runcache import RunCache
from .runner import ABORTED, ACTIVE, COAST, DONE, IDLE, KLIMA, RUN, WAIT, RunController
from .rusefi_page import RusefiPage
from .tasten_page import TastenBand, TastenPage
from .viewer import LogViewer
from .messen_page import GEAENDERT, LIVE_KEY, NACH_LAUF_FENSTER, START_NACHFRAGE, MessenMixin  # noqa: F401

log = logging.getLogger("pyst.gui")

SETTINGS_FILE = os.path.join(base_dir(), "einstellungen.json")
FILTER_S_DEFAULT = (1.75, 0.75)       # = LabVIEW 35/15 bei 20 Hz
# Gespeicherter Reiterstand: heute der Schluessel der Seite ("seite", siehe MainWindow.seiten).
# Aeltere Fenster: "reiter" (Index vor den Unterreitern) -> (Reiter, Unterreiter) ...
REITER_ALT = {0: (0, 0), 1: (1, 0), 2: (2, 0), 3: (3, 0), 4: (5, 2), 5: (5, 4), 6: (5, 5), 7: (4, 0), 8: (5, 0)}
REITER_KALIB = 5          # ... "reiter_neu" ohne "reiter_kalib": Indizes ab hier + 1 (Reiter Kalibrierung kam dazu)
# ... und "reiter_neu" (PyST 1.4: Messen, Fahrzeuge, Auswertung, Oszi, ECU, Kalibrierung, Einstellungen mit den
# Unterreitern Allgemein, Verbindung, Zange · Netz · Steckdosen, Tasten, Prüfen, Firmware) -> Seite heute
REITER_14 = ["messen", "fahrzeuge", "auswertung", "oszi", "ecu", "verluste", None]
UNTER_14 = ["rolle", "verbindung", "zange", "tasten", "pruefen", "firmware"]

# Woher die Uebersetzung nKW/nRolle eines Laufs kommt (Einstellung UEBERSETZUNG_ART): von Hand eingetragen bzw.
# eingemessen, oder aus den Funken jedes Laufs (runner.RunController.ratio_aus_funken)
UEBERSETZUNG_ART = "uebersetzung_art"
UEBERSETZUNG_ARTEN = (("hand", "von Hand (eingetragen/eingemessen)"), ("funken", "Funkenmessung (jeder Lauf)"))
UEBERSETZUNG_TIP = ("von Hand: der eingetragene bzw. eingemessene Wert gilt für jeden Lauf.\n"
                    "Funkenmessung: nach jedem Lauf wird die Übersetzung aus Zündung/Rolle des Laufs bestimmt "
                    "(getrimmter Mittelwert wie beim Einmessen) und der Lauf damit ausgewertet; der Wert wird "
                    "übernommen. Bis dahin rechnen Live-Kurve und Laufgrenzen mit dem eingetragenen Wert.")

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
        ("start_anstieg_s", "Start: steigt über [s]", 0.2, 5, 1),
        ("start_anstieg_min", "Start: Anstieg mind. [1/min]", 0, 5000, 0),
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
        ("m2", "quadratisch m2 [Nm/(1000/min)²]", -100, 100, 3),
    ],
    "Klima": [
        ("temp_c", "Lufttemperatur [°C]", -30, 60, 1),
        ("p_mbar", "Luftdruck [mbar]", 500, 1200, 1),
    ],
}


# Tooltips der Felder (auch an den Spiegeln unter Messen › Lauf-Einstellungen …)
PARAM_TIPS = {
    "start_anstieg_s": "Laufbeginn: Nach GO beginnt die Messung, sobald die Motordrehzahl (Rolle × Übersetzung) so "
                       "lange durchgehend steigt. Die Kurve beginnt am Anfang dieses Zeitfensters, nichts geht "
                       "verloren. Standard 1,0 s.",
    "start_anstieg_min": "Laufbeginn: Um so viel muss die Motordrehzahl im Zeitfenster mindestens steigen, damit "
                         "Halten mit Messrauschen nicht als Start gilt. Standard 150 1/min.",
    "n_min": "Ab dieser Motordrehzahl wird auf fallende Drehzahl (= Laufende, Gas weg) geprüft. Startet den Lauf "
             "nicht, der beginnt mit dem Drehzahlanstieg. Muss über der Haltedrehzahl liegen.",
    "n_stop": "Lauf endet spätestens bei dieser Motordrehzahl (0 = aus, Ende nur durch Gas weg).",
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(d: dict) -> bool:
    """Atomar: ein Absturz beim Schreiben laesst die alte Datei stehen. False = nicht gespeichert (Platte voll …)."""
    tmp = SETTINGS_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS_FILE)
        return True
    except (OSError, TypeError, ValueError) as exc:
        log.warning("Einstellungen nicht gespeichert: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


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
    t.setStyleSheet("QTableView::item { padding: 0px 4px; }")     # Rahmen/Gitter aus dem Thema (theme.py)
    t.setAttribute(QtCore.Qt.WA_MacShowFocusRect, False)
    t.setWordWrap(False)


def color_icon(color: str) -> QtGui.QIcon:
    pm = QtGui.QPixmap(14, 14)
    pm.fill(QtGui.QColor(color))
    return QtGui.QIcon(pm)


# =============================================================================================== Einstellungen
LOSS_KEYS = ("m0", "m1", "n1", "m2")


class SettingsPage(QtWidgets.QScrollArea):
    """Unterreiter Rolle/Lauf (oben Prüfstand, Lauf, Filter, Klima; unten Verluste mit dem Ausrollversuch) und, als
    eigene Seiten, Verbindung (conn_page) und die Gruppe Gemisch (box_gemisch, Unterreiter Lambda)."""
    losses_changed = QtCore.Signal()
    filter_art_geaendert = QtCore.Signal(bool)      # Filter in Sekunden (True) / Messpunkten, auch aus set_params

    def __init__(self, settings: dict):
        super().__init__()
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        # oben Prüfstand | Lauf | Filter | Klima (schmales Fenster: 2 x 2), unten Verluste (Werte) | Ausrollversuch
        # (KalibPage, Hauptfenster)
        root = QtWidgets.QVBoxLayout(inner)
        oben = UmbruchLayout(abstand=root.spacing(), zeilenabstand=root.spacing())
        root.addLayout(oben)
        unten = QtWidgets.QHBoxLayout()
        root.addLayout(unten, 1)
        self.verluste_lay = QtWidgets.QVBoxLayout()
        paare = {"Prüfstand": oben.neue_gruppe(), "Filter": oben.neue_gruppe()}
        paare["Lauf"], paare["Klima"] = paare["Prüfstand"], paare["Filter"]
        self._build_verbindung(settings)

        self.fields: Dict[str, QtWidgets.QDoubleSpinBox] = {}
        defaults = physics.DynoParams(**{k: v for k, v in settings.get("params", {}).items()
                                         if k in physics.DynoParams.__dataclass_fields__})
        self.filter_seconds = settings.get("filter_seconds", True)
        if defaults.ma_s <= 0:
            defaults.ma_s = settings.get("ma_s", FILTER_S_DEFAULT[0])
        if defaults.dq_s <= 0:
            defaults.dq_s = settings.get("dq_s", FILTER_S_DEFAULT[1])
        boxes: Dict[str, QtWidgets.QFormLayout] = {}
        for group in ("Prüfstand", "Lauf", "Filter", "Klima", "Verluste"):
            box = QtWidgets.QGroupBox("Verluste (Werte für die Rechnung)" if group == "Verluste" else group)
            boxes[group] = QtWidgets.QFormLayout(box)
            if group == "Verluste":
                unten.addWidget(box, 1, QtCore.Qt.AlignTop)
                unten.addLayout(self.verluste_lay, 3)
                self.verluste_box = box
            elif group == "Prüfstand":                 # darunter die Gruppe „Anzeige beim Messen“
                spalte = QtWidgets.QVBoxLayout()
                spalte.addWidget(box, 1)
                spalte.addWidget(self._anzeige_gruppe(settings))
                oben.add(spalte, 1, gruppe=paare[group])
            else:
                oben.add(box, 1, gruppe=paare[group])
        self.filter_form = boxes["Filter"]
        self.filter_mode = QtWidgets.QComboBox()
        self.filter_mode.addItems(["Sekunden", "Messpunkte (wie LabVIEW)"])
        self.filter_mode.setToolTip("Sekunden: passt sich der Messfrequenz an. Messpunkte: feste Anzahl wie LabVIEW.")
        self.filter_mode.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.filter_mode.setMinimumContentsLength(12)
        self.filter_mode.setCurrentIndex(0 if self.filter_seconds else 1)
        self.filter_form.addRow("Filter in", self.filter_mode)
        for group, fields in PARAM_FIELDS.items():
            for key, label, lo, hi, dec in fields:
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setDecimals(dec)
                if dec == 2 and key.endswith("_s"):
                    sp.setSingleStep(0.05)
                sp.setValue(float(getattr(defaults, key)))
                sp.setKeyboardTracking(False)
                if key in PARAM_TIPS:
                    sp.setToolTip(PARAM_TIPS[key])
                if key == "start_anstieg_s":
                    sp.setSingleStep(0.1)
                elif key == "start_anstieg_min":
                    sp.setSingleStep(50)
                self.fields[key] = sp
                ziel = "Verluste" if key in LOSS_KEYS else "Filter" if group.startswith("Filter") else group
                if key == "ratio":                 # Art, Wert und der Knopf „Übersetzung einmessen …“
                    self.ratio_art = QtWidgets.QComboBox()
                    for art, text in UEBERSETZUNG_ARTEN:
                        self.ratio_art.addItem(text, art)
                    self.ratio_art.setToolTip(UEBERSETZUNG_TIP)
                    self.ratio_art.setCurrentIndex(max(0, self.ratio_art.findData(
                        settings.get(UEBERSETZUNG_ART, "hand"))))
                    boxes[ziel].addRow("Übersetzung aus", self.ratio_art)
                    self.ratio_btn = QtWidgets.QPushButton("Übersetzung einmessen …")
                    self.ratio_btn.setToolTip("Übersetzung nKW/nRolle aus Zündung oder rusEFI einmessen "
                                              "(auch unter Messen › Lauf-Einstellungen …)")
                    self.abgleich_btn = QtWidgets.QPushButton("Live abgleichen …")
                    self.abgleich_btn.setToolTip("Übersetzung von Hand live abgleichen: PST-Drehzahl (Rolle × "
                                                 "Übersetzung) mit dem Drehzahlmesser des Motorrads vergleichen")
                    knoepfe = QtWidgets.QHBoxLayout()
                    knoepfe.addWidget(self.ratio_btn)
                    knoepfe.addWidget(self.abgleich_btn)
                    knoepfe.addStretch(1)
                    boxes[ziel].addRow(label, sp)
                    boxes[ziel].addRow("", knoepfe)
                    continue
                boxes[ziel].addRow(label, sp)
        fl = boxes["Lauf"]
        note = QtWidgets.QLabel("Start: Nach GO beginnt der Lauf, sobald die Drehzahl so lange steigt. "
                                "n vom Gas: ab hier wird auf fallende Drehzahl (= Laufende) geprüft, "
                                "muss über der Haltedrehzahl liegen.")
        note.setWordWrap(True)
        theme.rolle(note, "neben")
        fl.addRow(note)
        self.start_nachfrage = QtWidgets.QCheckBox("Vor START nachfragen, ob sich das Setup geändert hat")
        self.start_nachfrage.setToolTip("Ist in SETUP-WERTE (Messen) nichts geändert, zeigt START kurz die Schnellwerte "
                                        "zum Eintragen von Änderungen. Die Zangentaste „Neuer Lauf“ fragt nie. "
                                        "Einstellung „start_nachfrage_setup“.")
        self.start_nachfrage.setChecked(bool(settings.get("start_nachfrage_setup", True)))
        self.start_nachfrage.setStyleSheet("font-weight: 600;")
        fl.addRow(self.start_nachfrage)
        self.nach_lauf_fenster = QtWidgets.QCheckBox("Nach dem Lauf Notiz/Setup-Fenster zeigen")
        self.nach_lauf_fenster.setToolTip("Nach dem Lauf (mit dem Ergebnis) das Fenster „Lauf fertig“: Notiz und "
                                          "Setup-Werte des Laufs nachtragen, ohne Auto-Speichern dort speichern. "
                                          "Nicht modal – START, F1 und die Zangentaste gehen weiter. "
                                          "Einstellung „nach_lauf_fenster“.")
        self.nach_lauf_fenster.setChecked(bool(settings.get("nach_lauf_fenster", True)))
        self.nach_lauf_fenster.setStyleSheet("font-weight: 600;")
        fl.addRow(self.nach_lauf_fenster)
        self.filter_label = QtWidgets.QLabel()
        self.filter_label.setWordWrap(True)
        theme.rolle(self.filter_label, "neben")
        self.filter_form.addRow(self.filter_label)
        self.loss_label = QtWidgets.QLabel()
        self.loss_label.setWordWrap(True)
        theme.rolle(self.loss_label, "neben")
        boxes["Verluste"].addRow(self.loss_label)
        fl = boxes["Klima"]
        self.climate_auto = QtWidgets.QCheckBox("vor jedem Lauf vom Sensor lesen")
        self.climate_auto.setChecked(settings.get("climate_auto", True))
        self.climate_btn = QtWidgets.QPushButton("Klima jetzt lesen")
        self.ka_label = QtWidgets.QLabel()
        fl.addRow(self.climate_auto)
        fl.addRow(self.climate_btn, self.ka_label)
        klima_note = QtWidgets.QLabel("Der Klimasensor (BME280) sitzt auf dem Messboard; seine Adresse (bme_addr) "
                                      "steht unter Verbindung › Geräte-Einstellungen.")
        klima_note.setWordWrap(True)
        theme.rolle(klima_note, "neben")
        fl.addRow(klima_note)

        # Gemisch als λ / AFR: steht im Unterreiter Lambda
        self.box_gemisch = QtWidgets.QGroupBox("Anzeige")
        fl = QtWidgets.QFormLayout(self.box_gemisch)
        self.lambda_radio = QtWidgets.QRadioButton("Lambda λ")
        self.afr_radio = QtWidgets.QRadioButton("AFR")
        (self.lambda_radio if settings.get("lambda", True) else self.afr_radio).setChecked(True)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.lambda_radio)
        hb.addWidget(self.afr_radio)
        hb.addStretch(1)
        fl.addRow("Gemisch als", hb)

        for k in ("temp_c", "p_mbar"):
            self.fields[k].valueChanged.connect(self.update_ka)
        self.update_ka()
        # Herkunft der Verluste: Ausrollversuch, bis jemand M0/M1/n1/m2 von Hand aendert
        self.verlust_quelle = defaults.verlust_quelle
        # Rollen-Kalibrierung (Pruefstand, einstellungen.json "rollen_kalibrierung"): geht als rk_* in params()
        self.rollen_kalib = ausrollen.RollenKalibrierung.aus_dict(settings.get(ausrollen.KALIB_KEY))
        self._loss_guard = False
        for k in LOSS_KEYS:
            self.fields[k].valueChanged.connect(self._loss_edited)
        self._show_loss_source()
        self.filter_mode.currentIndexChanged.connect(lambda i: self.set_filter_seconds(i == 0))
        self.set_filter_seconds(self.filter_seconds)
        self._rate = 60.0

    def _anzeige_gruppe(self, settings: dict) -> QtWidgets.QGroupBox:
        """Gruppe „Anzeige beim Messen“: je Option (messen_page.ANZEIGE_OPTIONEN) ein Kaestchen in anzeige_boxen;
        das Hauptfenster verbindet sie mit dem Menue Ansicht (MessenMixin._anzeige_verbinden)."""
        from .messen_page import ANZEIGE_OPTIONEN
        box = QtWidgets.QGroupBox("Anzeige beim Messen")
        lay = QtWidgets.QVBoxLayout(box)
        self.anzeige_boxen: Dict[str, QtWidgets.QCheckBox] = {}
        for key, (std, text, tip) in ANZEIGE_OPTIONEN.items():
            cb = QtWidgets.QCheckBox(text)
            cb.setToolTip(tip + f". Auch im Menü Ansicht. Einstellung „{key}“.")
            cb.setChecked(bool(settings.get(key, std)))
            lay.addWidget(cb)
            self.anzeige_boxen[key] = cb
        return box

    def _build_verbindung(self, settings: dict):
        """Unterreiter Verbindung: Messboard (links), Prüfen (rechts, setzt das Hauptfenster ein), Datenbank unten."""
        g = self.box_messboard = QtWidgets.QGroupBox("Messboard")
        gl = QtWidgets.QGridLayout(g)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(280)
        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setFixedWidth(32)
        self.refresh_btn.setToolTip("Ports neu suchen")
        self.connect_btn = QtWidgets.QPushButton("Verbinden")
        theme.rolle(self.connect_btn, "primaer")
        self.replay_btn = QtWidgets.QPushButton("Lauf-Datei im Simulator abspielen …")
        self.live_check = QtWidgets.QCheckBox("Live-Anzeige auch ohne Lauf")
        self.live_check.setChecked(settings.get("live", True))
        self.fw_label = QtWidgets.QLabel("nicht verbunden")
        self.fw_label.setWordWrap(True)
        self.fw_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.fw_label.setStyleSheet("font-weight: 600;")
        self.auto_connect = QtWidgets.QCheckBox("Messboard automatisch verbinden (Prüfung alle 5 s)")
        self.auto_connect.setChecked(settings.get("auto_connect", True))
        art = QtWidgets.QLabel("PST Basis (USB oder WLAN), Arduino Mega (PST 3.x), STM32 (F446) oder Simulator")
        art.setWordWrap(True)
        theme.rolle(art, "neben")
        gl.addWidget(art, 0, 0, 1, 4)
        gl.addWidget(QtWidgets.QLabel("Port"), 1, 0)
        gl.addWidget(self.port_combo, 1, 1)
        gl.addWidget(self.refresh_btn, 1, 2)
        gl.addWidget(self.connect_btn, 1, 3)
        gl.addWidget(QtWidgets.QLabel("Zustand"), 2, 0)
        gl.addWidget(self.fw_label, 2, 1, 1, 3)
        gl.addWidget(self.auto_connect, 3, 1, 1, 3)
        gl.addWidget(self.live_check, 4, 1, 1, 3)
        sim = QtWidgets.QLabel("Simulator")
        theme.rolle(sim, "titel")
        gl.addWidget(sim, 5, 0)
        gl.addWidget(self.replay_btn, 5, 1, 1, 3, QtCore.Qt.AlignLeft)
        gl.setColumnStretch(1, 1)
        geraete = QtWidgets.QGroupBox("Geräte-Einstellungen der Basis (PST-Diag 1)")
        self.messboard_lay = QtWidgets.QVBoxLayout(geraete)      # Knopf setzt das Hauptfenster ein
        self.conn_page = QtWidgets.QScrollArea()
        self.conn_page.setWidgetResizable(True)
        conn = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(conn)
        row = QtWidgets.QHBoxLayout()
        links = QtWidgets.QVBoxLayout()
        links.addWidget(g)
        links.addWidget(geraete)
        links.addStretch(1)
        row.addLayout(links, 9)
        self.pruefen_box = QtWidgets.QGroupBox("Prüfen")
        self.pruefen_lay = QtWidgets.QVBoxLayout(self.pruefen_box)
        row.addWidget(self.pruefen_box, 11)
        cl.addLayout(row, 1)
        db = QtWidgets.QHBoxLayout()
        dbt = QtWidgets.QLabel("Datenbank")
        theme.rolle(dbt, "neben")
        self.data_label = QtWidgets.QLabel()
        self.data_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        theme.rolle(self.data_label, "neben")
        db.addWidget(dbt)
        db.addWidget(self.data_label, 1)
        cl.addLayout(db)
        self.conn_page.setWidget(conn)

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
        self.filter_art_geaendert.emit(on)

    def params(self) -> physics.DynoParams:
        d = {k: sp.value() for k, sp in self.fields.items()}
        d["ma"], d["dq"] = int(d["ma"]), int(d["dq"])
        if not self.filter_seconds:
            d["ma_s"] = d["dq_s"] = 0.0
        d["verlust_quelle"] = self.verlust_quelle
        p = physics.DynoParams(**d)
        k = getattr(self, "rollen_kalib", None)
        return k.in_params(p) if k is not None else p

    def set_rollen_kalib(self, k):
        """Neue Rollen-Kalibrierung (ausrollen.RollenKalibrierung oder None) fuer params()."""
        self.rollen_kalib = k if k is not None and k.gueltig else None
        self.losses_changed.emit()

    def set_params(self, p: physics.DynoParams):
        """Filter in Sekunden, wenn p sie hat, sonst Messpunkte (z.B. LabVIEW-Lauf im Simulator)."""
        self._loss_guard = True
        try:
            for k, sp in self.fields.items():
                v = float(getattr(p, k))
                if k in ("ma_s", "dq_s") and v <= 0:
                    continue                                # Sekundenwerte behalten
                sp.setValue(v)
        finally:
            self._loss_guard = False
        self.verlust_quelle = p.verlust_quelle
        self._show_loss_source()
        self.set_filter_seconds(p.ma_s > 0 or p.dq_s > 0)
        self.losses_changed.emit()

    def set_losses(self, m0: float, m1: float, n1: float, m2: float, quelle: str):
        """Verluste eintragen (Ausrollversuch uebernehmen), mit ihrer Herkunft."""
        self._loss_guard = True
        try:
            for k, v in zip(LOSS_KEYS, (m0, m1, n1, m2)):
                self.fields[k].setValue(float(v))
        finally:
            self._loss_guard = False
        self.verlust_quelle = quelle
        self._show_loss_source()
        self.losses_changed.emit()

    def _loss_edited(self, _v=None):
        if not self._loss_guard:
            self.verlust_quelle = ""                    # von Hand geaendert
            self._show_loss_source()
            self.losses_changed.emit()

    def _show_loss_source(self):
        v = {k: self.fields[k].value() for k in LOSS_KEYS}
        if not (v["m0"] or v["m1"] or v["m2"]):
            text = "Keine Verluste: Ergebnis = Radleistung. Verluste aus dem Ausrollversuch (rechts daneben)."
        else:
            text = ("Verluste: " + (self.verlust_quelle or "von Hand eingetragen")
                    + ". Ergebnis = Motorleistung (Rad + Verluste).")
        self.loss_label.setText(text)

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
class MainWindow(MessenMixin, QtWidgets.QMainWindow):
    def __init__(self, db: Optional[Database] = None):
        super().__init__()
        self.setWindowTitle(f"PyST {__version__}")
        self.resize(1500, 920)
        self.settings = load_settings()
        # Tag/Nacht/Auto vor dem Aufbau: die Seiten holen ihre Farben beim Bauen aus dem Thema
        theme.manager().setze(self.settings.get("thema", theme.STANDARD_WAHL), speichern=False)
        theme.verbinde(theme.manager().wahl_geaendert, self, self._thema_gewaehlt)
        self.db = db or Database()
        self.link: Optional[DynoLink] = None
        self.ctrl: Optional[RunController] = None
        self.ausroll_dlg: Optional[AusrollAssistent] = None      # Assistent Rollen-Kalibrierung (offen: kein Lauf)
        self.firmware = ""
        self.cache = RunCache()                    # run_id -> (run, result, channels, params)
        self.last_keepalive = 0.0
        self._done_handled = True
        self._klima_offen = False                  # Phase KLIMA des laufenden Laufs noch nicht uebernommen
        self._plot_due = 0.0
        self._shown_rate = 0.0
        self._cursor_n = 0.0
        self._cursor_inside = False
        self._cursor_names: Optional[Dict[str, tuple]] = None   # Laufnamen am Cursor, _reload_runs leert sie
        self._live_vals: Dict[str, str] = {}

        self.tabs = QtWidgets.QTabWidget()
        self.settings_tabs = QtWidgets.QTabWidget()
        self.band = TastenBand()
        central = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.band)
        cl.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.ratio_mess = tasten.UebersetzungMessung()
        self.page_settings = SettingsPage(self.settings)
        self.page_db = DatabasePage(self.db)
        self.viewer = LogViewer(self.db, self._viewer_channels)
        self.page_rusefi = RusefiPage(self.settings, lambda: save_settings(self.settings))
        self.page_oszi = OsziPage(self.settings, lambda: save_settings(self.settings), lambda: self.link,
                                  lambda: self.page_settings.params(), self.db.runs_dir,
                                  lambda: self.vehicle_combo.currentText() if self.vehicle_combo.currentData() else "")
        self.tabs.addTab(self._build_measure(), "Messen")
        self.tabs.addTab(self.page_db, "Fahrzeuge && Setups")
        self.tabs.addTab(self.viewer, "Auswertung")
        self.tabs.addTab(self.page_oszi, "Oszi / Dauermessung")
        self.page_hw = HardwarePage(self.settings, lambda: save_settings(self.settings), lambda: self.link,
                                    lambda: self.page_settings.params(), self.db.runs_dir)
        self.page_tasten = TastenPage(self.settings, lambda: save_settings(self.settings), lambda: self.link)
        self.page_pruef = PruefPage(lambda: self.link, self.page_hw.pair,
                                    lambda: bool(self.ctrl and self.ctrl.state in ACTIVE) or self.page_oszi.rec is not None)
        self.page_flash = FlashPage(self.settings, lambda: save_settings(self.settings), lambda: self.link,
                                    self.on_connect)
        self.page_kalib = KalibPage(self.settings, lambda: save_settings(self.settings))
        self.page_analog = AnalogPage(self.settings, lambda: save_settings(self.settings), lambda: self.link,
                                      busy=lambda: self.page_flash.busy)
        self.page_analog.geaendert.connect(self._analog_geaendert)
        self._build_settings_tabs()
        self.tabs.addTab(self.settings_tabs, "Einstellungen")
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
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self._esc)   # Ergebnis gross weg, sonst Abbruch
        # am Hauptfenster: Ein Shortcut wirkt nur, solange sein Widget sichtbar ist
        QtGui.QShortcut(QtGui.QKeySequence("F3"), self, activated=self.page_hw.mark,
                        context=QtCore.Qt.ApplicationShortcut)
        self.setAcceptDrops(True)                  # .pyst, Laeufe, Mitschnitte per Drag & Drop oeffnen
        self.page_oszi.saved.connect(self._dauer_saved)
        self.page_hw.log_saved.connect(lambda folder: self._dauer_saved(folder, "Zange-Log"))
        self.page_hw.connect_port.connect(self._connect_port)
        self.page_flash.flash_done.connect(lambda: setattr(self, "_manual_off", False))
        self.page_flash.set_datenordner(os.path.dirname(self.db.path))
        # Statusleiste: drei Felder Basis | Zange/Mega | Lambda/ECU, Kurzmeldungen, Umschalter Tag/Nacht/Auto
        self.statusleiste = Statusleiste()
        self.statusleiste.einbauen(self.statusBar())
        self.statusleiste.pdf_gewuenscht.connect(lambda: self.on_pdf_aktiv())   # Knopf „PDF-Bericht“ unten rechts
        self.statusleiste.speichern_gewuenscht.connect(lambda: self.lauf_speichern())   # daneben „Lauf speichern“
        self._speichern_stand()
        self._zange_state = ""
        self._zange_weitere = 0
        self.page_hw.zange_state.connect(self._zange_state_neu)
        self.page_hw.zange_state.connect(self.page_tasten.zange_zustand)
        self.page_hw.zange_state.connect(self.page_flash.verbindung_geaendert)    # Zange per Funk da/weg
        self.page_hw.zange_text.connect(self._zange_text_neu)
        self.page_hw.lambda_text.connect(lambda *_a: self._status_tick())
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self._status_tick)
        self.status_timer.start(500)
        theme.bei_wechsel(self, self._thema_gewechselt, sofort=False)
        self._warn_box = None
        self._tcp_again = ""                       # Port, an dem nach einem Abriss neu verbunden wird
        self._auth_stop = ""                       # Grund, warum die letzte Anmeldung abgelehnt wurde
        self._fault_todo = [r.id for r in self.db.runs() if r.fehler is None]
        self.fault_bg = Bg()
        self.fault_timer = QtCore.QTimer(self)     # Fehleranteile alter Laeufe nach und nach berechnen
        self.fault_timer.timeout.connect(self._fault_backfill)
        self.fault_timer.start(80)

    # ------------------------------------------------------------------ Aufbau (Messen: messen_page.py)
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
        dar = v.addMenu("Darstellung")
        grp = QtGui.QActionGroup(self)
        self.thema_aktionen: Dict[str, QtGui.QAction] = {}
        for wahl, label in ((theme.TAG, "☀ Tag (hell)"), (theme.NACHT, "☾ Nacht (dunkel)"),
                            (theme.AUTO, "Automatisch (wie das System)")):
            a = dar.addAction(label)
            a.setCheckable(True)
            a.setChecked(theme.manager().wahl == wahl)
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, w=wahl: theme.manager().setze(w))
            self.thema_aktionen[wahl] = a
        self._anzeige_verbinden(v)                 # Anzeige beim Messen: Ergebnis/Anweisungen groß, Legende
        m = self.menuBar().addMenu("Datei")
        self.speichern_aktion = m.addAction("Lauf speichern")         # wie der Knopf unten rechts
        self.speichern_aktion.setShortcut("Ctrl+S")
        self.speichern_aktion.setToolTip("Letzten Lauf speichern (nur ohne Auto-Speichern nötig)")
        self.speichern_aktion.triggered.connect(lambda _c=False: self.lauf_speichern())
        self.speichern_aktion.setEnabled(False)
        m.addSeparator()
        for text, slot, key in (("Öffnen / importieren (.pyst, Läufe, Mitschnitte) …", self.on_import, "Ctrl+O"),
                                ("Läufe importieren …", self.on_import, "Ctrl+I"),
                                ("Ordner importieren (mit Unterordnern) …", lambda: self.on_import_ordner(),
                                 "Ctrl+Shift+I"),
                                ("Läufe weitergeben (.pyst) …", lambda: self.on_share(), "Ctrl+Shift+S"),
                                ("Als HTML-Ansicht exportieren (ohne PyST ansehbar) …", lambda: self.on_share(html=True),
                                 "Ctrl+H"),
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
        s.lambda_radio.toggled.connect(self._lambda_mode_changed)
        for k in ("ma", "dq", "ma_s", "dq_s"):
            s.fields[k].valueChanged.connect(lambda _=None: s.update_filter_label(self._current_rate()))
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
        self.page_kalib.uebernehmen.connect(self._apply_coast)
        self.page_kalib.kalib_speichern.connect(self._rollen_kalib_speichern)
        self.page_kalib.lauf_auswerten.connect(self._coast_from_run)
        self.page_kalib.assistent.connect(self.on_ausroll_assistent)
        s.losses_changed.connect(lambda: self.page_kalib.set_current(s.params()))
        self.page_kalib.set_current(s.params())
        self._verbinde_lauf_einstellungen()       # START/Abbrechen, Lauf-Einstellungen … (messen_page)
        self.viewer.import_dateien.triggered.connect(self.on_import)
        self.viewer.import_ordner.triggered.connect(lambda: self.on_import_ordner())
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
        os.environ.pop("PYST_SCALED", None)
        os.environ.pop("QT_SCALE_FACTOR", None)
        os.execv(sys.executable, [sys.executable, "-m", "pyst"] + [a for a in sys.argv[1:]])

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

    def _cursor_cols(self):
        """Kanaele der frueheren Cursor-Tabelle (Einstellung „cursor_cols“): ECU-Kanaele darin kommen weiter mit ins
        Datenlager des Diagramms."""
        return self.settings.get("cursor_cols", ["lam", "egt1", "egt2"])

    # ---- unteres Diagramm: Belegung links/rechts
    def _lower_candidates(self, avail=None):
        """[(Schluessel, Bezeichnung, Einheit)]: Board-Kanaele und aufgezeichnete ECU-Kanaele."""
        out = [("lam", "Lambda / AFR (Board)", ""), ("egt1", "EGT 1 (Board)", "°C"), ("egt2", "EGT 2 (Board)", "°C")]
        for name, label, unit in (avail if avail is not None else self._ecu_available()):
            out.append((EXTRA_PREFIX + name, f"{label} (ECU)", unit))
        for _g, k in analog.aktive():                     # Analogeingaenge (Einstellungen › Analogeingänge)
            out.append((EXTRA_PREFIX + analog.live_key(k.eingang), analog.kanal_name(k), k.einheit_text()))
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
        labels = {k: (lab.replace(" (Board)", "").replace(analog.SUFFIX, "").replace("Lambda / AFR", "Lambda"), unit)
                  for k, (lab, unit) in keys.items()}
        self.plot.set_lower([k for k in left if k in keys], [k for k in right if k in keys], labels)

    def _toggle_ecu(self, name: str, on: bool, key: str):
        cur = [n for n in self.settings.get(key, []) if n != name]
        if on:
            cur.append(name)
        self.settings[key] = cur
        save_settings(self.settings)
        self._ecu_changed()

    def _ecu_names(self):
        """ECU-Kanaele, die zu jedem Lauf ins Diagramm-Datenlager kommen (Anzeigen, Kurven, Cursor)."""
        lower = [k[len(EXTRA_PREFIX):] for k in sum(self._lower_sides(), []) + self._cursor_cols()
                 if k.startswith(EXTRA_PREFIX)]
        return list(dict.fromkeys(self.settings.get("ecu_plot", []) + lower + ["RPMValue"]))

    def _ecu_extra_run(self, run, res) -> Dict[str, np.ndarray]:
        cols = run.get("columns", {})
        out = {n: cols[COLUMN_PREFIX + n][res.segment] for n in self._ecu_names() if COLUMN_PREFIX + n in cols}
        out.update(self._analog_extra_run(run, res))
        return out

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

    def _ecu_extra_live_alle(self, r) -> Dict[str, np.ndarray]:
        """Zusatzkanaele der laufenden Messung: ECU und Analogeingaenge."""
        out = self._ecu_extra_live(r)
        out.update(self._analog_extra_live(r))
        return out

    def _update_buttons(self):
        busy = self.ctrl is not None and self.ctrl.state in ACTIVE
        self.page_flash.verbindung_geaendert()             # Verbinden/Trennen: Karten der Firmware neu
        connected = self.link is not None
        # im Lauf: Abbrechen bzw. Ausrollen beenden; Assistent Rollen-Kalibrierung offen: kein Lauf
        self.start_btn.setEnabled((connected and self.ausroll_dlg is None) or busy)
        self._start_knopf_stil()
        self.lauf_btn.setEnabled(not busy)
        if busy and self.lauf_popup is not None:
            self.lauf_popup.hide()
        self.page_kalib.lauf_btn.setEnabled(not busy)
        self.page_kalib.assist_btn.setEnabled(not busy)
        self.page_kalib.apply_btn.setEnabled(not busy and self.page_kalib.result is not None
                                             and self.page_kalib.result.ok)
        s = self.page_settings
        s.climate_btn.setEnabled(connected and not busy)
        s.connect_btn.setText("Trennen" if connected else "Verbinden")
        self.connect_main.setText("Trennen" if connected else "Verbinden")
        self.connect_main.setToolTip(f"Messboard: {self.link.port}" if connected else
                                     f"Messboard verbinden ({s.port_combo.currentText()})")
        self.connect_main.setEnabled(not busy)
        for sp in s.fields.values():
            sp.setEnabled(not busy)
        self.vehicle_combo.setEnabled(not busy)
        self.setup_combo.setEnabled(not busy)
        self.setup_zeile.setEnabled(not busy)
        self.b_hinzu.setEnabled(not busy)
        self._speichern_stand()

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
        self._fahrzeug_ansicht_laden()

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
        self._setup_zeile_laden()

    def _vehicle_combo_changed(self, _i):
        self._reload_setup_combo()
        self._fahrzeug_ansicht_laden()
        self._reload_runs()                # gesperrte Plaetze je Fahrzeug

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
            return self.cache.get(rid)
        row = self.db.run(rid)
        if row is None or row.ist_mittelwert:      # Mittelwerte nur ueber mittelwert.laden (MessenMixin._mittelwert)
            return None
        try:
            run = wait_in_thread(lambda: storage.load_any(row.path))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Lauf laden", f"{row.path}:\n{exc}")
            return None
        p = physics.DynoParams(**row.params) if row.params else run["params"]

        def rechnen():
            res = None
            if run.get("kind") != "dauermessung" and len(run["dt"]):
                res = physics.evaluate(run["n_roll"], run["dt"], p, run["n_meas"], run["afr"], run["egt"],
                                       require_end=False)
            return res, run_channels(run, res, p), run_faults(run)[1] if row.fehler is None else None

        res, ch, fehler = wait_in_thread(rechnen)
        # Rohspalten braucht danach nur noch die ECU-Anzeige (_ecu_extra_run)
        run["columns"] = {k: v for k, v in run.get("columns", {}).items()
                          if k.startswith(COLUMN_PREFIX) or k.startswith(analog.SPALTE)}
        entry = (run, res, ch, p)
        self.cache.put(rid, entry)
        if fehler is not None:
            self.db.update_run(rid, fehler=fehler)
        return entry

    def _viewer_channels(self, rid):
        """(Kanaele, Kurvenbereich) fuer den Auswerter"""
        e = self._entry(rid)
        if e is None:
            return None
        run, res, ch, _p = e
        # Marker + Ereignisse der Zange/Steckdosen (PyST-Basis) als Linien im Auswerter
        extra = [(t, f"{kind}: {text}") for t, kind, text in run.get("events", []) if kind in ("Zange", "Steckdose")]
        return ch, (res.segment if res is not None else None), list(run.get("markers", [])) + extra

    def _run_label(self, row) -> str:
        d = row.datum.replace("T", " ")[:16]
        if len(d) == 16 and d[4] == "-":                   # 2026-09-30 12:53 -> 30.09.26 12:53
            d = f"{d[8:10]}.{d[5:7]}.{d[2:4]} {d[11:]}"
        return f"{d}  {row.name}" if row.quelle == "LabVIEW" else d

    def _open_in_viewer(self, rid):
        if rid is None:
            return
        self.tabs.setCurrentWidget(self.viewer)
        self.viewer.show_run(rid)

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

    # ------------------------------------------------------------------ Verbindung
    def on_connect(self, port: str = "", quiet: bool = False, lost: bool = False) -> bool:
        """Verbinden/Trennen. port: statt der Auswahl in den Einstellungen; quiet: ohne Fehlermeldung
        (automatisches Verbinden); lost: Messboard wurde abgezogen (danach wieder automatisch verbinden)."""
        s = self.page_settings
        if self.link:
            if self.ctrl and self.ctrl.state in ACTIVE:
                self.ctrl.abort()
            try:
                self.link.close()
            except Exception:
                pass
            self.link = self.ctrl = None
            self._manual_off = not lost                    # von Hand getrennt: nicht sofort wieder verbinden
            self._tcp_again = ""
            s.fw_label.setText("nicht verbunden")
            self._set_status("Messboard getrennt (abgezogen?)" if lost else "Nicht verbunden – „Verbinden“ drücken",
                             farben.BALKEN_FEHLER if lost else farben.BALKEN_NORMAL)
            self._update_buttons()
            return False
        self._manual_off = False
        self._tcp_again = ""
        self._auth_stop = ""
        port = port or s.port_combo.currentText().strip()
        if port != s.port_combo.currentText().strip():
            s.port_combo.setEditText(port)
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.link, self.firmware = wait_in_thread(lambda: self._open_link(port))
        except Exception as exc:
            if isinstance(exc, AuthError):
                if not isinstance(exc, AuthNoReply):
                    self._auth_stop = str(exc)
                self._set_status("Anmeldung an der Basis gescheitert", farben.BALKEN_FEHLER)
                s.fw_label.setText(f"{port}: nicht angemeldet")
                if not quiet:
                    QtWidgets.QMessageBox.critical(self, "Anmeldung an der Basis", str(exc))
            elif not quiet:
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
        if self.link.bootloader:
            s.fw_label.setText(f"{port}: Basis im Bootloader, Firmware unter Einstellungen › Software & Firmware "
                               "aufspielen")
            s.fw_label.setToolTip(self.link.bootloader)
        self.ctrl = RunController(self.link, s.params(), auto_climate=s.climate_auto.isChecked(),
                                  ecu=self.page_rusefi.active_link())
        self._set_status("Verbunden – START drücken (F1)", farben.BALKEN_NORMAL)
        if self.link.default_mode != "N":
            self.link.set_mode(self.link.default_mode)       # Basis mit Zange: Zuendstaerke im D-Telegramm
        if self.link.is_basis:
            s.fw_label.setText(f"{port}: PyST-Basis – {self.link.caps.get('fw', '')} · Zündung: "
                               + ("Funk-Zange" if self.link.ign_source == "zange" else "keine (Zange koppeln)"))
            self._basis_key()
        if self.link.is_zange_usb:
            s.fw_label.setText(f"{port}: Zange per USB – {self.link.caps.get('fw', '')}")
            self._set_status("Zange per USB – nur Auslesen/Testen", farben.BALKEN_FEHLER)
            self._warn_box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, "Zange per USB",
                                                   ZANGE_USB_WARNUNG, QtWidgets.QMessageBox.Ok, self)
            self._warn_box.open()                          # nicht blockierend
        if s.live_check.isChecked():
            self.link.start()
        self._persist()
        self._update_buttons()
        s.update_filter_label(self._current_rate())
        return True

    @staticmethod
    def _open_link(port: str):
        """Im Thread: mDNS, TCP und 'v' dauern Sekunden. -> (Verbindung, Antwort auf 'v')"""
        link = DynoLink(port)
        try:
            return link, link.info()
        except Exception:
            try:
                link.close()
            except Exception:
                pass
            raise

    def _tcp_reconnect(self):
        """Nach einem Abriss neu verbinden, die Anmeldung gilt je Verbindung. Eine abgelehnte Anmeldung wird nicht
        wiederholt: Jeder Fehlversuch verlaengert die Sperre der Basis."""
        port = self._tcp_again
        if self.on_connect(port, quiet=True):
            self._set_status("Wieder mit der Basis verbunden und angemeldet", farben.BALKEN_NORMAL)
        elif self._auth_stop:
            QtWidgets.QMessageBox.critical(self, "Anmeldung an der Basis", self._auth_stop)
        else:
            self._tcp_again = port
            self._set_status("Verbindung zur Basis weg, PyST verbindet neu …", farben.BALKEN_FEHLER)

    def _basis_key(self):
        """Per USB ist das Koppeln: Schlüssel für die Anmeldung per WLAN holen. Per TCP den Zustand anzeigen."""
        if self.link.auth_state == "alt":
            self.statusBar().showMessage("Diese Basis-Firmware kennt keine Anmeldung: WLAN-Port ungeschützt.", 10000)
        if self.link.needs_login:
            return
        try:
            neu = self.link.fetch_key()
        except AuthError as exc:
            self.statusBar().showMessage(str(exc), 10000)
            return
        except OSError as exc:
            self.statusBar().showMessage(f"Schlüssel der Basis nicht gespeichert: {exc.strerror or exc}", 10000)
            return
        if neu:
            self.statusBar().showMessage("Schlüssel der Basis gespeichert: Anmeldung per WLAN möglich.", 6000)

    def _auto_connect(self):
        """Alle 5 s: abgezogenes Messboard erkennen, angestecktes automatisch verbinden."""
        if self.page_flash.busy or waiting():              # Port gehoert gerade dem Flashen oder einer Abfrage
            return
        if self.link is not None:
            if self.link.needs_login:
                if not self.link.alive:                    # WLAN-Verbindung abgerissen, z.B. Neustart der Basis
                    port = self.link.port
                    self.on_connect(lost=True)
                    self._tcp_again = port
                    self._set_status("Verbindung zur Basis weg, PyST verbindet neu …", farben.BALKEN_FEHLER)
            elif not self.link.port.startswith(SIM_PORT) and self.link.port not in {d for d, _ in list_serial_ports()}:
                self.on_connect(lost=True)
            return
        if self._tcp_again:
            self._tcp_reconnect()
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
                self._set_status("Messboard automatisch verbunden – START drücken (F1)", farben.BALKEN_NORMAL)
                return
            # anderes Geraet (oder alte Firmware ohne Kennung): wieder trennen, bis zum Abziehen in Ruhe lassen
            self.on_connect(lost=True)
            self._set_status("Nicht verbunden – „Verbinden“ drücken", farben.BALKEN_NORMAL)
            self._auto_failed[port] = time.time()
        if self.link is None:
            zp = zange_usb_ports()
            if zp:                                         # Zange per USB nie automatisch (galvanische Verbindung)
                self.statusBar().showMessage(f"Zange per USB an {zp[0]}? Zum Auslesen unter Einstellungen › Verbindung "
                                             f"diesen Port wählen und verbinden.", 6000)

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
        c = wait_in_thread(self.link.climate)
        if c:
            self.page_settings.fields["temp_c"].setValue(c[0])
            self.page_settings.fields["p_mbar"].setValue(c[1])
            self._klima_gelesen(c)
            feuchte = f"   {c[2]:.0f} % rel. Feuchte" if c[2] > 0 else ""       # BMP280: keine Feuchte
            self.result_label.setText(f"Klima: {c[0]:.1f} °C   {c[1]:.1f} mbar{feuchte}")
        else:
            self.result_label.setText("Keine Klimadaten vom Sensor")
        if self.page_settings.live_check.isChecked():
            self.link.start()

    def on_measure_ratio(self):
        if self.ctrl and self.ctrl.state in ACTIVE:
            return

        def apply(r: float):
            self.page_settings.fields["ratio"].setValue(r)
            self._persist()
            self._set_status(f"Übersetzung = {r:.3f}", "#b8e6b8")

        dlg = RatioDialog(self, self.ctrl, self.page_rusefi, self.page_settings.params, apply,
                          source=self.settings.get("ratio_source", "board"),
                          current=self.page_settings.fields["ratio"].value())
        dlg.finished.connect(lambda _r: (self.settings.update(ratio_source=dlg.source()),
                                         save_settings(self.settings)))
        dlg.exec()

    # ------------------------------------------------------------------ Lauf
    def on_start(self, *_a, nachfrage: bool = True):
        """START (F1). nachfrage=False (Taste „Neuer Lauf“ der Zange): nicht nach dem Setup fragen
        (MessenMixin._setup_vor_start), geaenderte Werte gelten fuer diesen Lauf."""
        if not self.ctrl or self.ctrl.state in ACTIVE:
            return
        if self.ausroll_dlg is not None:
            self._set_status("Rollen-Kalibrierung (Assistent) offen – erst schließen, dann START", farben.BALKEN_FEHLER)
            return
        s = self.page_settings
        p = s.params()
        msg = physics.check_params(p)
        if msg:
            QtWidgets.QMessageBox.warning(self, "Einstellungen", msg)
            return
        self._lauf_fertig_schliessen(start=True)          # Fenster „Lauf fertig“ zu (Auto-Speichern: Eingaben gelten)
        if nachfrage and not self._setup_vor_start():
            return
        self._start_hinweis_ungespeichert(zange=not nachfrage)    # alter Lauf ungespeichert: kurz im Band
        self._persist()
        self.tabs.setCurrentIndex(0)
        self._gross_verbergen()
        self.ctrl.params = p
        self.ctrl.auto_climate = s.climate_auto.isChecked()
        self.ctrl.ausrollen = self.page_kalib.enabled()
        self.ctrl.coast_n_ende, self.ctrl.coast_t_max, self.ctrl.coast_quadratisch = self.page_kalib.config()
        self.ctrl.ecu = self.page_rusefi.active_link()
        self.ctrl.ratio_aus_funken = (s.ratio_art.currentData() or "hand") == "funken"
        self.plot.remove(LIVE_KEY)
        self.plot.freeze(True)                     # Skalierung wie beim vorherigen Lauf
        self.result_label.setText("")
        wait_in_thread(self.ctrl.start)                    # ohne Klima: Messung starten; mit: Phase KLIMA
        self._klima_offen = self.ctrl.state == KLIMA
        self.result_label.setText(self.ctrl.message)
        self._done_handled = False
        self._laufzustand_zeigen(self.ctrl.state, self.ctrl.live)    # „Klima wird gemessen …“ bzw. GO sofort
        self._update_buttons()

    def _klima_nach_start(self):
        """Phase KLIMA vorbei: frischen Sensorwert eintragen (oder Hinweis, dass der eingetragene Wert gilt)."""
        self._klima_offen = False
        c = self.ctrl.climate
        if c:
            s = self.page_settings
            s.fields["temp_c"].setValue(self.ctrl.params.temp_c)
            s.fields["p_mbar"].setValue(self.ctrl.params.p_mbar)
            self._klima_gelesen(c)
        elif self.ctrl.state == WAIT:
            self.statusBar().showMessage(self.ctrl.klima_text(), 8000)
        self.result_label.setText(self.ctrl.message)

    def on_abort(self):
        if self.ctrl and self.ctrl.state == COAST:
            self.ctrl.end_coast()                          # Lauf ist fertig: nur das Ausrollen beenden
        elif self.ctrl and self.ctrl.state in (KLIMA, WAIT, RUN):
            self.ctrl.abort()
            if self.page_settings.live_check.isChecked():
                self.link.start()
        self._update_buttons()

    def _finish_run(self):
        r = self.ctrl.result
        self.plot.freeze(False)
        coast = self.ctrl.coast
        if coast is None:
            self._laufende_gas_weg()                       # ohne Ausrollversuch: GAS WEG groß, dann das Ergebnis
        ratio_text = self._ratio_aus_funken_uebernehmen()
        if r is None:
            self._set_status("Fertig – keine Auswertung möglich", farben.BALKEN_FEHLER)
            if coast is not None:
                self.page_kalib.show_result(coast, self._coast_label(None))
            return
        klima = self.ctrl.klima_text()
        self.result_label.setText(r.summary() + f"\nEnde: {self.ctrl.message or r.end_reason}   |   "
                                  f"{len(self.ctrl.frames)} Telegramme, {self.link.lost} verloren"
                                  + (f"   |   {klima}" if klima else "")
                                  + (f"\n{ratio_text}" if ratio_text else "")
                                  + (f"\n{self.ctrl.coast_text()}" if coast is not None else ""))
        if r.verluste:
            self._set_status(f"Motor {r.p_max:.1f} PS bei {r.n_pmax:.0f} 1/min  ·  Rad {r.p_rad_max:.1f} PS",
                             "#b8e6b8")
        else:
            self._set_status(f"{r.p_max:.1f} PS bei {r.n_pmax:.0f} 1/min", "#b8e6b8")
        self._lauf_merken(self.ctrl)                      # Stand fuer „Lauf fertig“ und „Lauf speichern“
        rid = None
        if self.autosave.isChecked():
            rid = self.save_current_run()
        else:
            self._draw_live(final=True)
            self.plot.auto_scale()
            self._legende_zeigen()
        self._nachlauf_faellig = True                     # Fenster „Lauf fertig“ kommt mit dem Ergebnis
        self._ergebnis_gross_zeigen(r, rid, warten=coast is None)   # Option „Ergebnis groß nach dem Lauf“
        if coast is not None:
            self.page_kalib.show_result(coast, self._coast_label(rid), rid, n_pmax=r.n_pmax)
            self.statusBar().showMessage(self.ctrl.coast_text(), 10000)
            auf = self.page_kalib.aufteilung()
            if not coast.ok:
                band = f"Ausrollversuch: {coast.meldung}"
            elif auf is not None:
                band = f"Ausrollversuch: {auf.kurz(r.n_pmax)}"
            else:
                band = "Ausrollversuch ausgewertet – unter Einstellungen › Rolle/Lauf › Verluste prüfen und übernehmen"
            self.band.zeige(band, farben.GUT if coast.ok else farben.FEHLER, 8.0)

    def _ratio_aus_funken_uebernehmen(self) -> str:
        """Uebersetzung aus den Funken (Einstellung „uebersetzung_art“): gemessenen Wert eintragen. -> Hinweistext
        fuer die Ergebniszeile ("" bei Uebersetzung von Hand)."""
        c = self.ctrl
        if not getattr(c, "ratio_aus_funken", False):
            return ""
        if c.ratio_gemessen is None:
            text = (f"Übersetzung aus den Funken nicht bestimmbar (kein Zündsignal?) – eingestellte "
                    f"{c.params.ratio:.3f} verwendet")
            self.statusBar().showMessage(text, 10000)
            return text
        alt = self.page_settings.fields["ratio"].value()
        self.page_settings.fields["ratio"].setValue(c.ratio_gemessen)
        self._persist()
        return f"Übersetzung aus den Funken: {c.ratio_gemessen:.3f} (vorher {alt:.3f}) – übernommen"

    # ------------------------------------------------------------------ Ausrollversuch
    def _coast_label(self, rid) -> str:
        """Woher der Ausrollversuch kommt (Lauf, Fahrzeug/Setup)."""
        row = self.db.run(rid) if rid else None
        veh = self.vehicle_combo.currentText() if self.vehicle_combo.currentData() else ""
        setup = self.setup_combo.currentText().replace(GEAENDERT, "") if self.setup_combo.currentData() else ""
        if row is not None:
            vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
            veh = vehicles.get(row.vehicle_id, "")
            s = self.db.setup(row.setup_id) if row.setup_id else None
            setup = s["name"] if s else ""
        lauf = f"Lauf {self._run_label(row)}" if row is not None else "aktueller Lauf (nicht gespeichert)"
        return " · ".join(x for x in (lauf, veh, setup) if x)

    def _coast_from_run(self):
        """Ausrollphase des markierten Laufs auswerten (Reiter Messen, sonst Auswertung)."""
        rid = self._current_run_id() or self.viewer.current_run_id()
        if rid is None:
            QtWidgets.QMessageBox.information(self, "Ausrollversuch", "Bitte zuerst in der Laufliste (Reiter "
                                              "Messen) einen Lauf markieren.")
            return
        e = self._entry(rid)
        if e is None:
            return
        run, res, _ch, p = e
        start = res.end_index if res is not None and res.end_index >= 0 else None
        n_ende, _t_max, quad = self.page_kalib.config()
        c = wait_in_thread(lambda: ausrollen.analyse(run["n_roll"], run["dt"], p, start=start, n_meas=run["n_meas"],
                                                     n_ende=n_ende, quadratisch=quad))
        self.page_kalib.show_result(c, self._coast_label(rid), rid, n_pmax=res.n_pmax if res is not None else None)

    def on_ausroll_assistent(self):
        """Assistent Rollen-Kalibrierung (Rad abgehoben, Rolle allein; nicht modal, schon offen: nach vorn). Nicht
        waehrend eines Laufs; solange er offen ist, startet kein Lauf."""
        if self._lauf_aktiv():
            self.statusBar().showMessage("Rollen-Kalibrierung: erst nach dem Lauf", 5000)
            return
        d = self.ausroll_dlg
        if d is not None:
            d.raise_()
            d.activateWindow()
            return
        d = self.ausroll_dlg = AusrollAssistent(
            self, lambda: self.ctrl, self.page_settings.params, self.settings.setdefault("ausrollen", {}),
            lambda: save_settings(self.settings), self.db.runs_dir, self._assistent_verbinden, self._lauf_aktiv)
        d.kalib_speichern.connect(self._rollen_kalib_speichern)
        d.ausgewertet.connect(lambda c, text: self.page_kalib.show_result(c, text))

        def fertig(_r, d=d):
            if self.ausroll_dlg is d:
                self.ausroll_dlg = None
                self._update_buttons()
            d.deleteLater()

        d.finished.connect(fertig)
        self._update_buttons()
        d.show()
        d.raise_()

    def _assistent_verbinden(self) -> bool:
        """„Verbinden“ im Assistenten: Messboard wie mit dem Knopf verbinden, Daten anstossen."""
        if self.link is None:
            self.on_connect()
        if self.link is not None and not self.link.streaming():
            self.link.start()
        return self.link is not None

    def _rollen_kalib_speichern(self, c):
        """„Als Rollen-Kalibrierung speichern“ (Assistent bzw. Gruppe „Letzter Ausrollversuch“): Verluste der Rolle
        allein als Pruefstands-Kalibrierung ablegen (einstellungen.json), nicht in M0/M1. Gilt ab jetzt fuer neue
        Laeufe (Reifen an Rolle) und die Aufteilung der Ausrollversuche."""
        if c is None or not c.ok or not c.rolle_allein:
            return
        k = ausrollen.RollenKalibrierung.aus_ergebnis(c)
        self.settings[ausrollen.KALIB_KEY] = k.als_dict()
        self.page_settings.set_rollen_kalib(k)
        self.page_kalib.set_kalibrierung(k)
        self._persist()
        save_settings(self.settings)
        text = "Rollen-Kalibrierung gespeichert: " + k.kurztext(self.page_settings.params().ratio)
        self._set_status(text, "#b8e6b8")
        self.statusBar().showMessage(text, 8000)

    def _apply_coast(self, c, rid):
        """„Übernehmen“: Verluste eintragen, speichern und den Lauf des Ausrollversuchs damit neu rechnen. Ein
        Ergebnis mit abgehobenem Rad gehoert nicht in M0/M1: es wird Rollen-Kalibrierung."""
        if c is not None and c.rolle_allein:
            self._rollen_kalib_speichern(c)
            return
        s = self.page_settings
        s.set_losses(c.m0, c.m1, c.n1, c.m2, c.quelle())
        self._persist()
        self.settings.setdefault("ausrollen", {})["uebernommen"] = c.als_dict()
        save_settings(self.settings)
        text = f"Verluste übernommen: M0 {c.m0:.2f} / M1 {c.m1:.2f} Nm bei {c.n1:.0f} 1/min"
        if rid is not None and self.db.run(rid) is not None:
            e = self._entry(rid)
            if e is not None:
                q = c.params_fuer(e[3])
                if s.rollen_kalib is not None:             # Pruefstand ist kalibriert: auch fuer diesen Lauf
                    q = s.rollen_kalib.in_params(q)
                self.db.update_run(rid, params=asdict(q))
                self.cache.pop(rid, None)
                self.viewer.invalidate(rid)
                e = self._entry(rid)
                if e and e[1]:
                    r = e[1]
                    self.db.update_run(rid, p_max=r.p_max, n_pmax=r.n_pmax, m_max=r.m_max, n_mmax=r.n_mmax, ka=r.ka)
                    text += f" – Lauf neu gerechnet: Motor {r.p_max:.1f} PS, Rad {r.p_rad_max:.1f} PS"
                self._reload_runs()
        self._set_status(text, "#b8e6b8")
        self.statusBar().showMessage(text, 8000)

    def save_current_run(self, stand: Optional[dict] = None) -> Optional[int]:
        """Letzten Lauf speichern (Auto-Speichern, „Lauf speichern“, Fenster „Lauf fertig“). stand: Fahrzeug, Setup,
        Aenderungen und Notiz (messen_page._lauf_stand_jetzt); ohne den beim Laufende festgehaltenen bzw. den von jetzt."""
        ctrl = self._lauf_ctrl or self.ctrl
        if not ctrl or not ctrl.result:
            return None
        stand = stand or self._lauf_stand or self._lauf_stand_jetzt()
        vid, sid, vname = stand["vid"], stand["sid"], stand["vname"]
        folder = storage.save_run(ctrl, vehicle=vname, firmware=self.firmware, base_dir=self.db.runs_dir())
        r = ctrl.result
        werte, nur_lauf = self._setup_schnappschuss(stand)
        rid = self.db.add_run(folder, "lauf.json", "PyST", os.path.basename(folder),
                              QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate), r,
                              params=asdict(ctrl.params), rate=self._current_rate(), vehicle_id=vid,
                              setup_id=sid, notiz=stand["notiz"], farbe="", sichtbar=True,
                              setup_werte=werte, nur_lauf=nur_lauf)
        if ctrl is self._lauf_ctrl:
            self._lauf_rid = rid
            self._lauf_stand = stand
        self.plot.remove(LIVE_KEY)
        self._reload_runs()
        self.plot.auto_scale()
        self.result_label.setText(self.result_label.text() + "   |   gespeichert ✓")
        self.result_label.setToolTip(folder)
        self._speichern_stand()
        return rid

    def _draw_live(self, final: bool = False, res=None):
        r = res or (self.ctrl.result if final else self.ctrl.live_result)
        if r is None:
            return
        egt2 = np.array([f.egt2 for f in self.ctrl.frames])[r.segment] if self.ctrl.frames else None
        # live: die letzten dq Punkte sind noch nicht endgueltig (Differenzenquotient braucht Punkte
        # "aus der Zukunft") -> weglassen, statt einen falschen Knick zu zeigen
        k = len(r.n) if final else max(0, len(r.n) - int(r.dq_used or self.ctrl.params.dq))
        if k < 2:
            return
        cut = lambda a: None if a is None else a[:k]
        extra = {n: cut(v) for n, v in self._ecu_extra_live_alle(r).items()}
        self.plot.set_curves(LIVE_KEY, cut(r.n), cut(r.ps), cut(r.nm), cut(lambda_from_afr(r.afr)), cut(r.egt),
                             cut(egt2), color="#000000", width=3.0, rescale=False, extra=extra)
        self._update_max_display()

    # ------------------------------------------------------------------ Datenverwaltung
    def on_import(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Öffnen / importieren", os.path.expanduser("~"),
            "Alle unterstützten (*.pyst *.xml *.json *.log *.txt *.csv);;PyST-Dateien (*.pyst);;"
            "LabVIEW-XML / PyST lauf.json (*.xml *.json);;Rohmitschnitt Terminal (*.log *.txt *.csv);;Alle Dateien (*)")
        if files:
            self.open_files(files)

    def on_import_ordner(self, ordner: str = ""):
        """Ordner mit Unterordnern nach alten Messungen durchsuchen, Vorschau, importieren (ordner_import)."""
        return ordner_import.ordner_importieren(self, ordner)

    def open_files(self, files, show: bool = True) -> list:
        """Dateien uebernehmen (.pyst, LabVIEW-XML, PyST-Ordner/lauf.json, Terminal-Mitschnitt).
        show=True: die Laeufe anhaken und in der Auswertung zeigen. Liefert die Lauf-IDs."""
        vid, sid = self.vehicle_combo.currentData(), self.setup_combo.currentData()
        logs = [f for f in files if storage.is_terminal_log(f)]
        params = None
        if logs:
            dlg = ImportParamsDialog(logs, self.page_settings.params(), self)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                files = [f for f in files if f not in logs]
            else:
                params = dlg.params()
        ids, errors = [], []
        for f in files:
            try:
                if f.lower().endswith(storage.PYST_EXT):
                    ids += self.db.import_pyst(f, vehicle_id=vid, setup_id=sid)
                else:
                    ids.append(self.db.import_file(f, vehicle_id=vid, setup_id=sid,
                                                   params=params if f in logs else None))
            except Exception as exc:
                errors.append(f"{os.path.basename(f)}: {exc}")
        if show:
            for rid in ids:
                self.db.update_run(rid, sichtbar=True)
        self._reload_runs()
        if show and ids:
            self._open_in_viewer(ids[0])
            for rid in ids[1:]:
                self.viewer.show_run(rid)
        msg = f"{len(ids)} Lauf/Läufe übernommen" + (f" (Fahrzeug: {self.vehicle_combo.currentText()})" if vid else "")
        if logs and params is not None:
            msg += "\nMitschnitte: Prüfstandsdaten wie angegeben; Klima aus dem Mitschnitt, falls vorhanden."
        if errors:
            msg += "\n\nFehler:\n" + "\n".join(errors)
        if errors or not show:
            QtWidgets.QMessageBox.information(self, "Import", msg)
        else:
            self.statusBar().showMessage(msg, 8000)
        return ids

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        files = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if files:
            self.open_files(files)

    # ------------------------------------------------------------------ Weitergeben
    def _share_rows(self, scope: str, day: str = ""):
        rows = self.db.runs()
        if scope == "markiert":
            ids = {it.data(0, QtCore.Qt.UserRole) for it in self.run_tree.selectedItems()}
            rows = [r for r in rows if r.id in ids]
        elif scope == "sichtbar":
            rows = [r for r in rows if r.sichtbar]
        elif scope == "fahrzeug":
            rows = [r for r in rows if r.vehicle_id == self.vehicle_combo.currentData()]
        else:
            rows = [r for r in rows if r.datum[:10] == day]
        return sorted(rows, key=lambda r: r.datum)

    def _share_item(self, row) -> dict:
        veh = self.db.vehicle(row.vehicle_id) if row.vehicle_id else None
        setup = self.db.setup(row.setup_id) if row.setup_id else None
        return {"ordner": row.ordner, "name": row.name, "datum": row.datum, "quelle": row.quelle,
                "art": row.art, "setup_werte": row.setup_werte, "nur_lauf": row.nur_lauf,
                "fahrzeug": {k: v for k, v in (veh or {}).items() if k != "id"},
                "setup": {k: v for k, v in (setup or {}).items() if k not in ("id", "vehicle_id")},
                "notiz": row.notiz, "farbe": row.farbe, "parameter": row.params, "fehler": row.fehler,
                "ergebnis": {"p_max_ps": row.p_max, "n_pmax": row.n_pmax, "m_max_nm": row.m_max, "n_mmax": row.n_mmax}}

    def on_share(self, html: bool = False, path: str = "", scope: str = "", day: str = "", fmt: int = -1):
        """Laeufe an Kunden weitergeben: .pyst (eine Datei oder je Lauf) oder HTML-Ansicht ohne PyST."""
        rows_all = self.db.runs()
        days: Dict[str, int] = {}
        for r in rows_all:
            days[r.datum[:10]] = days.get(r.datum[:10], 0) + 1
        vid = self.vehicle_combo.currentData()
        if not scope:
            dlg = ExportDialog(len(self.run_tree.selectedItems()), sum(r.sichtbar for r in rows_all),
                               self.vehicle_combo.currentText() if vid else "",
                               sum(r.vehicle_id == vid for r in rows_all) if vid else 0, days, self, html=html)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return None
            scope, day, fmt = dlg.scope(), dlg.day.currentData() or "", dlg.fmt()
        rows = self._share_rows(scope, day)
        if not rows:
            QtWidgets.QMessageBox.information(self, "Weitergeben", "Keine Läufe ausgewählt.")
            return None
        title = self.vehicle_combo.currentText() if vid else (self.db.vehicle(rows[0].vehicle_id) or {}).get(
            "hersteller", "") if rows[0].vehicle_id else "Leistungsmessung"
        if rows[0].vehicle_id:
            v = self.db.vehicle(rows[0].vehicle_id)
            title = self.db.vehicle_name(v) if v else title
        title = title or "Leistungsmessung"
        stem = f"{title} {rows[0].datum[:10]}".replace("/", "_").replace(" ", "_")
        home = os.path.expanduser("~")
        if fmt == FORMAT_HTML:
            if not path:
                path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "HTML-Ansicht", os.path.join(home, stem + ".html"),
                                                                "HTML (*.html)")
            if not path:
                return None
            size = self.export_html(path, rows, title)
            self.statusBar().showMessage(f"HTML-Ansicht gespeichert: {path} ({size / 1024:.0f} KB)", 10000)
            return path
        items = [self._share_item(r) for r in rows]
        if fmt == FORMAT_FOLDER:
            if not path:
                path = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner für die .pyst-Dateien", home)
            if not path:
                return None
            for it in items:
                storage.export_pyst(os.path.join(path, it["name"] + storage.PYST_EXT), [it])
            self.statusBar().showMessage(f"{len(items)} .pyst-Dateien in {path}", 10000)
            return path
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Läufe weitergeben",
                                                            os.path.join(home, stem + storage.PYST_EXT),
                                                            "PyST-Datei (*.pyst)")
        if not path:
            return None
        path = storage.export_pyst(path, items)
        self.statusBar().showMessage(f"{len(items)} Lauf/Läufe gespeichert: {path} "
                                     f"({os.path.getsize(path) / 1024:.0f} KB)", 10000)
        return path

    def export_html(self, path: str, rows, title: str) -> int:
        payload = []
        setups = {}
        for r in rows:
            e = self._entry(r.id)
            if e is None:
                continue
            run, res, ch, p = e
            if r.setup_id and r.setup_id not in setups:
                setups[r.setup_id] = (self.db.setup(r.setup_id) or {}).get("name", "")
            veh = self.db.vehicle(r.vehicle_id) if r.vehicle_id else None
            label = self._run_label(r) + (f" – {r.p_max:.1f} PS" if res is not None else " – Dauermessung")
            payload.append(htmlexport.run_payload(
                label, run, res, ch, p, vehicle=self.db.vehicle_name(veh) if veh else "",
                setup=setups.get(r.setup_id, ""), notiz=r.notiz, datum=self._run_label(r).split("  ")[0],
                fehler=r.fehler))
        return htmlexport.export_html(path, payload, title)

    def _fault_cell(self, row) -> str:
        if not row.fehler:
            return "" if row.fehler is None else "–"
        worst = max(row.fehler.values())
        return "–" if worst < 0.05 else f"{worst:.0f} %" if worst >= 10 else f"{worst:.1f} %".replace(".", ",")

    def _fault_backfill(self):
        """Fehleranteile fuer Laeufe ohne Eintrag (aeltere PyST-Versionen) im Hintergrund nachtragen."""
        self.fault_bg.poll()
        if self.fault_bg.pending:
            return
        if not self._fault_todo:
            self.fault_timer.stop()
            return
        rid = self._fault_todo.pop(0)
        row = self.db.run(rid)
        if row is None or row.fehler is not None:
            return
        self.fault_bg.submit("fehler", lambda: run_faults(storage.load_any(row.path))[1],
                             lambda res, err: self._fault_done(rid, res, err))

    def _fault_done(self, rid: int, fehler, err):
        self.db.update_run(rid, fehler={} if err is not None else fehler)
        if not self._fault_todo:
            self._reload_runs()

    def _connect_port(self, port: str):
        """Mit einem anderen Port verbinden (z.B. Basis per WLAN)."""
        if self.link:
            self.on_connect()
        self.page_settings.port_combo.setEditText(port)
        self.on_connect()

    def _dauer_saved(self, folder: str, note: str = "Dauermessung"):
        try:
            rid = self.db.add_folder(folder, self.vehicle_combo.currentData(), self.setup_combo.currentData())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Dauermessung", f"{folder}:\n{exc}")
            return
        self.db.update_run(rid, notiz=note)
        self._reload_runs()
        self.statusBar().showMessage(f"Dauermessung gespeichert: {folder} – in der Laufliste und Auswertung", 10000)

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
        """Tabellenzeilen des PDF-Berichts: alles, was im Messen-Diagramm steht (bericht.messen_zeilen)."""
        return bericht.messen_zeilen(self)

    def on_pdf(self, path: str = "", quelle: str = bericht.MESSEN):
        """PDF-Bericht vom Messen-Diagramm (Datei-Menue, Knopf der Auswertung) bzw. quelle="auswertung"."""
        return bericht.erzeugen(self, path, quelle)

    def on_pdf_aktiv(self, path: str = ""):
        """Knopf „PDF-Bericht“ in der Statusleiste: im Reiter Auswertung deren Felder, sonst das Diagramm aus Messen."""
        quelle = bericht.AUSWERTUNG if self.current_page_key() == "auswertung" else bericht.MESSEN
        return bericht.erzeugen(self, path, quelle)

    # ------------------------------------------------------------------ Zyklus
    def _tick(self):
        if waiting():                                      # Verbindung ist gerade mit einer Abfrage beschaeftigt
            return
        self._kacheln_takt()
        if self.link is not None:
            for ev in self.link.drain_events():            # Ereignisse der Basis: Marker, Zange, Steckdosen
                if ev.kind == "ZTASTE":                    # Bedienung, gehört nicht in Lauf oder Aufzeichnung
                    self._ztaste(ev)
                    continue
                if self._abgleich_marker(ev):              # Zangen-Taste im Fenster „Übersetzung live abgleichen“
                    continue
                self.page_oszi.basis_event(ev)
                if self.ctrl:
                    self.ctrl.add_event(ev)
                self.page_hw.on_event(ev)
                self.page_tasten.on_event(ev)
                self.statusBar().showMessage(self._event_text(ev), 4000)
        self.page_oszi.poll()
        lauf = bool(self.ctrl and self.ctrl.state in ACTIVE)
        self.page_hw.automatic(lauf, self.page_oszi.rec is not None)
        self._ratio_tick()
        if not self.ctrl:
            return
        new = self.ctrl.poll()
        lv = self.ctrl.live
        if self._klima_offen and self.ctrl.state != KLIMA:
            self._klima_nach_start()
        if new and self.ausroll_dlg is not None and self.ctrl.state not in ACTIVE:
            self.ausroll_dlg.telegramme(new)               # Assistent Rollen-Kalibrierung: Rolle ohne Lauf
        if new:
            f = new[-1]
            ecu_rpm = self.page_rusefi.live("RPMValue")
            self.ratio_mess.probe(lv.n_meas, lv.n_roll, ecu_rpm)
            self._live_werte(lv, f, ecu_rpm)
            if abs(lv.rate - self._shown_rate) > 0.5:
                self._shown_rate = lv.rate
                self.page_settings.update_filter_label(lv.rate)

        st = self.ctrl.state
        self._laufzustand_zeigen(st, lv)
        if st in (WAIT, RUN) and time.time() > self._plot_due:
            self._plot_due = time.time() + 0.05
            self._draw_live()
        elif st == COAST and self._plot_due >= 0:
            self._plot_due = -1.0                          # Lauf ist fertig: Kurve einmal ganz zeichnen
            self._draw_live(final=True, res=self.ctrl.live_result)
        elif st in (DONE, ABORTED) and not self._done_handled:
            self._done_handled = True
            if st == DONE:
                self._finish_run()
            else:
                self.plot.freeze(False)
                self._set_status(self.ctrl.message or "Abgebrochen", farben.BALKEN_FEHLER)
            self._update_buttons()
        if st in ACTIVE:
            self._update_buttons()

        # Live-Anzeige: Ausgabe nach Autostopp der Firmware wieder anstossen
        if (self.link and self.page_settings.live_check.isChecked() and st in (IDLE, DONE, ABORTED)
                and not self.link.streaming() and time.time() - self.last_keepalive > 2.0):
            self.last_keepalive = time.time()
            self.link.keepalive()

    # ------------------------------------------------------------------ Tasten der Zange
    def _event_text(self, ev) -> str:
        """Marker in der Statusleiste so, wie er im Lauf bzw. in der Aufzeichnung heißt."""
        if not ev.is_marker:
            return ev.text
        if self.ctrl and self.ctrl.state in ACTIVE:
            nr = sum(e.is_marker for e in self.ctrl.basis_events)
        else:
            nr = len(self.page_oszi.markers)
        return ev.marker_text(nr) if nr else ev.text

    def _taste_sperre(self) -> str:
        return tasten.sperre(self.ctrl is not None, bool(self.ctrl and self.ctrl.state in ACTIVE),
                             self.page_oszi.rec is not None, self.page_flash.busy,
                             QtWidgets.QApplication.activeModalWidget() is not None, bool(self.page_pruef.bg.pending))

    def _taste_abgelehnt(self, was: str, grund: str):
        self.band.zeige(f"{was}: {grund}", farben.FEHLER, 6.0)
        self.statusBar().showMessage(f"Zange: {was} abgelehnt: {grund}", 6000)
        self.page_tasten.led(tasten.LED_FEHLER)

    def _ztaste(self, ev):
        """E;…;ZTASTE;lauf|uebersetzung: Die Zange wartet auf Antwort, jeder Weg hier führt zu einer LED."""
        art = ev.args[0] if ev.args else ""
        if art == "lauf":
            self._taste_lauf()
        elif art == "uebersetzung":
            self._taste_ratio()

    def _taste_lauf(self):
        """Wie der Start-Knopf, nur wenn ein Lauf möglich ist. Abbrechen geht nur am PC."""
        grund = self._taste_sperre() or ("Übersetzung wird gerade gemessen" if self.ratio_mess.aktiv else "") \
            or ("Rollen-Kalibrierung (Assistent) offen" if self.ausroll_dlg is not None else "") \
            or physics.check_params(self.page_settings.params())
        if grund:
            self._taste_abgelehnt("Neuer Lauf", grund)
            return
        self.page_tasten.led(tasten.LED_OK)                # vor dem Start: Klima lesen hält die Antwort sonst auf
        self.band.zeige("Neuer Lauf gestartet", farben.GUT, 3.0)
        self.on_start(nachfrage=False)

    def _taste_ratio(self):
        grund = self._taste_sperre()
        if grund:
            self._taste_abgelehnt("Übersetzung", grund)
            return
        now = time.time()
        bisher = self.page_settings.fields["ratio"].value()
        was, wert = self.ratio_mess.taste(now, bisher)
        if was == "laeuft":
            self.page_tasten.led(tasten.LED_LAEUFT)
        elif was == "start":
            self.ctrl.params = self.page_settings.params()
            if not self.link.streaming():
                self.link.start()
            self.page_tasten.led(tasten.LED_LAEUFT)
            self._ratio_band(now)
        else:
            self.page_settings.fields["ratio"].setValue(wert)
            self._persist()
            self._set_status(f"Übersetzung = {wert:.3f}", "#b8e6b8")
            self.band.zeige(f"Übersetzung {wert:.3f} übernommen (bisher {self.ratio_mess.bisher:.3f})", farben.GUT, 6.0)
            self.page_tasten.led(tasten.LED_OK)

    def _ratio_band(self, now: float):
        m = self.ratio_mess
        if m.zustand == m.MISST:
            self.band.zeige(f"Übersetzung wird gemessen, Drehzahl halten … {m.rest_s(now)} s", farben.WARNUNG)
        elif m.zustand == m.WARTET:
            geste = tasten.geste_von(self.page_tasten.setup, "uebersetzung")
            self.band.zeige(f"Übersetzung neu {m.wert:.3f}   bisher {m.bisher:.3f}\n"
                            f"Übernehmen: {geste} noch einmal drücken ({m.rest_s(now)} s)", "#1f5fbf")

    def _ratio_tick(self):
        """Messung weiterführen; was ihr den Boden entzieht, verwirft sie."""
        m = self.ratio_mess
        if not m.aktiv:
            return
        now = time.time()
        grund = self._taste_sperre()
        if grund:
            m.abbrechen()
            self._taste_abgelehnt("Übersetzung verworfen", grund)
            return
        ereignis = m.tick(now)
        if ereignis is None:
            self._ratio_band(now)
        elif ereignis.art == "fertig":
            self.page_tasten.led(tasten.LED_FERTIG)
            self._ratio_band(now)
        else:
            self.band.zeige(f"Übersetzung nicht übernommen: {ereignis.text}", farben.FEHLER, 8.0)
            self.statusBar().showMessage(f"Übersetzung nicht übernommen: {ereignis.text}", 8000)
            self.page_tasten.led(tasten.LED_FEHLER)

    # ------------------------------------------------------------------ Einstellungen: Unterreiter
    @staticmethod
    def _seite(*widgets, stretch_last: bool = False) -> QtWidgets.QScrollArea:
        """Scrollbare Seite aus vorhandenen Gruppen (werden dabei aus ihrer alten Seite herausgenommen)."""
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        for i, w in enumerate(widgets):
            if isinstance(w, QtWidgets.QLayout):
                lay.addLayout(w, 1 if stretch_last and i == len(widgets) - 1 else 0)
            else:
                lay.addWidget(w, 1 if stretch_last and i == len(widgets) - 1 else 0)
        if not stretch_last:
            lay.addStretch(1)
        area.setWidget(inner)
        return area

    @staticmethod
    def _einbetten(area: QtWidgets.QScrollArea):
        """Seite als Gruppe in einer anderen Seite: ohne Rahmen, Hintergrund der Gruppe (Tag/Nacht)."""
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.viewport().setAutoFillBackground(False)
        if area.widget() is not None:
            area.widget().setAutoFillBackground(False)

    def _build_settings_tabs(self):
        """Einstellungen: Rolle/Lauf | Verbindung | Zange | Lambda | ECU | Firmware | WiFi (Entwurf 08.11.2026).
        Jede Seite ist ein Unterreiter ohne weitere Reiter darin; die Teile (Ausrollversuch, Prüfen, Tasten) stecken
        als Gruppen in ihren Seiten."""
        from . import einst_widgets as ew
        from .pruef_page import LAMBDA, ZANGE
        s, hw = self.page_settings, self.page_hw
        # Rolle/Lauf: rechts Verluste mit dem Ausrollversuch (früher Hauptreiter Kalibrierung)
        kalib_box = QtWidgets.QGroupBox("Verluste – Ausrollversuch")
        kl = QtWidgets.QVBoxLayout(kalib_box)
        kl.setContentsMargins(4, 4, 4, 4)
        self._einbetten(self.page_kalib)
        kl.addWidget(self.page_kalib)
        s.verluste_lay.addWidget(kalib_box, 1)
        s.ratio_btn.clicked.connect(self.on_measure_ratio)
        s.abgleich_btn.clicked.connect(self.on_abgleich_live)
        self.settings.setdefault("start_nachfrage_setup", True)
        s.start_nachfrage.toggled.connect(self._start_nachfrage_geaendert)
        self.settings.setdefault(NACH_LAUF_FENSTER, True)
        s.nach_lauf_fenster.toggled.connect(self._nach_lauf_fenster_geaendert)
        # Verbindung: Messboard mit den Geräte-Einstellungen der Basis, Prüfen (Sammelknopf)
        self.dev_settings_btn = QtWidgets.QPushButton("Geräte-Einstellungen der Basis …")
        self.dev_settings_btn.setToolTip("#GET/#SET der Basis bzw. des Messboards (PST-Diag 1): Messfrequenz, Flanken, "
                                         "Filter, AFR-Eingang, BME280 …")
        self.dev_settings_btn.clicked.connect(self._messboard_einstellungen)
        s.messboard_lay.addWidget(self.dev_settings_btn, 0, QtCore.Qt.AlignLeft)
        s.messboard_lay.addWidget(ew.neben("Gilt für PST-Diag-Geräte: PyST-Basis, Arduino Mega ab 3.1, STM32 (F446) "
                                           "ab 1.1. Lambda/AFR-Werte auch unter Lambda."))
        s.pruefen_lay.addWidget(self.page_pruef)
        s.pruefen_lay.addWidget(ew.neben("„Alles prüfen“ sammelt die Prüfungen aller Unterreiter (Basis, Klima, "
                                         "ESP/WLAN, Lambda, Zange). Einzeln: Zange › Tests, Lambda › Prüfen."))
        # Zange: Tasten und Tests als Spalten
        t = self.page_tasten                       # ganze Spalte ohne eigene Rollbalken
        self._einbetten(t)
        t.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        t.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        t.setMinimumHeight(t.widget().sizeHint().height() + 40)
        t.setMinimumWidth(t.widget().minimumSizeHint().width())     # ohne Rollbalken nie schmaler als der Inhalt
        hw.tasten_lay.addWidget(t)
        self.page_pruef_zange = PruefPage(self.page_pruef.get_link, hw.pair, self.page_pruef.aufnahme, bereich=ZANGE)
        hw.tests_lay.addWidget(self.page_pruef_zange)
        hw.zur_firmware.connect(lambda: self.show_page(self.page_flash))
        self.page_flash.zange_verfuegbar.connect(hw.set_fw_verfuegbar)
        self.page_flash.get_info = lambda: self.firmware
        self.page_flash.get_zange = lambda: f"Zange {hw.zst.id}" if hw.zst is not None and hw.zst.id else ""
        self.page_flash.get_zange_state = lambda: getattr(self, "_zange_state", "")
        # Lambda: Heizung, Anzeige λ/AFR, AFR-Eingang links; Prüfen und Firmware rechts
        self.page_pruef_lambda = PruefPage(self.page_pruef.get_link, hw.pair, self.page_pruef.aufnahme, bereich=LAMBDA)
        pr = QtWidgets.QGroupBox("Prüfen: Wideband (F042) und Heizung")
        QtWidgets.QVBoxLayout(pr).addWidget(self.page_pruef_lambda)
        fw = QtWidgets.QGroupBox("Firmware")
        fl = QtWidgets.QVBoxLayout(fw)
        fl.addWidget(ew.neben("Der Wideband-Controller (F042) wird über die Basis geflasht: "
                              "Einstellungen › Software & Firmware, Expertenmodus, Zeile Lambda (F042)."))
        b = QtWidgets.QPushButton("Zur Firmware …")
        b.clicked.connect(lambda: self.show_page(self.page_flash))
        fl.addWidget(b, 0, QtCore.Qt.AlignLeft)
        cols = QtWidgets.QHBoxLayout()
        links = QtWidgets.QVBoxLayout()
        for g in (hw.box_lambda, s.box_gemisch, hw.box_afr):
            links.addWidget(g)
        links.addStretch(1)
        rechts = QtWidgets.QVBoxLayout()
        rechts.addWidget(pr, 1)
        rechts.addWidget(fw)
        cols.addLayout(links, 11)
        cols.addLayout(rechts, 9)
        self.page_lambda = self._seite(cols, stretch_last=True)
        # WiFi: WLAN der Basis links, Steckdosen rechts
        row = QtWidgets.QHBoxLayout()
        net = QtWidgets.QVBoxLayout()
        net.addWidget(hw.box_net)
        net.addStretch(1)
        row.addLayout(net, 4)
        row.addWidget(hw.box_plugs, 7)
        self.page_wifi = self._seite(row, stretch_last=True)
        for page, text in ((s, "Rolle/Lauf"), (s.conn_page, "Verbindung"), (hw, "Zange"), (self.page_lambda, "Lambda"),
                           (self.page_analog, "Analogeingänge"), (self.page_rusefi, "ECU"), (self.page_flash, "Software && Firmware"),
                           (self.page_wifi, "WiFi")):
            self.settings_tabs.addTab(page, text)
        self.settings_tabs.setStyleSheet("QTabBar::tab { min-width: 110px; }")
        # Schluessel der Seiten (gespeicherter Reiterstand, show_page); Unterseiten stecken in einer Seite
        self.seiten: Dict[str, QtWidgets.QWidget] = {
            "messen": self.tabs.widget(0), "fahrzeuge": self.page_db, "auswertung": self.viewer, "oszi": self.page_oszi,
            "rolle": s, "verbindung": s.conn_page, "zange": hw, "lambda": self.page_lambda, "analog": self.page_analog,
            "ecu": self.page_rusefi,
            "firmware": self.page_flash, "wifi": self.page_wifi}
        self.unterseiten: Dict[str, QtWidgets.QWidget] = {
            "verluste": self.page_kalib, "pruefen": self.page_pruef, "tasten": self.page_tasten}

    def _start_nachfrage_geaendert(self, on: bool):
        """Rolle/Lauf: „Vor START nachfragen, ob sich das Setup geändert hat“ (gelesen beim START unter Messen)."""
        self.settings["start_nachfrage_setup"] = bool(on)
        save_settings(self.settings)

    def _nach_lauf_fenster_geaendert(self, on: bool):
        """Rolle/Lauf: „Nach dem Lauf Notiz/Setup-Fenster zeigen“ (gelesen beim naechsten Laufende)."""
        self.settings[NACH_LAUF_FENSTER] = bool(on)
        save_settings(self.settings)

    def _messboard_einstellungen(self):
        if self.link is None or not getattr(self.link, "has_diag", False):
            QtWidgets.QMessageBox.information(self, "Messboard", "Zuerst ein Messboard mit PST-Diag verbinden "
                                                                 "(PyST-Basis oder STM32-Firmware).")
            return
        self.page_oszi.device_settings()

    # ------------------------------------------------------------------ Statusleiste, Thema
    def _zange_state_neu(self, state: str):
        self._zange_state = state
        self._status_tick()

    def _zange_text_neu(self, text: str):
        m = re.search(r"\(\+(\d+) weitere\)", text or "")
        self._zange_weitere = int(m.group(1)) if m else 0

    def status_eingang(self) -> Eingang:
        """Momentaufnahme fuer die Statusleiste aus Verbindung, Zange, Lambda und ECU."""
        e = Eingang(zange_state=self._zange_state, zangen_weitere=self._zange_weitere,
                    weg="Verbindung weg, PyST verbindet neu …" if self._tcp_again else "",
                    anmeldung_fehler=self._auth_stop if self.link is None else "")
        L = self.link
        if L is not None:
            e.verbunden, e.port = True, L.port
            if L.is_zange_usb:
                e.board = "zange_usb"
            elif L.is_basis:
                e.board = "basis"
            else:
                e.board = "f446" if L.is_stm else "mega"
            first = self.firmware.splitlines()[0].strip() if self.firmware else ""
            e.fw = L.caps.get("fw", "") if L.is_basis or L.is_zange_usb else first[:40]
            e.bootloader = bool(L.bootloader)
            if self.ctrl is not None and L.streaming():
                e.hz = self.ctrl.live.rate
            if L.is_basis:
                e.zange, e.lam = self.page_hw.zst, self.page_hw.lam
        R = self.page_rusefi.link
        if R is not None:
            e.ecu = {"fehler": R.error or "", "ini": R.ini is not None, "passt": bool(R.signature_ok),
                     "hz": R.measured_hz}
        return e

    def _status_tick(self):
        if waiting():                                      # Verbindung gerade in einer Abfrage
            return
        try:
            self.statusleiste.set_zustaende(zustaende(self.status_eingang()))
        except Exception as exc:                           # Anzeige darf den Takt nie stoppen
            log.debug("Statusleiste: %s", exc)

    def _thema_gewaehlt(self, wahl: str):
        """Tag/Nacht/Auto gewaehlt (Menue oder Umschalter): merken."""
        self.settings["thema"] = wahl
        save_settings(self.settings)
        a = getattr(self, "thema_aktionen", {}).get(wahl)
        if a is not None:
            a.setChecked(True)

    def _thema_gewechselt(self, _thema: str):
        """Nach dem Wechsel: Farbfelder der Laufliste und Zustandszeile neu (Diagramme folgen selbst)."""
        a = getattr(self, "thema_aktionen", {}).get(theme.manager().wahl)
        if a is not None:
            a.setChecked(True)
        if getattr(self, "_status_last", None):
            self._set_status(*self._status_last)
        self._reload_runs()

    # ------------------------------------------------------------------ Fenster-Layout
    def show_page(self, page: QtWidgets.QWidget):
        """Seite zeigen, auch wenn sie ein Unterreiter von Einstellungen ist oder als Gruppe in einer Seite steckt
        (Ausrollversuch, Prüfen, Tasten): dann dorthin scrollen."""
        w = page
        while w is not None:
            par = w.parentWidget()
            if isinstance(par, QtWidgets.QStackedWidget) and isinstance(par.parentWidget(), QtWidgets.QTabWidget):
                par.parentWidget().setCurrentWidget(w)
            if isinstance(w, QtWidgets.QScrollArea) and w is not page and w.widget() is not None \
                    and w.widget().isAncestorOf(page):
                w.ensureWidgetVisible(page, 0, 0)
            w = par

    def current_page(self) -> QtWidgets.QWidget:
        """Innerste sichtbare Seite (durch alle Reiterebenen)."""
        w = self.tabs.currentWidget()
        while isinstance(w, QtWidgets.QTabWidget):
            w = w.currentWidget()
        return w

    def current_page_key(self) -> str:
        cur = self.current_page()
        return next((k for k, w in self.seiten.items() if w is cur), "messen")

    def save_window_layout(self):
        """Fenster, Aufteilungen, Spaltenbreiten und Reiter (beim Beenden)."""
        b64 = lambda ba: bytes(ba.toBase64()).decode("ascii")
        self.settings["fenster"] = {
            "geometrie": b64(self.saveGeometry()),
            "messen_teilung": b64(self.measure_split.saveState()),
            "auswertung_teilung": b64(self.viewer.split.saveState()),
            "laeufe_spalten": [self.run_tree.columnWidth(c) for c in range(self.run_tree.columnCount())],
            "seite": self.current_page_key(),
        }

    @staticmethod
    def alte_seite(f: dict) -> str:
        """Seitenschluessel aus einem gespeicherten Reiterstand frueherer Versionen ("reiter", "reiter_neu")."""
        haupt, unter = f.get("reiter_neu") or REITER_ALT.get(f.get("reiter"), (0, 0))
        if not f.get("reiter_kalib") and haupt >= REITER_KALIB:
            haupt += 1                             # gespeichert von der Version ohne Reiter Kalibrierung
        if not 0 <= haupt < len(REITER_14):
            return "messen"
        key = REITER_14[haupt]
        if key is None:
            key = UNTER_14[unter] if 0 <= unter < len(UNTER_14) else "rolle"
        return key

    def restore_window_layout(self) -> bool:
        """True, wenn Fenstergroesse/-position wiederhergestellt wurde."""
        f = self.settings.get("fenster") or {}
        ba = lambda text: QtCore.QByteArray.fromBase64(text.encode("ascii"))
        try:
            if f.get("messen_teilung"):
                self.measure_split.restoreState(ba(f["messen_teilung"]))
            if f.get("auswertung_teilung"):
                self.viewer.split.restoreState(ba(f["auswertung_teilung"]))
            for c, wd in enumerate(f.get("laeufe_spalten", [])[:self.run_tree.columnCount()]):
                if wd > 0:
                    self.run_tree.setColumnWidth(c, wd)
            alle = {**self.seiten, **self.unterseiten}
            key = f.get("seite") if f.get("seite") in alle else self.alte_seite(f)
            self.show_page(alle.get(key, self.seiten["messen"]))
            return bool(f.get("geometrie")) and self.restoreGeometry(ba(f["geometrie"]))
        except (TypeError, ValueError, AttributeError):
            return False

    def closeEvent(self, ev):
        self.save_window_layout()
        self.settings["laeufe_leiste"] = self.laeufe_leiste.zustand()
        self._persist()
        self.page_oszi.close_page()
        self.page_hw.close_page()
        self.page_analog.close_page()
        if self.link:
            self.link.close()
        self.page_rusefi.close_link()
        super().closeEvent(ev)


DESIGN_W, DESIGN_H = 1480, 880          # Groesse, fuer die das Layout gedacht ist (logische Punkte)


def _apply_ui_scale(app: QtWidgets.QApplication) -> bool:
    """Skalierung aus Einstellung/Bildschirmgroesse; True = Prozess wurde mit QT_SCALE_FACTOR neu gestartet."""
    if os.environ.get("PYST_SCALED"):
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
    os.environ["PYST_SCALED"] = "1"
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
    icons = QtGui.QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icons.addFile(os.path.join(os.path.dirname(__file__), "data", "icon", f"pyst_{size}.png"))
    app.setWindowIcon(icons)
    win = MainWindow()
    # Dateien (pyst datei.pyst, Doppelklick): relativ zum Aufrufort (Startskripte wechseln das Verzeichnis)
    cwd = os.environ.get("PYST_CWD", os.getcwd())
    files = [os.path.join(cwd, a) for a in sys.argv[1:] if not a.startswith("-")]
    files = [f for f in files if os.path.exists(f)]
    if "--replay" in sys.argv and files:
        win.on_replay(files.pop(0))
    elif "--sim" in sys.argv:
        win.page_settings.port_combo.setEditText(SIM_PORT if "--synth" in sys.argv else DEFAULT_SIM_PORT)
        win.on_connect()
    _fit_window(win, app)
    win.page_flash.pyst_update.connect(lambda v: win.band.zeige(f"Neue PyST-Version {v} verfügbar", "#1f5fbf", 8.0))
    win.page_flash.check_updates(__version__)
    if files:                                   # pyst datei.pyst: gleich in der Auswertung zeigen
        QtCore.QTimer.singleShot(0, lambda: win.open_files(files))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
