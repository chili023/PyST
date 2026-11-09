"""
Assistent Rollen-Kalibrierung (Einstellungen › Rolle/Lauf, Gruppe „Rollen-Kalibrierung (Ausrollen ohne Rad)“, Knopf
„Rollen-Kalibrierung (Assistent) …“): Ausrollen ohne Leistungslauf, mit abgehobenem Rad. Einmal je Pruefstand,
wiederholen nur nach Umbau an der Rolle. Nicht modal, fuenf Schritte:

  1 Vorbereitung   Messboard verbunden und Rollendaten kommen (sonst Hinweis und „Verbinden“), was gemessen wird
  2 Beschleunigen  Rolle mit dem Motorrad auf die Zieldrehzahl bringen: Rolle 1/min und km/h gross, Balken bis zum
                   Ziel; „Weiter“ erst, wenn das Ziel erreicht ist (Ziel einstellbar, Standard ziel_standard)
  3 Rad ab         Gas weg, Rad von der Rolle nehmen, Sicherheitshinweis. „Messung starten“ nur ueber der
                   Mindestdrehzahl (mindest_drehzahl), sonst zurueck zu 2 mit „zu langsam – nochmal beschleunigen“
  4 Messung        Aufzeichnung ab „Messung starten“ (AuslaufAufnahme), Live-Kurve Drehzahl ueber Zeit. Ende bei
                   n_ende (Motordrehzahl Rolle × i wie beim Ausrollen nach dem Lauf), Stillstand, Wiederanstieg,
                   keinen Daten mehr oder t_max; „Beenden und auswerten“ oder „Abbrechen“ jederzeit
  5 Ergebnis       ausrollen.analyse(..., start=0, kupplung=False): keine Kupplungserkennung, der kurze Uebergang
                   am Anfang wird verworfen. Diagramm und Werte wie die Gruppe „Letzter Ausrollversuch“
                   (kalib_page.VerlustDiagramm mit der bisherigen Kalibrierung, werte_html), Hinweise; „Als
                   Rollen-Kalibrierung speichern“ (Signal kalib_speichern, das Hauptfenster speichert sie in
                   einstellungen.json „rollen_kalibrierung“), „Nochmal“ (zurueck zu 2), „Schließen“

Physik: Mit abgehobenem Rad laeuft nur die Rolle aus, gemessen werden die Verluste der Rolle selbst (Lager, Luft),
nicht Reifen, Kette und Getriebeausgang. Das Ergebnis geht NICHT in M0/M1 (die bleiben die Verluste gesamt aus dem
Ausrollversuch nach dem Lauf), sondern ist die Rollen-Kalibrierung (ausrollen.RollenKalibrierung, DynoParams rk_*):
Damit teilt PyST den Ausrollversuch nach dem Lauf in Rolle + Reifen/Kette/Getriebe auf und weist im Ergebnis
„Reifen an Rolle“ (Rad + Rollenverlust) und den Antriebsverlust (Motor − Reifen an Rolle) aus.

Daten: Das Hauptfenster reicht ausserhalb eines Laufs jedes Telegramm weiter (telegramme(frames), aus
RunController.poll wie die Live-Anzeige); keine Laufaufzeichnung, kein Speichern als Lauf. Die Rohdaten der Messung
landen in messungen/JJMMTT_HHMMSS_Rollen-Kalibrierung/: roh.csv wie bei einem Lauf, lauf.json mit art
"rollen_kalibrierung", den Parametern und dem Ergebnis; der Ordner steht in der Kalibrierung (rohdaten). Nachrechnen: r = storage.load_any(ordner);
ausrollen.analyse(r["n_roll"], r["dt"], r["params"], start=0, n_ende=…, kupplung=False).
Einstellungen (settings["ausrollen"], Gruppe Ausrollversuch): n_ende, t_max, quadratisch; dazu assistent_ziel
(Zieldrehzahl der Rolle [1/min]).
Waehrend eines Leistungslaufs laesst er sich nicht oeffnen, solange er offen ist, startet kein Lauf (Hauptfenster).
"""
import datetime as _dt
import csv
import json
import os
import time
from dataclasses import asdict
from typing import Any, Callable, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from . import __version__, ausrollen, physics, storage, theme
from .kacheln import ZonenBalken, zahl
from .kalib_page import VerlustDiagramm, werte_html
from .runner import COAST_N_ENDE, COAST_STILL_S, COAST_T_MAX

ART = "rollen_kalibrierung"            # lauf.json "art"
ORDNER_ENDUNG = "_Rollen-Kalibrierung"
VERALTET_S = 1.0                       # so lange ohne Telegramm: keine Rollendaten
MEDIAN_S = 0.3                         # Anzeige und Ende: Median der Rollendrehzahl ueber diese Zeit
SCHRITTE = ("Vorbereitung", "Beschleunigen", "Rad von der Rolle", "Messung", "Ergebnis")
VORBEREITUNG, BESCHLEUNIGEN, RAD_AB, MESSUNG, ERGEBNIS = range(5)
FARBE_LIVE = "#1f5fbf"                 # Kurve der Messung (theme.kurvenfarbe passt sie dem Thema an)

TITEL = "Rollen-Kalibrierung (Ausrollen ohne Rad)"
PHYSIK = ("<b>Einmalig: Wie schnell rollt die Rolle von allein aus?</b> Gemessen werden nur die Verluste der Rolle "
          "selbst (Lager, Luft). Wiederholen nur nach einem Umbau an der Rolle. Danach teilt PyST den Ausrollversuch "
          "nach dem Lauf (Kupplung ziehen) auf: Rolle und Reifen/Kette/Getriebe.")
SPEICHERN_HINWEIS = ("„Als Rollen-Kalibrierung speichern“ legt das Ergebnis für den Prüfstand ab (nicht je Fahrzeug). "
                     "M0/M1 bleiben die Verluste gesamt aus dem Ausrollversuch nach dem Lauf; mit der Kalibrierung "
                     "zeigt PyST zusätzlich „Reifen an Rolle“ und den Antriebsverlust (Reifen, Kette, Getriebe).")
SPEICHERN_KNOPF = "Als Rollen-Kalibrierung speichern"
SICHERHEIT = ("⚠  Vorsicht: Rolle und Hinterrad drehen weiter. Gang heraus, nicht in Rolle, Rad oder Kette greifen, "
              "nichts dagegenhalten. Motorrad sicher abstützen.")


# ---------------------------------------------------------------------------------------------- ohne Qt
def n_ende_rolle(n_ende_motor: float, ratio: float) -> float:
    """Untere Drehzahl der Rolle [1/min] aus n_ende (Motordrehzahl Rolle × i, 0 = bis Stillstand)."""
    return n_ende_motor / ratio if n_ende_motor > 0 and ratio > 0 else 0.0


def ziel_standard(n_ende_motor: float, ratio: float) -> float:
    """Zieldrehzahl der Rolle [1/min], wenn keine eingestellt ist: deutlich ueber n_ende (2,5-fach, mindestens
    n_ende + 600 und 800 1/min), auf 50 gerundet."""
    ne = n_ende_rolle(n_ende_motor, ratio)
    return float(round(max(2.5 * ne, ne + 600.0, 800.0) / 50.0) * 50.0)


def mindest_drehzahl(ziel: float, n_ende_roll: float) -> float:
    """So schnell muss die Rolle bei „Messung starten“ mindestens sein [1/min]: 70 % des Ziels und genug Abstand zu
    n_ende fuer den Drehzahlbereich der Auswertung (40 % ueber n_ende, mindestens 2 × MIN_SPANNE_RPM)."""
    return float(max(0.7 * ziel, 1.4 * n_ende_roll, n_ende_roll + 2 * ausrollen.MIN_SPANNE_RPM))


def kmh(n_roll: float, p: physics.DynoParams) -> float:
    return n_roll / 60.0 * p.roll_circ * 3.6


class AuslaufAufnahme:
    """Aufzeichnung der frei auslaufenden Rolle ab „Messung starten“ (ohne Qt, wie das Ausrollen im RunController).
    add(frames, now) -> Endegrund ("" = weiter): Rolle unter n_ende (Median der letzten 0,3 s), Stillstand,
    Drehzahl steigt wieder, t_max vergangen (Messzeit = Summe der Telegramm-Abstaende), COAST_STILL_S keine
    Telegramme (Uhrzeit)."""

    def __init__(self, p: physics.DynoParams, n_ende_motor: float, t_max: float, now: Optional[float] = None):
        self.p = p
        self.n_ende_motor = float(n_ende_motor)
        self.n_ende_roll = n_ende_rolle(n_ende_motor, p.ratio)
        self.t_max = float(t_max)
        self.t0 = time.time() if now is None else now
        self.t_letzt = self.t0
        self.t_daten = 0.0                     # Messzeit [s] aus den Telegrammen
        self.frames: list = []
        self.grund = ""
        self._hist: List[float] = []
        self._min = float("inf")

    def n_roll(self, f) -> float:
        return f.roll_hz * 60.0 / self.p.inkr

    def dauer(self) -> float:
        """Bisherige Messzeit [s]."""
        return self.t_daten

    def add(self, frames, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        if self.grund:
            return self.grund
        if frames:
            self.frames.extend(frames)
            self.t_letzt = now
            self.t_daten += sum(1.0 / f.rate for f in frames if f.rate > 0)
            rate = max(frames[-1].rate, 1.0)
            keep = max(3, int(round(MEDIAN_S * rate)))
            self._hist = (self._hist + [self.n_roll(f) for f in frames])[-keep:]
            n_med = float(np.median(self._hist))
            self._min = min(self._min, n_med)
            if self.n_ende_roll > 0 and n_med < self.n_ende_roll:
                self.grund = f"ausgerollt bis Rolle {zahl(self.n_ende_roll)} 1/min (Motor {zahl(self.n_ende_motor)})"
            elif n_med < ausrollen.N_STILL:
                self.grund = "Rolle steht"
            elif n_med > self._min * 1.03 + 50.0 / max(self.p.ratio, 1e-6):
                self.grund = "Drehzahl steigt wieder – Messung beendet"
        if not self.grund:
            if self.t_daten > self.t_max:
                self.grund = f"nach {zahl(self.t_max)} s beendet"
            elif now - self.t_letzt > COAST_STILL_S:
                self.grund = "keine Messdaten mehr – Rolle steht"
        return self.grund

    def arrays(self):
        """(n_roll [1/min], dt [s], n_meas [1/min]) wie RunController.arrays."""
        fr, p = self.frames, self.p
        n_roll = np.array([f.roll_hz for f in fr], float) * 60.0 / p.inkr
        dt = np.array([1.0 / f.rate if f.rate > 0 else 0.0 for f in fr], float)
        n_meas = np.array([f.ign_hz for f in fr], float) * 60.0 / p.imp
        return n_roll, dt, n_meas

    def auswerten(self, quadratisch: bool = True) -> ausrollen.AusrollErgebnis:
        n_roll, dt, _n_meas = self.arrays()
        return ausrollen.analyse(n_roll, dt, self.p, start=0, n_ende=self.n_ende_motor, quadratisch=quadratisch,
                                 kupplung=False)


def speichern(base_dir: str, aufnahme: AuslaufAufnahme, res: Optional[ausrollen.AusrollErgebnis],
              extra: Optional[dict] = None) -> str:
    """Rohdaten der Messung ablegen: base_dir/JJMMTT_HHMMSS_Ausrollversuch_Rolle/ mit roh.csv (wie ein Lauf) und
    lauf.json (art ART, Parameter, Ergebnis, Einstellungen des Assistenten). Liefert den Ordner."""
    stamp = _dt.datetime.now()
    folder = os.path.join(base_dir, stamp.strftime("%y%m%d_%H%M%S") + ORDNER_ENDUNG)
    n = 1
    while os.path.exists(folder):
        n += 1
        folder = os.path.join(base_dir, stamp.strftime("%y%m%d_%H%M%S") + f"{ORDNER_ENDUNG}_{n}")
    os.makedirs(folder)
    fr, p = aufnahme.frames, aufnahme.p
    t0 = fr[0].t if fr else 0.0
    diag = any(getattr(f, "diag", None) for f in fr)
    zange = diag and storage.has_zange_fields(fr)
    with open(os.path.join(folder, "roh.csv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(storage.ROH_HEADER + (storage.DIAG_HEADER if diag else []) + (storage.ZANGE_HEADER if zange else []))
        for f in fr:
            w.writerow(storage.frame_row(f, t0, p, diag, zange))
    meta = {
        "programm": f"PyST {__version__}", "art": ART, "datum": stamp.isoformat(timespec="seconds"),
        "parameter": asdict(p), "telegramme": len(fr),
        "assistent": {"start_index": 0, "n_ende": aufnahme.n_ende_motor, "t_max": aufnahme.t_max,
                      "ende": aufnahme.grund, "rad_abgehoben": True, **(extra or {})},
        "hinweis": ("Rollen-Kalibrierung, Rad abgehoben: Verluste der Rolle allein (Lager, Luft). Nachrechnen: "
                    "ausrollen.analyse(n_rolle, dt, parameter, start=0, n_ende=assistent.n_ende, kupplung=False)"),
        "ausrollen": res.als_dict(punkte=True) if res is not None else None,
    }
    with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    return folder


# ---------------------------------------------------------------------------------------------- Bausteine
class LiveZahlen(QtWidgets.QFrame):
    """Rolle [1/min] und Geschwindigkeit [km/h] gross (vom Motorrad aus lesbar), darunter Motor (Rolle × i)."""

    def __init__(self, gross_px: int = 110):
        super().__init__()
        self.setObjectName("live_zahlen")
        self.gross_px = gross_px
        self.da = False
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(28)
        self.spalten = []
        for titel, einheit in (("Rolle", "1/min"), ("Geschwindigkeit", "km/h")):
            v = QtWidgets.QVBoxLayout()
            v.setSpacing(0)
            t = QtWidgets.QLabel(titel)
            z = QtWidgets.QLabel("–")
            z.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            z.setAccessibleName(titel)
            e = QtWidgets.QLabel(einheit)
            e.setAlignment(QtCore.Qt.AlignRight)
            v.addWidget(t)
            v.addWidget(z)
            v.addWidget(e)
            lay.addLayout(v, 1)
            self.spalten.append((t, z, e))
        self.rolle, self.v = self.spalten[0][1], self.spalten[1][1]
        self.motor = QtWidgets.QLabel("")
        self.motor.setWordWrap(True)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(f"QFrame#live_zahlen {{ background: {T['panel']}; border: 1px solid {T['border']};"
                           " border-radius: 6px; }")
        farbe = T["value"] if self.da else T["dim"]
        for t, z, e in self.spalten:
            t.setStyleSheet(f"color: {T['text2']}; font-size: 17px; font-weight: 700;")
            z.setStyleSheet(f"color: {farbe}; font-size: {self.gross_px}px; font-weight: 800;")
            e.setStyleSheet(f"color: {T['text2']}; font-size: 20px; font-weight: 700;")
        self.motor.setStyleSheet(f"color: {T['text2']}; font-size: 16px;")

    def setze(self, n_roll: Optional[float], p: physics.DynoParams):
        da = n_roll is not None
        self.rolle.setText(zahl(n_roll, 0))
        self.v.setText(zahl(kmh(n_roll, p), 1) if da else "–")
        self.motor.setText(f"Motor (Rolle × i = {zahl(p.ratio, 2)}): {zahl(n_roll * p.ratio, 0)} 1/min" if da
                           else "Keine Rollendaten")
        if da != self.da:
            self.da = da
            self._stil()


def _text(px: int = 16, fett: bool = False, wrap: bool = True) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel("")
    lab.setWordWrap(wrap)
    lab.setProperty("px", px)
    lab.setProperty("fett", fett)
    return lab


# ---------------------------------------------------------------------------------------------- Assistent
class AusrollAssistent(QtWidgets.QDialog):
    """ctrl(): RunController oder None (Hauptfenster), params(): aktuelle DynoParams (Rolle/Lauf), cfg: settings
    ["ausrollen"] (n_ende, t_max, quadratisch, assistent_ziel), save(): Einstellungen speichern, runs_dir(): Ordner
    messungen, verbinden(): Messboard verbinden (True = verbunden), lauf_aktiv(): Leistungslauf laeuft.
    Daten kommen ueber telegramme(frames) vom Hauptfenster."""
    kalib_speichern = QtCore.Signal(object)            # AusrollErgebnis (rohdaten = Ordner) -> Rollen-Kalibrierung
    ausgewertet = QtCore.Signal(object, str)           # AusrollErgebnis, Herkunft (Gruppe „Letzter Ausrollversuch“)

    def __init__(self, parent, ctrl: Callable[[], Any], params: Callable[[], physics.DynoParams], cfg: dict,
                 save: Callable[[], None], runs_dir: Callable[[], str], verbinden: Callable[[], bool],
                 lauf_aktiv: Callable[[], bool]):
        super().__init__(parent)
        self.setWindowTitle(TITEL + " – Assistent")
        self.setModal(False)
        self.ctrl, self.params, self.cfg, self.save = ctrl, params, cfg, save
        self.runs_dir, self.verbinden, self.lauf_aktiv = runs_dir, verbinden, lauf_aktiv
        self.schritt = VORBEREITUNG
        self.aufnahme: Optional[AuslaufAufnahme] = None
        self.result: Optional[ausrollen.AusrollErgebnis] = None
        self.ordner = ""
        self.uebernommen = False
        self.ziel_erreicht = False
        self.zu_langsam: Tuple[str, str] = ("", "")    # Hinweis beim Beschleunigen (zu langsam, abgebrochen), Stufe
        self._t_letzt = 0.0                            # letztes Telegramm (Zeit)
        self._hist: List[float] = []
        self._keepalive = 0.0
        self.n_live: Optional[float] = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(10)
        self.titel = QtWidgets.QLabel(TITEL)
        lay.addWidget(self.titel)
        leiste = QtWidgets.QHBoxLayout()
        leiste.setSpacing(6)
        self.schritt_labels: List[QtWidgets.QLabel] = []
        for i, name in enumerate(SCHRITTE):
            lab = QtWidgets.QLabel(f"{i + 1}  {name}")
            lab.setAlignment(QtCore.Qt.AlignCenter)
            leiste.addWidget(lab, 1)
            self.schritt_labels.append(lab)
        lay.addLayout(leiste)
        self.banner = QtWidgets.QLabel("")              # Lauf läuft, nicht verbunden …
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        lay.addWidget(self.banner)
        self.seiten = QtWidgets.QStackedWidget()
        lay.addWidget(self.seiten, 1)
        self.texte: List[QtWidgets.QLabel] = []
        for bau in (self._seite_vorbereitung, self._seite_beschleunigen, self._seite_rad_ab, self._seite_messung,
                    self._seite_ergebnis):
            self.seiten.addWidget(bau())

        unten = QtWidgets.QHBoxLayout()
        self.fuss = QtWidgets.QLabel("")
        self.fuss.setWordWrap(True)
        theme.rolle(self.fuss, "neben")
        unten.addWidget(self.fuss, 1)
        self.b_schliessen = QtWidgets.QPushButton("Schließen")
        self.b_zurueck = QtWidgets.QPushButton("‹  Zurück")
        self.b_weiter = QtWidgets.QPushButton("Weiter  ›")
        theme.rolle(self.b_weiter, "primaer")
        for b in (self.b_schliessen, self.b_zurueck, self.b_weiter):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMinimumSize(150, 46)
            unten.addWidget(b)
        lay.addLayout(unten)
        self.b_weiter.clicked.connect(self._weiter)
        self.b_zurueck.clicked.connect(self._zurueck)
        self.b_schliessen.clicked.connect(self._schliessen_knopf)

        theme.bei_wechsel(self, lambda _t: self._stil())
        self.resize(1100, 740)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.takt)
        self.timer.start(100)
        self.zeige(VORBEREITUNG)

    # ------------------------------------------------------------------ Seiten
    def _seite(self) -> Tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(10)
        return w, v

    def _anleitung(self, text: str) -> QtWidgets.QLabel:
        lab = _text(24, True)
        lab.setText(text)
        self.texte.append(lab)
        return lab

    def _label(self, px: int = 16, fett: bool = False) -> QtWidgets.QLabel:
        lab = _text(px, fett)
        self.texte.append(lab)
        return lab

    def _seite_vorbereitung(self) -> QtWidgets.QWidget:
        w, v = self._seite()
        v.addWidget(self._anleitung("Rolle kalibrieren: Verluste der Rolle allein messen – ohne Leistungslauf"))
        zeile = QtWidgets.QHBoxLayout()
        self.v_status = self._label(20, True)
        zeile.addWidget(self.v_status, 1)
        self.v_verbinden = QtWidgets.QPushButton("Verbinden")
        self.v_verbinden.setMinimumSize(150, 44)
        self.v_verbinden.setAutoDefault(False)
        self.v_verbinden.clicked.connect(self._verbinden)
        zeile.addWidget(self.v_verbinden)
        v.addLayout(zeile)
        ablauf = self._label(17)
        ablauf.setText("So geht es:<br>"
                       "1. Rolle mit dem Motorrad auf Drehzahl bringen.<br>"
                       "2. Gas weg, Rad von der Rolle nehmen (anheben) – die Rolle läuft frei aus.<br>"
                       "3. „Messung starten“: PyST zeichnet das Auslaufen auf und bestimmt daraus das Verlustmoment "
                       "der Rolle (M0, M1, m2).<br>"
                       "4. Ergebnis prüfen und als Rollen-Kalibrierung speichern.")
        v.addWidget(ablauf)
        self.v_physik = QtWidgets.QLabel(PHYSIK)
        self.v_physik.setWordWrap(True)
        v.addWidget(self.v_physik)
        self.v_einst = self._label(15)
        theme.rolle(self.v_einst, "neben")
        v.addWidget(self.v_einst)
        v.addStretch(1)
        return w

    def _seite_beschleunigen(self) -> QtWidgets.QWidget:
        w, v = self._seite()
        v.addWidget(self._anleitung("Rolle mit dem Motorrad auf Drehzahl bringen."))
        self.b_zahlen = LiveZahlen()
        v.addWidget(self.b_zahlen)
        self.b_balken = ZonenBalken(0.0, 1000.0, hoehe=30)
        v.addWidget(self.b_balken)
        zeile = QtWidgets.QHBoxLayout()
        lab = self._label(17, True)
        lab.setText("Ziel Rolle")
        zeile.addWidget(lab)
        self.ziel = QtWidgets.QDoubleSpinBox()
        self.ziel.setRange(200, 5000)
        self.ziel.setDecimals(0)
        self.ziel.setSingleStep(50)
        self.ziel.setSuffix(" 1/min")
        self.ziel.setKeyboardTracking(False)
        self.ziel.setMinimumWidth(150)
        self.ziel.setToolTip("Zieldrehzahl der Rolle: deutlich über der Enddrehzahl der Messung (gespeichert)")
        p = self.params()
        self.ziel.setValue(float(self.cfg.get("assistent_ziel") or ziel_standard(self._n_ende(), p.ratio)))
        self.ziel.valueChanged.connect(self._ziel_geaendert)
        zeile.addWidget(self.ziel)
        self.b_ziel_info = self._label(16)
        theme.rolle(self.b_ziel_info, "neben")
        zeile.addWidget(self.b_ziel_info, 1)
        v.addLayout(zeile)
        self.b_status = self._label(22, True)
        v.addWidget(self.b_status)
        v.addWidget(self.b_zahlen.motor)
        v.addStretch(1)
        return w

    def _seite_rad_ab(self) -> QtWidgets.QWidget:
        w, v = self._seite()
        v.addWidget(self._anleitung("Gas weg, Rad von der Rolle nehmen (anheben) – die Rolle läuft frei aus."))
        self.r_sicher = QtWidgets.QLabel(SICHERHEIT)
        self.r_sicher.setWordWrap(True)
        theme.rolle(self.r_sicher, "banner_warnung")
        v.addWidget(self.r_sicher)
        self.r_zahlen = LiveZahlen()
        v.addWidget(self.r_zahlen)
        self.r_status = self._label(22, True)
        v.addWidget(self.r_status)
        hint = self._label(16)
        hint.setText("Erst das Rad ganz abheben, dann „Messung starten“. Die Rolle nicht abbremsen. "
                     "Die Messung endet von selbst.")
        v.addWidget(hint)
        v.addWidget(self.r_zahlen.motor)
        v.addStretch(1)
        return w

    def _seite_messung(self) -> QtWidgets.QWidget:
        w, v = self._seite()
        v.addWidget(self._anleitung("Messung läuft – Rolle frei auslaufen lassen, nicht berühren."))
        zeile = QtWidgets.QHBoxLayout()
        self.m_zahlen = LiveZahlen(gross_px=72)
        zeile.addWidget(self.m_zahlen, 3)
        rechts = QtWidgets.QVBoxLayout()
        self.m_zeit_titel = self._label(17, True)
        self.m_zeit_titel.setText("Zeit")
        self.m_zeit = QtWidgets.QLabel("0,0 s")
        self.m_zeit.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        rechts.addWidget(self.m_zeit_titel)
        rechts.addWidget(self.m_zeit)
        self.m_ende = self._label(15)
        theme.rolle(self.m_ende, "neben")
        rechts.addWidget(self.m_ende)
        rechts.addStretch(1)
        zeile.addLayout(rechts, 1)
        v.addLayout(zeile)
        self.m_plot = pg.PlotWidget(background=theme.farbe("plot"))
        self.m_plot.setMenuEnabled(False)
        self.m_plot.showGrid(x=True, y=True, alpha=0.35)
        self.m_plot.setLabel("bottom", "Zeit [s]")
        self.m_plot.setLabel("left", "Rolle [1/min]")
        self.m_plot.setMinimumHeight(220)
        self.m_kurve = self.m_plot.plot([], [], pen=pg.mkPen(FARBE_LIVE, width=2.5))
        self.m_linie = pg.InfiniteLine(angle=0, movable=False)
        self.m_plot.addItem(self.m_linie)
        theme.diagramm(self.m_plot, self._plot_thema)
        v.addWidget(self.m_plot, 1)
        return w

    def _seite_ergebnis(self) -> QtWidgets.QWidget:
        w, v = self._seite()
        self.e_titel = self._anleitung("Rollen-Kalibrierung: Verluste der Rolle allein (Lager, Luft)")
        v.addWidget(self.e_titel)
        self.e_meldung = QtWidgets.QLabel("")
        self.e_meldung.setWordWrap(True)
        v.addWidget(self.e_meldung)
        zeile = QtWidgets.QHBoxLayout()
        self.e_werte = QtWidgets.QLabel("")
        self.e_werte.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.e_werte.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.e_werte.setMinimumWidth(330)
        zeile.addWidget(self.e_werte)
        self.e_diagramm = VerlustDiagramm(self.cfg.get("anzeige", "ps"), hoehe=240)
        zeile.addWidget(self.e_diagramm, 1)
        v.addLayout(zeile, 1)
        self.e_physik = self._label(14)
        self.e_physik.setText(SPEICHERN_HINWEIS)
        theme.rolle(self.e_physik, "neben")
        v.addWidget(self.e_physik)
        self.e_datei = self._label(13)
        self.e_datei.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        theme.rolle(self.e_datei, "neben")
        v.addWidget(self.e_datei)
        return w

    # ------------------------------------------------------------------ Stil
    def _stil(self):
        T = theme.tokens()
        self.titel.setStyleSheet(f"color: {T['text']}; font-size: 22px; font-weight: 800;")
        for lab in self.texte:
            px, fett = lab.property("px") or 16, bool(lab.property("fett"))
            if lab.property("rolle") == "neben":
                lab.setStyleSheet(f"font-size: {px}px;" + (" font-weight: 700;" if fett else ""))
            else:
                lab.setStyleSheet(f"color: {T['text']}; font-size: {px}px;" + (" font-weight: 700;" if fett else ""))
        self.v_physik.setStyleSheet(f"color: {T['text']}; background: {T['panel']}; border: 1px solid "
                                    f"{T['warn_fg']}; border-radius: 5px; padding: 8px; font-size: 16px;")
        self.r_sicher.setStyleSheet("font-size: 17px; padding: 8px;")
        self.m_zeit.setStyleSheet(f"color: {T['value']}; font-size: 64px; font-weight: 800;")
        for b in (self.b_schliessen, self.b_zurueck, self.b_weiter):
            b.setStyleSheet("QPushButton { font-size: 16px; font-weight: 700; min-height: 36px; min-width: 130px; }"
                            f" QPushButton:disabled {{ color: {T['dim']}; }}")
        self.v_verbinden.setStyleSheet("QPushButton { font-size: 16px; font-weight: 700; min-height: 36px;"
                                       f" min-width: 130px; }} QPushButton:disabled {{ color: {T['dim']}; }}")
        self.ziel.setStyleSheet("QDoubleSpinBox { font-size: 17px; font-weight: 700; min-height: 30px; }")
        self._schritt_stil()
        self._status_stil()

    def _schritt_stil(self):
        T = theme.tokens()
        for i, lab in enumerate(self.schritt_labels):
            if i == self.schritt:
                css = (f"background: {T['accent']}; color: {T['accent_text']}; border: 1px solid "
                       f"{T['accent_border']}; font-weight: 800;")
            elif i < self.schritt:
                css = f"background: {T['panel']}; color: {T['ok_fg']}; border: 1px solid {T['border']}; font-weight: 700;"
            else:
                css = f"background: {T['panel']}; color: {T['dim']}; border: 1px solid {T['border']};"
            lab.setStyleSheet(css + " border-radius: 4px; padding: 6px 4px; font-size: 15px;")

    def _status_stil(self):
        """Farben der Zustandszeilen (GUT/WARNUNG/FEHLER als Schrift) und des Banners."""
        T = theme.tokens()
        tok = {theme.GUT: "ok_fg", theme.WARNUNG: "warn_fg", theme.FEHLER: "err_fg"}
        for lab in (self.v_status, self.b_status, self.r_status):
            st = lab.property("stufe_aa") or ""
            farbe = T[tok[st]] if st in tok else T["text"]
            px = lab.property("px") or 20
            lab.setStyleSheet(f"color: {farbe}; font-size: {px}px; font-weight: 700;")
        if self.result is not None:
            self._ergebnis_meldung_stil()

    def _plot_thema(self, _thema: str):
        self.m_kurve.setPen(pg.mkPen(theme.kurvenfarbe(FARBE_LIVE), width=2.5))
        self.m_linie.setPen(pg.mkPen(theme.farbe("plot_axis"), width=1.5, style=QtCore.Qt.DashLine))

    def _zustand(self, lab: QtWidgets.QLabel, text: str, stufe: str = ""):
        if lab.text() != text:
            lab.setText(text)
        if (lab.property("stufe_aa") or "") != stufe:
            lab.setProperty("stufe_aa", stufe)
            self._status_stil()

    # ------------------------------------------------------------------ Einstellungen
    def _n_ende(self) -> float:
        return float(self.cfg.get("n_ende", COAST_N_ENDE))

    def _t_max(self) -> float:
        return float(self.cfg.get("t_max", COAST_T_MAX))

    def _quadratisch(self) -> bool:
        return bool(self.cfg.get("quadratisch", True))

    def _ziel_geaendert(self, v: float):
        self.cfg["assistent_ziel"] = float(v)
        self.save()
        self.takt()

    def mindest(self) -> float:
        return mindest_drehzahl(self.ziel.value(), n_ende_rolle(self._n_ende(), self.params().ratio))

    # ------------------------------------------------------------------ Daten
    def telegramme(self, frames):
        """Neue Telegramme vom Hauptfenster (ausserhalb eines Laufs)."""
        if not frames:
            return
        now = time.time()
        p = self.params()
        self._t_letzt = now
        rate = max(frames[-1].rate, 1.0)
        keep = max(3, int(round(MEDIAN_S * rate)))
        self._hist = (self._hist + [f.roll_hz * 60.0 / p.inkr for f in frames])[-keep:]
        self.n_live = float(np.median(self._hist))
        if self.schritt == MESSUNG and self.aufnahme is not None:
            if self.aufnahme.add(frames, now):
                self._messung_fertig()

    def daten_da(self) -> bool:
        return self.ctrl() is not None and time.time() - self._t_letzt <= VERALTET_S

    def live_n(self) -> Optional[float]:
        return self.n_live if self.daten_da() else None

    def _datenstrom(self):
        """Ausgabe des Messboards am Laufen halten (Live-Anzeige aus, Autostopp der Firmware bei Stillstand)."""
        c = self.ctrl()
        if c is None or self.schritt == ERGEBNIS or self.lauf_aktiv() or not self.isVisible():
            return
        now = time.time()
        link = getattr(c, "link", None)
        if link is not None and not link.streaming() and now - self._keepalive > 2.0:
            self._keepalive = now
            try:
                link.keepalive()
            except Exception:
                pass

    # ------------------------------------------------------------------ Ablauf
    def zeige(self, schritt: int):
        self.schritt = schritt
        self.seiten.setCurrentIndex(schritt)
        if schritt == BESCHLEUNIGEN:
            self.ziel_erreicht = False
        self._schritt_stil()
        self.takt()

    def _weiter(self):
        s = self.schritt
        if s == VORBEREITUNG and self.live_n() is not None and not self.lauf_aktiv():
            self.zu_langsam = ("", "")
            self.zeige(BESCHLEUNIGEN)
        elif s == BESCHLEUNIGEN and self.ziel_erreicht:
            self.zeige(RAD_AB)
        elif s == RAD_AB:
            self.messung_starten()
        elif s == MESSUNG:
            self._messung_fertig("Messung von Hand beendet")
        elif s == ERGEBNIS:
            self._uebernehmen()

    def _zurueck(self):
        s = self.schritt
        if s == MESSUNG:
            self.messung_abbrechen()
        elif s == ERGEBNIS:                         # Nochmal
            self.result = None
            self.zu_langsam = ("", "")
            self.zeige(BESCHLEUNIGEN)
        elif s > VORBEREITUNG:
            self.zu_langsam = ("", "")
            self.zeige(s - 1)

    def _schliessen_knopf(self):
        if self.schritt == MESSUNG:
            self.messung_abbrechen()
        else:
            self.close()

    def _verbinden(self):
        self.verbinden()
        self.takt()

    def messung_starten(self) -> bool:
        """„Messung starten“: nur ueber der Mindestdrehzahl, sonst zurueck zum Beschleunigen."""
        if self.lauf_aktiv():
            return False
        n = self.live_n()
        mind = self.mindest()
        if n is None or n < mind:
            self.zu_langsam = (f"Zu langsam ({zahl(n or 0.0)} 1/min, mindestens {zahl(mind)} 1/min) – "
                               "nochmal beschleunigen.", theme.FEHLER)
            self.zeige(BESCHLEUNIGEN)
            return False
        self.zu_langsam = ("", "")
        self.aufnahme = AuslaufAufnahme(self.params(), self._n_ende(), self._t_max())
        self.m_kurve.setData([], [])
        self.m_linie.setPos(self.aufnahme.n_ende_roll)
        self.m_linie.setVisible(self.aufnahme.n_ende_roll > 0)
        self.m_plot.enableAutoRange()
        self.zeige(MESSUNG)
        return True

    def messung_abbrechen(self):
        """Abbrechen: nichts auswerten, nichts speichern, zurueck zum Beschleunigen."""
        self.aufnahme = None
        self.zu_langsam = ("Messung abgebrochen.", theme.WARNUNG)
        self.zeige(BESCHLEUNIGEN)

    def _messung_fertig(self, grund: str = ""):
        a = self.aufnahme
        if a is None or self.schritt != MESSUNG:
            return
        if grund and not a.grund:
            a.grund = grund
        self.aufnahme = None
        res = a.auswerten(self._quadratisch())
        self.result = res
        self.uebernommen = False
        try:
            self.ordner = speichern(self.runs_dir(), a, res, {"ziel_rolle": self.ziel.value(),
                                                              "quadratisch": self._quadratisch()})
        except OSError as exc:
            self.ordner = ""
            self.e_datei.setText(f"Rohdaten nicht gespeichert: {exc}")
        else:
            res.rohdaten = self.ordner
            self.e_datei.setText(f"Ende: {a.grund}.  Rohdaten: {os.path.join(self.ordner, 'roh.csv')}")
        self._ergebnis_zeigen(a)
        self.zeige(ERGEBNIS)
        self.ausgewertet.emit(res, "Rollen-Kalibrierung (Assistent): Rolle allein, Rad abgehoben"
                              + (f" · {os.path.basename(self.ordner)}" if self.ordner else ""))

    def _ergebnis_zeigen(self, a: AuslaufAufnahme):
        r = self.result
        self.e_werte.setText(werte_html(r))
        self._diagramm()
        self._ergebnis_meldung_stil()

    def _ergebnis_meldung_stil(self):
        r = self.result
        if r is None:
            return
        T = theme.tokens()
        if r.ok:
            text = "<br>".join(r.hinweise) if r.hinweise else "Auswertung in Ordnung."
            if self.uebernommen:
                text = ("✔  Als Rollen-Kalibrierung gespeichert – gilt ab jetzt für neue Läufe und den Ausrollversuch "
                        "nach dem Lauf." + ("<br>" + text if r.hinweise else ""))
            farbe = T["warn_fg"] if r.hinweise else T["ok_fg"]
        else:
            text = r.meldung
            farbe = T["err_fg"]
        self.e_meldung.setText(text)
        self.e_meldung.setStyleSheet(f"color: {farbe}; font-size: 17px; font-weight: 700;")

    def _diagramm(self):
        """Ergebnis mit der bisherigen Rollen-Kalibrierung (aus params(), nach dem Speichern = dieses Ergebnis)."""
        p = self.params()
        self.e_diagramm.zeige(self.result, p, ausrollen.RollenKalibrierung.aus_params(p))

    def _uebernehmen(self):
        """„Als Rollen-Kalibrierung speichern“ (das Hauptfenster speichert, nicht in M0/M1)."""
        r = self.result
        if r is None or not r.ok or self.uebernommen:
            return
        self.kalib_speichern.emit(r)
        self.uebernommen = True
        self._diagramm()
        self._ergebnis_meldung_stil()
        self.takt()

    # ------------------------------------------------------------------ Takt
    def takt(self):
        self._datenstrom()
        p = self.params()
        n = self.live_n()
        s = self.schritt
        lauf = self.lauf_aktiv()
        verbunden = self.ctrl() is not None
        if lauf:
            self.banner.setText("Ein Leistungslauf läuft – der Assistent wartet, bis er fertig ist.")
            theme.rolle(self.banner, "banner_fehler")
        self.banner.setVisible(lauf)
        ne_roll = n_ende_rolle(self._n_ende(), p.ratio)
        weiter, zurueck, schliessen = "Weiter  ›", "‹  Zurück", "Schließen"
        w_an, z_an = False, s > VORBEREITUNG
        if s == VORBEREITUNG:
            if not verbunden:
                self._zustand(self.v_status, "✖  Kein Messboard verbunden – „Verbinden“ drücken.", theme.FEHLER)
            elif n is None:
                self._zustand(self.v_status, "⚠  Messboard verbunden, aber keine Rollendaten.", theme.WARNUNG)
            else:
                self._zustand(self.v_status, f"✔  Messboard verbunden – Rollendaten kommen (Rolle {zahl(n)} 1/min).",
                              theme.GUT)
            self.v_verbinden.setVisible(not verbunden)
            ende = (f"Rolle {zahl(ne_roll)} 1/min (Motor {zahl(self._n_ende())} 1/min)" if ne_roll > 0
                    else "Stillstand")
            self.v_einst.setText(f"Die Messung endet bei {ende}, bei Stillstand oder spätestens nach "
                                 f"{zahl(self._t_max())} s – einstellbar in der Gruppe „Ausrollversuch“. "
                                 f"Auswertung {'mit quadratischem Anteil m2' if self._quadratisch() else 'linear'}.")
            w_an = n is not None
        elif s == BESCHLEUNIGEN:
            ziel = self.ziel.value()
            self.b_zahlen.setze(n, p)
            hi = max(ziel * 1.3, (n or 0.0) * 1.05)
            if abs(self.b_balken.hi - hi) > 1.0 or self.b_balken.zonen[:1] != [(0.0, ziel, "cold")]:
                self.b_balken.set_bereich(0.0, hi, ticks=[0.0, round(self.mindest()), ziel])
                self.b_balken.set_zonen([(0.0, ziel, "cold"), (ziel, hi, "ok")])
            self.b_balken.set_wert(n)
            self.b_ziel_info.setText(f"≈ {zahl(kmh(ziel, p), 0)} km/h · Motor ≈ {zahl(ziel * p.ratio)} 1/min "
                                     f"(Rolle × i) · Messung starten ab {zahl(self.mindest())} 1/min")
            if n is not None and n >= ziel:
                self.ziel_erreicht = True
            if n is None:
                self._zustand(self.b_status, "Keine Rollendaten – Messboard prüfen.", theme.FEHLER)
            elif self.ziel_erreicht:
                vorher = self.zu_langsam[0] + "  " if self.zu_langsam[0] else ""
                self._zustand(self.b_status, vorher + "✔  Ziel erreicht – „Weiter“.", theme.GUT)
            elif self.zu_langsam[0]:
                self._zustand(self.b_status, self.zu_langsam[0] + f"  Noch {zahl(ziel - n)} 1/min bis zum Ziel.",
                              self.zu_langsam[1])
            else:
                self._zustand(self.b_status, f"Noch {zahl(ziel - n)} 1/min bis zum Ziel.", "")
            w_an = self.ziel_erreicht
        elif s == RAD_AB:
            self.r_zahlen.setze(n, p)
            mind = self.mindest()
            if n is None:
                self._zustand(self.r_status, "Keine Rollendaten – Messboard prüfen.", theme.FEHLER)
            elif n >= mind:
                self._zustand(self.r_status, f"Rolle schnell genug (mindestens {zahl(mind)} 1/min) – "
                                             "Rad ab? Dann „Messung starten“.", theme.GUT)
            else:
                self._zustand(self.r_status, f"Rolle zu langsam (unter {zahl(mind)} 1/min) – zurück und nochmal "
                                             "beschleunigen.", theme.FEHLER)
            weiter = "Messung starten"
            w_an = n is not None
        elif s == MESSUNG:
            a = self.aufnahme
            self.m_zahlen.setze(n, p)
            if a is not None:
                self.m_zeit.setText(f"{zahl(a.dauer(), 1)} s")
                ende = f"Rolle {zahl(a.n_ende_roll)} 1/min, " if a.n_ende_roll > 0 else ""
                self.m_ende.setText(f"Ende bei {ende}Stillstand oder nach {zahl(a.t_max)} s.")
                if a.frames:
                    n_roll, dt, _nm = a.arrays()
                    self.m_kurve.setData(ausrollen.zeitachse(dt), n_roll)
                if a.add([], time.time()):
                    self._messung_fertig()
                    return
            weiter, zurueck, schliessen = "Beenden und auswerten", "Abbrechen", "Abbrechen"
            w_an = True
            z_an = True
        elif s == ERGEBNIS:
            r = self.result
            weiter, zurueck = SPEICHERN_KNOPF, "Nochmal"
            w_an = r is not None and r.ok and not self.uebernommen
            z_an = True
        if lauf and s != ERGEBNIS:
            w_an = False
        self.b_zurueck.setVisible(s not in (VORBEREITUNG, MESSUNG))
        for b, text, an in ((self.b_weiter, weiter, w_an), (self.b_zurueck, zurueck, z_an),
                            (self.b_schliessen, schliessen, True)):
            if b.text() != text:
                b.setText(text)
            if b.isEnabled() != an:
                b.setEnabled(an)
        fuss = {VORBEREITUNG: "Der Assistent zeichnet nur die Messung auf, keinen Leistungslauf.",
                BESCHLEUNIGEN: "Ziel erreicht? Dann „Weiter“ – die Drehzahl muss dort nicht gehalten werden.",
                RAD_AB: "Erst Rad abheben, dann „Messung starten“.",
                MESSUNG: "„Beenden und auswerten“ wertet die bisherigen Daten aus. „Abbrechen“ verwirft sie.",
                ERGEBNIS: "„Nochmal“ beginnt wieder beim Beschleunigen."}[s]
        if self.fuss.text() != fuss:
            self.fuss.setText(fuss)

    # ------------------------------------------------------------------ Schliessen
    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape and self.schritt == MESSUNG:
            self.messung_abbrechen()                    # Esc in der Messung: abbrechen, Fenster bleibt
            ev.accept()
            return
        super().keyPressEvent(ev)

    def done(self, r):
        self.timer.stop()
        self.aufnahme = None
        super().done(r)

    def closeEvent(self, ev):
        self.timer.stop()
        self.aufnahme = None
        super().closeEvent(ev)
