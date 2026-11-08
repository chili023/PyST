"""
Prüfen: Inbetriebnahme von Basis und Zange ohne Terminal. Die Pruefungen selbst stehen in pruef.py;
hier nur Bedienung und Anzeige. Befehle laufen im Hintergrund-Thread, die Oberflaeche bleibt bedienbar.

Drei Zuschnitte (bereich): ALLE = Sammelknopf unter Einstellungen › Verbindung (alle Prüfungen, Protokoll),
ZANGE = Gruppe „Tests“ im Unterreiter Zange (Prüfungen der Zange, Log, Funken zählen, Einzelblitz),
LAMBDA = Prüfzeilen Wideband (F042) und Lambda-Heizung im Unterreiter Lambda.
"""
import time
from typing import Callable, List

from PySide6 import QtCore, QtGui, QtWidgets

from . import farben
from . import theme
from . import pruef
from .bg import Bg
from .pruef import Check
from .umbruch import UmbruchLayout

def farbe(ergebnis) -> str:
    """Schriftfarbe eines Pruefergebnisses im aktiven Thema."""
    return {pruef.OK: farben.GUT, pruef.WARN: farben.WARNUNG, pruef.FEHLER: farben.FEHLER}.get(ergebnis, farben.NEUTRAL)
ALLE, ZANGE, LAMBDA = "alle", "zange", "lambda"
TASTEN_PRUEFEN_FEHLT = ("Kommt mit einer Zangen-Firmware, die jeden Tastendruck an PyST meldet. Bis dahin: "
                        "Zange › Tasten der Zange, Rückmeldung über die LED.")


def gehoert_zu(c: Check, bereich: str) -> bool:
    """Prüfzeile für den Zuschnitt: Zange = Gruppen „Zange (Funk)“/„Zange (USB)“, Lambda = Wideband und Heizung."""
    if bereich == ZANGE:
        return c.gruppe.startswith("Zange") or c.gruppe == "Gerät"
    if bereich == LAMBDA:
        return "Wideband" in c.name or "Lambda" in c.name or c.gruppe == "Gerät"
    return True


BLITZ_WARNUNG = ("Nicht in die LED schauen. Mit kurzem Puls beginnen und U_DS am Blitzkopf (TP7 gegen TP8) messen: "
                 "über 24 V sofort aufhören.")


class PruefPage(QtWidgets.QWidget):
    def __init__(self, get_link: Callable[[], object], open_pairing: Callable[[], None],
                 aufnahme: Callable[[], bool] = lambda: False, bereich: str = ALLE):
        super().__init__()
        self.bereich = bereich
        self.get_link = get_link
        self.aufnahme = aufnahme
        self.open_pairing = open_pairing
        self.link = None
        self.bg = Bg()
        self.checks: List[Check] = []
        self._busy_since = 0.0
        root = QtWidgets.QVBoxLayout(self)
        if bereich != ALLE:
            root.setContentsMargins(0, 0, 0, 0)
        self.device = QtWidgets.QLabel("Kein Gerät verbunden.")
        self.device.setStyleSheet("font-size: 15px; font-weight: 600;")
        root.addWidget(self.device)
        self.device.setVisible(bereich == ALLE)
        row = QtWidgets.QHBoxLayout()
        self.b_all = QtWidgets.QPushButton({ALLE: "Alles prüfen", ZANGE: "Alles prüfen",
                                            LAMBDA: "Lambda prüfen"}[bereich])
        theme.rolle(self.b_all, "primaer")
        self.b_all.setToolTip({ALLE: "Sammelknopf: Basis (Takt, Spannungen, Sensoren, Lambda), ESP, WLAN, Klima, Zange. "
                                     "Zange per USB: Firmware, Zähler.",
                               ZANGE: "Nur die Zange: Funkverbindung, Akku, Zeitabgleich, Funken, Log.",
                               LAMBDA: "Wideband-Controller (F042) und Lambda-Heizung laut Basis."}[bereich])
        self.b_pair = QtWidgets.QPushButton("Koppeln …")
        self.b_pair.setToolTip("Kopplungsfenster der Basis 30 s öffnen, an der Zange T1 + T2 gleichzeitig 3 s halten.")
        self.b_log = QtWidgets.QPushButton("Log prüfen")
        self.b_log.setToolTip("Zange per USB: Log auslesen und auf Zeilenarten, Funken und Neustarts prüfen.")
        self.b_tasten = QtWidgets.QPushButton("Tasten prüfen")
        self.b_tasten.setEnabled(False)
        self.b_tasten.setToolTip(TASTEN_PRUEFEN_FEHLT)
        self.b_copy = QtWidgets.QPushButton("Protokoll kopieren")
        for b in (self.b_all, self.b_pair, self.b_log, self.b_tasten, self.b_copy):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        self.b_pair.setVisible(False)                   # Koppeln steht unter Zange › Kopplung
        self.b_log.setVisible(bereich == ZANGE)
        self.b_tasten.setVisible(bereich == ZANGE)
        tools = UmbruchLayout(abstand=root.spacing(), zeilenabstand=root.spacing())   # schmal: untereinander
        tools.add(self._build_funken())
        tools.add(self._build_blitz(), 1)
        root.addLayout(tools)
        self.funken_box.setVisible(bereich == ZANGE)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Prüfung", "", "Wert", "Soll"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 240 if bereich == ALLE else 170)
        self.tree.setColumnWidth(1, 30)
        self.tree.setColumnWidth(2, 300 if bereich == ALLE else 130)
        self.tree.setMinimumHeight(150)
        root.addWidget(self.tree, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        theme.rolle(self.msg, "neben")
        root.addWidget(self.msg)
        self.b_all.clicked.connect(self.run_all)
        self.b_pair.clicked.connect(self.pair)
        self.b_log.clicked.connect(self.run_log)
        self.b_funken.clicked.connect(self.run_funken)
        self.b_blitz.clicked.connect(self.run_blitz)
        self.b_copy.clicked.connect(self.copy)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(200)
        self._update_device()

    def _build_funken(self):
        g = self.funken_box = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(g)
        lay.setContentsMargins(0, 0, 0, 0)
        self.f_sec = QtWidgets.QSpinBox()
        self.f_sec.setRange(2, 120)
        self.f_sec.setValue(10)
        self.f_sec.setSuffix(" s")
        self.f_soll = QtWidgets.QDoubleSpinBox()
        self.f_soll.setRange(0, 500)
        self.f_soll.setDecimals(1)
        self.f_soll.setSpecialValueText("beliebig")
        self.f_soll.setSuffix(" Hz")
        self.f_soll.setToolTip("Erwartete Zündfrequenz, z. B. vom Generator. 0 = nur messen.")
        self.b_funken = QtWidgets.QPushButton("Start")
        lay.addWidget(QtWidgets.QLabel("Funken zählen:"))
        lay.addWidget(self.f_sec)
        lay.addWidget(QtWidgets.QLabel("Soll"))
        lay.addWidget(self.f_soll)
        lay.addWidget(self.b_funken)
        return g

    def _build_blitz(self):
        self.blitz_box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(self.blitz_box)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Einzelblitz (USB):"))
        self.z_us = QtWidgets.QSpinBox()
        self.z_us.setRange(pruef.BLITZ_MIN_US, pruef.BLITZ_MAX_US)
        self.z_us.setValue(5)
        self.z_us.setSuffix(" µs")
        self.b_blitz = QtWidgets.QPushButton("Ein Blitz")
        row.addWidget(QtWidgets.QLabel("Puls"))
        row.addWidget(self.z_us)
        row.addWidget(self.b_blitz)
        row.addStretch(1)
        lay.addLayout(row)
        warn = self.blitz_warn = QtWidgets.QLabel(BLITZ_WARNUNG)
        warn.setWordWrap(True)
        theme.rolle(warn, "banner_fehler")
        lay.addWidget(warn)
        return self.blitz_box

    # ------------------------------------------------------------------ Ablauf
    def _start(self, key: str, text: str, fn: Callable):
        """Pruefung im Hintergrund; das Ergebnis haengt sich unten an die Tabelle an."""
        link = self.link
        if link is None or self.bg.pending:
            return
        self.msg.setText(text)
        self._busy_since = time.time()
        self._update_device()
        self.bg.submit(key, lambda: fn(link), self._done)

    def _done(self, res, err):
        self._busy_since = 0.0
        if err is not None:
            self.msg.setText(f"Fehler: {err}")
        else:
            self._add(res or [])
            n = sum(c.ergebnis == pruef.FEHLER for c in self.checks)
            w = sum(c.ergebnis == pruef.WARN for c in self.checks)
            self.msg.setText(f"Fertig: {n} Fehler, {w} Warnungen.")
        self._update_device()

    def _add(self, checks: List[Check]):
        # dieselbe Pruefung ersetzt ihr altes Ergebnis, die Tabelle bleibt ein aktueller Stand
        neu = {(c.gruppe, c.name) for c in checks}
        self.checks = [c for c in self.checks if (c.gruppe, c.name) not in neu] + checks
        self.tree.clear()
        for c in self.checks:
            name = f"{c.gruppe}: {c.name}" if self.bereich == ALLE else c.name
            it = QtWidgets.QTreeWidgetItem([name, pruef.SYMBOL[c.ergebnis], c.wert, c.soll])
            it.setForeground(1, QtGui.QBrush(QtGui.QColor(farbe(c.ergebnis))))
            if c.ergebnis == pruef.FEHLER:
                it.setForeground(2, QtGui.QBrush(QtGui.QColor(farbe(c.ergebnis))))
            self.tree.addTopLevelItem(it)

    def run_all(self):
        self.checks = []
        self.tree.clear()
        aufnahme = self.aufnahme()
        bereich = self.bereich
        self._start("alle", "Prüfe …", lambda link: [c for c in pruef.run_all(link, aufnahme)
                                                     if gehoert_zu(c, bereich)])

    def run_funken(self):
        sec, soll = float(self.f_sec.value()), self.f_soll.value()
        self._start("funken", f"Zähle Funken ({sec:.0f} s) …", lambda link: pruef.run_funken(link, sec, soll))

    def run_blitz(self):
        us = self.z_us.value()
        self._start("blitz", f"Blitz {us} µs …", lambda link: pruef.run_blitz(link, us))

    def run_log(self):
        self._start("log", "Lese Log …", pruef.run_log)

    def pair(self):
        self.open_pairing()
        self.msg.setText("Kopplungsfenster 30 s offen – an der Zange T1 + T2 gleichzeitig 3 s halten, "
                         "danach „Alles prüfen“.")

    def copy(self):
        QtWidgets.QApplication.clipboard().setText(pruef.protokoll(self.checks, self.device.text()))
        self.msg.setText("Protokoll in der Zwischenablage.")

    # ------------------------------------------------------------------ Zustand
    def _update_device(self):
        link = self.link
        basis = bool(link and link.is_basis)
        usb = bool(link and link.is_zange_usb)
        idle = not self.bg.pending
        if usb:
            self.device.setText(f"Zange per USB – {link.caps.get('fw', '')}")
        elif basis:
            self.device.setText(link.caps.get("fw", "PyST-Basis"))
        elif link is not None:
            self.device.setText("Das verbundene Gerät ist weder Basis noch Zange.")
        else:
            self.device.setText("Kein Gerät verbunden. Basis oder Zange unter Einstellungen › Verbindung verbinden.")
        self.b_all.setEnabled((basis if self.bereich == LAMBDA else basis or usb) and idle)
        self.b_pair.setEnabled(basis and idle)
        self.b_log.setEnabled(usb and idle)
        self.b_funken.setEnabled((basis or usb) and idle)
        self.b_blitz.setEnabled(usb and idle)
        self.blitz_box.setVisible(self.bereich == ZANGE)
        self.blitz_warn.setVisible(usb and self.bereich == ZANGE)

    def poll(self):
        link = self.get_link()
        if link is not self.link:
            self.link = link
            self.checks = []
            self.tree.clear()
            self.msg.setText("")
            self._update_device()
        self.bg.poll()
