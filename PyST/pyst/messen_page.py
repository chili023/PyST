"""
Reiter Messen (Entwurf Oberflaeche 2026-11-08, Runden 2 und 3): Bausteine und Messlogik. MessenMixin wird von
gui.MainWindow geerbt (die Methoden arbeiten auf dem Hauptfenster: self.plot, self.ctrl, self.db …).

Aufbau von oben nach unten:
  Kopfzeile    Fahrzeug | Setup | Notiz | Auto-Speichern | Laufzustand (StatusFeld) | Trennen/Verbinden |
               Lauf-Einstellungen … (Unterzeile: Übersetzung, Filter; Popup LaufEinstellungen mit Übersetzungsart von
               Hand/Funkenmessung, Einmessen, Filter, n vom Gas/n Stop – dieselben Felder wie Einstellungen › Rolle/Lauf) |
               START (F1), im Lauf derselbe Knopf rot: Abbrechen (Esc) bzw. Ausrollen beenden (Esc)
  Setup-Werte  Schnellwerte (db.schnellwert_plaetze je Fahrzeug, 0 … SCHNELLWERT_MAX Plaetze: „＋“ fuegt an, Titel-Menue
               waehlt das Feld oder entfernt den Platz), direkt editierbar;
               geaendert: gelb mit „vorher 135“, Marke „✎ Setup: …“, Knoepfe Als neues Setup speichern … /
               nur für diesen Lauf / Verwerfen; die Setup-Auswahl zeigt „✎ geändert“
  Mitte        Läufe als SeitenLeiste (32-px-Streifen, Overlay nach 300 ms, Anpinnen) | Diagramm mit „GAS WEG“,
               Legende der Referenz und der Zeile „▸ Werte am Cursor“ (Klick: Tabelle) | rechte Spalte
  rechts       UMGEBUNG T/p mit Alter, LIVE Drehzahl gross + 5 Plaetze (db.live_plaetze je Fahrzeug, Kanal per
               Klick auf den Titel, Balkengrenzen per Rechtsklick), ERGEBNIS Pmax/Mmax
Diagramm: 5 Plaetze (db.messplaetze) = gesperrte Laeufe (Schloss, auch Mittelwert-Referenzen mit Band min–max)
und die neuesten Laeufe (rollierend). Das Haekchen blendet einen Platz aus, aeltere Laeufe kommen per Schloss hinein.
T/p werden nur zwischen den Laeufen gelesen (link.climate() unterbricht den Datenstrom): beim START (wie bisher),
von Hand und alle KLIMA_ALLE_S, solange nichts laeuft.

Einstellung START_NACHFRAGE ("start_nachfrage_setup", Standard True): bei geaendertem, nicht gespeichertem Setup
vor START fragen (Neues Setup speichern / Nur für diesen Lauf / Abbrechen). Schalter: Einstellungen › Rolle/Lauf.
"""
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import mittelwert, theme
from .bg import wait_in_thread, waiting
from .channels import fault_text, lambda_from_afr
from .db import (ART_LAUF, ECU_PREFIX, LIVE_KANAELE, LIVE_STANDARD, MESSPLAETZE, SCHNELLWERT_MAX,
                 SCHNELLWERT_STANDARD, SETUP_FIELDS, field_label, messplaetze, wert_text)
from .kacheln import GROSS, KLEIN, Kanal, SeitenLeiste, WertKachel, farb_icon, schloss_icon, zahl
from .plots import EXTRA_PREFIX, DynoPlot, fmt_value, run_color
from .runner import ACTIVE, COAST, DONE, RUN, WAIT
from .statusleiste import StatusFeld
from .umbruch import UmbruchLayout

LIVE_KEY = "live"                          # Schluessel der laufenden Messung im Diagramm
START_NACHFRAGE = "start_nachfrage_setup"  # Einstellung (bool, Standard True), Schalter unter Rolle/Lauf
KLIMA_ALLE_S = 300.0                       # T/p zwischen den Laeufen hoechstens so oft lesen
KLIMA_ERSTES_S = 10.0                      # erstes Lesen so lange nach dem Verbinden (falls kein START davor)
GAS_WEG_HALTEN_S = 2.0                     # „GAS WEG“ steht so lange nach dem letzten Erreichen von n Stop
GEAENDERT = "  ✎ geändert"                 # Zusatz in der Setup-Auswahl
START_TEXT = "START  (F1)"                  # derselbe Knopf im Lauf: ABBRECHEN_TEXT bzw. AUSROLLEN_TEXT
ABBRECHEN_TEXT = "Abbrechen  (Esc)"
AUSROLLEN_TEXT = "Ausrollen beenden\n(Esc)"
LEISTE_BREITE = 440                        # Laeufe-Liste (Overlay bzw. angepinnt)
SPALTE_BREITE = 330                        # rechte Spalte
MW_BREITE = 3.6                            # Linienbreite Mittelwert (Laeufe 2,0)

# Kurzname eines Setup-Felds in der Marke („HD 135 → HD 138“) und im Vorschlag fuer den neuen Setup-Namen
KURZ = {"hauptduese": "HD", "nadelduese": "ND", "leerlaufduese": "LLD", "clip": "Clip", "zzp": "ZZP",
        "nadel": "Nadel", "sekundaer": "Sek.", "primaer": "Prim.", "kerze": "Kerze", "auspuff": "Auspuff"}

# Balkenbereich je PST-Kanal: (min, max, Nachkommastellen, Zonen, Marke statt Fuellung)
_STD = {p["kanal"]: p for p in LIVE_STANDARD}
KANAL_BEREICH: Dict[str, Tuple[float, float, int, list, bool]] = {
    "n": (0.0, 13000.0, 0, [], False),
    "ign": (0.0, 13000.0, 0, [], False),
    "v": (0.0, 200.0, 1, [], False),
    "egt1": (200.0, 1000.0, 0, _STD["egt1"]["zonen"], False),
    "egt2": (200.0, 1000.0, 0, _STD["egt1"]["zonen"], False),
    "lam": (0.7, 1.3, 2, _STD["lam"]["zonen"], True),
    "ratio": (0.0, 10.0, 3, [], False),
    "ratio_ecu": (0.0, 10.0, 3, [], False),
    "rate": (0.0, 100.0, 1, [], False),
}
DREHZAHL_QUELLEN = [("n", "Drehzahl Motor (Rolle × i)", "PST"), ("ign", "Drehzahl Motor (Zündung)", "PST"),
                    (ECU_PREFIX + "RPMValue", "Drehzahl Motor (ECU)", "ECU")]
DREHZAHL_STANDARD = {"quelle": "n", "min": 0.0, "max": 13000.0,
                     "zonen": [[10500.0, 11500.0, "warn"], [11500.0, 13000.0, "err"]]}
STUFEN_TEXT = [("", "–"), ("ok", "gut (grün)"), ("warn", "Warnung (gelb)"), ("err", "Fehler (rot)"),
               ("cold", "kalt (blau)")]


def ecu_bereich(name: str) -> Tuple[float, float, int, list, bool]:
    """Vorschlag fuer den Balken eines ECU-Kanals (rusEFI-Name), solange keiner gespeichert ist."""
    n = name.lower()
    if "rpm" in n:
        return 0.0, 13000.0, 0, [], False
    if "coolant" in n or n.startswith("clt"):
        return 0.0, 130.0, 0, _STD[ECU_PREFIX + "coolant"]["zonen"], False
    if "lambda" in n:
        return 0.7, 1.3, 2, _STD["lam"]["zonen"], True
    if "afr" in n:
        return 10.0, 20.0, 1, [], True
    if "intake" in n or n.startswith("iat"):
        return 0.0, 80.0, 0, [], False
    if "map" in n:
        return 0.0, 250.0, 0, [], False
    if "tps" in n or "throttle" in n:
        return 0.0, 100.0, 0, [], False
    if "ve" in n:
        return 0.0, 120.0, 0, [], False
    return 0.0, 100.0, 1, [], False


# Titel bekannter rusEFI-Kanaele, solange die .ini (noch) keinen liefert
ECU_TITEL = {"coolant": ("CLT", "°C"), "intake": ("IAT", "°C"), "RPMValue": ("Drehzahl (ECU)", "1/min"),
             "veValue": ("Fuel VE", "%"), "MAPValue": ("MAP", "kPa"), "TPSValue": ("TPS", "%"),
             "AFRValue": ("AFR (ECU)", ""), "lambdaValue": ("Lambda (ECU)", "λ"), "VBatt": ("Batterie", "V")}


def ecu_titel(name: str, titel: str = "", einheit: str = "") -> Tuple[str, str]:
    """Titel und Einheit eines ECU-Kanals; ohne .ini (Titel = Rohname) aus ECU_TITEL."""
    if titel and titel != name:
        return titel, einheit
    t, e = ECU_TITEL.get(name, (name, ""))
    return t, einheit or e


def kanal_bereich(kanal: str) -> Tuple[float, float, int, list, bool]:
    if kanal.startswith(ECU_PREFIX):
        return ecu_bereich(kanal[len(ECU_PREFIX):])
    return KANAL_BEREICH.get(kanal, (0.0, 100.0, 1, [], False))


def kurz(feld: str) -> str:
    return KURZ.get(feld) or field_label(feld).split(" [")[0]


def alter_text(sek: float) -> str:
    """Alter eines Messwerts: „gerade eben“, „vor 40 s“, „vor 3 min“, „vor 2 h“."""
    if sek < 10:
        return "gerade eben"
    if sek < 60:
        return f"vor {int(sek) // 10 * 10} s"
    if sek < 3600:
        return f"vor {int(sek // 60)} min"
    return f"vor {int(sek // 3600)} h"


def _speichern(settings: dict):
    from .gui import save_settings                 # spaet: gui importiert dieses Modul
    save_settings(settings)


# =============================================================================================== Kacheln
def _rahmen(w: QtWidgets.QFrame, name: str):
    w.setObjectName(name)
    T = theme.tokens()
    w.setStyleSheet(f"QFrame#{name} {{ background: {T['panel']}; border: 1px solid {T['border']}; border-radius: 5px; }}"
                    "QLabel { background: transparent; border: none; }")


def abschnitt(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)   # lange Titel kuerzen
    theme.rolle(lab, "abschnitt")
    return lab


BAND_TEXT = {"minmax": "Band = min–max der Quellläufe", "sigma": "Band = Mittel ± σ der Quellläufe", "kein": ""}


def band_art(row) -> str:
    """Bandart eines Mittelwert-Laufs, wie die Auswertung sie in mittelwert.json ablegt ("minmax"|"sigma"|"kein")."""
    try:
        with open(os.path.join(row.ordner, mittelwert.JSON_NAME), encoding="utf-8") as fh:
            art = json.load(fh).get("band", "minmax")
    except (OSError, ValueError, TypeError, AttributeError):
        return "minmax"
    return art if art in BAND_TEXT else "minmax"


def tick_dez(lo: float, hi: float) -> int:
    """Nachkommastellen der Skala (0–200 -> 0, 0,7–1,3 -> 1)."""
    from .plots import nice_step
    st = nice_step((hi - lo) / 4)
    return 0 if st >= 1 else (1 if st >= 0.1 else 2)


def kachel_groesse(k: WertKachel, gross: bool):
    """Kachel zwischen gross (Messen) und kompakt (wenig Hoehe) umschalten."""
    k.groesse = GROSS if gross else KLEIN
    if isinstance(k, DrehzahlKachel):
        k.balken.bar_h = 20 if gross else 14
    else:
        k.balken.bar_h = 14 if gross else 10
    k.balken.setFixedHeight(k.balken.bar_h + 18)
    k._stil()


class KlimaKachel(QtWidgets.QFrame):
    """Kleine Kachel T bzw. p der Basis: Titel, Wert mit Einheit, darunter das Alter („gelesen vor 3 min“)."""

    def __init__(self, titel: str, einheit: str):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(0)
        self.titel = QtWidgets.QLabel(titel)
        lay.addWidget(self.titel)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        row.addStretch(1)
        self.wert = QtWidgets.QLabel("–")
        self.einheit = QtWidgets.QLabel(einheit)
        row.addWidget(self.wert)
        row.addWidget(self.einheit, 0, QtCore.Qt.AlignBottom)
        lay.addLayout(row)
        self.alter = QtWidgets.QLabel("")
        self.alter.setAlignment(QtCore.Qt.AlignRight)
        lay.addWidget(self.alter)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        _rahmen(self, "klimakachel")
        self.titel.setStyleSheet(f"color: {T['text2']}; font-size: 13px;")
        self.wert.setStyleSheet(f"color: {T['value']}; font-size: 20px; font-weight: 700;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 13px; padding-bottom: 3px;")
        self.alter.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")

    def set(self, wert: str, alter: str):
        if self.wert.text() != wert:
            self.wert.setText(wert)
        if self.alter.text() != alter:
            self.alter.setText(alter)


class ErgebnisKachel(QtWidgets.QFrame):
    """Pmax bzw. Mmax: Titel („Pmax @ 10.420“) und grosse Zahl in der Achsenfarbe."""

    def __init__(self, titel: str, einheit: str, token: str):
        super().__init__()
        self.token = token
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(0)
        self.titel = QtWidgets.QLabel(titel)
        lay.addWidget(self.titel)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        row.addStretch(1)
        self.wert = QtWidgets.QLabel("–")
        self.einheit = QtWidgets.QLabel(einheit)
        row.addWidget(self.wert)
        row.addWidget(self.einheit, 0, QtCore.Qt.AlignBottom)
        lay.addLayout(row)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        _rahmen(self, "ergebniskachel")
        self.titel.setStyleSheet(f"color: {T['text2']}; font-size: 13px;")
        self.wert.setStyleSheet(f"color: {T[self.token]}; font-size: 30px; font-weight: 800;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 13px; padding-bottom: 5px;")

    def set(self, titel: str, wert: str):
        self.titel.setText(titel)
        self.wert.setText(wert)


class _Spalte(QtWidgets.QScrollArea):
    """Rollbereich der rechten Spalte, meldet die sichtbare Hoehe."""

    def __init__(self, bei_hoehe):
        super().__init__()
        self._bei_hoehe = bei_hoehe

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QtCore.QTimer.singleShot(0, self, lambda: self._bei_hoehe(self.viewport().height()))


class DrehzahlKachel(WertKachel):
    """Grosse Drehzahl (Motor aus Rolle × i, Zündung oder ECU – Wahl per Klick auf den Titel) mit Balken und
    zwei Unterzeilen (Rolle km/h, Zündung)."""

    def __init__(self):
        super().__init__(platz=0, groesse=GROSS,
                         kanaele=[Kanal(k, t, "1/min", q, 0, 0.0, 13000.0) for k, t, q in DREHZAHL_QUELLEN])
        self.balken.bar_h = 20
        self.balken.setFixedHeight(20 + 18)
        unter = QtWidgets.QHBoxLayout()
        unter.setContentsMargins(0, 0, 0, 0)
        self.unter_links = QtWidgets.QLabel("")
        self.unter_rechts = QtWidgets.QLabel("")
        self.unter_rechts.setAlignment(QtCore.Qt.AlignRight)
        unter.addWidget(self.unter_links)
        unter.addStretch(1)
        unter.addWidget(self.unter_rechts)
        self.layout().addLayout(unter)
        self.layout().setSpacing(1)
        self._stil()

    def _stil(self):
        super()._stil()
        T = theme.tokens()
        farbe = T["dim"] if (self._fehlt_text or self.wert is None) else T["value"]
        gross = self.groesse == GROSS
        self.wert_label.setStyleSheet(f"color: {farbe}; font-size: {46 if gross else 34}px; font-weight: 800;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 14px; padding-bottom: {9 if gross else 6}px;")
        for lab in (getattr(self, "unter_links", None), getattr(self, "unter_rechts", None)):
            if lab is not None:
                lab.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")

    def _menue_fuellen(self):
        super()._menue_fuellen()
        acts = self.menue.actions()                # „leer lassen“ gibt es hier nicht
        for i, a in enumerate(acts):
            if a.text() == "leer lassen":
                self.menue.removeAction(a)
                if i and acts[i - 1].isSeparator():
                    self.menue.removeAction(acts[i - 1])
                break

    def set_unter(self, links: str, rechts: str):
        if self.unter_links.text() != links:
            self.unter_links.setText(links)
        if self.unter_rechts.text() != rechts:
            self.unter_rechts.setText(rechts)


class GrenzenDialog(QtWidgets.QDialog):
    """Balkengrenzen eines Live-Platzes: von, bis und bis zu 4 Zonen (gut/Warnung/Fehler/kalt)."""

    def __init__(self, titel: str, lo: float, hi: float, zonen: Sequence, dez: int, standard: tuple, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Balkengrenzen – {titel}")
        self.standard = standard
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QGridLayout()
        dez = max(0, min(3, dez))
        self.lo, self.hi = self._feld(lo, dez), self._feld(hi, dez)
        form.addWidget(QtWidgets.QLabel("Balken von"), 0, 0)
        form.addWidget(self.lo, 0, 1)
        form.addWidget(QtWidgets.QLabel("bis"), 0, 2)
        form.addWidget(self.hi, 0, 3)
        self.zonen = []
        zonen = list(zonen or [])
        for i in range(4):
            z = zonen[i] if i < len(zonen) else (0.0, 0.0, "")
            von, bis, st = self._feld(z[0], dez), self._feld(z[1], dez), QtWidgets.QComboBox()
            for key, text in STUFEN_TEXT:
                st.addItem(text, key)
            st.setCurrentIndex(max(0, st.findData(z[2])))
            form.addWidget(QtWidgets.QLabel(f"Zone {i + 1} von"), i + 1, 0)
            form.addWidget(von, i + 1, 1)
            form.addWidget(QtWidgets.QLabel("bis"), i + 1, 2)
            form.addWidget(bis, i + 1, 3)
            form.addWidget(st, i + 1, 4)
            self.zonen.append((von, bis, st))
        lay.addLayout(form)
        hint = QtWidgets.QLabel("Zonen färben den Balken (Stufe „–“ = Zone nicht benutzt). Gilt für das gewählte Fahrzeug.")
        hint.setWordWrap(True)
        theme.rolle(hint, "neben")
        lay.addWidget(hint)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
                                          | QtWidgets.QDialogButtonBox.RestoreDefaults)
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("Abbrechen")
        btns.button(QtWidgets.QDialogButtonBox.RestoreDefaults).setText("Standard")
        btns.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(self._standard)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @staticmethod
    def _feld(v: float, dez: int) -> QtWidgets.QDoubleSpinBox:
        f = QtWidgets.QDoubleSpinBox()
        f.setRange(-100000.0, 100000.0)
        f.setDecimals(dez)
        f.setValue(float(v))
        return f

    def _standard(self):
        lo, hi, _dez, zonen, _m = self.standard
        self.lo.setValue(lo)
        self.hi.setValue(hi)
        for i, (von, bis, st) in enumerate(self.zonen):
            z = zonen[i] if i < len(zonen) else (0.0, 0.0, "")
            von.setValue(z[0])
            bis.setValue(z[1])
            st.setCurrentIndex(max(0, st.findData(z[2])))

    def werte(self) -> Dict[str, Any]:
        zonen = [[von.value(), bis.value(), st.currentData()] for von, bis, st in self.zonen
                 if st.currentData() and bis.value() > von.value()]
        return {"min": self.lo.value(), "max": self.hi.value(), "zonen": zonen}


# =============================================================================================== Setup-Werte
class KurzText(QtWidgets.QLabel):
    """Einzeiliger Hinweis, der sich kuerzt statt die Zeile umzubrechen (zu schmal: ganz leer). text() = voller Text."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._voll = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str):
        self._voll = text or ""
        self._kuerzen()

    def text(self) -> str:
        return self._voll

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self._voll) + 4, super().sizeHint().height())

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def _kuerzen(self):
        b = self.width() - 4
        t = "" if b < 60 else self.fontMetrics().elidedText(self._voll, QtCore.Qt.ElideRight, b)
        if super().text() != t:
            super().setText(t)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._kuerzen()


class SetupZeile(QtWidgets.QFrame):
    """Zeile SETUP-WERTE: Schnellwerte des Setups (Anzahl je Fahrzeug waehlbar, „＋“ fuegt einen Platz an, das
    Titel-Menue aendert oder entfernt ihn), direkt editierbar. Aenderungen gelten zunaechst nur fuer die Messung
    („ungespeichert“) – sie werden als neues Setup gespeichert, fuer den Lauf bestaetigt oder verworfen."""
    plaetze_geaendert = QtCore.Signal(list)        # Felder aller Plaetze ("" = leer) nach Wahl/Hinzufuegen/Entfernen
    geaendert = QtCore.Signal()                    # Aenderungen oder „nur für diesen Lauf“ anders
    neu_speichern = QtCore.Signal()
    HOEHE = 46

    def __init__(self):
        super().__init__()
        self.setObjectName("setupzeile")
        # eine Zeile ist HOEHE hoch; bei schmalem Fenster bricht die Zeile um (Gruppen untereinander)
        self.setMinimumHeight(self.HOEHE)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.sid: Optional[int] = None
        self.name = ""
        self.basis: Dict[str, Any] = {}
        self.felder: List[str] = list(SCHNELLWERT_STANDARD)
        self.aend: Dict[str, str] = {}
        self.nur_lauf = False
        self.fahrzeug = ""
        self.gemisch = ""
        self._geladen = False
        self.lay = UmbruchLayout(self, abstand=6, zeilenabstand=6)
        self.lay.setContentsMargins(10, 4, 10, 4)
        self.titel = QtWidgets.QLabel("SETUP-WERTE")
        self.titel_knoepfe: List[QtWidgets.QToolButton] = []
        self.edits: List[QtWidgets.QLineEdit] = []
        self.vorher: List[QtWidgets.QLabel] = []
        self.b_plus = QtWidgets.QToolButton()
        self.b_plus.setText("＋")
        self.b_plus.setAutoRaise(True)
        self.b_plus.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.b_plus.setToolTip("Platz hinzufügen: Setup-Feld wählen (gilt für das Fahrzeug)")
        m = QtWidgets.QMenu(self.b_plus)
        m.aboutToShow.connect(lambda m=m: self._menue(-1, m))
        self.b_plus.setMenu(m)
        self.ruhe = KurzText()
        self.ruhe.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.marke = QtWidgets.QLabel("")
        self.marke.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.marke.setMinimumWidth(160)
        self.marke.installEventFilter(self)
        self.b_neu = QtWidgets.QPushButton("Als neues Setup speichern …")
        theme.rolle(self.b_neu, "primaer")
        self.b_neu.setToolTip("Neues Setup aus dem gewählten und den geänderten Werten anlegen")
        self.b_lauf = QtWidgets.QPushButton("nur für diesen Lauf")
        self.b_lauf.setToolTip("Setup bleibt, die Werte werden nur beim Lauf gespeichert")
        self.b_weg = QtWidgets.QPushButton("Verwerfen")
        self.b_neu.clicked.connect(self.neu_speichern.emit)
        self.b_lauf.clicked.connect(lambda: self.set_nur_lauf(True))
        self.b_weg.clicked.connect(self.verwerfen)
        self._plaetze_bauen()
        theme.bei_wechsel(self, lambda _t: self._zeigen())

    @staticmethod
    def _wert_breite(e: QtWidgets.QLineEdit, text: str):
        """Wertfeld so breit wie sein Text (Zahlen 68 px, Texte wie „Akrapovic“ bis 150 px)."""
        f = QtGui.QFont(e.font())
        f.setPixelSize(15)
        f.setWeight(QtGui.QFont.ExtraBold)
        b = max(68, min(150, QtGui.QFontMetrics(f).horizontalAdvance(text) + 22))
        if e.width() != b or e.minimumWidth() != b:
            e.setFixedWidth(b)

    def _plaetze_bauen(self):
        """So viele Plaetze (Titel, Wert, „vorher“) wie Felder, dann die Zeile neu anordnen: Titel | Plaetze | ＋ |
        Stretch | Ruhetext | Marke + Knoepfe."""
        while len(self.titel_knoepfe) < len(self.felder):
            i = len(self.titel_knoepfe)
            b = QtWidgets.QToolButton(self)
            b.setAutoRaise(True)
            b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            b.setToolTip("Setup-Feld für diesen Platz wählen oder den Platz entfernen (gilt für das Fahrzeug)")
            m = QtWidgets.QMenu(b)
            m.aboutToShow.connect(lambda i=i, m=m: self._menue(i, m))
            b.setMenu(m)
            e = QtWidgets.QLineEdit(self)
            e.setFixedWidth(68)
            e.setAlignment(QtCore.Qt.AlignCenter)
            e.textEdited.connect(lambda _t, i=i: self._editiert(i))
            e.textChanged.connect(lambda t, e=e: self._wert_breite(e, t))
            v = QtWidgets.QLabel("", self)
            v.hide()
            self.titel_knoepfe.append(b)
            self.edits.append(e)
            self.vorher.append(v)
        while len(self.titel_knoepfe) > len(self.felder):
            for w in (self.titel_knoepfe.pop(), self.edits.pop(), self.vorher.pop()):
                w.hide()
                w.deleteLater()
        lay = self.lay
        lay.leeren()
        lay.add(self.titel)
        lay.addSpacing(4)
        for b, e, v in zip(self.titel_knoepfe, self.edits, self.vorher):
            lay.add_gruppe(b, e, v)                    # Feld, Wert und „vorher“ bleiben beim Umbruch zusammen
            lay.addSpacing(10)
        lay.add(self.b_plus)
        lay.add(self.ruhe, 1)                          # nimmt den Rest der Zeile, kuerzt sich statt umzubrechen
        aktion = lay.neue_gruppe()                     # Marke und Knoepfe brechen gemeinsam um
        lay.add(self.marke, 3, gruppe=aktion)
        for b in (self.b_neu, self.b_lauf, self.b_weg):
            lay.add(b, gruppe=aktion)
        self.b_plus.setVisible(len(self.felder) < SCHNELLWERT_MAX)
        self.updateGeometry()

    # ------------------------------------------------------------ Daten
    def set_setup(self, sid: Optional[int], name: str, basis: Optional[Dict[str, Any]], felder: Sequence[str],
                  fahrzeug: str = "", gemisch: str = "") -> bool:
        """Gewaehltes Setup. Gleiches Setup wie vorher: Aenderungen bleiben. -> True, wenn Aenderungen verworfen
        wurden (anderes Setup gewaehlt)."""
        weg = bool(self.aend) and (sid != self.sid or not self._geladen)
        if sid != self.sid or not self._geladen:
            self.aend, self.nur_lauf = {}, False
        self.sid, self.name, self.basis = sid, name or "", dict(basis or {})
        self.fahrzeug, self.gemisch = fahrzeug, gemisch or ""
        self._geladen = True
        self.set_felder(felder)
        self.geaendert.emit()
        return weg

    def set_felder(self, felder: Sequence[str]):
        """Felder der Plaetze (Anzahl beliebig bis SCHNELLWERT_MAX, "" = Platz leer)."""
        self.felder = list(felder)[:SCHNELLWERT_MAX]
        if len(self.felder) != len(self.titel_knoepfe):
            self._plaetze_bauen()
        self._zeigen()

    def wert(self, feld: str) -> str:
        return self.aend.get(feld, wert_text(self.basis.get(feld)))

    def aenderungen(self) -> Dict[str, str]:
        return dict(self.aend)

    def ist_geaendert(self) -> bool:
        return bool(self.aend)

    def set_wert(self, feld: str, wert: str):
        """Wert eines Felds setzen wie bei der Eingabe (auch fuer Felder, die gerade auf keinem Platz stehen)."""
        alt = wert_text(self.basis.get(feld))
        neu = wert_text(wert)
        vorher = dict(self.aend)
        if neu == alt:
            self.aend.pop(feld, None)
        else:
            self.aend[feld] = neu
        if self.aend != vorher:
            self.nur_lauf = False                  # neue Aenderung: neu entscheiden
            self._zeigen()
            self.geaendert.emit()

    def _editiert(self, i: int):
        feld = self.felder[i]
        if feld:
            self.set_wert(feld, self.edits[i].text())

    def set_nur_lauf(self, an: bool):
        if self.nur_lauf != bool(an) and (self.aend or not an):
            self.nur_lauf = bool(an) and bool(self.aend)
            self._zeigen()
            self.geaendert.emit()

    def verwerfen(self):
        if self.aend or self.nur_lauf:
            self.aend, self.nur_lauf = {}, False
            self._zeigen()
            self.geaendert.emit()

    def aenderung_text(self) -> str:
        """„HD 135 → HD 138, Clip 3 → 4“"""
        return ", ".join(f"{kurz(f)} {wert_text(self.basis.get(f)) or '–'} → {kurz(f)} {neu or '–'}"
                         for f, neu in self.aend.items())

    def marke_text(self) -> str:
        if not self.aend:
            return ""
        zustand = "nur für diesen Lauf, Setup bleibt" if self.nur_lauf else "geändert, ungespeichert"
        return f"✎  Setup: {self.name or 'ohne Setup'}  ·  {self.aenderung_text()}  ({zustand})"

    def _marke_kuerzen(self):
        """Marke passend zur Breite: zuerst wird der Setup-Name gekuerzt, die Aenderung bleibt lesbar."""
        text = self.marke_text()
        if not text:
            self.marke.setText("")
            return
        f = QtGui.QFont(self.marke.font())
        f.setPixelSize(14)
        f.setBold(True)
        fm = QtGui.QFontMetrics(f)
        platz = self.marke.width() - 24
        if platz <= 0 or fm.horizontalAdvance(text) <= platz:
            self.marke.setText(text)
            return
        name = self.name or "ohne Setup"
        rest = fm.horizontalAdvance(text) - fm.horizontalAdvance(name)
        kurz_name = fm.elidedText(name, QtCore.Qt.ElideRight, max(0, platz - rest))
        knapp = "nur dieser Lauf" if self.nur_lauf else "ungespeichert"
        for t in ([text.replace(name, kurz_name, 1)] if len(kurz_name) >= 10 else []) + [
                f"✎  {self.aenderung_text()}  ({knapp})", f"✎  {self.aenderung_text()}"]:
            if fm.horizontalAdvance(t) <= platz:
                self.marke.setText(t)
                return
        self.marke.setText(fm.elidedText(f"✎  {self.aenderung_text()}", QtCore.Qt.ElideRight, platz))

    def eventFilter(self, obj, ev):
        if obj is self.marke and ev.type() == QtCore.QEvent.Resize:
            self._marke_kuerzen()
        return super().eventFilter(obj, ev)

    def neuer_name(self) -> str:
        """Vorschlag fuer das neue Setup: „Serie, HD 135, Auspuff“ -> „Serie, HD 138, Auspuff“."""
        name = self.name or "Setup"
        for f, neu in self.aend.items():
            k, alt = kurz(f), wert_text(self.basis.get(f))
            if alt and f"{k} {alt}" in name:
                name = name.replace(f"{k} {alt}", f"{k} {neu}")
            else:
                name += f", {k} {neu}"
        return name

    # ------------------------------------------------------------ Darstellung
    def _menue(self, i: int, m: QtWidgets.QMenu):
        """Feldwahl fuer Platz i; i = -1: Menue des Knopfs „＋“ (neuer Platz am Ende)."""
        m.clear()
        efi = self.gemisch.startswith("EFI")
        haupt = ["Einspritzung / ECU" if efi else "Vergaser", "Zündung", "Antrieb", "Ein- und Auslass"]
        gruppen = dict(SETUP_FIELDS)
        jetzt = self.felder[i] if i >= 0 else None

        def eintrag(menu, key, label):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(jetzt == key)
            if i < 0 and key in self.felder and key:
                a.setEnabled(False)                    # steht schon auf einem Platz
            a.triggered.connect(lambda _c=False, k=key: self._waehle(i, k))

        if i < 0:
            m.addSection("Platz hinzufügen")
        for g in haupt:
            m.addSection(g)
            for key, label, _typ in gruppen.get(g, []):
                eintrag(m, key, label)
        weitere = m.addMenu("Weitere Felder")
        for g, felder in SETUP_FIELDS:
            if g in haupt:
                continue
            weitere.addSection(g)
            for key, label, _typ in felder:
                eintrag(weitere, key, label)
        m.addSeparator()
        eintrag(m, "", "leer lassen")
        if i >= 0:
            weg = m.addAction("Platz entfernen")
            weg.triggered.connect(lambda _c=False: self.platz_entfernen(i))
        if self.fahrzeug:
            h = m.addAction(f"Auswahl gilt für {self.fahrzeug}")
            h.setEnabled(False)

    def _waehle(self, i: int, key: str):
        if i < 0:
            self.platz_hinzufuegen(key)
            return
        self.felder[i] = key
        self._zeigen()
        self.plaetze_geaendert.emit(list(self.felder))

    def platz_hinzufuegen(self, key: str = "") -> bool:
        """Neuen Platz am Ende (False, wenn schon SCHNELLWERT_MAX Plaetze da sind)."""
        if len(self.felder) >= SCHNELLWERT_MAX:
            return False
        self.set_felder(self.felder + [key])
        self.plaetze_geaendert.emit(list(self.felder))
        return True

    def platz_entfernen(self, i: int) -> bool:
        if not 0 <= i < len(self.felder):
            return False
        self.set_felder(self.felder[:i] + self.felder[i + 1:])
        self.plaetze_geaendert.emit(list(self.felder))
        return True

    def _zeigen(self):
        T = theme.tokens()
        geaendert = bool(self.aend)
        if geaendert:
            self.setStyleSheet(f"QFrame#setupzeile {{ background: {T['strip_changed_bg']}; border: 2px solid"
                               f" {T['zone_warn']}; border-radius: 4px; }} QLabel {{ background: transparent; }}")
        else:
            self.setStyleSheet(f"QFrame#setupzeile {{ background: {T['panel']}; border: 1px solid {T['border']};"
                               " border-radius: 4px; } QLabel { background: transparent; }")
        self.titel.setStyleSheet(f"color: {T['text']}; font-size: 13px; font-weight: 700; letter-spacing: 1px;")
        self.b_plus.setStyleSheet(f"QToolButton {{ color: {T['text2']}; font-size: 15px; font-weight: 700;"
                                  f" border: 1px dashed {T['border_strong']}; border-radius: 4px; padding: 1px 7px;"
                                  f" background: transparent; }} QToolButton:hover {{ color: {T['text']};"
                                  f" border-color: {T['accent_border']}; }} QToolButton::menu-indicator {{ image: none; }}")
        for i, feld in enumerate(self.felder):
            b, e, v = self.titel_knoepfe[i], self.edits[i], self.vorher[i]
            b.setText(f"{field_label(feld) if feld else 'leer'}  ▾")
            b.setStyleSheet(f"QToolButton {{ color: {T['text'] if feld else T['dim']}; font-size: 13px; font-weight: 700;"
                            " border: none; padding: 0 2px; background: transparent; }"
                            "QToolButton::menu-indicator { image: none; }")
            e.setEnabled(bool(feld))
            text = self.wert(feld) if feld else ""
            if e.text() != text:
                e.setText(text)
            alt = wert_text(self.basis.get(feld)) if feld else ""
            anders = bool(feld) and feld in self.aend
            if anders:
                e.setStyleSheet(f"QLineEdit {{ font-size: 15px; font-weight: 800; background: {T['changed_bg']};"
                                f" color: {T['changed_text']}; border: 1px solid {T['border_strong']}; }}")
            else:
                e.setStyleSheet("QLineEdit { font-size: 15px; font-weight: 800; }")
            v.setText(f"vorher {alt or '–'}")
            v.setStyleSheet(f"color: {T['text']}; font-size: 13px; font-weight: 700;")
            v.setVisible(anders)
        self.marke.setToolTip(self.marke_text())
        self.marke.setStyleSheet(f"QLabel {{ background: {T['changed_bg']}; color: {T['changed_text']}; font-size: 14px;"
                                 " font-weight: 800; border-radius: 3px; padding: 4px 10px; }")
        self._marke_kuerzen()
        self.marke.setVisible(geaendert)
        if self.sid:
            ruhe = f"Setup unverändert: {self.name}"
        elif self.fahrzeug:
            ruhe = "Kein Setup gewählt – geänderte Werte werden nur beim Lauf gespeichert"
        else:
            ruhe = "Kein Fahrzeug gewählt"
        self.ruhe.setText(ruhe)
        self.ruhe.setToolTip(ruhe)
        self.ruhe.setStyleSheet(f"color: {T['text2']}; font-size: 13px;")
        self.ruhe.setVisible(not geaendert)
        self.b_neu.setVisible(geaendert)
        self.b_neu.setEnabled(bool(self.fahrzeug))
        self.b_lauf.setVisible(geaendert and not self.nur_lauf)
        self.b_weg.setVisible(geaendert)


# =============================================================================================== Diagramm-Zubehoer
class GasWegHinweis(QtWidgets.QWidget):
    """„GAS WEG“ gross ueber dem Diagramm mit dem Grund darunter; verschwindet GAS_WEG_HALTEN_S nach dem letzten
    zeige() von selbst (takt() regelmaessig aufrufen)."""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.gross = QtWidgets.QLabel("GAS WEG")
        self.gross.setAlignment(QtCore.Qt.AlignCenter)
        self.gross.setFixedHeight(112)
        self.unter = QtWidgets.QLabel("")
        self.unter.setAlignment(QtCore.Qt.AlignCenter)
        self.unter.setFixedHeight(34)
        lay.addWidget(self.gross)
        lay.addWidget(self.unter)
        self._bis = 0.0
        parent.installEventFilter(self)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self.hide()

    def _stil(self):
        T = theme.tokens()
        self.gross.setStyleSheet(f"QLabel {{ background: {T['err_bg']}; color: {T['err_text']}; font-size: 68px;"
                                 f" font-weight: 900; border: 3px solid {T['err_text']}; border-radius: 10px;"
                                 " letter-spacing: 6px; }")
        self.unter.setStyleSheet(f"QLabel {{ background: {T['panel']}; color: {T['text']}; font-size: 16px;"
                                 f" font-weight: 700; border: 1px solid {T['border']}; border-radius: 5px; }}")

    def zeige(self, titel: str, unter: str, halten: float = GAS_WEG_HALTEN_S):
        self.gross.setText(titel)
        self.unter.setText(unter)
        self.unter.setVisible(bool(unter))
        self._bis = time.time() + halten
        self._platzieren()
        if not self.isVisible():
            self.show()
        self.raise_()

    def takt(self):
        if self.isVisible() and time.time() > self._bis:
            self.hide()

    def verbergen(self):
        self._bis = 0.0
        self.hide()

    def _platzieren(self):
        p = self.parentWidget()
        w = min(560, max(200, p.width() - 40))
        h = 112 + (40 if self.unter.isVisibleTo(self) or self.unter.text() else 0)
        self.setGeometry((p.width() - w) // 2, int(p.height() * 0.22), w, h)

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QtCore.QEvent.Resize and self.isVisible():
            self._platzieren()
        return False


class ErgebnisText(QtWidgets.QLabel):
    """Ergebnis-/Hinweistext (frueher eigene Zeile unter dem Diagramm): einzeilig, gekuerzt, voller Text im
    Tooltip. text() liefert den vollen Text."""

    def __init__(self):
        super().__init__()
        self._voll = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        theme.rolle(self, "neben")

    def setText(self, text: str):
        self._voll = text or ""
        self.setToolTip(self._voll)
        self._kuerzen()

    def text(self) -> str:
        return self._voll

    def _kuerzen(self):
        einzeilig = "  ·  ".join(t.strip() for t in self._voll.splitlines() if t.strip())
        super().setText(self.fontMetrics().elidedText(einzeilig, QtCore.Qt.ElideRight, max(40, self.width() - 4)))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._kuerzen()


class CursorZeile(QtWidgets.QFrame):
    """Schmale Zeile „▸ Werte am Cursor …“ unter dem Diagramm; Klick klappt die Tabelle auf. Rechts der
    Ergebnis-/Hinweistext."""
    HINWEIS = "Maus ins Diagramm – Werte aller sichtbaren Läufe erscheinen hier (Klick: Tabelle)"
    HINWEIS_KURZ = "Maus ins Diagramm – Werte am Cursor (Klick: Tabelle)"
    TRENN = " &nbsp;·&nbsp; "

    def __init__(self):
        super().__init__()
        self.setObjectName("cursorzeile")
        self.setFixedHeight(28)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 8, 0)
        lay.setSpacing(8)
        self.knopf = QtWidgets.QToolButton()
        self.knopf.setCheckable(True)
        self.knopf.setAutoRaise(True)
        self.knopf.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        lay.addWidget(self.knopf)
        self.werte = QtWidgets.QLabel("")
        self.werte.setTextFormat(QtCore.Qt.RichText)
        self.werte.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.werte.installEventFilter(self)
        lay.addWidget(self.werte, 3)
        self.spalten = QtWidgets.QToolButton()
        self.spalten.setText("Spalten …")
        self.spalten.setAutoRaise(True)
        theme.rolle(self.spalten, "klein")
        self.spalten.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        lay.addWidget(self.spalten)
        self.ergebnis = ErgebnisText()
        lay.addWidget(self.ergebnis, 2)
        self.knopf.toggled.connect(self._pfeil)
        self._html = ""
        theme.bei_wechsel(self, lambda _t: self._stil())
        self._pfeil(False)

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(f"QFrame#cursorzeile {{ background: {T['panel']}; border: 1px solid {T['border']};"
                           " border-radius: 3px; } QLabel { background: transparent; }")
        self.knopf.setStyleSheet(f"QToolButton {{ color: {T['text']}; font-size: 13px; font-weight: 700; border: none;"
                                 " background: transparent; padding: 0; }")
        self.set_werte(self._html)

    def _pfeil(self, auf: bool):
        self.knopf.setText(("▾" if auf else "▸") + " Werte am Cursor")
        self.spalten.setVisible(auf)

    def set_werte(self, html: str):
        """Werte am Cursor („n … · Lauf 1 … · Lauf 2 …“, Teile mit TRENN) oder leer = Hinweis. Bei wenig Platz
        fallen hintere Laeufe ganz weg („· …“), die Tabelle (Klick) zeigt alle."""
        self._html = html or ""
        T = theme.tokens()
        platz = self.werte.width()
        if self._html:
            teile = self._html.split(self.TRENN)
            html = self._html
            for n in range(len(teile), 0, -1):
                html = self.TRENN.join(teile[:n]) + ("" if n == len(teile) else self.TRENN + "…")
                if n == 1 or self._breite(html) <= platz:
                    break
            self.werte.setText(f"<span style='color:{T['text']}'>{html}</span>")
        else:
            text = self.HINWEIS if self._breite(self.HINWEIS) <= platz else self.HINWEIS_KURZ
            self.werte.setText(f"<span style='color:{T['text2']}'>{text}</span>")

    def _breite(self, html: str) -> float:
        doc = QtGui.QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self.werte.font())
        doc.setHtml(html)
        return doc.idealWidth()

    def eventFilter(self, obj, ev):
        if obj is self.werte and ev.type() == QtCore.QEvent.Resize and ev.size().width() != ev.oldSize().width():
            self.set_werte(self._html)
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.knopf.toggle()
        super().mousePressEvent(ev)


class LaufListe(QtWidgets.QWidget):
    """Inhalt der Seitenleiste „Läufe“: Plaetze-Hinweis, Filter, Referenz laden …, Liste, Knoepfe."""
    SPALTEN = ["", "Lauf", "PS", "bei", "Nm", "Fehler"]

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.info = QtWidgets.QLabel("")
        lay.addWidget(self.info)
        row = QtWidgets.QHBoxLayout()
        self.run_filter = QtWidgets.QComboBox()
        self.run_filter.addItems(["alle", "aktuelles Fahrzeug", "aktuelles Setup"])
        row.addWidget(self.run_filter, 1)
        self.b_ref = QtWidgets.QPushButton("Referenz laden …")
        theme.rolle(self.b_ref, "primaer")
        self.b_ref.setToolTip("Gespeicherten Mittelwert-Lauf als Referenz auf einen gesperrten Platz legen")
        row.addWidget(self.b_ref)
        lay.addLayout(row)
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(self.SPALTEN)
        t.setRootIsDecorated(False)
        t.setIndentation(0)
        t.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        t.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        t.header().setStretchLastSection(True)
        t.setUniformRowHeights(True)
        t.setIconSize(QtCore.QSize(16, 16))
        t.setStyleSheet("QTreeView::item { padding: 0px; }")
        self.run_tree = t
        lay.addWidget(t, 1)
        self.hinweis = QtWidgets.QLabel("Schloss = Platz bleibt im Diagramm stehen (auch ältere Läufe) · Haken = "
                                        "Lauf zeigen/ausblenden · Maus weg = zuklappen, Klick in die Grafik = zu")
        self.hinweis.setWordWrap(True)
        theme.rolle(self.hinweis, "neben")
        lay.addWidget(self.hinweis)
        # die Übersetzung steht nicht mehr hier, sondern oben unter „Lauf-Einstellungen …“ (gilt fuer den naechsten
        # Lauf, nicht fuer die Laeufe der Liste; nachtraeglich je Lauf: Kontextmenue „Mit aktuellen Einstellungen
        # neu rechnen“)
        knoepfe = QtWidgets.QHBoxLayout()
        knoepfe.setSpacing(4)
        self.b_none = QtWidgets.QPushButton("Alle ausblenden")
        self.b_view = QtWidgets.QPushButton("In Auswertung")
        self.b_del = QtWidgets.QPushButton("Löschen")
        for b in (self.b_none, self.b_view, self.b_del):
            knoepfe.addWidget(b)
        lay.addLayout(knoepfe)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.info.setStyleSheet(f"background: {T['lock_bg']}; color: {T['text']}; font-size: 13px; font-weight: 700;"
                                " border-radius: 3px; padding: 4px 8px;")


class ReferenzDialog(QtWidgets.QDialog):
    """Mittelwert-Lauf als Referenz waehlen."""

    def __init__(self, rows, weitere=(), parent=None):
        """rows: Mittelwerte, die die Auswertung fuers Messfenster anbietet (sichtbar=True); weitere: die uebrigen,
        erst nach Klick auf „weitere …“."""
        super().__init__(parent)
        self.setWindowTitle("Referenz laden")
        self.resize(560, 360)
        self.rows, self.weitere = list(rows), list(weitere)
        lay = QtWidgets.QVBoxLayout(self)
        lab = QtWidgets.QLabel("Mittelwert-Läufe, die in der Auswertung „im Messfenster als Referenz anbieten“ haben. "
                               "Die Referenz belegt einen gesperrten Platz im Diagramm und wird dick mit ihrem Band "
                               "gezeichnet.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.liste = QtWidgets.QListWidget()
        self.liste.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.liste, 1)
        self.b_weitere = QtWidgets.QCheckBox(f"weitere … ({len(self.weitere)} nicht angebotene Mittelwerte zeigen)")
        self.b_weitere.setVisible(bool(self.weitere))
        self.b_weitere.setChecked(not self.rows)
        self.b_weitere.toggled.connect(lambda _on: self._fuellen())
        lay.addWidget(self.b_weitere)
        self._fuellen()
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Als Referenz laden")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("Abbrechen")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _fuellen(self):
        self.liste.clear()
        for r in self.rows + (self.weitere if self.b_weitere.isChecked() else []):
            it = QtWidgets.QListWidgetItem(farb_icon(r.farbe or "@avg_color", True),
                                           f"{r.name}   ·   {zahl(r.p_max, 1)} PS bei {zahl(r.n_pmax)}   ·   "
                                           f"{r.datum.replace('T', ' ')[:16]}" + ("" if r.sichtbar else "   (weitere)"))
            it.setData(QtCore.Qt.UserRole, r.id)
            it.setToolTip(r.notiz or r.name)
            self.liste.addItem(it)
        if self.liste.count():
            self.liste.setCurrentRow(0)

    def gewaehlt(self) -> Optional[int]:
        it = self.liste.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None


# =============================================================================================== Lauf-Einstellungen
LAUF_FELDER = (("ratio", "Übersetzung nKW/nRolle"), ("ma_s", "Gleitender Mittelwert [s]"),
               ("dq_s", "Differenzenquotient ± [s]"), ("ma", "Gleitender Mittelwert [Punkte]"),
               ("dq", "Differenzenquotient [Punkte]"), ("n_min", "n vom Gas [1/min]"),
               ("n_stop", "n Stop [1/min] (0 = aus)"))
ART_KURZ = {"hand": ("Hand", "von Hand"), "funken": ("Funken", "Funkenmessung, zuletzt gemessen")}


def filter_text(seite, kurz: bool = False) -> str:
    """Filter der Rolle/Lauf-Seite kurz: „Standard“ (1,75 / 0,75 s), „2,00 / 0,75 s“ bzw. „35 / 15 Pkt.“"""
    from .gui import FILTER_S_DEFAULT
    f = seite.fields
    if seite.filter_seconds:
        ma, dq = f["ma_s"].value(), f["dq_s"].value()
        if abs(ma - FILTER_S_DEFAULT[0]) < 1e-9 and abs(dq - FILTER_S_DEFAULT[1]) < 1e-9:
            return "Std." if kurz else "Standard"
        return f"{zahl(ma, 2)} / {zahl(dq, 2)} s"
    return f"{zahl(f['ma'].value())} / {zahl(f['dq'].value())} Pkt."


def lauf_stand(seite) -> Tuple[List[str], str]:
    """Stand fuer den Knopf „Lauf-Einstellungen …“: (Unterzeilen vom laengsten zum kuerzesten, Tooltip)."""
    f = seite.fields
    i = zahl(f["ratio"].value(), 3)
    a, a_lang = ART_KURZ.get(seite.ratio_art.currentData() or "hand", ART_KURZ["hand"])
    filt = filter_text(seite)
    zeilen = [f"Übersetzung {i} ({a}) · Filter: {filt}", f"i {i} ({a}) · Filter {filt}",
              f"i {i} {a} · Filter {filter_text(seite, kurz=True)}", f"i {i} {a}"]
    n_stop = f["n_stop"].value()
    werte = (f"{zahl(f['ma_s'].value(), 2)} / {zahl(f['dq_s'].value(), 2)} s" if filt == "Standard" else "")
    tip = (f"Übersetzung nKW/nRolle {i} ({a_lang})\nFilter: {filt}" + (f" ({werte})" if werte else "")
           + f"\nn vom Gas {zahl(f['n_min'].value())} 1/min · n Stop "
           + (f"{zahl(n_stop)} 1/min" if n_stop else "aus")
           + "\n\nKlick: Übersetzung, Filter und Laufgrenzen ändern")
    return zeilen, tip


class LaufKnopf(QtWidgets.QPushButton):
    """Knopf „Lauf-Einstellungen …“ mit kleiner zweiter Zeile (Stand der Übersetzung und Filter). Die zweite Zeile
    nimmt den laengsten Text, der passt; der volle Stand steht im Tooltip."""
    TITEL = "Lauf-Einstellungen …"

    def __init__(self):
        super().__init__()
        self.setAccessibleName(self.TITEL)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 1, 6, 2)
        lay.setSpacing(0)
        self.oben = QtWidgets.QLabel(self.TITEL)
        self.unten = QtWidgets.QLabel("")
        for lab in (self.oben, self.unten):
            lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            lay.addWidget(lab)
        self._zeilen: List[str] = []
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        an = self.isEnabled()
        self.oben.setStyleSheet(f"color: {T['text'] if an else T['dim']}; font-size: 13px; font-weight: 700;")
        self.unten.setStyleSheet(f"color: {T['text2'] if an else T['dim']}; font-size: 11px;")

    def changeEvent(self, ev):
        super().changeEvent(ev)
        if ev.type() == QtCore.QEvent.EnabledChange:
            self._stil()

    def set_stand(self, zeilen: Sequence[str], tip: str):
        self._zeilen = list(zeilen)
        self.setToolTip(tip)
        self._kuerzen()
        self.updateGeometry()

    def stand(self) -> str:
        """Gezeigte Unterzeile."""
        return self.unten.text()

    @staticmethod
    def _fm(px: int, fett: bool = False) -> QtGui.QFontMetrics:
        f = QtGui.QFont()
        f.setPixelSize(px)
        f.setBold(fett)
        return QtGui.QFontMetrics(f)

    def _kuerzen(self):
        fm = self._fm(11)
        platz = self.width() - 14
        text = next((z for z in self._zeilen if fm.horizontalAdvance(z) <= platz), None)
        if text is None:
            text = fm.elidedText(self._zeilen[-1], QtCore.Qt.ElideRight, platz) if self._zeilen else ""
        if self.unten.text() != text:
            self.unten.setText(text)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._kuerzen()

    def sizeHint(self) -> QtCore.QSize:
        unter = self._zeilen[1] if len(self._zeilen) > 1 else ""
        w = max(self._fm(13, True).horizontalAdvance(self.TITEL), self._fm(11).horizontalAdvance(unter))
        return QtCore.QSize(w + 24, 36)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self._fm(13, True).horizontalAdvance(self.TITEL) + 18, 36)


class LaufEinstellungen(QtWidgets.QFrame):
    """Popup am Knopf „Lauf-Einstellungen …“: Übersetzung (Art von Hand / Funkenmessung, Wert, „Übersetzung
    einmessen …“), Filter und Laufgrenzen (n vom Gas, n Stop). Die Felder spiegeln die von Einstellungen › Rolle/Lauf
    (seite = SettingsPage, dieselben Einstellungen): Aenderungen gelten sofort und stehen an beiden Orten."""
    einmessen = QtCore.Signal()
    alle_einstellungen = QtCore.Signal()
    geschlossen = QtCore.Signal()

    def __init__(self, seite, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent, QtCore.Qt.Popup)
        from .gui import UEBERSETZUNG_ARTEN, UEBERSETZUNG_TIP
        self.setObjectName("laufpopup")
        self.seite = seite
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        lay.addWidget(abschnitt("ÜBERSETZUNG  nKW/nRolle"))
        self.art: Dict[str, QtWidgets.QRadioButton] = {}
        self._gruppe = QtWidgets.QButtonGroup(self)
        texte_art = {"hand": "von Hand (Wert eintragen oder einmessen)",
                     "funken": "Funkenmessung (aus Zündung/Rolle jedes Laufs)"}
        for art, text in UEBERSETZUNG_ARTEN:
            rb = QtWidgets.QRadioButton(texte_art.get(art, text))
            rb.setToolTip(UEBERSETZUNG_TIP)
            self._gruppe.addButton(rb)
            rb.toggled.connect(lambda an, art=art: an and self._art_gewaehlt(art))
            lay.addWidget(rb)
            self.art[art] = rb
        self.spins: Dict[str, QtWidgets.QDoubleSpinBox] = {}
        row = QtWidgets.QHBoxLayout()
        self.ratio_label = QtWidgets.QLabel("Übersetzung")
        row.addWidget(self.ratio_label)
        row.addWidget(self._spiegel("ratio"))
        self.b_einmessen = QtWidgets.QPushButton("Übersetzung einmessen …")
        self.b_einmessen.setToolTip("Übersetzung bei konstanter Drehzahl aus Zündung oder rusEFI einmessen")
        self.b_einmessen.clicked.connect(self._einmessen)
        row.addWidget(self.b_einmessen)
        row.addStretch(1)
        lay.addLayout(row)
        self.art_hinweis = QtWidgets.QLabel("")
        self.art_hinweis.setWordWrap(True)
        theme.rolle(self.art_hinweis, "neben")
        lay.addWidget(self.art_hinweis)

        lay.addWidget(abschnitt("FILTER"))
        self.filter_form = QtWidgets.QFormLayout()
        self.filter_form.setLabelAlignment(QtCore.Qt.AlignRight)
        self.filter_mode = QtWidgets.QComboBox()
        self.filter_mode.addItems([seite.filter_mode.itemText(i) for i in range(seite.filter_mode.count())])
        self.filter_mode.currentIndexChanged.connect(lambda i: seite.filter_mode.setCurrentIndex(i))
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self.filter_mode, 1)
        self.b_standard = QtWidgets.QPushButton("Standard")
        self.b_standard.setToolTip("Filter auf den Standard: Mittelwert 1,75 s, Differenz ±0,75 s (= LabVIEW 35/15 "
                                   "bei 20 Hz)")
        self.b_standard.clicked.connect(self._filter_standard)
        fr.addWidget(self.b_standard)
        self.filter_form.addRow("Filter in", fr)
        texte = dict(LAUF_FELDER)
        for key in ("ma_s", "dq_s", "ma", "dq"):
            self.filter_form.addRow(texte[key], self._spiegel(key))
        self.filter_hinweis = QtWidgets.QLabel("")
        self.filter_hinweis.setWordWrap(True)
        theme.rolle(self.filter_hinweis, "neben")
        self.filter_form.addRow(self.filter_hinweis)
        lay.addLayout(self.filter_form)

        lay.addWidget(abschnitt("LAUFGRENZEN"))
        grenzen = QtWidgets.QFormLayout()
        grenzen.setLabelAlignment(QtCore.Qt.AlignRight)
        for key in ("n_min", "n_stop"):
            grenzen.addRow(texte[key], self._spiegel(key))
        lay.addLayout(grenzen)
        self.b_alle = QtWidgets.QPushButton("Alle Einstellungen › Rolle/Lauf …")
        self.b_alle.setCursor(QtCore.Qt.PointingHandCursor)
        self.b_alle.clicked.connect(self._alle)
        lay.addWidget(self.b_alle, 0, QtCore.Qt.AlignLeft)

        seite.filter_art_geaendert.connect(lambda _on: self.abgleichen())
        seite.ratio_art.currentIndexChanged.connect(lambda _i: self.abgleichen())
        theme.bei_wechsel(self, lambda _t: self._stil())
        self.abgleichen()

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(f"QFrame#laufpopup {{ background: {T['panel']}; border: 1px solid {T['border_strong']};"
                           " border-radius: 4px; }")
        self.b_alle.setStyleSheet(f"QPushButton {{ color: {T['info_fg']}; border: none; background: transparent;"
                                  " text-decoration: underline; padding: 2px 0; font-weight: 600; }")

    def _spiegel(self, key: str) -> QtWidgets.QDoubleSpinBox:
        """Drehfeld, das das gleichnamige Feld der Rolle/Lauf-Seite spiegelt (in beide Richtungen)."""
        src = self.seite.fields[key]
        sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(src.minimum(), src.maximum())
        sp.setDecimals(src.decimals())
        sp.setSingleStep(src.singleStep())
        sp.setKeyboardTracking(False)
        sp.setMinimumWidth(96)
        sp.setValue(src.value())
        sp.valueChanged.connect(src.setValue)

        def nach(v, sp=sp):
            if sp.value() != v:
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
            self._hinweise()
        src.valueChanged.connect(nach)
        self.spins[key] = sp
        return sp

    def abgleichen(self):
        """Alles aus der Rolle/Lauf-Seite uebernehmen (auch nach Aenderungen dort ohne Signal, z.B. set_params)."""
        s = self.seite
        for key, sp in self.spins.items():
            if sp.value() != s.fields[key].value():
                sp.blockSignals(True)
                sp.setValue(s.fields[key].value())
                sp.blockSignals(False)
            sp.setEnabled(s.fields[key].isEnabled())
        idx = 0 if s.filter_seconds else 1
        if self.filter_mode.currentIndex() != idx:
            self.filter_mode.blockSignals(True)
            self.filter_mode.setCurrentIndex(idx)
            self.filter_mode.blockSignals(False)
        for key in ("ma_s", "dq_s"):
            self.filter_form.setRowVisible(self.spins[key], s.filter_seconds)
        for key in ("ma", "dq"):
            self.filter_form.setRowVisible(self.spins[key], not s.filter_seconds)
        rb = self.art.get(s.ratio_art.currentData() or "hand")
        if rb is not None and not rb.isChecked():
            rb.blockSignals(True)
            rb.setChecked(True)
            rb.blockSignals(False)
        self._hinweise()
        self.adjustSize()

    def _hinweise(self):
        s = self.seite
        funken = (s.ratio_art.currentData() or "hand") == "funken"
        self.ratio_label.setText("Startwert" if funken else "Übersetzung")
        self.art_hinweis.setText("Wird nach jedem Lauf aus Zündung/Rolle bestimmt und übernommen; bis dahin rechnen "
                                 "Live-Kurve und Laufgrenzen mit dem Startwert." if funken else
                                 "Gilt für jeden Lauf, bis sie geändert oder neu eingemessen wird.")
        self.filter_hinweis.setText(s.filter_label.text())
        self.b_standard.setEnabled(filter_text(s) != "Standard")

    def _art_gewaehlt(self, art: str):
        c = self.seite.ratio_art
        i = c.findData(art)
        if i >= 0 and c.currentIndex() != i:
            c.setCurrentIndex(i)
        self._hinweise()

    def _filter_standard(self):
        from .gui import FILTER_S_DEFAULT
        s = self.seite
        s.filter_mode.setCurrentIndex(0)
        s.fields["ma_s"].setValue(FILTER_S_DEFAULT[0])
        s.fields["dq_s"].setValue(FILTER_S_DEFAULT[1])
        self.abgleichen()

    def _einmessen(self):
        self.hide()
        self.einmessen.emit()

    def _alle(self):
        self.hide()
        self.alle_einstellungen.emit()

    def zeigen_an(self, knopf: QtWidgets.QWidget):
        """Unter dem Knopf, rechtsbuendig zu ihm, innerhalb des Bildschirms."""
        self.abgleichen()
        self.adjustSize()
        g = knopf.mapToGlobal(QtCore.QPoint(knopf.width(), knopf.height() + 2))
        x, y = g.x() - self.width(), g.y()
        scr = knopf.screen().availableGeometry() if knopf.screen() is not None else None
        if scr is not None:
            scr = scr.united(knopf.window().frameGeometry())    # Fenster groesser als der Bildschirm: im Fenster
            x = max(scr.left(), min(x, scr.right() - self.width()))
            if y + self.height() > scr.bottom():
                y = max(scr.top(), knopf.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.height() - 2)
        self.move(x, y)
        self.show()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.geschlossen.emit()


# =============================================================================================== Logik
class MessenMixin:
    """Reiter Messen im Hauptfenster (gui.MainWindow erbt das)."""

    # ------------------------------------------------------------------ Aufbau
    def _build_measure(self) -> QtWidgets.QWidget:
        self._plaetze: Tuple[List[int], List[int]] = ([], [])
        self._laeufe: Dict[int, Any] = {}              # Lauf-ID -> RunRow der Liste (inkl. gesperrter Referenzen)
        self._gezeichnet: List[int] = []
        self._mw_cache: Dict[int, Any] = {}            # Mittelwert-ID -> MittelwertErgebnis
        self._live: Dict[str, Optional[float]] = {}
        self._kachel_t = 0.0
        self._kachel_cache: Dict[int, tuple] = {}
        self._klima_t = 0.0                            # letztes Lesen von T/p (0 = noch nie)
        self._klima_feuchte = 0.0
        self._klima_versuch_t = 0.0
        self._klima_link = None
        self._verbunden_t = 0.0
        self._status_last = None

        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(5)

        # ---- Kopfzeile
        kopf = UmbruchLayout(abstand=6)
        self.vehicle_combo = QtWidgets.QComboBox()
        self.setup_combo = QtWidgets.QComboBox()
        # Breiten: ab 1280 px passt alles in eine Zeile, breitere Fenster geben Setup, Notiz und Zustand mehr
        # Platz; schmaler bricht die Zeile um (Laufzustand und Knoepfe in die zweite Zeile)
        self.vehicle_combo.setMinimumWidth(120)
        self.vehicle_combo.setMaximumWidth(260)
        self.setup_combo.setMinimumWidth(170)
        self.setup_combo.setMaximumWidth(340)              # Name + „✎ geändert“
        self.run_note = QtWidgets.QLineEdit()
        self.run_note.setPlaceholderText("Notiz zu diesem Lauf (z.B. Änderung gegenüber vorher)")
        self.run_note.setMinimumWidth(100)
        self.autosave = QtWidgets.QCheckBox("Auto-Speichern")
        self.autosave.setChecked(self.settings.get("autosave", True))
        for label, wdg in (("Fahrzeug", self.vehicle_combo), ("Setup", self.setup_combo)):
            kopf.add_gruppe(QtWidgets.QLabel(label), wdg, stretch={wdg: 1 if wdg is self.vehicle_combo else 2})
        kopf.add(self.run_note, 1)
        kopf.add(self.autosave)
        kopf.addSpacing(6)
        rechts = kopf.neue_gruppe()                        # Laufzustand + Knoepfe bleiben zusammen
        self.zustand = StatusFeld("", hoehe=36, schrift_px=15)
        self.zustand.setMinimumWidth(160)
        self.zustand.setMaximumWidth(300)
        self.zustand.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.status = self.zustand                 # frueher die grosse Statuszeile (text())
        kopf.add(self.zustand, 3, breite=260, gruppe=rechts)     # bei Platzmangel nicht zuerst auf 190
        # Knoepfe: Trennen/Verbinden | Lauf-Einstellungen … | START (im Lauf: Abbrechen bzw. Ausrollen beenden)
        self.connect_main = QtWidgets.QPushButton("Verbinden")
        self.lauf_btn = LaufKnopf()
        self.start_btn = QtWidgets.QPushButton(START_TEXT)
        # feste Mindestbreiten: der Text im Lauf („Abbrechen (Esc)“) aendert die Zeile nicht (kein Umbruch im Lauf)
        for b, breite in ((self.connect_main, 90), (self.lauf_btn, 0), (self.start_btn, 130)):
            b.setFixedHeight(36)
            if breite:
                b.setMinimumWidth(breite)
            kopf.add(b, gruppe=rechts)
        theme.bei_wechsel(self.start_btn, lambda _t: self._start_knopf_stil())
        self.lauf_popup: Optional[LaufEinstellungen] = None
        lay.addLayout(kopf)

        # ---- Setup-Werte
        self.setup_zeile = SetupZeile()
        self.setup_zeile.plaetze_geaendert.connect(self._schnellwert_plaetze)
        self.setup_zeile.geaendert.connect(self._setup_combo_marke)
        self.setup_zeile.neu_speichern.connect(self._setup_neu_dialog)
        lay.addWidget(self.setup_zeile)

        # ---- Mitte: Diagramm mit Zeile „Werte am Cursor“
        center = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(3)
        self.plot = DynoPlot()
        self.plot.set_limits(self.settings.get("plot_limits", {}))
        self.plot.limits_changed.connect(self._limits_changed)
        cl.addWidget(self.plot, 1)
        self.gas_weg = GasWegHinweis(self.plot)
        self.cursor_zeile = CursorZeile()
        self.cursor_toggle = self.cursor_zeile.knopf
        self.cursor_cols_btn = self.cursor_zeile.spalten
        self.cursor_menu = QtWidgets.QMenu(self.cursor_cols_btn)
        self.cursor_menu.aboutToShow.connect(self._fill_cursor_menu)
        self.cursor_cols_btn.setMenu(self.cursor_menu)
        self.result_label = self.cursor_zeile.ergebnis
        cl.addWidget(self.cursor_zeile)
        self.cursor_table = QtWidgets.QTableWidget(0, 8)
        self.cursor_table.setHorizontalHeaderLabels(["", "Lauf", "n [1/min]", "PS", "Nm", "λ", "EGT 1", "EGT 2"])
        self.cursor_table.verticalHeader().setVisible(False)
        self.cursor_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.cursor_table.setColumnWidth(0, 18)
        self.cursor_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        from .gui import compact_table
        compact_table(self.cursor_table, row_h=24)
        self.cursor_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._fit_cursor_table()
        cl.addWidget(self.cursor_table)
        self.cursor_toggle.toggled.connect(self._show_cursor_table)
        self.cursor_toggle.setChecked(bool(self.settings.get("cursor_tabelle", False)))
        self._show_cursor_table(self.cursor_toggle.isChecked())

        # ---- Laeufe links (Seitenleiste)
        self.laufliste = LaufListe()
        ll = self.laufliste
        self.run_tree, self.run_filter, self.b_ref = ll.run_tree, ll.run_filter, ll.b_ref
        self.b_none, self.b_view, self.b_del = ll.b_none, ll.b_view, ll.b_del
        self.run_filter.setCurrentIndex(self.settings.get("run_filter", 1))
        self.laeufe_leiste = SeitenLeiste("Läufe", ll, center, breite=LEISTE_BREITE)
        self.laeufe_leiste.set_zustand(self.settings.get("laeufe_leiste"))
        self.laeufe_leiste.angepinnt_geaendert.connect(self._leiste_gemerkt)
        self.run_tree.itemClicked.connect(self._lauf_geklickt)
        self.run_tree.itemSelectionChanged.connect(self._update_max_display)
        self.b_ref.clicked.connect(self._referenz_dialog)

        # ---- rechte Spalte
        self.rechte_spalte = self._rechte_spalte()
        split = QtWidgets.QSplitter()
        split.setChildrenCollapsible(False)
        split.addWidget(self.laeufe_leiste)
        split.addWidget(self.rechte_spalte)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1200, SPALTE_BREITE])
        self.measure_split = split
        lay.addWidget(split, 1)

        self.setup_combo.currentIndexChanged.connect(lambda _i: self._setup_zeile_laden())
        for key in ("temp_c", "p_mbar"):
            self.page_settings.fields[key].valueChanged.connect(lambda _v=None: self._klima_anzeigen())
        self.page_settings.climate_auto.toggled.connect(lambda _on: self._klima_anzeigen())
        self.klima_timer = QtCore.QTimer(self)
        self.klima_timer.timeout.connect(self._klima_takt)
        self.klima_timer.start(1000)
        self._set_status("Nicht verbunden – „Verbinden“ drücken", "")
        self._klima_anzeigen()
        return w

    def _rechte_spalte(self) -> QtWidgets.QWidget:
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 2, 0)
        lay.setSpacing(5)
        lay.addWidget(abschnitt("UMGEBUNG  (PST-Basis, BME280)"))
        tp = QtWidgets.QHBoxLayout()
        tp.setSpacing(6)
        self.k_temp = KlimaKachel("T Luft", "°C")
        self.k_druck = KlimaKachel("p Luft", "mbar")
        for k in (self.k_temp, self.k_druck):
            k.setToolTip("Gelesen wird nur zwischen den Läufen (beim START, von Hand unter Einstellungen › Rolle/Lauf "
                         f"und alle {KLIMA_ALLE_S / 60:.0f} min, solange nichts läuft)")
            tp.addWidget(k)
        lay.addLayout(tp)
        self.live_titel = abschnitt("LIVE")
        lay.addWidget(self.live_titel)
        self.k_drehzahl = DrehzahlKachel()
        self.k_drehzahl.kanal_gewaehlt.connect(self._drehzahl_gewaehlt)
        self.k_drehzahl.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.k_drehzahl.customContextMenuRequested.connect(lambda _p: self._grenzen_menue(-1))
        lay.addWidget(self.k_drehzahl)
        self.live_kacheln: List[WertKachel] = []
        for i in range(len(LIVE_STANDARD)):
            k = WertKachel(platz=i + 1, groesse=GROSS)
            k.kanal_gewaehlt.connect(lambda key, i=i: self._live_gewaehlt(i, key))
            k.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            k.customContextMenuRequested.connect(lambda _p, i=i: self._grenzen_menue(i))
            k.setToolTip("Klick auf den Titel: Kanal wählen · Rechtsklick: Balkengrenzen")
            self.live_kacheln.append(k)
            lay.addWidget(k)
        lay.addStretch(1)
        self.ergebnis_titel = abschnitt("ERGEBNIS")
        lay.addWidget(self.ergebnis_titel)
        pm = QtWidgets.QHBoxLayout()
        pm.setSpacing(6)
        self.k_pmax = ErgebnisKachel("Pmax", "PS", "p_color")
        self.k_mmax = ErgebnisKachel("Mmax", "Nm", "m_color")
        pm.addWidget(self.k_pmax)
        pm.addWidget(self.k_mmax)
        lay.addLayout(pm)
        area = _Spalte(self._spalte_anpassen)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setWidget(inner)
        area.setMinimumWidth(280)
        self._spalte_gross, self._spalte_h_gross = True, 0
        return area

    def _spalte_anpassen(self, h: int):
        """Kacheln kompakt, solange die Spalte sonst rollen muesste (kleine Fenster), sonst gross."""
        inner = self.rechte_spalte.widget() if hasattr(self, "rechte_spalte") else None
        if inner is None:
            return
        if self._spalte_gross:
            self._spalte_h_gross = inner.minimumSizeHint().height()
        gross = h >= self._spalte_h_gross
        if gross == self._spalte_gross:
            return
        self._spalte_gross = gross
        for k in [self.k_drehzahl] + self.live_kacheln:
            kachel_groesse(k, gross)

    def _leiste_gemerkt(self, *_a):
        self.settings["laeufe_leiste"] = self.laeufe_leiste.zustand()
        _speichern(self.settings)

    # ------------------------------------------------------------------ Knoepfe oben rechts
    def _lauf_aktiv(self) -> bool:
        return self.ctrl is not None and self.ctrl.state in ACTIVE

    def _start_oder_abbruch(self):
        """Ein Knopf: START (F1) bzw. im Lauf Abbrechen/Ausrollen beenden (Esc)."""
        if self._lauf_aktiv():
            self.on_abort()
        else:
            self.on_start()

    def _start_knopf_stil(self):
        """START normal (fett), im Lauf rot wie ein Fehler: Abbrechen bzw. Ausrollen beenden."""
        b = self.start_btn
        T = theme.tokens()
        if self._lauf_aktiv():
            coast = self.ctrl.state == COAST
            b.setText(AUSROLLEN_TEXT if coast else ABBRECHEN_TEXT)
            b.setToolTip("Ausrollen beenden – der Lauf ist schon fertig und wird ausgewertet (Esc)" if coast else
                         "Lauf abbrechen (Esc)")
            b.setStyleSheet(f"QPushButton {{ font-size: {12 if coast else 14}px; font-weight: 800; background: {T['err_bg']};"
                            f" color: {T['err_text']}; border: 1px solid {T['err_bg']}; }}"
                            f"QPushButton:hover {{ border: 2px solid {T['err_text']}; }}")
        else:
            b.setText(START_TEXT)
            b.setToolTip("Lauf starten (F1)")
            b.setStyleSheet("QPushButton { font-size: 16px; font-weight: 800; }")

    def _lauf_knopf_stand(self, *_a):
        """Unterzeile/Tooltip von „Lauf-Einstellungen …“ nach Aenderung von Übersetzung, Art, Filter, Grenzen."""
        if hasattr(self, "lauf_btn"):
            self.lauf_btn.set_stand(*lauf_stand(self.page_settings))

    def _lauf_popup_zeigen(self):
        if self._lauf_aktiv():
            return
        if self.lauf_popup is None:
            p = self.lauf_popup = LaufEinstellungen(self.page_settings, self)
            p.einmessen.connect(self.on_measure_ratio)
            p.alle_einstellungen.connect(lambda: self.show_page(self.page_settings))
            p.geschlossen.connect(self._persist)
        self.lauf_popup.zeigen_an(self.lauf_btn)

    def _uebersetzung_art_geaendert(self, _i=None):
        from .gui import UEBERSETZUNG_ART
        art = self.page_settings.ratio_art.currentData() or "hand"
        if self.settings.get(UEBERSETZUNG_ART) != art:
            self.settings[UEBERSETZUNG_ART] = art
            _speichern(self.settings)
        self._lauf_knopf_stand()

    def _verbinde_lauf_einstellungen(self):
        """Knopf, Popup und Stand an die Rolle/Lauf-Seite haengen (aus gui._wire)."""
        s = self.page_settings
        self.start_btn.clicked.connect(self._start_oder_abbruch)
        self.lauf_btn.clicked.connect(self._lauf_popup_zeigen)
        for key, _t in LAUF_FELDER:
            s.fields[key].valueChanged.connect(self._lauf_knopf_stand)
        s.filter_art_geaendert.connect(self._lauf_knopf_stand)
        s.ratio_art.currentIndexChanged.connect(self._uebersetzung_art_geaendert)
        self._lauf_knopf_stand()
        self._start_knopf_stil()

    # ------------------------------------------------------------------ Laufzustand
    def _set_status(self, text: str, color: str):
        """Laufzustand neben START. color: Stufe (ok/warn/err/info, "" = neutral) oder eine alte feste Farbe."""
        self._status_last = (text, color)
        from . import farben
        stufe = farben.stufe_von(color)
        if stufe == "info":
            stufe = theme.WARNUNG                  # Handlungsaufforderung (Kupplung ziehen …)
        elif not stufe:
            stufe = theme.GUT if "START" in text else theme.AUS
        self.zustand.set_zustand(stufe, text.replace(" (F1)", ""))   # F1 steht schon auf dem Knopf daneben
        self.zustand.setToolTip(text)

    def _laufzustand_zeigen(self, st: str, lv):
        """Zustand des Laufs im Feld neben START, „GAS WEG“ gross ueber dem Diagramm."""
        if st == WAIT:
            self._set_status("GO – Vollgas!", theme.GUT)
        elif st == RUN:
            if self.ctrl.gas_off():
                aus = self.ctrl.ausrollen
                self._set_status("GAS WEG – KUPPLUNG ZIEHEN!" if aus else "GAS WEG!", theme.FEHLER)
                grund = f"n Stop {zahl(self.ctrl.params.n_stop)} 1/min erreicht – " + (
                    "Kupplung ziehen, ausrollen lassen" if aus else "Lauf wird ausgewertet")
                self.gas_weg.zeige("GAS WEG", grund)
            else:
                self._set_status(f"Messung … {zahl(lv.n_calc)} 1/min", theme.WARNUNG)
        elif st == COAST:
            self._set_status(f"KUPPLUNG ZIEHEN – ausrollen lassen … {zahl(lv.n_calc)} 1/min", "info")
        self.gas_weg.takt()

    # ------------------------------------------------------------------ Fahrzeug: Ansicht laden
    def _fahrzeug(self) -> Tuple[Optional[int], str]:
        vid = self.vehicle_combo.currentData()
        return vid, (self.vehicle_combo.currentText() if vid else "")

    def _fahrzeug_ansicht_laden(self):
        """Belegung der Live-Plaetze, Drehzahl, Schnellwerte fuer das gewaehlte Fahrzeug."""
        _vid, name = self._fahrzeug()
        self.live_titel.setText(f"LIVE  ·  Belegung für {name}" if name else "LIVE  ·  Belegung (ohne Fahrzeug)")
        self._live_plaetze_anwenden()

    def _setup_zeile_laden(self):
        vid, vname = self._fahrzeug()
        sid = self.setup_combo.currentData()
        setup = (self.db.setup(sid) or {}) if sid else {}
        veh = (self.db.vehicle(vid) or {}) if vid else {}
        weg = self.setup_zeile.set_setup(sid, setup.get("name", ""), setup, self.db.schnellwert_plaetze(vid), vname,
                                         veh.get("gemisch", ""))
        if weg:
            self.statusBar().showMessage("Anderes Setup gewählt – die ungespeicherten Setup-Werte wurden verworfen.",
                                         8000)
        self._setup_combo_marke()

    def _setup_combo_marke(self):
        """Setup-Auswahl: „✎ geändert“ hinter dem gewaehlten Setup, solange Werte geaendert sind."""
        c = self.setup_combo
        cur = c.currentIndex()
        for i in range(c.count()):
            t = c.itemText(i).replace(GEAENDERT, "")
            if i == cur and self.setup_zeile.ist_geaendert() and i > 0:
                t += GEAENDERT
            if c.itemText(i) != t:
                c.setItemText(i, t)

    def _schnellwert_plaetze(self, felder: List[str]):
        """Plaetze der Setup-Werte geaendert (Feld gewaehlt, Platz hinzugefuegt/entfernt): je Fahrzeug speichern."""
        vid, _name = self._fahrzeug()
        self.db.set_schnellwert_plaetze(vid, felder)

    # ------------------------------------------------------------------ Setup speichern / nachfragen
    def setup_neu_anlegen(self, name: str, notiz: str = "", werte: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Neues Setup aus dem gewaehlten Setup und den geaenderten Werten (werte: statt dessen alle Werte) anlegen,
        auswaehlen und die Aenderungen loeschen. -> neue Setup-ID (None ohne Fahrzeug)."""
        vid, _name = self._fahrzeug()
        if not vid:
            return None
        if werte is None:
            sid = self.setup_combo.currentData()
            alt = (self.db.setup(sid) or {}) if sid else {}
            werte = {k: v for k, v in alt.items() if k not in ("id", "vehicle_id", "name", "notiz")}
            werte.update(self.setup_zeile.aenderungen())
        neu = self.db.save_setup(vid, name or "Setup", werte, notiz)
        self._setup_neu_fertig(vid, neu)
        return neu

    def _setup_neu_fertig(self, vid: int, sid: int):
        self.setup_zeile.verwerfen()
        self._reload_vehicle_combo(vid, sid)
        self.page_db.reload()
        self._persist()
        if self.run_filter.currentIndex():
            self._reload_runs()
        self.statusBar().showMessage(f"Neues Setup „{self.setup_combo.currentText()}“ gespeichert und gewählt.", 8000)

    def _setup_neu_dialog(self) -> bool:
        """„Als neues Setup speichern …“: Setup-Dialog (dialogs.EditDialog) mit den Werten vorbelegt."""
        vid, _name = self._fahrzeug()
        if not vid:
            QtWidgets.QMessageBox.information(self, "Setup", "Bitte zuerst ein Fahrzeug wählen.")
            return False
        from .dialogs import EditDialog
        sid = self.setup_combo.currentData()
        alt = (self.db.setup(sid) or {}) if sid else {}
        werte = {k: v for k, v in alt.items() if k not in ("id", "vehicle_id", "name", "notiz")}
        werte.update(self.setup_zeile.aenderungen())
        dlg = EditDialog(self.db, vid, None, self, "Als neues Setup speichern")
        dlg.sname.setText(self.setup_zeile.neuer_name())
        dlg.snotiz.setText((f"aus „{alt.get('name', '')}“: " if alt else "") + self.setup_zeile.aenderung_text())
        dlg.sform.set_values(werte)
        if dlg.exec() != QtWidgets.QDialog.Accepted or not dlg.sid:
            return False
        self._setup_neu_fertig(dlg.vid or vid, dlg.sid)
        return True

    def _setup_frage(self) -> str:
        """Dialog vor START: "neu" | "lauf" | "" (Abbrechen)."""
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, "Setup geändert",
                                    f"{self.setup_zeile.marke_text()}\n\nDie geänderten Werte sind nicht gespeichert.",
                                    parent=self)
        b_neu = box.addButton("Neues Setup speichern …", QtWidgets.QMessageBox.AcceptRole)
        b_lauf = box.addButton("Nur für diesen Lauf", QtWidgets.QMessageBox.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(b_lauf)
        box.setInformativeText("Abschaltbar unter Einstellungen › Rolle/Lauf (vor START nachfragen).")
        box.exec()
        return {b_neu: "neu", b_lauf: "lauf"}.get(box.clickedButton(), "")

    def _setup_vor_start(self) -> bool:
        """Vor START: geaendertes, ungespeichertes Setup? Je nach Einstellung nachfragen. False = nicht starten."""
        z = self.setup_zeile
        if not z.ist_geaendert() or z.nur_lauf or not self.settings.get(START_NACHFRAGE, True):
            return True
        antwort = self._setup_frage()
        if antwort == "neu":
            return self._setup_neu_dialog()
        if antwort == "lauf":
            z.set_nur_lauf(True)
            return True
        return False

    def _setup_schnappschuss(self) -> Tuple[List[Dict[str, str]], bool]:
        """(setup_werte, nur_lauf) fuer den naechsten gespeicherten Lauf."""
        from .db import all_setup_keys, setup_schnappschuss
        sid = self.setup_combo.currentData()
        setup = (self.db.setup(sid) or {}) if sid else {}
        aend = self.setup_zeile.aenderungen()
        felder = list(dict.fromkeys([f for f in self.setup_zeile.felder if f]
                                    + [k for k in all_setup_keys() if wert_text(setup.get(k))] + list(aend)))
        return setup_schnappschuss(setup, felder, aend), bool(aend)

    # ------------------------------------------------------------------ Live-Plaetze
    def _live_kanaele(self) -> List[Kanal]:
        lam_mode = self.page_settings.lambda_radio.isChecked()
        out = []
        for key, (titel, einheit) in LIVE_KANAELE.items():
            lo, hi, dez, _z, _m = kanal_bereich(key)
            if key == "lam" and not lam_mode:
                titel, einheit = "AFR", ""
            out.append(Kanal(key, titel, einheit, "PST", dez, lo, hi))
        for name, label, unit in self._ecu_available():
            lo, hi, dez, _z, _m = ecu_bereich(name)
            label, unit = ecu_titel(name, label, unit)
            out.append(Kanal(ECU_PREFIX + name, label, unit, "ECU", dez, lo, hi))
        return out

    def _live_plaetze_anwenden(self):
        """Kanaele, Balken und Zonen der 5 Plaetze und der Drehzahl aus der Datenbank (je Fahrzeug)."""
        if not hasattr(self, "live_kacheln"):
            return
        vid, name = self._fahrzeug()
        kan = self._live_kanaele()
        hinweis = f"Auswahl gilt für {name}" if name else "Auswahl gilt ohne Fahrzeug"
        self._kachel_cache.clear()
        for k, p in zip(self.live_kacheln, self.db.live_plaetze(vid)):
            kk = list(kan)
            if p["kanal"] and not any(x.schluessel == p["kanal"] for x in kk):
                ecu = p["kanal"].startswith(ECU_PREFIX)
                titel, einheit = (ecu_titel(p["kanal"][len(ECU_PREFIX):]) if ecu
                                  else LIVE_KANAELE.get(p["kanal"], (p["kanal"], "")))
                dez = kanal_bereich(p["kanal"])[2]
                kk.append(Kanal(p["kanal"], titel, einheit, "ECU" if ecu else "PST", dez, p["min"], p["max"]))
            k.set_kanaele(kk)
            k.set_kanal(p["kanal"])
            k.set_bereich(p["min"], p["max"], zonen=[tuple(z) for z in p["zonen"]], dez=tick_dez(p["min"], p["max"]),
                          marke=kanal_bereich(p["kanal"])[4] if p["kanal"] else False)
            k.set_menue_hinweis(hinweis)
            k.set_wert(None)
        d = self._drehzahl_einstellung()
        k = self.k_drehzahl
        k.set_kanal(d["quelle"], quelle="")
        k.set_bereich(d["min"], d["max"], zonen=[tuple(z) for z in d["zonen"]], dez=0)
        k.set_menue_hinweis(hinweis)
        k.set_wert(None)
        self._kacheln_zeigen(alle=True)

    def _drehzahl_einstellung(self) -> Dict[str, Any]:
        vid, _name = self._fahrzeug()
        d = self.db.vehicle_pref(vid, "drehzahl", None)
        out = dict(DREHZAHL_STANDARD)
        if isinstance(d, dict):
            for key in out:
                if key in d:
                    out[key] = d[key]
        if out["quelle"] not in [q for q, _t, _s in DREHZAHL_QUELLEN]:
            out["quelle"] = DREHZAHL_STANDARD["quelle"]
        try:
            lo, hi = float(out["min"]), float(out["max"])
        except (TypeError, ValueError):
            lo, hi = DREHZAHL_STANDARD["min"], DREHZAHL_STANDARD["max"]
        out["min"], out["max"] = (lo, hi) if hi > lo else (DREHZAHL_STANDARD["min"], DREHZAHL_STANDARD["max"])
        return out

    def _drehzahl_gewaehlt(self, key: str):
        if not key:                                # „leer lassen“ gibt es fuer die Drehzahl nicht
            key = DREHZAHL_STANDARD["quelle"]
        vid, _name = self._fahrzeug()
        d = self._drehzahl_einstellung()
        d["quelle"] = key
        self.db.set_vehicle_pref(vid, "drehzahl", d)
        self._live_plaetze_anwenden()

    def _live_gewaehlt(self, i: int, key: str):
        vid, _name = self._fahrzeug()
        plaetze = self.db.live_plaetze(vid)
        lo, hi, dez, zonen, marke = kanal_bereich(key) if key else (0.0, 100.0, 0, [], False)
        plaetze[i] = {"kanal": key, "min": lo, "max": hi, "zonen": [list(z) for z in zonen]}
        self.db.set_live_plaetze(vid, plaetze)
        self._live_plaetze_anwenden()

    def _grenzen_menue(self, i: int):
        """Rechtsklick auf einen Platz (i = -1: Drehzahl): Balkengrenzen einstellen."""
        k = self.k_drehzahl if i < 0 else self.live_kacheln[i]
        if not k.kanal:
            return
        m = QtWidgets.QMenu(self)
        a_set = m.addAction("Balkengrenzen …")
        a_std = m.addAction("Standardgrenzen")
        act = m.exec(QtGui.QCursor.pos())
        if act == a_set:
            self._grenzen_dialog(i)
        elif act == a_std:
            self._grenzen_setzen(i, None)

    def _grenzen_dialog(self, i: int) -> bool:
        k = self.k_drehzahl if i < 0 else self.live_kacheln[i]
        vid, _name = self._fahrzeug()
        if i < 0:
            d = self._drehzahl_einstellung()
            lo, hi, zonen = d["min"], d["max"], d["zonen"]
            std = (DREHZAHL_STANDARD["min"], DREHZAHL_STANDARD["max"], 0, DREHZAHL_STANDARD["zonen"], False)
        else:
            p = self.db.live_plaetze(vid)[i]
            lo, hi, zonen = p["min"], p["max"], p["zonen"]
            std = kanal_bereich(k.kanal)
        info = k.kanal_info()
        dlg = GrenzenDialog(info.titel if info else k.kanal, lo, hi, zonen, info.dez if info else 1, std, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return False
        self._grenzen_setzen(i, dlg.werte())
        return True

    def _grenzen_setzen(self, i: int, werte: Optional[Dict[str, Any]]):
        """Grenzen eines Platzes speichern (werte None = Standard des Kanals)."""
        vid, _name = self._fahrzeug()
        if i < 0:
            d = self._drehzahl_einstellung()
            if werte is None:
                werte = {k: DREHZAHL_STANDARD[k] for k in ("min", "max", "zonen")}
            d.update(werte)
            self.db.set_vehicle_pref(vid, "drehzahl", d)
        else:
            plaetze = self.db.live_plaetze(vid)
            if werte is None:
                lo, hi, _dez, zonen, _m = kanal_bereich(plaetze[i]["kanal"])
                werte = {"min": lo, "max": hi, "zonen": [list(z) for z in zonen]}
            plaetze[i] = {**plaetze[i], **werte}
            self.db.set_live_plaetze(vid, plaetze)
        self._live_plaetze_anwenden()

    def _live_werte(self, lv, f, ecu_rpm=None):
        """Neue Telegramme: Werte der PST-Kanaele merken (angezeigt in _kacheln_takt)."""
        fin = lambda x: x is not None and isinstance(x, (int, float)) and math.isfinite(x)
        afr = f.afr if fin(f.afr) and f.afr > 0.5 else None
        self._live = {
            "n": lv.n_calc if fin(lv.n_calc) else None, "ign": lv.n_meas if fin(lv.n_meas) else None,
            "v": lv.v_kmh if fin(lv.v_kmh) else None,
            "egt1": f.egt1 if fin(f.egt1) and f.egt1 > 0 else None,
            "egt2": f.egt2 if fin(f.egt2) and f.egt2 > 0 else None,
            "lam": afr / 14.7 if afr else None, "afr": afr,
            "ratio": lv.n_meas / lv.n_roll if lv.n_roll > 1 and fin(lv.n_meas) and lv.n_meas > 0 else None,
            "ratio_ecu": ecu_rpm / lv.n_roll if ecu_rpm is not None and lv.n_roll > 1 else None,
            "rate": lv.rate if fin(lv.rate) and lv.rate > 0 else None,
        }

    def _kacheln_takt(self):
        """Aus _tick: Live-Kacheln hoechstens 10-mal je Sekunde neu."""
        now = time.time()
        if now - self._kachel_t < 0.1:
            return
        self._kachel_t = now
        if self.link is None and self._live:
            self._live = {}
        self._kacheln_zeigen()

    def _kacheln_zeigen(self, alle: bool = False):
        if not hasattr(self, "live_kacheln"):
            return
        if alle:
            self._kachel_cache.clear()
        for i, k in enumerate(self.live_kacheln):
            self._kachel_wert(i, k)
        self._kachel_wert(-1, self.k_drehzahl)
        w = self._live
        self.k_drehzahl.set_unter("Rolle " + (f"{zahl(w['v'], 1)} km/h" if w.get("v") is not None else "–"),
                                  ("Rolle × i " + zahl(w.get("n"))) if self.k_drehzahl.kanal == "ign" else
                                  ("Zündung " + (zahl(w["ign"]) if w.get("ign") else "–")))

    def _kachel_wert(self, i: int, k: WertKachel):
        kanal = k.kanal
        if not kanal:
            return
        text, quelle, fehlt = None, None, ""
        if kanal.startswith(ECU_PREFIX):
            if self.page_rusefi.active_link() is None:
                fehlt, v = "ECU fehlt", None
            else:
                v = self.page_rusefi.live(kanal[len(ECU_PREFIX):])
                quelle = "ECU"
        else:
            v = self._live.get(kanal)
            quelle = "" if i < 0 else "PST"
            if kanal == "lam":
                if v is not None and not self.page_settings.lambda_radio.isChecked():
                    text = zahl(self._live.get("afr"), 2)
                lam = self.page_hw.lam if (self.link is not None and self.link.is_basis) else None
                if v is None and lam is not None and getattr(lam, "known", False):
                    if getattr(lam, "low_voltage", False) or getattr(lam, "uv", False):
                        quelle = "12 V zu niedrig"
                    elif lam.st == "heizt":
                        quelle = "heizt auf"
                    elif not lam.on:
                        quelle = "Heizung aus"
        merk = (kanal, fehlt, quelle, text, None if v is None else round(float(v), 4))
        if self._kachel_cache.get(i) == merk:
            return
        self._kachel_cache[i] = merk
        if fehlt:
            k.set_fehlt(fehlt)
            return
        if quelle is not None:
            k.set_quelle(quelle)
        k.set_wert(v, text)
        if v is not None:                          # ueber/unter dem Balken: am Ende stehen (Farbe der Endzone)
            b = k.balken
            b.set_wert(min(max(float(v), b.lo), b.hi))

    # ------------------------------------------------------------------ Klima (T/p)
    def _klima_gelesen(self, c):
        self._klima_t = time.time()
        self._klima_feuchte = c[2] if c and len(c) > 2 else 0.0
        self._klima_anzeigen()

    def _klima_anzeigen(self):
        if not hasattr(self, "k_temp"):
            return
        s = self.page_settings
        if self._klima_t:
            alter = "gelesen " + alter_text(time.time() - self._klima_t)
        elif not s.climate_auto.isChecked():
            alter = "von Hand eingetragen"
        else:
            alter = "noch nicht gelesen"
        self.k_temp.set(zahl(s.fields["temp_c"].value(), 1), alter)
        self.k_druck.set(zahl(s.fields["p_mbar"].value(), 0), alter)
        if self._klima_feuchte > 0:
            self.k_temp.setToolTip(f"rel. Feuchte {self._klima_feuchte:.0f} %")

    def _klima_lesen_moeglich(self) -> bool:
        L = self.link
        if L is None or getattr(L, "is_zange_usb", False) or getattr(L, "bootloader", "") \
                or not self.page_settings.climate_auto.isChecked():
            return False
        if self.ctrl is not None and self.ctrl.state in ACTIVE:
            return False
        if waiting() or self.page_oszi.rec is not None or self.ratio_mess.aktiv:
            return False
        if getattr(getattr(self, "page_flash", None), "busy", False):
            return False
        pruef = getattr(self, "page_pruef", None)
        if pruef is not None and pruef.bg.pending:
            return False
        if QtWidgets.QApplication.activeModalWidget() is not None or QtWidgets.QApplication.activePopupWidget():
            return False
        return self.isVisible() and self.tabs.currentIndex() == 0

    def _klima_takt(self):
        """Jede Sekunde: Alter anzeigen; zwischen den Laeufen T/p hoechstens alle KLIMA_ALLE_S lesen."""
        now = time.time()
        if self.link is not self._klima_link:
            self._klima_link, self._verbunden_t = self.link, now
        self._klima_anzeigen()
        if not self._klima_lesen_moeglich() or now - self._verbunden_t < KLIMA_ERSTES_S:
            return
        if now - max(self._klima_t, self._klima_versuch_t) < KLIMA_ALLE_S:
            return
        self._klima_versuch_t = now
        link = self.link
        c = wait_in_thread(link.climate)
        if c:
            self.page_settings.fields["temp_c"].setValue(c[0])
            self.page_settings.fields["p_mbar"].setValue(c[1])
            self._klima_gelesen(c)
        if self.link is link and self.page_settings.live_check.isChecked():
            link.start()

    # ------------------------------------------------------------------ ECU, Lambda-Anzeige
    def _ecu_changed(self):
        """Live-Plaetze und Diagramm-Kanaele nach Auswahl/Verbindung der ECU neu aufbauen."""
        avail = {n: (lab, unit) for n, lab, unit in self._ecu_available()}
        self.plot.set_overlays([(n, *avail[n]) for n in self.settings.get("ecu_plot", []) if n in avail])
        self._apply_lower([(n, *avail[n]) for n in avail])
        self._live_plaetze_anwenden()
        self._redraw_runs()
        if self.ctrl and self.ctrl.frames:
            self._draw_live(final=self.ctrl.state == DONE)

    def _lambda_mode_changed(self, lam: bool):
        self.plot.set_lambda_mode(lam)
        self._live_plaetze_anwenden()
        self._redraw_runs()

    # ------------------------------------------------------------------ Laeufe: Liste, Plaetze, Schloss
    def _lauf_farbe(self, row) -> str:
        """Basisfarbe eines Laufs (theme.laufarbe passt sie dem Thema an); Mittelwert: avg_color."""
        if row.ist_mittelwert:
            return row.farbe or "@avg_color"
        return run_color(row)

    def _reload_runs(self):
        self._cursor_names = None
        f = self.run_filter.currentIndex()
        vid = self.vehicle_combo.currentData() if f == 1 else None
        sid = self.setup_combo.currentData() if f == 2 else None
        if f == 0:
            rows = self.db.runs()
        elif vid or sid:
            rows = self.db.runs(vehicle_id=vid, setup_id=sid)
        else:
            rows = []
        cur_vid, _name = self._fahrzeug()
        fest, roll = messplaetze([r.id for r in rows if r.art == ART_LAUF], self.db.gesperrte_plaetze(cur_vid))
        laeufe = {r.id: r for r in rows}
        for rid in fest:                           # Referenzen und Laeufe ausserhalb des Filters
            if rid not in laeufe:
                r = self.db.run(rid)
                if r is not None:
                    laeufe[rid] = r
        fest = [i for i in fest if i in laeufe]
        self._plaetze, self._laeufe = (fest, roll), laeufe
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        t = self.run_tree
        sel = {it.data(0, QtCore.Qt.UserRole) for it in t.selectedItems()}
        t.blockSignals(True)
        t.clear()
        aeltere = [r for r in rows if r.id not in fest and r.id not in roll]
        if fest:
            self._gruppe("GESPERRT")
            for rid in fest:
                self._lauf_item(laeufe[rid], True, True, vehicles)
        if roll:
            self._gruppe(f"ROLLIEREND  (die letzten {len(roll)})")
            for rid in roll:
                self._lauf_item(laeufe[rid], False, True, vehicles)
        if aeltere:
            if fest or roll:
                self._gruppe("ÄLTERE LÄUFE")
            for r in aeltere:
                self._lauf_item(r, False, False, vehicles)
        for i in range(t.topLevelItemCount()):
            it = t.topLevelItem(i)
            if it.data(0, QtCore.Qt.UserRole) in sel:
                it.setSelected(True)
        t.blockSignals(False)
        spalten = (self.settings.get("fenster") or {}).get("laeufe_spalten")
        if not spalten:
            t.setColumnWidth(0, 28)
            for c in range(2, len(LaufListe.SPALTEN)):
                t.resizeColumnToContents(c)
            rest = sum(t.columnWidth(c) for c in range(t.columnCount()) if c != 1)
            t.setColumnWidth(1, max(150, min(t.sizeHintForColumn(1), LEISTE_BREITE - 40 - rest)))   # ohne Rollbalken
        self._laeufe_kopf()
        self._redraw_runs()
        self.viewer.reload()

    def _gruppe(self, text: str):
        g = QtWidgets.QTreeWidgetItem([text])
        g.setFlags(QtCore.Qt.ItemIsEnabled)
        f = g.font(0)
        f.setBold(True)
        g.setFont(0, f)
        g.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.farbe("text2"))))
        self.run_tree.addTopLevelItem(g)
        g.setFirstColumnSpanned(True)

    def _lauf_item(self, row, gesperrt: bool, im_diagramm: bool, vehicles: Dict[int, str]):
        mw = row.ist_mittelwert
        dauer = row.quelle == "Dauermessung"
        label = row.name if mw else self._run_label(row)
        it = QtWidgets.QTreeWidgetItem([
            "", label, "Dauer" if dauer else zahl(row.p_max, 1), "" if dauer else zahl(row.n_pmax),
            "" if dauer else zahl(row.m_max, 1), "Ref." if mw else self._fault_cell(row)])
        it.setData(0, QtCore.Qt.UserRole, row.id)
        it.setIcon(0, schloss_icon(gesperrt))
        it.setToolTip(0, "Schloss lösen: Platz wird wieder rollierend frei" if gesperrt else
                      "Schloss: Platz sperren – der Lauf bleibt im Diagramm stehen")
        it.setIcon(1, farb_icon(self._lauf_farbe(row), mw))
        flags = it.flags()
        if im_diagramm:
            it.setFlags(flags | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(1, QtCore.Qt.Checked if row.sichtbar else QtCore.Qt.Unchecked)
        tip = [label]
        if mw:
            tip.append("Mittelwert aus " + (", ".join(self._run_label(q) for q in
                                                       (self.db.run(i) for i in row.quellen) if q) or "–"))
        else:
            tip.append(f"{row.quelle}: {row.path}")
            if row.rate:
                tip.append(f"{row.rate:.0f} Hz")
        veh = vehicles.get(row.vehicle_id, "")
        if veh:
            tip.append(f"Fahrzeug: {veh}")
        if row.notiz:
            tip.append(f"Notiz: {row.notiz}")
        if not im_diagramm:
            tip.append("Nicht im Diagramm – Schloss klicken, um ihn auf einen gesperrten Platz zu legen")
        it.setToolTip(1, "\n".join(tip))
        for c in range(2, 6):
            it.setTextAlignment(c, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if row.fehler and not mw:
            it.setToolTip(5, "Fehleranteil (Messfehler Rolle/Zündung): " + fault_text(row.fehler))
            if max(row.fehler.values()) >= 1.0:
                it.setForeground(5, QtGui.QBrush(QtGui.QColor(theme.farbe("err_fg"))))
        if gesperrt:
            bg = QtGui.QBrush(QtGui.QColor(theme.farbe("lock_bg")))
            for c in range(6):
                it.setBackground(c, bg)
            f = it.font(1)
            f.setBold(True)
            it.setFont(1, f)
        if not im_diagramm:
            it.setForeground(1, QtGui.QBrush(QtGui.QColor(theme.farbe("text2"))))
        self.run_tree.addTopLevelItem(it)
        return it

    def _laeufe_kopf(self):
        """Hinweis „5 Plätze …“, Streifen und Farbfelder der Laeufe im Diagramm."""
        fest, roll = self._plaetze
        frei = MESSPLAETZE - len(fest)
        self.laufliste.info.setText(f"{MESSPLAETZE} Plätze im Diagramm:  {len(fest)} gesperrt,  {frei} rollierend "
                                    "(die letzten Läufe)")
        self.laeufe_leiste.set_streifen_text(f"Läufe · {MESSPLAETZE} Plätze: {len(fest)} gesperrt, {frei} rollierend")
        self.laeufe_leiste.set_streifen_farben([self._lauf_farbe(self._laeufe[i]) for i in fest + roll
                                                if i in self._laeufe and self._laeufe[i].sichtbar])

    def _lauf_geklickt(self, it, col: int):
        rid = it.data(0, QtCore.Qt.UserRole)
        if col == 0 and rid is not None:
            self.schloss(rid)

    def schloss(self, rid: int) -> bool:
        """Schloss eines Laufs umschalten (gesperrter Platz im Diagramm, je Fahrzeug). False = alle Plaetze voll."""
        vid, _name = self._fahrzeug()
        g = self.db.gesperrte_plaetze(vid)
        if rid in g:
            g.remove(rid)
        else:
            if len(g) >= MESSPLAETZE:
                self.statusBar().showMessage(f"Alle {MESSPLAETZE} Plätze sind gesperrt – zuerst ein Schloss lösen.", 8000)
                return False
            g.append(rid)
            row = self.db.run(rid)
            if row is not None and not row.sichtbar:
                self.db.update_run(rid, sichtbar=True)
        self.db.set_gesperrte_plaetze(vid, g)
        self._reload_runs()
        return True

    def _run_item_changed(self, it, col):
        rid = it.data(0, QtCore.Qt.UserRole)
        if col != 1 or rid is None or not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
            return
        an = it.checkState(1) == QtCore.Qt.Checked
        self.db.update_run(rid, sichtbar=an)
        if rid in self._laeufe:
            self._laeufe[rid].sichtbar = an
        self._cursor_names = None
        self._laeufe_kopf()
        self._redraw_runs()

    def _current_run_id(self) -> Optional[int]:
        it = self.run_tree.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def referenz_laden(self, rid: int) -> bool:
        """Mittelwert-Lauf als Referenz auf einen gesperrten Platz legen."""
        vid, _name = self._fahrzeug()
        g = self.db.gesperrte_plaetze(vid)
        if rid in g:
            return True
        if len(g) >= MESSPLAETZE:
            QtWidgets.QMessageBox.information(self, "Referenz laden", f"Alle {MESSPLAETZE} Plätze sind gesperrt – "
                                                                      "bitte zuerst ein Schloss lösen.")
            return False
        self.db.set_gesperrte_plaetze(vid, [rid] + g)
        self.db.update_run(rid, sichtbar=True)
        self._mw_cache.pop(rid, None)
        self._reload_runs()
        return True

    def _referenz_dialog(self):
        vid, _name = self._fahrzeug()
        rows = [r for r in self.db.runs(vehicle_id=vid, mittelwert=True) if r.ist_mittelwert]
        if not rows and vid:
            rows = [r for r in self.db.runs(mittelwert=True) if r.ist_mittelwert]
        if not rows:
            QtWidgets.QMessageBox.information(
                self, "Referenz laden", "Es ist noch kein Mittelwert-Lauf gespeichert.\n\nIn der Auswertung Läufe "
                                        "markieren und „Mittelwert bilden …“ wählen.")
            return
        dlg = ReferenzDialog([r for r in rows if r.sichtbar], [r for r in rows if not r.sichtbar], self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.gewaehlt() is not None:
            self.referenz_laden(dlg.gewaehlt())

    def _mittelwert(self, row):
        erg = self._mw_cache.get(row.id)
        if erg is None:
            try:
                erg = mittelwert.laden(row)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(f"Referenz {row.name}: {exc}", 8000)
                return None
            self._mw_cache[row.id] = erg
        return erg

    # ------------------------------------------------------------------ Diagramm
    def _redraw_runs(self):
        fest, roll = getattr(self, "_plaetze", ([], []))
        laeufe = getattr(self, "_laeufe", {})
        ids = [i for i in fest + roll if i in laeufe and laeufe[i].sichtbar]
        keys = {str(i) for i in ids}
        for key in list(self.plot.items) + list(self.plot.bands):
            if key != LIVE_KEY and key not in keys:
                self.plot.remove(key)
        gezeichnet, legende = [], []
        for rid in ids:
            row = laeufe[rid]
            if row.ist_mittelwert:
                if self._mittelwert_zeichnen(row):
                    gezeichnet.append(rid)
                    c = theme.laufarbe(self._lauf_farbe(row))
                    band = BAND_TEXT[band_art(row)]
                    legende.append(f"<span style='color:{c}; font-weight:800'>━━ {row.name}</span>"
                                   "&nbsp; gesperrt, Referenz" + (f" · {band}" if band else ""))
                continue
            e = self._entry(rid)
            if e is None or e[1] is None:
                continue
            _run, res, ch, _p = e
            seg = res.segment
            egt2 = ch["EGT 2"].values[seg] if "EGT 2" in ch else None
            self.plot.set_curves(str(rid), res.n, res.ps, res.nm, lambda_from_afr(res.afr), res.egt, egt2,
                                 color=self._run_color(row), width=2.0, rescale=False,
                                 extra=self._ecu_extra_run(_run, res))
            gezeichnet.append(rid)
        self._gezeichnet = gezeichnet
        self.plot.set_legende("<br>".join(legende))
        if not self.plot.frozen and self.plot.data:
            self.plot.auto_scale()
        self._cursor_names = None
        self._cursor_moved(self._cursor_n)
        self._update_max_display()

    def _mittelwert_zeichnen(self, row) -> bool:
        erg = self._mittelwert(row)
        if erg is None or "ps" not in erg.werte or "nm" not in erg.werte or not len(erg.n):
            return False
        w = erg.werte
        color = self._lauf_farbe(row)
        extra = {k[len(ECU_PREFIX):]: v for k, v in w.items() if k.startswith(ECU_PREFIX)}
        key = str(row.id)
        self.plot.set_curves(key, erg.n, w["ps"], w["nm"], w.get("lam"), w.get("egt1"), w.get("egt2"), color=color,
                             width=MW_BREITE, rescale=False, extra=extra)
        art = band_art(row)
        if art == "kein":
            self.plot.remove_band(key)
        else:
            baender = {k: erg.band(k, art) for k in ("ps", "nm")}
            self.plot.set_band(key, erg.n, {k: b for k, b in baender.items() if b is not None}, color)
        return True

    def diagramm_laeufe(self) -> List[int]:
        """Lauf-IDs, die gerade im Diagramm stehen (ohne die laufende Messung)."""
        return list(self._gezeichnet)

    def _ref_key(self) -> Optional[str]:
        """Lauf fuer ERGEBNIS: laufende/letzte Messung, sonst der gewaehlte, sonst der neueste Lauf im Diagramm."""
        if LIVE_KEY in self.plot.data:
            return LIVE_KEY
        rid = self._current_run_id()
        if rid is not None and str(rid) in self.plot.data:
            return str(rid)
        laeufe = [self._laeufe[i] for i in self._gezeichnet if i in self._laeufe and str(i) in self.plot.data]
        echte = [r for r in laeufe if not r.ist_mittelwert] or laeufe
        if not echte:
            return None
        return str(max(echte, key=lambda r: r.datum).id)

    def _update_max_display(self):
        if not hasattr(self, "k_pmax"):
            return
        key = self._ref_key()
        d = self.plot.data.get(key) if key else None
        if key == LIVE_KEY:
            busy = self.ctrl is not None and self.ctrl.state in ACTIVE
            titel = "ERGEBNIS  (laufender Lauf, bis jetzt)" if busy else "ERGEBNIS  (letzter Lauf, nicht gespeichert)"
        elif key is not None:
            row = self._laeufe.get(int(key)) or self.db.run(int(key))
            art = "gewählter Lauf" if key == str(self._current_run_id()) else "letzter Lauf"
            name = (row.name if row.ist_mittelwert else self._run_label(row).split("  ")[0]) if row else ""
            titel = f"ERGEBNIS  ({'Referenz' if row is not None and row.ist_mittelwert else art} {name})"
        else:
            titel = "ERGEBNIS"
        self.ergebnis_titel.setText(titel)
        self.ergebnis_titel.setToolTip(titel)
        if d is None or not len(d["n"]) or not np.isfinite(d["ps"]).any():
            self.k_pmax.set("Pmax", "–")
            self.k_mmax.set("Mmax", "–")
            return
        jp = int(np.nanargmax(d["ps"]))
        jm = int(np.nanargmax(d["nm"]))
        self.k_pmax.set(f"Pmax @ {zahl(d['n'][jp])}", zahl(d["ps"][jp], 1))
        self.k_mmax.set(f"Mmax @ {zahl(d['n'][jm])}", zahl(d["nm"][jm], 1))

    # ------------------------------------------------------------------ Werte am Cursor
    def _show_cursor_table(self, on: bool):
        self.cursor_table.setVisible(on)
        if self.settings.get("cursor_tabelle", False) != on:
            self.settings["cursor_tabelle"] = on
            _speichern(self.settings)

    def _fit_cursor_table(self):
        """Cursor-Tabelle genau so hoch wie ihr Inhalt (mind. eine Zeile), Rest bekommt das Diagramm."""
        t = self.cursor_table
        rows = max(1, t.rowCount())
        t.setFixedHeight(t.horizontalHeader().height() + rows * t.verticalHeader().defaultSectionSize()
                         + 2 * t.frameWidth() + 1)

    def _cursor_namen(self) -> Dict[str, tuple]:
        """Schluessel im Diagramm -> (Bezeichnung, Farbe, kurze Bezeichnung)."""
        if self._cursor_names is None:
            names = {}
            for rid in self._gezeichnet:
                r = self._laeufe.get(rid)
                if r is None:
                    continue
                if r.ist_mittelwert:
                    names[str(rid)] = (r.name, theme.laufarbe(self._lauf_farbe(r)), "Ø")
                else:
                    lab = self._run_label(r)
                    names[str(rid)] = (lab, self._run_color(r), lab.split("  ")[0][-5:])
            names[LIVE_KEY] = ("laufende Messung", theme.farbe("live_curve"), "live")
            self._cursor_names = names
        return self._cursor_names

    def _cursor_left(self):
        self._cursor_inside = False
        self.cursor_zeile.set_werte("")

    def _cursor_moved(self, n_rpm: float):
        self._cursor_n = n_rpm
        self._cursor_inside = getattr(self.plot, "_cursor_inside", False)
        names = self._cursor_namen()
        lam_mode = self.page_settings.lambda_radio.isChecked()
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

        rows, teile = [], []
        for key in self.plot.data:
            v = self.plot.values_at(key, n_rpm)
            label, color, kurz_name = names.get(key, (key, theme.farbe("live_curve"), key))
            if v is None:
                rows.append((color, label, "", "", "") + ("",) * len(cols))
                continue
            rows.append((color, label, f"{v['n']:.0f}", f"{v['ps']:.2f}", f"{v['nm']:.2f}")
                        + tuple(fmt(k, v.get(k)) for k in cols))
            lam = v.get("lam")
            lam_t = ""
            if lam is not None and np.isfinite(lam) and lam > 0:
                lam_t = f" λ {zahl(lam, 2)}" if lam_mode else f" AFR {zahl(lam * 14.7, 1)}"
            teile.append(f"<span style='color:{color}; font-weight:900'>■</span> {kurz_name} "
                         f"<b>{zahl(v['ps'], 1)}</b> PS <b>{zahl(v['nm'], 1)}</b> Nm{lam_t}")
        if self._cursor_inside and teile:
            self.cursor_zeile.set_werte(f"<b>n {zahl(n_rpm)}</b> &nbsp;·&nbsp; " + " &nbsp;·&nbsp; ".join(teile))
        elif not self._cursor_inside:
            self.cursor_zeile.set_werte("")
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

    # ------------------------------------------------------------------ Liste: Kontextmenue, Knoepfe
    def _run_context_menu(self, pos):
        it = self.run_tree.itemAt(pos)
        rid = it.data(0, QtCore.Qt.UserRole) if it is not None else None
        if rid is None:
            return
        row = self.db.run(rid)
        if row is None:
            return
        mw = row.ist_mittelwert
        gesperrt = rid in self._plaetze[0]
        m = QtWidgets.QMenu(self)
        a_lock = m.addAction("Schloss lösen" if gesperrt else "Platz sperren (Schloss)")
        a_view = m.addAction("In Auswertung öffnen")
        a_note = m.addAction("Notiz bearbeiten …")
        a_color = m.addAction("Farbe ändern …")
        a_assign = m.addAction("Aktuellem Fahrzeug/Setup zuordnen")
        a_params = m.addAction("Mit aktuellen Einstellungen neu rechnen")
        m.addSeparator()
        a_csv = m.addAction("CSV-Export …")
        a_del = m.addAction("Löschen …")
        for a in (a_params, a_csv):
            a.setEnabled(not mw)
        act = m.exec(self.run_tree.viewport().mapToGlobal(pos))
        if act == a_lock:
            self.schloss(rid)
        elif act == a_view:
            self._open_in_viewer(rid)
        elif act == a_note:
            text, ok = QtWidgets.QInputDialog.getText(self, "Notiz", "Notiz zum Lauf:", text=row.notiz)
            if ok:
                self.db.update_run(rid, notiz=text)
                self._reload_runs()
        elif act == a_color:
            c = QtWidgets.QColorDialog.getColor(QtGui.QColor(theme.laufarbe(self._lauf_farbe(row))), self)
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

    def _ausgewaehlte_ids(self) -> List[int]:
        return [i for i in (it.data(0, QtCore.Qt.UserRole) for it in self.run_tree.selectedItems()) if i is not None]

    def on_hide_all(self):
        for row in self.db.runs(mittelwert=True):
            if row.sichtbar:
                self.db.update_run(row.id, sichtbar=False)
        self._reload_runs()

    def on_delete(self):
        ids = self._ausgewaehlte_ids()
        if not ids:
            return
        ans = QtWidgets.QMessageBox.question(
            self, "Löschen", f"{len(ids)} Lauf/Läufe aus der Datenbank löschen?\n"
                             "Messdateien im PyST-Datenordner werden mitgelöscht.")
        if ans != QtWidgets.QMessageBox.Yes:
            return
        for rid in ids:
            self.db.delete_run(rid, delete_files=True)
            self.cache.pop(rid, None)
            self._mw_cache.pop(rid, None)
        self._reload_runs()
