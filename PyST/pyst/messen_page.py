"""
Reiter Messen (Entwurf Oberflaeche 2026-11-08, Runden 2 und 3): Bausteine und Messlogik. MessenMixin wird von
gui.MainWindow geerbt (die Methoden arbeiten auf dem Hauptfenster: self.plot, self.ctrl, self.db …).

Aufbau von oben nach unten:
  Kopfzeile    Fahrzeug | Setup | Notiz | Auto-Speichern | Laufzustand (StatusFeld) | Trennen/Verbinden |
               Lauf-Einstellungen … (Unterzeile: Übersetzung, Filter; Popup LaufEinstellungen mit Übersetzungsart von
               Hand/Funkenmessung, Einmessen, Live abgleichen (abgleich_dialog), Filter, n vom Gas/n Stop – dieselben
               Felder wie Einstellungen › Rolle/Lauf) |
               START (F1), im Lauf derselbe Knopf rot: Abbrechen (Esc) bzw. Ausrollen beenden (Esc)
  Setup-Werte  Schnellwerte (db.schnellwert_plaetze je Fahrzeug, 0 … SCHNELLWERT_MAX Plaetze: „＋“ fuegt an, Titel-Menue
               waehlt das Feld oder entfernt den Platz), direkt editierbar;
               geaendert: gelb mit „vorher 135“, Marke „✎ Setup: …“, Knoepfe Als neues Setup speichern … /
               nur für diesen Lauf / Verwerfen; die Setup-Auswahl zeigt „✎ geändert“
  Mitte        Läufe als SeitenLeiste (32-px-Streifen, Overlay nach 300 ms, Anpinnen) | Diagramm mit Fähnchen am
               Cursor (plots.CursorFahnen), Legende (Referenz; Option ANZEIGE_LEGENDE: alle Läufe), grossen Anzeigen
               (AnweisungGross, ErgebnisGross) und darunter einer schmalen Ergebniszeile (ErgebnisText) | rechte Spalte
  rechts       UMGEBUNG T/p mit Alter, LIVE Drehzahl gross (beide fest oben), darunter rollend 0 … LIVE_MAX Plaetze
               (db.live_plaetze je Fahrzeug, „＋“ fuegt an, Titel-Menue waehlt den Kanal oder entfernt den Platz,
               Balkengrenzen per Rechtsklick), unten ERGEBNIS Pmax/Mmax. Maus im Diagramm (kein Lauf aktiv): die
               Kacheln zeigen die Werte des neuesten Laufs an der Cursor-Drehzahl (Titel „LAUF 16:29 bei 9.500 1/min“)
Diagramm: 5 Plaetze (db.messplaetze) = gesperrte Laeufe (Schloss, auch Mittelwert-Referenzen mit Band min–max)
und die neuesten Laeufe (rollierend). Das Haekchen blendet einen Platz aus, aeltere Laeufe kommen per Schloss hinein.
„＋“ im Kopf der Seitenleiste Läufe oeffnet LaeufeDialog („Läufe hinzufügen“): gespeicherte Laeufe und Mittelwerte (Ø)
aller Fahrzeuge waehlen, sie kommen gesperrt auf freie Plaetze (laeufe_hinzufuegen). Die Breite der Seitenleiste
laesst sich aufgeklappt am rechten Rand und angepinnt am Teiler ziehen (eine Breite, gespeichert in laeufe_leiste).
Daneben der Haken „Ø automatisch mitteln“ (Einstellung AUTO_MITTEL, Standard aus, auch im Menue Ansicht): aus den
Laeufen im Diagramm mit demselben Setup wie der neueste (auto_mittel_quellen, mind. 2, ohne Mittelwerte) rechnet
mittelwert.mitteln einen Mittelwert, gezeichnet wie ein Mittelwert-Lauf (dick, Band min–max) unter AUTO_KEY. Er belegt
keinen der Plaetze, steht oben in der Liste („Ø AUTOMATISCH“, AUTO_ROLLE) und wird bei jedem Neuzeichnen nachgefuehrt
(neuer Lauf, Schloss, Haken, Loeschen, Setup-Wechsel). Gespeichert wird er nur mit „speichern“ (auto_mittel_speichern):
dann ist er ein echter Mittelwert-Lauf wie aus Auswertung › Mittelwert bilden.
T/p werden nur zwischen den Laeufen gelesen (link.climate() unterbricht den Datenstrom): nach START in der Phase
„Klima wird gemessen …“ (runner.KLIMA),
von Hand und alle KLIMA_ALLE_S, solange nichts laeuft.

Einstellung START_NACHFRAGE ("start_nachfrage_setup", Standard True): ist in SETUP-WERTE nichts geaendert, vor
START kurz fragen „Setup unverändert – Änderungen eintragen?“ (StartNachfrage mit den Schnellwert-Plaetzen; Unverändert
starten / Übernehmen und starten / Abbrechen). Geaendertes Setup startet ohne Frage (Werte gelten fuer diesen Lauf),
die Zangentaste „Neuer Lauf“ fragt nie. Schalter: Einstellungen › Rolle/Lauf.

Anzeige beim Messen (Einstellungen › Rolle/Lauf und Menue Ansicht, ANZEIGE_OPTIONEN):
  ERGEBNIS_GROSS   (Standard an)  nach dem Lauf Pmax/Mmax gross ueber dem Diagramm (weg mit START, Klick, Esc)
  ANWEISUNG_GROSS  (Standard an)  Anweisung gross fuer den Fahrer: Klima wird gemessen … (nach START, neutral), GO –
                                  Vollgas! bis die Drehzahl start_anstieg_s lang steigt (Laufbeginn), waehrend der Messung
                                  nichts, am Laufende GAS WEG! bzw. GAS WEG – KUPPLUNG ZIEHEN! (Ausrollversuch),
                                  danach das Ergebnis; aus: nur „GAS WEG“
  ANZEIGE_LEGENDE  (Standard aus) Legende aller Läufe im Diagramm (Farbe, Datum/Zeit, Setup bzw. Notiz)
"""
import html
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import analog, mittelwert, tasten, theme
from .abgleich_dialog import AbgleichDialog, skala_bereich
from .bg import wait_in_thread, waiting
from .channels import fault_text, lambda_from_afr
from .db import (ART_LAUF, ECU_PREFIX, LIVE_KANAELE, LIVE_MAX, LIVE_STANDARD, MESSPLAETZE, SCHNELLWERT_MAX,
                 SCHNELLWERT_STANDARD, SETUP_FIELDS, field_label, messplaetze, wert_text)
from .kacheln import GROSS, KLEIN, MINI, Kanal, SeitenLeiste, WertKachel, farb_icon, schloss_icon, zahl
from .plots import EXTRA_PREFIX, DynoPlot, run_color
from .runner import ACTIVE, COAST, DONE, KLIMA, RUN, WAIT
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
LIVE_EINSPALTIG = 4                        # mehr Live-Plaetze: zweispaltig und kompakt (MINI)
ANWEISUNG_HALTEN_S = 0.6                   # grosse Anweisung (ausser GAS WEG) so lange nach dem letzten Zustand
AUTO_MITTEL = "auto_mittel"                # Einstellung (bool, Standard False): Ø automatisch mitteln
AUTO_KEY = "auto_mittel"                   # Schluessel des automatischen Mittelwerts im Diagramm
AUTO_ROLLE = QtCore.Qt.UserRole + 1        # markiert Kopf und Zeile des automatischen Mittelwerts in der Laufliste
AUTO_TEXT = "Ø automatisch mitteln"
AUTO_TIP = ("Aus den Läufen im Diagramm mit demselben Setup wie der neueste Lauf (mindestens 2, Mittelwerte zählen "
            "nicht) einen Mittelwert bilden und mit Band min–max zeigen. Belegt keinen der Plätze und wird nach jedem "
            "Lauf neu gerechnet; „speichern“ an seiner Zeile macht daraus einen Mittelwert-Lauf.")
# Anzeige beim Messen: Einstellung -> (Standard, Text im Menue Ansicht bzw. unter Einstellungen › Rolle/Lauf, Tooltip)
ERGEBNIS_GROSS = "anzeige_ergebnis_gross"
ANWEISUNG_GROSS = "anzeige_anweisung_gross"
ANZEIGE_LEGENDE = "anzeige_legende"
ANZEIGE_OPTIONEN = {
    ERGEBNIS_GROSS: (True, "Ergebnis groß nach dem Lauf",
                     "Nach dem Lauf Pmax und Mmax groß über dem Diagramm (weg mit START, Klick oder Esc)"),
    ANWEISUNG_GROSS: (True, "Anweisungen groß (GO, GAS WEG …)",
                      "Laufzustand für den Fahrer groß über dem Diagramm: GO – Vollgas!, GAS WEG, KUPPLUNG ZIEHEN … "
                      "(aus: nur „GAS WEG“)"),
    ANZEIGE_LEGENDE: (False, "Legende im Diagramm",
                      "Legende aller Läufe im Diagramm: Farbe, Datum/Zeit, Setup bzw. Notiz"),
}
# Live-Kanal -> Kanal eines gespeicherten Laufs (channels.run_channels), fuer die Werte am Cursor rechts
LAUF_KANAL = {"ign": "Drehzahl Zündung", "v": "Geschwindigkeit", "egt1": "EGT 1", "egt2": "EGT 2",
              "lam": "Lambda", "ratio": "Übersetzung gefiltert", "ratio_ecu": "Übersetzung ECU/Rolle gefiltert",
              "rate": "Messfrequenz"}

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
    if kanal.startswith(analog.PREFIX):
        lo, hi, dez = analog.bereich(kanal)
        return lo, hi, dez, [], False
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


def kachel_groesse(k: WertKachel, groesse):
    """Kachel umschalten: GROSS (Messen, eine Spalte), KLEIN (wenig Hoehe) oder MINI (zweispaltig: kleine Schrift,
    duenner Balken ohne Skala). True/False wie frueher = GROSS/KLEIN."""
    if isinstance(groesse, bool):
        groesse = GROSS if groesse else KLEIN
    k.groesse = groesse
    b = k.balken
    if isinstance(k, DrehzahlKachel):
        b.bar_h = 20 if groesse == GROSS else 14
    else:
        b.bar_h = {GROSS: 14, KLEIN: 10, MINI: 6}.get(groesse, 10)
    b.skala = groesse != MINI
    b.setFixedHeight(b.bar_h + (18 if b.skala else 4))
    mini = groesse == MINI
    k.layout().setContentsMargins(*((6, 2, 6, 3) if mini else (8, 3, 8, 3)))
    k.nr.setFixedSize(*((15, 15) if mini else (18, 18)))
    k._stil()
    k._titel_zeigen()


class KlimaZeile(QtWidgets.QFrame):
    """UMGEBUNG als eine schmale Zeile: „T 22,3 °C   p 1.010 mbar   gelesen vor 3 min“ (Alter gekuerzt)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("klimazeile")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(3)
        self.labels: Dict[str, Tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = {}
        for key, titel, einheit in (("t", "T", "°C"), ("p", "p", "mbar")):
            t, w, e = QtWidgets.QLabel(titel), QtWidgets.QLabel("–"), QtWidgets.QLabel(einheit)
            for x in (t, w, e):
                lay.addWidget(x, 0, QtCore.Qt.AlignBaseline)
            lay.addSpacing(10)
            self.labels[key] = (t, w, e)
        self.alter = KurzText("")
        self.alter.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.alter, 1)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        _rahmen(self, "klimazeile")
        for t, w, e in self.labels.values():
            t.setStyleSheet(f"color: {T['text2']}; font-size: 13px; font-weight: 700;")
            w.setStyleSheet(f"color: {T['value']}; font-size: 17px; font-weight: 700;")
            e.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")
        self.alter.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")

    def set(self, t: str, p: str, alter: str):
        for key, wert in (("t", t), ("p", p)):
            lab = self.labels[key][1]
            if lab.text() != wert:
                lab.setText(wert)
        if self.alter.text() != alter:
            self.alter.setText(alter)

    def text(self) -> str:
        """„T 22,3 °C · p 1.010 mbar · gelesen …“ (fuer Tests)."""
        return " · ".join([" ".join(x.text() for x in self.labels[k]) for k in ("t", "p")] + [self.alter.text()])


class ErgebnisKachel(QtWidgets.QFrame):
    """Pmax bzw. Mmax kompakt in einer Zeile: links „Pmax“ und klein darunter „@ 10.420“, rechts die Zahl in
    der Achsenfarbe."""

    def __init__(self, titel: str, einheit: str, token: str):
        super().__init__()
        self.token = token
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(3)
        links = QtWidgets.QVBoxLayout()
        links.setSpacing(0)
        self.titel = KurzText(titel)
        self.bei = KurzText("")
        links.addWidget(self.titel)
        links.addWidget(self.bei)
        lay.addLayout(links, 1)
        self.wert = QtWidgets.QLabel("–")
        self.einheit = QtWidgets.QLabel(einheit)
        lay.addWidget(self.wert, 0, QtCore.Qt.AlignVCenter)
        lay.addWidget(self.einheit, 0, QtCore.Qt.AlignVCenter)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        _rahmen(self, "ergebniskachel")
        self.titel.setStyleSheet(f"color: {T['text2']}; font-size: 12px; font-weight: 700;")
        self.bei.setStyleSheet(f"color: {T['text2']}; font-size: 11px;")
        self.wert.setStyleSheet(f"color: {T[self.token]}; font-size: 22px; font-weight: 800;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")

    def set(self, titel: str, wert: str):
        """titel „Pmax @ 10.420“: Name oben, „@ 10.420“ klein darunter."""
        name, _s, bei = titel.partition(" @ ")
        self.titel.setText(name)
        self.bei.setText(f"@ {bei}" if bei else "")
        self.setToolTip(titel)
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
class AnweisungGross(QtWidgets.QWidget):
    """Anweisung fuer den Fahrer gross mittig ueber dem Diagramm (aus Entfernung lesbar): „GO – Vollgas!“,
    „GAS WEG!“, „GAS WEG – KUPPLUNG ZIEHEN!“ in der Farbe der Stufe (theme.status_farben), darunter der Grund.
    Ersetzt das fruehere GAS-WEG-Band (Option ANWEISUNG_GROSS aus: nur noch „GAS WEG“ wie bisher).
    Schrift: fest je Breite des Diagramms (REFERENZ passt in eine Zeile), nicht je Text – laengere Texte brechen um.
    zeige() haelt die Anzeige `halten` Sekunden, takt() (regelmaessig aufrufen) blendet danach aus. Die Maus geht
    hindurch."""
    GROSS_MAX, GROSS_MIN = 96, 34               # Schriftgroesse der Anweisung (nur von der Breite abhaengig)
    REFERENZ = "KUPPLUNG ZIEHEN!"                # laengste Zeile der Anweisungen: bestimmt die Schriftgroesse
    RAND = 48                                    # Innenabstand + Rahmen links und rechts zusammen [px]

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.gross = QtWidgets.QLabel("")
        self.gross.setAlignment(QtCore.Qt.AlignCenter)
        self.gross.setWordWrap(True)
        self.unter = QtWidgets.QLabel("")
        self.unter.setAlignment(QtCore.Qt.AlignCenter)
        self.unter.setFixedHeight(36)
        lay.addWidget(self.gross)
        lay.addWidget(self.unter)
        self.stufe = theme.FEHLER
        self._bis = 0.0
        self._px = self.GROSS_MAX
        parent.installEventFilter(self)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self.hide()

    def _stil(self):
        T = theme.tokens()
        bg, fg, _sym = theme.status_farben(self.stufe)
        self.gross.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; font-size: {self._px}px; font-weight: 900;"
                                 f" border: 3px solid {fg}; border-radius: 12px; letter-spacing: 2px;"
                                 " padding: 0 18px; }")
        self.unter.setStyleSheet(f"QLabel {{ background: {T['panel']}; color: {T['text']}; font-size: 18px;"
                                 f" font-weight: 700; border: 1px solid {T['border']}; border-radius: 5px;"
                                 " padding: 0 10px; }")

    def text(self) -> str:
        """Angezeigte Anweisung (Zeilen mit Leerzeichen verbunden, fuer Tests)."""
        return " ".join(self.gross.text().split("\n"))

    def zeige(self, titel: str, unter: str = "", stufe: str = theme.FEHLER, halten: float = GAS_WEG_HALTEN_S):
        """titel: Anweisung (mehrere Zeilen mit \\n), unter: Grund/Zusatz ("" = keiner), stufe: ok | warn | err."""
        neu = (titel, unter, stufe) != (self.gross.text(), self.unter.text(), self.stufe)
        self._bis = time.time() + halten
        if neu:
            self.gross.setText(titel)
            self.unter.setText(unter)
            self.unter.setVisible(bool(unter))
            if stufe != self.stufe:
                self.stufe = stufe
            self._platzieren()
        if not self.isVisible():
            self.show()
        self.raise_()

    def haelt(self, stufe: str) -> bool:
        """True, solange eine Anweisung dieser Stufe noch steht (z.B. GAS WEG seine Haltezeit)."""
        return self.isVisible() and self.stufe == stufe and time.time() <= self._bis

    def rest_s(self) -> float:
        """So lange steht die Anweisung noch (0 = verborgen bzw. abgelaufen)."""
        return max(0.0, self._bis - time.time()) if self.isVisible() else 0.0

    def schrift_px(self) -> int:
        return self._px

    def takt(self):
        if self.isVisible() and time.time() > self._bis:
            self.hide()

    def verbergen(self):
        self._bis = 0.0
        self.hide()

    def _font(self, px: int) -> QtGui.QFont:
        f = QtGui.QFont(self.gross.font())
        f.setWeight(QtGui.QFont.Black)
        f.setPixelSize(px)
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 2)
        return f

    def _platzieren(self):
        p = self.parentWidget()
        w = min(980, max(240, p.width() - 40))
        px = self.GROSS_MAX
        while px > self.GROSS_MIN:                     # groesste Schrift, bei der REFERENZ in die Breite passt
            if QtGui.QFontMetrics(self._font(px)).horizontalAdvance(self.REFERENZ) + self.RAND <= w:
                break
            px -= 4
        self._px = px
        self._stil()
        # Hoehe mit Umbruch (laengere Texte brechen um, die Schrift bleibt)
        fm = QtGui.QFontMetrics(self._font(px))
        br = fm.boundingRect(QtCore.QRect(0, 0, w - self.RAND, 10000),
                             int(QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap), self.gross.text() or " ")
        h_gross = max(br.height(), fm.height()) + 22
        self.gross.setFixedHeight(h_gross)
        h = h_gross + (42 if self.unter.text() else 0)
        self.setGeometry((p.width() - w) // 2, max(8, int(p.height() * 0.16)), w, h)

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QtCore.QEvent.Resize and self.isVisible():
            self._platzieren()
        return False


class ErgebnisGross(QtWidgets.QFrame):
    """Ergebnis des eben gemessenen Laufs gross mittig ueber dem Diagramm: Farbmarke des Laufs, „Lauf 16:29 · Setup“,
    Pmax mit Drehzahl und Mmax mit Drehzahl. Halbtransparenter Hintergrund, Rahmen in der Laufarbe. Klick blendet
    aus (geschlossen), ebenso START und Esc (MessenMixin)."""
    geschlossen = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("ergebnisgross")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Klick oder Esc: ausblenden")
        self.farbe = "#000000"
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 18, 10)
        lay.setSpacing(14)
        self.marke = QtWidgets.QFrame()
        self.marke.setFixedWidth(10)
        lay.addWidget(self.marke)
        rechts = QtWidgets.QVBoxLayout()
        rechts.setSpacing(2)
        self.kopf = QtWidgets.QLabel("")
        rechts.addWidget(self.kopf)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        self.zeilen = {}
        for r, (key, name) in enumerate((("p", "Pmax"), ("m", "Mmax"))):
            lab = QtWidgets.QLabel(name)
            wert = QtWidgets.QLabel("–")
            wert.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBaseline)
            einheit = QtWidgets.QLabel("")
            bei = QtWidgets.QLabel("")
            grid.addWidget(lab, r, 0, QtCore.Qt.AlignBaseline)
            grid.addWidget(wert, r, 1, QtCore.Qt.AlignBaseline)
            grid.addWidget(einheit, r, 2, QtCore.Qt.AlignBaseline)
            grid.addWidget(bei, r, 3, QtCore.Qt.AlignBaseline)
            self.zeilen[key] = (lab, wert, einheit, bei)
        grid.setColumnStretch(3, 1)
        rechts.addLayout(grid)
        self.hinweis = QtWidgets.QLabel("Klick oder Esc: ausblenden")
        self.hinweis.setAlignment(QtCore.Qt.AlignRight)
        rechts.addWidget(self.hinweis)
        lay.addLayout(rechts, 1)
        parent.installEventFilter(self)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self.hide()

    def _stil(self):
        T = theme.tokens()
        c = QtGui.QColor(T["panel"])
        farbe = theme.laufarbe(self.farbe)
        self.setStyleSheet(f"QFrame#ergebnisgross {{ background: rgba({c.red()},{c.green()},{c.blue()},222);"
                           f" border: 3px solid {farbe}; border-radius: 12px; }}"
                           "QLabel { background: transparent; border: none; }")
        self.marke.setStyleSheet(f"QFrame {{ background: {farbe}; border: none; border-radius: 4px; }}")
        self.kopf.setStyleSheet(f"color: {T['text']}; font-size: 17px; font-weight: 700;")
        for key, (lab, wert, einheit, bei) in self.zeilen.items():
            lab.setStyleSheet(f"color: {T['text2']}; font-size: 20px; font-weight: 700;")
            wert.setStyleSheet(f"color: {T['p_color' if key == 'p' else 'm_color']}; font-size: 54px;"
                               " font-weight: 900;")
            einheit.setStyleSheet(f"color: {T['text']}; font-size: 24px; font-weight: 800;")
            bei.setStyleSheet(f"color: {T['text']}; font-size: 20px; font-weight: 600;")
        self.hinweis.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")

    def zeige(self, farbe: str, kopf: str, p_max: float, n_pmax: float, m_max: float, n_mmax: float):
        """farbe: Basisfarbe des Laufs (theme.laufarbe passt sie an), kopf z.B. „Lauf 16:29 · HD 140, Clip 2“."""
        self.farbe = farbe
        self.kopf.setText(kopf)
        kw = p_max * 0.73549875 if p_max is not None and math.isfinite(p_max) else None
        for key, (v, n, einheit) in (("p", (p_max, n_pmax, "PS")), ("m", (m_max, n_mmax, "Nm"))):
            _lab, wert, ein, bei = self.zeilen[key]
            wert.setText(zahl(v, 1))
            ein.setText(einheit)
            zusatz = f"   ({zahl(kw, 1)} kW)" if key == "p" and kw is not None else ""
            bei.setText(f"bei {zahl(n)} 1/min{zusatz}")
        self._stil()
        self._platzieren()
        self.show()
        self.raise_()

    def werte_text(self) -> str:
        """„Pmax 31,6 PS bei 9.850 1/min · Mmax …“ (fuer Tests)."""
        return " · ".join(" ".join(x.text() for x in self.zeilen[k]) for k in ("p", "m"))

    def _platzieren(self):
        p = self.parentWidget()
        self.adjustSize()
        w = min(max(self.sizeHint().width(), 420), max(200, p.width() - 40))
        h = self.sizeHint().height()
        self.setGeometry((p.width() - w) // 2, max(8, int(p.height() * 0.14)), w, h)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.hide()
            self.geschlossen.emit()
        super().mousePressEvent(ev)

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


def auto_mittel_quellen(rows: Sequence, setups: Dict[int, dict]) -> List[int]:
    """Quellen des automatischen Mittelwerts aus den Laeufen im Diagramm (rows, db.RunRow): alle Messungen mit
    demselben Setup wie die neueste – gleiches Fahrzeug, gleiche Setup-ID und gleiche Setup-Werte (setups: Lauf-ID ->
    db.lauf_setup; „nur für diesen Lauf“ geaenderte zaehlen so als anderes Setup). Mittelwerte und Dauermessungen
    zaehlen nicht. Neueste zuerst; [] bei weniger als mittelwert.MIN_LAEUFE."""
    laeufe = sorted((r for r in rows if not r.ist_mittelwert and r.art == ART_LAUF),
                    key=lambda r: (r.datum or "", r.id), reverse=True)
    if not laeufe:
        return []
    neu = laeufe[0]
    ref = setups.get(neu.id) or {}
    ids = [r.id for r in laeufe if r.vehicle_id == neu.vehicle_id and r.setup_id == neu.setup_id
           and not mittelwert.setup_abweichungen([ref, setups.get(r.id) or {}])]
    return ids if len(ids) >= mittelwert.MIN_LAEUFE else []


class LaufListe(QtWidgets.QWidget):
    """Inhalt der Seitenleiste „Läufe“: Plaetze-Hinweis, Filter, Liste, Knoepfe. Der Knopf „＋“ (Läufe hinzufügen …,
    LaeufeDialog) sitzt im Kopf der Seitenleiste neben dem Titel (MessenMixin setzt ihn dort ein)."""
    SPALTEN = ["", "Lauf", "PS", "bei", "Nm", "Fehler"]

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.b_hinzu = QtWidgets.QPushButton("＋")
        theme.rolle(self.b_hinzu, "primaer")
        self.b_hinzu.setToolTip("Läufe hinzufügen … (gespeicherte Läufe oder Mittelwerte gesperrt ins Diagramm)")
        self.b_hinzu.setFixedSize(40, 28)
        self.auto = QtWidgets.QCheckBox(AUTO_TEXT)          # sitzt im Kopf neben „＋“ (MessenMixin)
        self.auto.setToolTip(AUTO_TIP + " Auch im Menü Ansicht.")
        self.info = QtWidgets.QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.run_filter = QtWidgets.QComboBox()
        self.run_filter.addItems(["alle", "aktuelles Fahrzeug", "aktuelles Setup"])
        lay.addWidget(self.run_filter)
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
                                        "Lauf zeigen/ausblenden · Maus weg = zuklappen, Klick in die Grafik = zu · "
                                        "rechter Rand: Breite ziehen")
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
        self.b_del.setToolTip("Markierte Läufe samt Messdateien löschen (anklicken, mehrere mit Strg oder Umschalt)")
        for b in (self.b_none, self.b_view, self.b_del):
            knoepfe.addWidget(b)
        lay.addLayout(knoepfe)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.info.setStyleSheet(f"background: {T['lock_bg']}; color: {T['text']}; font-size: 13px; font-weight: 700;"
                                " border-radius: 3px; padding: 4px 8px;")
        self.b_hinzu.setStyleSheet("QPushButton { font-size: 20px; font-weight: 800; padding: 0px; }")


def datum_kurz(datum: str) -> str:
    """„2026-09-30T12:53:10“ -> „30.09.26 12:53“"""
    d = (datum or "").replace("T", " ")[:16]
    if len(d) == 16 and d[4] == "-":
        return f"{d[8:10]}.{d[5:7]}.{d[2:4]} {d[11:]}"
    return d


class LaeufeDialog(QtWidgets.QDialog):
    """„Läufe hinzufügen“ (Knopf „＋“ der Seitenleiste Läufe): gespeicherte Läufe und Mittelwert-Läufe (Ø, fett) wählen.
    Sie kommen gesperrt (Schloss) auf die Messplätze des Diagramms (MessenMixin.laeufe_hinzufuegen). Filter Fahrzeug
    (Standard: das aktuelle, „alle Fahrzeuge“ wählbar), Art (alle / nur Messungen / nur Mittelwerte), Suche;
    Mehrfachauswahl, Doppelklick nimmt einen Lauf direkt. Läufe mit Schloss stehen grau und nicht wählbar darin;
    wer mehr wählt, als Plätze frei sind, bekommt den Hinweis und kann nicht hinzufügen."""
    SPALTEN = ["Datum/Zeit", "Setup", "Notiz", "Pmax", "Mmax"]

    def __init__(self, db, vid: Optional[int], gesperrt: Sequence[int] = (), rollierend: Sequence[int] = (),
                 parent=None):
        from .db import ART_MITTELWERT
        super().__init__(parent)
        self.setWindowTitle("Läufe hinzufügen")
        self.resize(820, 500)
        self.gesperrt, self.rollierend = set(gesperrt), set(rollierend)
        self.frei = max(0, MESSPLAETZE - len(self.gesperrt))
        self.rows = [r for r in db.runs(mittelwert=True) if r.art in (ART_LAUF, ART_MITTELWERT)]
        self.fahrzeuge = {r["id"]: r["name"] or "" for r in db.vehicles()}
        self.setups = {s["id"]: s["name"] or "" for v in self.fahrzeuge for s in db.setups(v)}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(8)
        hinweis = QtWidgets.QLabel("Gewählte Läufe kommen gesperrt (Schloss) ins Diagramm und bleiben dort, bis das "
                                   "Schloss gelöst wird. Ø = Mittelwert aus mehreren Läufen, gezeichnet mit Band.")
        hinweis.setWordWrap(True)
        theme.rolle(hinweis, "neben")
        lay.addWidget(hinweis)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.fz = QtWidgets.QComboBox()
        self.fz.addItem("alle Fahrzeuge", None)
        for fid, name in self.fahrzeuge.items():
            self.fz.addItem(name, fid)
        self.fz.setCurrentIndex(max(0, self.fz.findData(vid)) if vid else 0)
        self.art = QtWidgets.QComboBox()
        for key, text in (("", "alle"), (ART_LAUF, "nur Messungen"), (ART_MITTELWERT, "nur Mittelwerte")):
            self.art.addItem(text, key)
        self.suche = QtWidgets.QLineEdit()
        self.suche.setPlaceholderText("Suchen: Datum, Setup, Notiz …")
        self.suche.setClearButtonEnabled(True)
        for text, w, st in (("Fahrzeug", self.fz, 2), ("Art", self.art, 1), ("", self.suche, 3)):
            if text:
                row.addWidget(QtWidgets.QLabel(text))
            row.addWidget(w, st)
        lay.addLayout(row)
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(self.SPALTEN)
        t.setRootIsDecorated(False)
        t.setIndentation(0)
        t.setUniformRowHeights(True)
        t.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        t.setIconSize(QtCore.QSize(16, 16))
        t.setAllColumnsShowFocus(True)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.liste = t
        lay.addWidget(t, 1)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        knoepfe = QtWidgets.QHBoxLayout()
        knoepfe.addStretch(1)
        self.b_ok = QtWidgets.QPushButton("Hinzufügen")
        theme.rolle(self.b_ok, "primaer")
        self.b_ok.setDefault(True)
        self.b_ab = QtWidgets.QPushButton("Abbrechen")
        self.b_ab.setAutoDefault(False)
        knoepfe.addWidget(self.b_ab)
        knoepfe.addWidget(self.b_ok)
        lay.addLayout(knoepfe)
        self.b_ok.clicked.connect(self.accept)
        self.b_ab.clicked.connect(self.reject)
        t.itemSelectionChanged.connect(self._pruefen)
        t.itemDoubleClicked.connect(self._doppelklick)
        self.fz.currentIndexChanged.connect(lambda _i: self._fuellen())
        self.art.currentIndexChanged.connect(lambda _i: self._fuellen())
        self.suche.textChanged.connect(lambda _t: self._fuellen())
        self._fuellen()
        for c in (0, 3, 4):
            t.resizeColumnToContents(c)
        self.suche.setFocus()

    def _zeile(self, r) -> List[str]:
        mw = r.ist_mittelwert
        setup = self.setups.get(r.setup_id, "")
        if self.fz.currentData() is None and self.fahrzeuge.get(r.vehicle_id):
            setup = f"{self.fahrzeuge[r.vehicle_id]} · {setup}" if setup else self.fahrzeuge[r.vehicle_id]
        notiz = (r.name + (f" – {r.notiz}" if r.notiz else "")) if mw else r.notiz
        return [datum_kurz(r.datum), setup, notiz or "", f"{zahl(r.p_max, 1)} PS", f"{zahl(r.m_max, 1)} Nm"]

    def _fuellen(self):
        t = self.liste
        sel = set(self.gewaehlt())
        fid, art = self.fz.currentData(), self.art.currentData()
        such = self.suche.text().strip().lower()
        t.blockSignals(True)
        t.clear()
        for r in self.rows:
            if (fid is not None and r.vehicle_id != fid) or (art and r.art != art):
                continue
            texte = self._zeile(r)
            if such and such not in " ".join(texte + [r.name, self.fahrzeuge.get(r.vehicle_id, "")]).lower():
                continue
            mw = r.ist_mittelwert
            it = QtWidgets.QTreeWidgetItem(texte)
            it.setData(0, QtCore.Qt.UserRole, r.id)
            for c in (3, 4):
                it.setTextAlignment(c, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if mw:
                f = it.font(0)
                f.setBold(True)
                for c in range(len(texte)):
                    it.setFont(c, f)
            tip = [("Mittelwert: " if mw else "Lauf: ") + (r.name if mw else texte[0])]
            if self.fahrzeuge.get(r.vehicle_id):
                tip.append(f"Fahrzeug: {self.fahrzeuge[r.vehicle_id]}")
            if r.id in self.gesperrt:
                it.setIcon(0, schloss_icon(True))
                it.setFlags(it.flags() & ~QtCore.Qt.ItemIsSelectable)
                for c in range(len(texte)):
                    it.setForeground(c, QtGui.QBrush(QtGui.QColor(theme.farbe("dim"))))
                tip.append("Liegt schon gesperrt im Diagramm")
            else:
                it.setIcon(0, farb_icon((r.farbe or "@avg_color") if mw else run_color(r), mw))
                if r.id in self.rollierend:
                    tip.append("Steht gerade rollierend im Diagramm – Hinzufügen sperrt den Platz")
            for c in range(len(texte)):
                it.setToolTip(c, "\n".join(tip))
            t.addTopLevelItem(it)
            if r.id in sel and r.id not in self.gesperrt:
                it.setSelected(True)
        t.blockSignals(False)
        t.resizeColumnToContents(1)                  # Setup (mit Fahrzeug bei „alle Fahrzeuge“) moeglichst ganz
        t.setColumnWidth(1, min(max(t.columnWidth(1), 120), 300))
        self._pruefen()

    def gewaehlt(self) -> List[int]:
        """Gewaehlte Lauf-IDs in der Reihenfolge der Liste."""
        t = self.liste
        return [t.topLevelItem(i).data(0, QtCore.Qt.UserRole) for i in range(t.topLevelItemCount())
                if t.topLevelItem(i).isSelected()]

    def _pruefen(self):
        n = len(self.gewaehlt())
        frei = f"{self.frei} von {MESSPLAETZE} Plätzen frei"
        if n > self.frei:
            text = (f"{n} gewählt, aber nur {frei.replace(' frei', '')} frei – weniger wählen oder in der Liste "
                    "„Läufe“ ein Schloss lösen.")
            st = theme.WARNUNG
        elif not self.frei:
            text, st = f"Alle {MESSPLAETZE} Plätze sind gesperrt – zuerst in der Liste „Läufe“ ein Schloss lösen.", \
                theme.WARNUNG
        else:
            text, st = (f"{n} gewählt · " if n else "") + frei + (" · Mehrfachauswahl mit Strg/Umschalt, Doppelklick "
                                                                  "nimmt einen Lauf direkt" if not n else ""), ""
        self.status.setText(text)
        theme.stufe(self.status, st)
        self.b_ok.setText(f"Hinzufügen ({n})" if n else "Hinzufügen")
        self.b_ok.setEnabled(0 < n <= self.frei)

    def _doppelklick(self, it, _col):
        if not it.flags() & QtCore.Qt.ItemIsSelectable or not self.frei:
            return
        self.liste.clearSelection()
        it.setSelected(True)
        self.accept()


class StartNachfrage(QtWidgets.QDialog):
    """Kurzes Fenster vor START, wenn in SETUP-WERTE nichts geaendert ist: „Setup unverändert – Änderungen
    eintragen?“ mit den Schnellwert-Plaetzen (Feldname + aktueller Wert) als Felder. Knoepfe: Unverändert starten
    (Standard, Enter/F1, Fokus) | Übernehmen und starten (erst nach einer Aenderung, dann Enter) | Abbrechen (Esc).
    Nach exec(): Accepted mit aenderungen() {feld: wert} (leer = unveraendert starten), Rejected = nicht starten."""
    SPALTEN = 4                                    # Felder je Zeile

    def __init__(self, felder: Sequence[Tuple[str, str]], setup_name: str = "", parent=None):
        """felder: [(Setup-Feld, aktueller Wert)] der belegten Schnellwert-Plaetze."""
        super().__init__(parent)
        self.setWindowTitle("Setup unverändert")
        self.setModal(True)
        self.alt = {f: w for f, w in felder}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        self.frage = QtWidgets.QLabel("Setup unverändert – Änderungen eintragen?")
        lay.addWidget(self.frage)
        if setup_name:
            name = QtWidgets.QLabel(f"Setup: {setup_name}")
            theme.rolle(name, "neben")
            lay.addWidget(name)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        self.edits: Dict[str, QtWidgets.QLineEdit] = {}
        self.titel: List[QtWidgets.QLabel] = []
        for i, (feld, wert) in enumerate(felder):
            r, c = divmod(i, self.SPALTEN)
            lab = QtWidgets.QLabel(field_label(feld))
            lab.setContentsMargins(0, 8 if r else 0, 0, 0)
            e = QtWidgets.QLineEdit(wert)
            e.setAlignment(QtCore.Qt.AlignCenter)
            e.setMinimumWidth(96)
            e.textEdited.connect(lambda _t: self._pruefen())
            grid.addWidget(lab, 2 * r, c)
            grid.addWidget(e, 2 * r + 1, c)
            self.titel.append(lab)
            self.edits[feld] = e
        lay.addLayout(grid)
        self.hinweis = QtWidgets.QLabel("Geänderte Werte gelten für diesen Lauf (gelb in SETUP-WERTE, wie dort getippt).")
        self.hinweis.setWordWrap(True)
        theme.rolle(self.hinweis, "neben")
        lay.addWidget(self.hinweis)
        knoepfe = QtWidgets.QHBoxLayout()
        self.b_ab = QtWidgets.QPushButton("Abbrechen")
        self.b_ueber = QtWidgets.QPushButton("Übernehmen und starten")
        self.b_start = QtWidgets.QPushButton("Unverändert starten")
        for b in (self.b_ab, self.b_ueber, self.b_start):
            b.setAutoDefault(False)
            b.setMinimumHeight(34)
        knoepfe.addWidget(self.b_ab)
        knoepfe.addStretch(1)
        knoepfe.addWidget(self.b_ueber)
        knoepfe.addWidget(self.b_start)
        lay.addLayout(knoepfe)
        self.b_ab.clicked.connect(self.reject)
        self.b_start.clicked.connect(self._unveraendert)
        self.b_ueber.clicked.connect(self.accept)
        QtGui.QShortcut(QtGui.QKeySequence("F1"), self, activated=self.b_start.click)
        self._uebernehmen = False
        self._pruefen()
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.frage.setStyleSheet(f"color: {T['text']}; font-size: 16px; font-weight: 800;")
        for lab in self.titel:
            lab.setStyleSheet(f"color: {T['text']}; font-size: 13px; font-weight: 700;")
        for feld, e in self.edits.items():
            if wert_text(e.text()) != wert_text(self.alt.get(feld)):
                e.setStyleSheet(f"QLineEdit {{ font-size: 15px; font-weight: 800; background: {T['changed_bg']};"
                                f" color: {T['changed_text']}; border: 1px solid {T['border_strong']}; }}")
            else:
                e.setStyleSheet("QLineEdit { font-size: 15px; font-weight: 800; }")

    def _offen(self) -> Dict[str, str]:
        """Felder, deren Wert gerade anders ist als vorbelegt."""
        return {f: e.text().strip() for f, e in self.edits.items()
                if wert_text(e.text()) != wert_text(self.alt.get(f))}

    def aenderungen(self) -> Dict[str, str]:
        """Nach „Übernehmen und starten“: {feld: neuer Wert}; nach „Unverändert starten“ leer."""
        return self._offen() if self._uebernehmen else {}

    def _pruefen(self):
        """„Übernehmen und starten“ erst nach einer Aenderung – dann ist es der Standard (Enter)."""
        geaendert = bool(self._offen())
        self.b_ueber.setEnabled(geaendert)
        self.b_ueber.setDefault(geaendert)
        self.b_start.setDefault(not geaendert)
        theme.rolle(self.b_ueber, "primaer" if geaendert else "")
        theme.rolle(self.b_start, "" if geaendert else "primaer")
        self._stil()

    def _unveraendert(self):
        self._uebernehmen = False
        super().accept()

    def accept(self):
        """„Übernehmen und starten“ (auch Enter nach einer Aenderung)."""
        self._uebernehmen = bool(self._offen())
        super().accept()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.b_start.setFocus()                    # Enter/F1 gleich hinterher startet unveraendert


# =============================================================================================== Lauf-Einstellungen
LAUF_FELDER = (("ratio", "Übersetzung nKW/nRolle"), ("ma_s", "Gleitender Mittelwert [s]"),
               ("dq_s", "Differenzenquotient ± [s]"), ("ma", "Gleitender Mittelwert [Punkte]"),
               ("dq", "Differenzenquotient [Punkte]"), ("start_anstieg_s", "Start: steigt über [s]"),
               ("start_anstieg_min", "Start: Anstieg mind. [1/min]"), ("n_min", "n vom Gas [1/min]"),
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
           + f"\nStart: Drehzahl steigt {zahl(f['start_anstieg_s'].value(), 1)} s um mind. "
             f"{zahl(f['start_anstieg_min'].value())} 1/min"
           + f"\nn vom Gas {zahl(f['n_min'].value())} 1/min · n Stop "
           + (f"{zahl(n_stop)} 1/min" if n_stop else "aus")
           + "\n\nKlick: Übersetzung, Filter, Start und Laufende ändern")
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
    einmessen …“), Filter und Lauf (Start: Anstieg über s / mind. 1/min, n vom Gas, n Stop). Die Felder spiegeln die
    von Einstellungen › Rolle/Lauf
    (seite = SettingsPage, dieselben Einstellungen): Aenderungen gelten sofort und stehen an beiden Orten."""
    einmessen = QtCore.Signal()
    live_abgleich = QtCore.Signal()
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
        self.b_abgleich = QtWidgets.QPushButton("Live abgleichen …")
        self.b_abgleich.setToolTip("Übersetzung von Hand live abgleichen: PST-Drehzahl (Rolle × Übersetzung) mit dem "
                                   "Drehzahlmesser des Motorrads vergleichen und mit Pfeilen verstellen")
        self.b_abgleich.clicked.connect(self._live_abgleich)
        row.addWidget(self.b_abgleich)
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

        lay.addWidget(abschnitt("LAUF  Start und Ende"))
        grenzen = QtWidgets.QFormLayout()
        grenzen.setLabelAlignment(QtCore.Qt.AlignRight)
        for key in ("start_anstieg_s", "start_anstieg_min", "n_min", "n_stop"):
            grenzen.addRow(texte[key], self._spiegel(key))
        self.lauf_hinweis = QtWidgets.QLabel("Nach GO beginnt der Lauf, sobald die Drehzahl so lange steigt; ab n vom "
                                             "Gas wird auf das Laufende geprüft.")
        self.lauf_hinweis.setWordWrap(True)
        theme.rolle(self.lauf_hinweis, "neben")
        grenzen.addRow(self.lauf_hinweis)
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
        sp.setToolTip(src.toolTip())
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

    def _live_abgleich(self):
        self.hide()
        self.live_abgleich.emit()

    def _alle(self):
        self.hide()
        self.alle_einstellungen.emit()

    def zeigen_an(self, knopf: QtWidgets.QWidget):
        """Unter dem Knopf, rechtsbuendig zu ihm, innerhalb des Bildschirms."""
        self.abgleichen()
        self.adjustSize()
        h = self.layout().heightForWidth(self.width())     # umbrechende Hinweise brauchen ihre Hoehe
        if h > self.height():
            self.resize(self.width(), h)
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
        self._auto: Optional[Dict[str, Any]] = None    # automatischer Mittelwert (_auto_mittel_rechnen)
        self._auto_grund = ""                          # warum keiner da ist (Zeile in der Liste)
        self._auto_gewaehlt = False                    # seine Zeile war vor dem Neuaufbau der Liste gewaehlt
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
        self.abgleich_dlg: Optional[AbgleichDialog] = None
        lay.addLayout(kopf)

        # ---- Setup-Werte
        self.setup_zeile = SetupZeile()
        self.setup_zeile.plaetze_geaendert.connect(self._schnellwert_plaetze)
        self.setup_zeile.geaendert.connect(self._setup_combo_marke)
        self.setup_zeile.neu_speichern.connect(self._setup_neu_dialog)
        lay.addWidget(self.setup_zeile)

        # ---- Mitte: Diagramm (Fähnchen am Cursor, grosse Anzeigen) und schmale Ergebniszeile
        center = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(1)
        self.plot = DynoPlot()
        self.plot.set_limits(self.settings.get("plot_limits", {}))
        self.plot.limits_changed.connect(self._limits_changed)
        cl.addWidget(self.plot, 1)
        self.anweisung = AnweisungGross(self.plot)
        self.gas_weg = self.anweisung              # frueher GasWegHinweis (zeige/verbergen wie bisher)
        self.ergebnis_gross = ErgebnisGross(self.plot)
        self._ergebnis_wartet: Optional[tuple] = None   # Ergebnis nach GAS WEG (_ergebnis_gross_zeigen warten=True)
        self._ergebnis_timer = QtCore.QTimer(self)
        self._ergebnis_timer.setSingleShot(True)
        self._ergebnis_timer.timeout.connect(self._ergebnis_nach_gas_weg)
        self._lauf_st = None                       # Laufzustand beim letzten _laufzustand_zeigen
        self.result_label = ErgebnisText()
        self.result_label.setFixedHeight(18)
        self.result_label.setContentsMargins(6, 0, 6, 0)
        cl.addWidget(self.result_label)
        self._cursor_lauf: Optional[str] = None    # Lauf, dessen Werte die rechte Spalte gerade am Cursor zeigt

        # ---- Laeufe links (Seitenleiste)
        self.laufliste = LaufListe()
        ll = self.laufliste
        self.run_tree, self.run_filter, self.b_hinzu = ll.run_tree, ll.run_filter, ll.b_hinzu
        self.b_none, self.b_view, self.b_del = ll.b_none, ll.b_view, ll.b_del
        self.run_filter.setCurrentIndex(self.settings.get("run_filter", 1))
        self.laeufe_leiste = SeitenLeiste("Läufe", ll, center, breite=LEISTE_BREITE)
        self.laeufe_leiste.kopf.insertWidget(1, self.b_hinzu)     # „＋“ oben im Kopf neben dem Titel
        self.laeufe_leiste.kopf.insertWidget(2, ll.auto)          # daneben „Ø automatisch mitteln“
        ll.auto.setChecked(self.auto_mittel_an())
        ll.auto.toggled.connect(self.auto_mittel_setzen)
        self.laeufe_leiste.set_zustand(self.settings.get("laeufe_leiste"))
        self.laeufe_leiste.angepinnt_geaendert.connect(self._leiste_gemerkt)
        self.laeufe_leiste.breite_geaendert.connect(self._leiste_gemerkt)   # gezogen (eine Breite fuer beide)
        self.run_tree.itemClicked.connect(self._lauf_geklickt)
        self.run_tree.itemSelectionChanged.connect(self._update_max_display)
        self.b_hinzu.clicked.connect(self._laeufe_dialog)

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
        self.setup_combo.currentIndexChanged.connect(lambda _i: self.auto_mittel_an() and self._redraw_runs())
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
        """UMGEBUNG (eine Zeile) und Drehzahl (gross) fest oben, die Live-Plaetze darunter (bis LIVE_EINSPALTIG
        untereinander, mehr zweispaltig und kompakt; rollen nur, wenn sie trotzdem nicht passen), ERGEBNIS
        (eine Zeile Pmax | Mmax) fest unten."""
        spalte = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(spalte)
        lay.setContentsMargins(0, 0, 2, 0)
        lay.setSpacing(5)
        self.klima = KlimaZeile()
        self.klima.setToolTip("UMGEBUNG (PST-Basis, BME280). Gelesen wird nur zwischen den Läufen (beim START, von "
                              "Hand unter Einstellungen › Rolle/Lauf und alle "
                              f"{KLIMA_ALLE_S / 60:.0f} min, solange nichts läuft)")
        lay.addWidget(self.klima)
        self.live_titel = abschnitt("LIVE")
        lay.addWidget(self.live_titel)
        self.k_drehzahl = DrehzahlKachel()
        self.k_drehzahl.kanal_gewaehlt.connect(self._drehzahl_gewaehlt)
        self.k_drehzahl.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.k_drehzahl.customContextMenuRequested.connect(lambda _p: self._grenzen_menue(-1))
        lay.addWidget(self.k_drehzahl)
        # Live-Plaetze: „＋“ in der naechsten freien Zelle fuegt einen Platz an
        inner = QtWidgets.QWidget()
        self.live_lay = QtWidgets.QGridLayout(inner)
        self.live_lay.setContentsMargins(0, 0, 0, 0)
        self.live_lay.setSpacing(5)
        self.live_kacheln: List[WertKachel] = []
        self.live_plus = QtWidgets.QToolButton()
        self.live_plus.setText("＋")
        self.live_plus.setAutoRaise(True)
        self.live_plus.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.live_plus.setToolTip("Platz hinzufügen: Kanal wählen (gilt für das Fahrzeug)")
        self.live_plus.setFixedHeight(26)
        self.live_plus.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        m = QtWidgets.QMenu(self.live_plus)
        m.aboutToShow.connect(lambda m=m: self._live_plus_menue(m))
        self.live_plus.setMenu(m)
        theme.bei_wechsel(self.live_plus, lambda _t: self.live_plus.setStyleSheet(
            f"QToolButton {{ color: {theme.farbe('text2')}; font-size: 15px; font-weight: 700; background: transparent;"
            f" border: 1px dashed {theme.farbe('border_strong')}; border-radius: 4px; }}"
            f"QToolButton:hover {{ color: {theme.farbe('text')}; border-color: {theme.farbe('accent_border')}; }}"
            "QToolButton::menu-indicator { image: none; }"))
        area = _Spalte(self._spalte_anpassen)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setWidget(inner)
        area.setMinimumHeight(40)
        self.live_flaeche = area
        lay.addWidget(area, 1)
        self.ergebnis_titel = abschnitt("ERGEBNIS")
        lay.addWidget(self.ergebnis_titel)
        pm = QtWidgets.QHBoxLayout()
        pm.setSpacing(5)
        self.k_pmax = ErgebnisKachel("Pmax", "PS", "p_color")
        self.k_mmax = ErgebnisKachel("Mmax", "Nm", "m_color")
        pm.addWidget(self.k_pmax)
        pm.addWidget(self.k_mmax)
        lay.addLayout(pm)
        spalte.setMinimumWidth(280)
        self._spalte_gross, self._spalte_h_gross = True, 0
        self._live_kacheln_bauen(len(LIVE_STANDARD))
        return spalte

    def _live_groesse(self) -> str:
        """Groesse der Live-Kacheln: zweispaltig MINI, sonst GROSS bzw. KLEIN (wenig Hoehe)."""
        if len(self.live_kacheln) > LIVE_EINSPALTIG:
            return MINI
        return GROSS if self._spalte_gross else KLEIN

    def _live_kacheln_bauen(self, anzahl: int):
        """So viele Live-Kacheln wie Plaetze (Nummer 1 …), anordnen, „＋“ nur unter LIVE_MAX."""
        while len(self.live_kacheln) < anzahl:
            i = len(self.live_kacheln)
            k = WertKachel(platz=i + 1, groesse=GROSS)
            k.entfernbar = True
            k.kanal_gewaehlt.connect(lambda key, i=i: self._live_gewaehlt(i, key))
            k.entfernen.connect(lambda i=i: self.live_platz_entfernen(i))
            k.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            k.customContextMenuRequested.connect(lambda _p, i=i: self._grenzen_menue(i))
            k.setToolTip("Klick auf den Titel: Kanal wählen oder Platz entfernen · Rechtsklick: Balkengrenzen")
            self.live_kacheln.append(k)
        while len(self.live_kacheln) > anzahl:
            k = self.live_kacheln.pop()
            self.live_lay.removeWidget(k)
            k.hide()
            k.deleteLater()
        self._live_anordnen()
        QtCore.QTimer.singleShot(0, self.live_flaeche, lambda: self._spalte_anpassen(
            self.live_flaeche.viewport().height()))

    def _live_anordnen(self):
        """Kacheln ins Raster: bis LIVE_EINSPALTIG eine Spalte, sonst zwei; „＋“ in die naechste freie Zelle (neben
        einer Kachel so hoch wie sie), darunter Luft."""
        g = self.live_lay
        groesse = self._live_groesse()
        spalten = 2 if groesse == MINI else 1
        for w in self.live_kacheln + [self.live_plus]:
            g.removeWidget(w)
        for r in range(g.rowCount()):
            g.setRowStretch(r, 0)
        for c in range(2):
            g.setColumnStretch(c, 1 if c < spalten else 0)
        for i, k in enumerate(self.live_kacheln):
            if k.groesse != groesse:
                kachel_groesse(k, groesse)
            g.addWidget(k, i // spalten, i % spalten)
            k.show()
        n = len(self.live_kacheln)
        self.live_plus.setVisible(n < LIVE_MAX)
        neben = spalten == 2 and n % 2 == 1
        self.live_plus.setFixedHeight(self.live_kacheln[-1].sizeHint().height() if neben else 26)
        g.addWidget(self.live_plus, n // spalten, n % spalten if neben else 0, 1, 1 if neben else spalten)
        g.setRowStretch(n // spalten + 1, 1)

    def _spalte_anpassen(self, h: int):
        """Einspaltig: Kacheln kompakt, solange sie sonst rollen muessten (kleine Fenster), sonst gross.
        Zweispaltig sind sie immer kompakt (MINI). Die Drehzahl oben bleibt gross."""
        area = getattr(self, "live_flaeche", None)
        if area is None or area.widget() is None or len(self.live_kacheln) > LIVE_EINSPALTIG:
            return
        if self._spalte_gross:
            self._spalte_h_gross = area.widget().minimumSizeHint().height()
        gross = h >= self._spalte_h_gross
        if gross == self._spalte_gross:
            return
        self._spalte_gross = gross
        self._live_anordnen()

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
            p.live_abgleich.connect(self.on_abgleich_live)
            p.alle_einstellungen.connect(lambda: self.show_page(self.page_settings))
            p.geschlossen.connect(self._persist)
        self.lauf_popup.zeigen_an(self.lauf_btn)

    def on_abgleich_live(self):
        """Fenster „Übersetzung live abgleichen“ (nicht modal; schon offen: nach vorn)."""
        if self._lauf_aktiv():
            return
        d = self.abgleich_dlg
        if d is not None and d.isVisible():
            d.raise_()
            d.activateWindow()
            return
        vid, _name = self._fahrzeug()
        eigen = self.db.vehicle_pref(vid, "drehzahl", None)
        dz = self._drehzahl_einstellung()
        hi, zonen = skala_bereich(dz["max"] if isinstance(eigen, dict) and "max" in eigen else None,
                                  [tuple(z) for z in dz["zonen"]])
        d = self.abgleich_dlg = AbgleichDialog(self, self.page_settings, lambda: self.ctrl, hi, zonen,
                                               zange=self._abgleich_zange_text)
        self.page_tasten.set_override(tasten.ABGLEICH_SETUP)    # alle Tasten der Zange Marker, solange offen

        def fertig(_r, d=d):
            if self.abgleich_dlg is d:
                self.abgleich_dlg = None
                self.page_tasten.set_override(None)                 # eigenes Tasten-Setup wieder an die Zange
            d.deleteLater()

        d.uebernommen.connect(lambda v: (self._persist(), self._set_status(f"Übersetzung = {zahl(v, 3)}", theme.GUT)))
        d.finished.connect(fertig)
        d.show()
        d.raise_()

    def _zange_blitz(self) -> bool:
        zst = getattr(self.page_hw, "zst", None)
        return bool(zst is not None and zst.blitz)

    def _abgleich_zange_text(self) -> str:
        if not self.page_tasten.sync.da:
            return "Zange nicht verbunden"
        if self._zange_blitz():
            return "Zange im Blitzmodus – Tasten wirken dort nicht"
        return tasten.ABGLEICH_TEXT

    def _abgleich_marker(self, ev) -> bool:
        """MARK-Ereignis einer Zangen-Taste bei offenem Abgleich-Fenster: verstellt die Übersetzung statt eines Markers
        (True = verbraucht). Im Blitzmodus ist T1 der feste Marker der Firmware und bleibt einer."""
        d = self.abgleich_dlg
        a = ev.args
        if d is None or ev.kind != "MARK" or not a or a[0].lower() != "zange" or self._zange_blitz():
            return False
        kennung = tasten.abgleich_kennung(a[1] if len(a) > 1 else "")
        if kennung is None:
            return False
        self.page_tasten.led(tasten.LED_OK if d.zange_taste(kennung) else tasten.LED_FEHLER)
        return True

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
        """Zustand des Laufs im Feld neben START und gross ueber dem Diagramm (Option ANWEISUNG_GROSS; aus: nur
        „GAS WEG“). Gross: nach START „Klima wird gemessen …“ (neutral, nur mit Klima vom Sensor), dann „GO – Vollgas!“
        bis die Messung beginnt (Drehzahl steigt start_anstieg_s lang), waehrend der Messung nichts (nur das
        Feld neben START zeigt „Messung · 9.500 1/min“), am Laufende mit Ausrollversuch „GAS WEG – KUPPLUNG ZIEHEN!“ bis das
        Ausrollen fertig ist, ohne „GAS WEG!“ (mind. GAS_WEG_HALTEN_S, siehe _laufende_gas_weg), danach das Ergebnis."""
        gross = self.anzeige_an(ANWEISUNG_GROSS)
        a = self.anweisung
        vorher, self._lauf_st = self._lauf_st, st
        if st == KLIMA:
            self._set_status("Klima wird gemessen …", theme.AUS)
            if gross:
                a.zeige("Klima wird\ngemessen …", "Drehzahl halten – gleich kommt GO", theme.AUS,
                        ANWEISUNG_HALTEN_S)
        elif st == WAIT:
            if vorher == KLIMA and a.isVisible() and a.stufe == theme.AUS:
                a.verbergen()                              # Klima fertig: gleich GO
            self._set_status("GO – Vollgas!", theme.GUT)
            if gross:
                fenster = getattr(self.ctrl.params, "start_anstieg_s", 1.0)
                a.zeige("GO – Vollgas!", f"Messung beginnt, sobald die Drehzahl {zahl(fenster, 1)} s lang steigt",
                        theme.GUT, ANWEISUNG_HALTEN_S)
        elif st == RUN:
            if a.isVisible() and a.stufe in (theme.GUT, theme.AUS):
                a.verbergen()                              # Laufbeginn: GO weg, waehrend der Messung nichts
            if self.ctrl.gas_off():
                aus = self.ctrl.ausrollen
                self._set_status("GAS WEG – KUPPLUNG ZIEHEN!" if aus else "GAS WEG!", theme.FEHLER)
                grund = f"n Stop {zahl(self.ctrl.params.n_stop)} 1/min erreicht – " + (
                    "Kupplung ziehen, ausrollen lassen" if aus else "Lauf wird ausgewertet")
                titel = ("GAS WEG –\nKUPPLUNG ZIEHEN!" if aus else "GAS WEG!") if gross else "GAS WEG"
                a.zeige(titel, grund, theme.FEHLER)
            elif not a.haelt(theme.FEHLER):                # GAS WEG steht seine Zeit, auch im Feld
                # „ · “: bei wenig Platz faellt die Drehzahl ganz weg („Messung …“, statusleiste.kurz_text)
                self._set_status(f"Messung · {zahl(lv.n_calc)} 1/min", theme.WARNUNG)
        elif st == COAST:
            self._set_status(f"KUPPLUNG ZIEHEN · ausrollen lassen · {zahl(lv.n_calc)} 1/min", "info")
            if gross:
                a.zeige("GAS WEG –\nKUPPLUNG ZIEHEN!", f"ausrollen lassen … {zahl(lv.n_calc)} 1/min", theme.FEHLER,
                        ANWEISUNG_HALTEN_S)
            elif vorher in (WAIT, RUN) and not a.haelt(theme.FEHLER):
                a.zeige("GAS WEG", "Laufende – Kupplung ziehen, ausrollen lassen", theme.FEHLER)
        a.takt()

    def _laufende_gas_weg(self):
        """Laufende ohne Ausrollversuch (gui._finish_run): „GAS WEG!“ gross (Option aus: „GAS WEG“), falls es nicht
        schon seit n Stop steht; es haelt GAS_WEG_HALTEN_S, danach kommt das Ergebnis (_ergebnis_gross_zeigen mit
        warten=True)."""
        if not self.anweisung.haelt(theme.FEHLER):
            titel = "GAS WEG!" if self.anzeige_an(ANWEISUNG_GROSS) else "GAS WEG"
            self.anweisung.zeige(titel, "Laufende – Lauf wird ausgewertet", theme.FEHLER)

    # ------------------------------------------------------------------ Anzeige beim Messen (Optionen, gross)
    def anzeige_an(self, key: str) -> bool:
        return bool(self.settings.get(key, ANZEIGE_OPTIONEN[key][0]))

    def anzeige_setzen(self, key: str, an: bool):
        """Option der Anzeige beim Messen schalten (Einstellungen › Rolle/Lauf und Menue Ansicht), speichern."""
        an = bool(an)
        if self.settings.get(key) != an:
            self.settings[key] = an
            _speichern(self.settings)
        for w in (getattr(self.page_settings, "anzeige_boxen", {}).get(key),
                  getattr(self, "anzeige_aktionen", {}).get(key)):
            if w is not None and w.isChecked() != an:
                w.blockSignals(True)
                w.setChecked(an)
                w.blockSignals(False)
        if key == ERGEBNIS_GROSS and not an:
            self.ergebnis_gross.hide()
        elif key == ANWEISUNG_GROSS and not an and self.anweisung.stufe != theme.FEHLER:
            self.anweisung.verbergen()
        elif key == ANZEIGE_LEGENDE:
            self._legende_zeigen()

    def _anzeige_verbinden(self, menue: QtWidgets.QMenu):
        """Schalter der Anzeige beim Messen: Kaestchen unter Einstellungen › Rolle/Lauf (page_settings.anzeige_boxen)
        und abhakbare Eintraege im Menue Ansicht."""
        self.anzeige_aktionen: Dict[str, QtGui.QAction] = {}
        menue.addSection("Anzeige beim Messen")
        for key, (_std, text, tip) in ANZEIGE_OPTIONEN.items():
            act = menue.addAction(text)
            act.setCheckable(True)
            act.setChecked(self.anzeige_an(key))
            act.setToolTip(tip)
            act.toggled.connect(lambda an, k=key: self.anzeige_setzen(k, an))
            self.anzeige_aktionen[key] = act
            box = getattr(self.page_settings, "anzeige_boxen", {}).get(key)
            if box is not None:
                box.setChecked(self.anzeige_an(key))
                box.toggled.connect(lambda an, k=key: self.anzeige_setzen(k, an))
        self.auto_aktion = menue.addAction(AUTO_TEXT)          # wie der Haken in der Seitenleiste Läufe
        self.auto_aktion.setCheckable(True)
        self.auto_aktion.setChecked(self.auto_mittel_an())
        self.auto_aktion.setToolTip(AUTO_TIP)
        self.auto_aktion.toggled.connect(self.auto_mittel_setzen)
        menue.setToolTipsVisible(True)

    def _gross_verbergen(self):
        """START: grosse Anzeigen weg (Ergebnis des letzten Laufs, Reste der Anweisung, wartendes Ergebnis)."""
        self._ergebnis_timer.stop()
        self._ergebnis_wartet = None
        self.ergebnis_gross.hide()
        self.anweisung.verbergen()

    def _esc(self):
        """Esc: zuerst das grosse Ergebnis ausblenden, sonst wie bisher Abbrechen bzw. Ausrollen beenden."""
        if self.ergebnis_gross.isVisible() and not self._lauf_aktiv():
            self.ergebnis_gross.hide()
            return
        self.on_abort()

    def _ergebnis_gross_zeigen(self, r, rid: Optional[int] = None, warten: bool = False):
        """Nach dem Lauf (gui._finish_run): Pmax/Mmax gross, Farbe und Zeit des gespeicherten Laufs (rid) bzw. der
        ungespeicherten Kurve. warten (Laufende ohne Ausrollversuch): erst wenn „GAS WEG“ seine Zeit gestanden hat
        (_ergebnis_timer), sonst sofort (mit Ausrollversuch: das Ausrollen ist fertig)."""
        self._ergebnis_timer.stop()
        self._ergebnis_wartet = None
        rest = self.anweisung.rest_s() if warten and self.anweisung.haelt(theme.FEHLER) else 0.0
        if rest > 0:
            self._ergebnis_wartet = (r, rid)
            self._ergebnis_timer.start(max(1, int(rest * 1000)))
            return
        self.anweisung.verbergen()
        if r is None or not self.anzeige_an(ERGEBNIS_GROSS):
            return
        row = self.db.run(rid) if rid is not None else None
        farbe = self._run_color(row) if row is not None else theme.farbe("live_curve")
        zeit = (self._run_label(row).split("  ")[0][-5:] if row is not None
                else time.strftime("%H:%M") + " (nicht gespeichert)")
        sid = self.setup_combo.currentData()
        setup = ((self.db.setup(sid) or {}).get("name", "") if sid else "") or self.run_note.text().strip()
        kopf = f"Lauf {zeit}" + (f"  ·  {setup}" if setup else "")
        self.ergebnis_gross.zeige(farbe, kopf, r.p_max, r.n_pmax, r.m_max, r.n_mmax)

    def _ergebnis_nach_gas_weg(self):
        wartet, self._ergebnis_wartet = self._ergebnis_wartet, None
        if wartet is not None and not self._lauf_aktiv():
            self._ergebnis_gross_zeigen(*wartet)

    def _legende_zeigen(self):
        """Legende im Diagramm: Referenzen (Mittelwert mit Band) immer, mit ANZEIGE_LEGENDE alle Läufe
        (Farbe, Datum/Zeit, Setup bzw. Notiz kurz)."""
        zeilen = []
        alle = self.anzeige_an(ANZEIGE_LEGENDE)
        setups: Dict[int, str] = {}
        for rid in self._gezeichnet:
            row = self._laeufe.get(rid)
            if row is None:
                continue
            c = theme.laufarbe(self._lauf_farbe(row))
            if row.ist_mittelwert:
                band = BAND_TEXT[band_art(row)]
                zeilen.append(f"<span style='color:{c}; font-weight:800'>━━ {row.name}</span>"
                              "&nbsp; gesperrt, Referenz" + (f" · {band}" if band else ""))
                continue
            if not alle:
                continue
            if row.setup_id and row.setup_id not in setups:
                setups[row.setup_id] = (self.db.setup(row.setup_id) or {}).get("name", "")
            text = setups.get(row.setup_id, "") if row.setup_id else ""
            notiz = (row.notiz or "").strip()
            if notiz:
                text = f"{text} · {notiz}" if text else notiz
            if len(text) > 42:
                text = text[:41] + "…"
            zeilen.append(f"<span style='color:{c}; font-weight:900'>━━</span>&nbsp;<b>{self._run_label(row).split('  ')[0]}</b>"
                          + (f"&nbsp; {html.escape(text)}" if text else ""))
        if self._auto is not None and AUTO_KEY in self.plot.data:
            c = theme.laufarbe("@avg_color")
            zeilen.insert(0, f"<span style='color:{c}; font-weight:800'>━━ {html.escape(self._auto['titel'])}</span>")
        if alle and LIVE_KEY in self.plot.data:
            c = theme.laufarbe(theme.farbe("live_curve"))
            zeilen.append(f"<span style='color:{c}; font-weight:900'>━━</span>&nbsp;<b>aktuelle Messung</b>"
                          "&nbsp; nicht gespeichert")
        self.plot.set_legende("<br>".join(zeilen))

    # ------------------------------------------------------------------ Fahrzeug: Ansicht laden
    def _fahrzeug(self) -> Tuple[Optional[int], str]:
        vid = self.vehicle_combo.currentData()
        return vid, (self.vehicle_combo.currentText() if vid else "")

    def _fahrzeug_ansicht_laden(self):
        """Belegung der Live-Plaetze, Drehzahl, Schnellwerte fuer das gewaehlte Fahrzeug."""
        self._fahrzeug_titel()
        self._live_plaetze_anwenden()

    def _fahrzeug_titel(self):
        """Titel ueber den Live-Kacheln (am Cursor zeigt er statt dessen den Lauf, _cursor_moved)."""
        if getattr(self, "_cursor_lauf", None) is not None:
            return
        _vid, name = self._fahrzeug()
        self.live_titel.setText(f"LIVE  ·  Belegung für {name}" if name else "LIVE  ·  Belegung (ohne Fahrzeug)")
        self.live_titel.setToolTip("")

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

    def _start_frage(self, felder: List[Tuple[str, str]]) -> Optional[Dict[str, str]]:
        """Fenster StartNachfrage mittig ueber dem Reiter Messen. -> {feld: neuer Wert} (leer = unveraendert
        starten) bzw. None (Abbrechen)."""
        dlg = StartNachfrage(felder, self.setup_zeile.name, self)
        dlg.adjustSize()
        seite = self.tabs.widget(0) if hasattr(self, "tabs") else self
        mitte = seite.mapToGlobal(seite.rect().center())
        dlg.move(mitte.x() - dlg.width() // 2, mitte.y() - dlg.height() // 2)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.aenderungen()

    def _setup_vor_start(self) -> bool:
        """Vor START (Knopf/F1, nicht die Zangentaste): Ist in SETUP-WERTE nichts geaendert, kurz nachfragen, ob sich
        das Setup geaendert hat (StartNachfrage mit den belegten Schnellwert-Plaetzen). Eingetragene Werte landen in
        der Zeile wie getippt und gelten fuer diesen Lauf. Schon geaendert: ohne Frage starten, die Werte gelten fuer
        diesen Lauf. Keine Frage ohne Fahrzeug, ohne belegte Plaetze oder mit Schalter aus. False = nicht starten."""
        z = self.setup_zeile
        if z.ist_geaendert():
            z.set_nur_lauf(True)
            return True
        vid, _name = self._fahrzeug()
        felder = [f for f in z.felder if f]
        if not vid or not felder or not self.settings.get(START_NACHFRAGE, True):
            return True
        aend = self._start_frage([(f, z.wert(f)) for f in felder])
        if aend is None:
            return False
        for feld, wert in aend.items():
            z.set_wert(feld, wert)
        if z.ist_geaendert():
            z.set_nur_lauf(True)
        return True

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
        for _g, k in analog.aktive():                      # Einstellungen › Analogeingänge
            key = analog.live_key(k.eingang)
            lo, hi, dez = analog.bereich(key)
            out.append(Kanal(key, k.titel(), k.einheit_text(), analog.GRUPPE, dez, lo, hi))
        return out

    def _live_plaetze_anwenden(self):
        """Kanaele, Balken und Zonen der Live-Plaetze (Anzahl je Fahrzeug) und der Drehzahl aus der Datenbank."""
        if not hasattr(self, "live_kacheln"):
            return
        vid, name = self._fahrzeug()
        kan = self._live_kanaele()
        hinweis = f"Auswahl gilt für {name}" if name else "Auswahl gilt ohne Fahrzeug"
        self._kachel_cache.clear()
        plaetze = self.db.live_plaetze(vid)
        self._live_kacheln_bauen(len(plaetze))
        for k, p in zip(self.live_kacheln, plaetze):
            kk = list(kan)
            if p["kanal"] and not any(x.schluessel == p["kanal"] for x in kk):
                ecu = p["kanal"].startswith(ECU_PREFIX)
                ain = analog.kanal(p["kanal"])             # Analogkanal, der gerade nicht aktiv ist
                titel, einheit = (ecu_titel(p["kanal"][len(ECU_PREFIX):]) if ecu
                                  else (ain[1].titel(), ain[1].einheit_text()) if ain
                                  else LIVE_KANAELE.get(p["kanal"], (p["kanal"], "")))
                dez = kanal_bereich(p["kanal"])[2]
                kk.append(Kanal(p["kanal"], titel, einheit, "ECU" if ecu else analog.GRUPPE if ain else "PST", dez,
                                p["min"], p["max"]))
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

    @staticmethod
    def _live_platz_neu(key: str) -> Dict[str, Any]:
        lo, hi, _dez, zonen, _marke = kanal_bereich(key) if key else (0.0, 100.0, 0, [], False)
        return {"kanal": key, "min": lo, "max": hi, "zonen": [list(z) for z in zonen]}

    def _live_gewaehlt(self, i: int, key: str):
        vid, _name = self._fahrzeug()
        plaetze = self.db.live_plaetze(vid)
        if not 0 <= i < len(plaetze):
            return
        plaetze[i] = self._live_platz_neu(key)
        self.db.set_live_plaetze(vid, plaetze)
        self._live_plaetze_anwenden()

    def live_platz_hinzufuegen(self, key: str = "") -> bool:
        """Neuen Live-Platz am Ende (False, wenn schon LIVE_MAX Plaetze da sind)."""
        vid, _name = self._fahrzeug()
        plaetze = self.db.live_plaetze(vid)
        if len(plaetze) >= LIVE_MAX:
            return False
        self.db.set_live_plaetze(vid, plaetze + [self._live_platz_neu(key)])
        self._live_plaetze_anwenden()
        QtCore.QTimer.singleShot(50, self.live_flaeche, lambda: self.live_flaeche.ensureWidgetVisible(
            self.live_plus))
        return True

    def live_platz_entfernen(self, i: int) -> bool:
        vid, _name = self._fahrzeug()
        plaetze = self.db.live_plaetze(vid)
        if not 0 <= i < len(plaetze):
            return False
        self.db.set_live_plaetze(vid, plaetze[:i] + plaetze[i + 1:])
        self._live_plaetze_anwenden()
        return True

    def _live_plus_menue(self, m: QtWidgets.QMenu):
        """Menue des Knopfs „＋“: Kanal fuer den neuen Platz (gruppiert nach Quelle wie im Titel einer Kachel)."""
        m.clear()
        m.addSection("Platz hinzufügen")
        gruppen: Dict[str, List[Kanal]] = {}
        for k in self._live_kanaele():
            gruppen.setdefault(k.quelle or "PST", []).append(k)
        for q in [q for q in ("PST", "ECU") if q in gruppen] + [q for q in gruppen if q not in ("PST", "ECU")]:
            m.addSection(q)
            for k in gruppen[q]:
                a = m.addAction(k.titel + (f" [{k.einheit}]" if k.einheit else ""))
                a.triggered.connect(lambda _c=False, s=k.schluessel: self.live_platz_hinzufuegen(s))
        m.addSeparator()
        m.addAction("leer").triggered.connect(lambda _c=False: self.live_platz_hinzufuegen(""))
        _vid, name = self._fahrzeug()
        h = m.addAction(f"Auswahl gilt für {name}" if name else "Auswahl gilt ohne Fahrzeug")
        h.setEnabled(False)

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
        self._live.update(self._analog_live(f))

    def _analog_live(self, f) -> Dict[str, Optional[float]]:
        """Umgerechnete Analogwerte des Telegramms f (Frame.ain) fuer die aktiven Kanaele des verbundenen Geraets."""
        werte = getattr(f, "ain", None) or {}
        g = analog.geraet_von_link(self.link)
        return {analog.live_key(k.eingang): k.wert(werte.get(k.eingang)) for _g, k in analog.aktive(g)} if g else {}

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
        if self._cursor_lauf is not None:          # Maus im Diagramm: Werte des Laufs am Cursor
            if not self._lauf_aktiv():
                if alle:
                    self._cursor_moved(self._cursor_n)
                return
            self._cursor_lauf = None               # Lauf gestartet: wieder live
            self._fahrzeug_titel()
            alle = True
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
            quelle = "" if i < 0 else (analog.GRUPPE if kanal.startswith(analog.PREFIX) else "PST")
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
        if not hasattr(self, "klima"):
            return
        s = self.page_settings
        if self._klima_t:
            alter = "gelesen " + alter_text(time.time() - self._klima_t)
        elif not s.climate_auto.isChecked():
            alter = "von Hand eingetragen"
        else:
            alter = "noch nicht gelesen"
        self.klima.set(zahl(s.fields["temp_c"].value(), 1), zahl(s.fields["p_mbar"].value(), 0), alter)
        if self._klima_feuchte > 0:
            self.klima.labels["t"][1].setToolTip(f"rel. Feuchte {self._klima_feuchte:.0f} %")

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
    def _analog_geaendert(self):
        """Einstellungen › Analogeingänge geaendert: Laeufe neu rechnen (Kanaele der Auswertung), Kacheln und Diagramm."""
        for rid in list(self.cache.items):
            self.cache.pop(rid)
        self._ecu_changed()

    def _analog_extra_run(self, run, res) -> Dict[str, np.ndarray]:
        """Aktive Analogkanaele eines gespeicherten Laufs fuer das Diagramm (Schluessel 'ain:<Eingang>')."""
        return {analog.live_key(lk.kanal.eingang): lk.werte[res.segment] for lk in analog.lauf_kanaele(run)
                if len(lk.werte) == len(run["dt"])}

    def _analog_extra_live(self, r) -> Dict[str, np.ndarray]:
        """Aktive Analogkanaele der laufenden Messung (Frame.ain) fuer das Diagramm."""
        g = analog.geraet_von_link(self.link)
        akt = analog.aktive(g) if g else []
        frames = self.ctrl.frames[r.segment] if akt else []
        if not frames or not any(getattr(f, "ain", None) for f in frames):
            return {}
        out = {}
        for _g, k in akt:
            u = np.array([(f.ain or {}).get(k.eingang, np.nan) if f.ain else np.nan for f in frames], float)
            out[analog.live_key(k.eingang)] = k.werte(u)
        return out

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
        self._auto_gewaehlt = self._auto_zeile_gewaehlt()
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
        self.laeufe_leiste.set_streifen_farben((["@avg_color"] if getattr(self, "_auto", None) else [])
                                               + [self._lauf_farbe(self._laeufe[i]) for i in fest + roll
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

    def laeufe_hinzufuegen(self, ids: Sequence[int], melden: bool = True) -> int:
        """Gespeicherte Laeufe/Mittelwerte gesperrt (Schloss) auf die Messplaetze legen. Schon gesperrte bleiben, wie
        sie sind (nicht doppelt); rollierende werden gesperrt. Reichen die freien Plaetze nicht, kommt nichts hinzu
        und eine Meldung erscheint (melden=False: nur Statuszeile). -> Anzahl neu gesperrter Laeufe (-1 = kein Platz)."""
        vid, _name = self._fahrzeug()
        g = self.db.gesperrte_plaetze(vid)
        neu = [int(i) for i in dict.fromkeys(ids) if int(i) not in g and self.db.run(int(i)) is not None]
        if not neu:
            return 0
        frei = MESSPLAETZE - len(g)
        if len(neu) > frei:
            text = (f"{len(neu)} Läufe gewählt, aber nur {frei} von {MESSPLAETZE} Plätzen frei.\n\n"
                    "Bitte weniger wählen oder in der Liste „Läufe“ per Schloss einen gesperrten Platz lösen.") \
                if frei else (f"Alle {MESSPLAETZE} Plätze im Diagramm sind gesperrt.\n\n"
                              "Bitte zuerst in der Liste „Läufe“ per Schloss einen Platz lösen.")
            if melden:
                QtWidgets.QMessageBox.information(self, "Läufe hinzufügen", text)
            else:
                self.statusBar().showMessage(text.replace("\n\n", " "), 8000)
            return -1
        for rid in neu:
            row = self.db.run(rid)
            if row is not None and not row.sichtbar:
                self.db.update_run(rid, sichtbar=True)
            self._mw_cache.pop(rid, None)
        self.db.set_gesperrte_plaetze(vid, g + neu)
        self._reload_runs()
        self.statusBar().showMessage(f"{len(neu)} {'Lauf' if len(neu) == 1 else 'Läufe'} gesperrt ins Diagramm "
                                     "gelegt.", 6000)
        return len(neu)

    def _laeufe_dialog(self):
        """„＋“ der Seitenleiste Läufe: LaeufeDialog, die gewaehlten Laeufe kommen gesperrt ins Diagramm."""
        vid, _name = self._fahrzeug()
        _fest, roll = self._plaetze
        g = self.db.gesperrte_plaetze(vid)
        if len(g) >= MESSPLAETZE:
            QtWidgets.QMessageBox.information(self, "Läufe hinzufügen",
                                              f"Alle {MESSPLAETZE} Plätze im Diagramm sind gesperrt.\n\nBitte zuerst "
                                              "in der Liste „Läufe“ per Schloss einen Platz lösen.")
            return
        dlg = LaeufeDialog(self.db, vid, g, roll, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.gewaehlt():
            self.laeufe_hinzufuegen(dlg.gewaehlt())

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
            if key not in (LIVE_KEY, AUTO_KEY) and key not in keys:
                self.plot.remove(key)
        gezeichnet = []
        for rid in ids:
            row = laeufe[rid]
            if row.ist_mittelwert:
                if self._mittelwert_zeichnen(row):
                    gezeichnet.append(rid)
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
        self._auto_mittel_zeichnen()
        self._legende_zeigen()
        if not self.plot.frozen and self.plot.data:
            self.plot.auto_scale()
        self._cursor_names = None
        self._cursor_moved(self._cursor_n)
        self._update_max_display()

    def _mittelwert_zeichnen(self, row) -> bool:
        return self._mw_kurven(str(row.id), self._mittelwert(row), self._lauf_farbe(row), band_art(row))

    def _mw_kurven(self, key: str, erg, color: str, art: str) -> bool:
        """Mittelwert (mittelwert.MittelwertErgebnis) dick ins Diagramm, Band nach art ("minmax"|"sigma"|"kein")."""
        if erg is None or "ps" not in erg.werte or "nm" not in erg.werte or not len(erg.n):
            return False
        w = erg.werte
        extra = {k[len(ECU_PREFIX):]: v for k, v in w.items() if k.startswith(ECU_PREFIX)}
        self.plot.set_curves(key, erg.n, w["ps"], w["nm"], w.get("lam"), w.get("egt1"), w.get("egt2"), color=color,
                             width=MW_BREITE, rescale=False, extra=extra)
        if art == "kein":
            self.plot.remove_band(key)
        else:
            baender = {k: erg.band(k, art) for k in ("ps", "nm")}
            self.plot.set_band(key, erg.n, {k: b for k, b in baender.items() if b is not None}, color)
        return True

    # ------------------------------------------------------------------ Ø automatisch mitteln
    def auto_mittel_an(self) -> bool:
        return bool(self.settings.get(AUTO_MITTEL, False))

    def auto_mittel_setzen(self, an: bool):
        """Haken „Ø automatisch mitteln“ (Seitenleiste Läufe und Menue Ansicht) schalten, speichern, neu zeichnen."""
        an = bool(an)
        if self.settings.get(AUTO_MITTEL) != an:
            self.settings[AUTO_MITTEL] = an
            _speichern(self.settings)
        for w in (self.laufliste.auto, getattr(self, "auto_aktion", None)):
            if w is not None and w.isChecked() != an:
                w.blockSignals(True)
                w.setChecked(an)
                w.blockSignals(False)
        self._redraw_runs()

    def _auto_mittel_rechnen(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """Automatischer Mittelwert aus den Laeufen im Diagramm (auto_mittel_quellen) -> (Angaben, "") bzw.
        (None, Grund). Angaben: erg (MittelwertErgebnis), ids (Quellen, neueste zuerst), vid, sid, name (Vorschlag
        beim Speichern), titel (mit „(automatisch)“). Gleiche Quellen und Auswertungen: das letzte Ergebnis."""
        if not self.auto_mittel_an():
            return None, ""
        rows = [self._laeufe[i] for i in self._gezeichnet if i in self._laeufe]
        setups = {r.id: self.db.lauf_setup(r) for r in rows if not r.ist_mittelwert}
        ids = auto_mittel_quellen(rows, setups)
        if not ids:
            return None, (f"Ø automatisch: noch keine {mittelwert.MIN_LAEUFE} Läufe mit gleichem Setup im Diagramm")
        eintraege = [self._entry(i) for i in ids]
        if any(e is None or e[1] is None for e in eintraege):
            return None, "Ø automatisch: Lauf ohne Auswertung"
        neu = self._laeufe[ids[0]]
        setup = ((self.db.setup(neu.setup_id) or {}).get("name", "") if neu.setup_id else "") or "ohne Setup"
        from .mittelwert_dialog import namensvorschlag
        name = namensvorschlag(len(ids), setup)
        schluessel = tuple((i, id(e[1])) for i, e in zip(ids, eintraege))
        alt = self._auto
        if alt is not None and alt["schluessel"] == schluessel:
            erg = alt["erg"]
        else:
            try:
                erg = mittelwert.mitteln([mittelwert.kurven_aus_ergebnis(e[1], e[0]) for e in eintraege],
                                         quellen=ids, setups=[setups[i] for i in ids])
            except ValueError as exc:
                return None, f"Ø automatisch: {exc}"
        return {"erg": erg, "ids": ids, "vid": neu.vehicle_id, "sid": neu.setup_id, "name": name,
                "titel": f"{name} (automatisch)", "schluessel": schluessel}, ""

    def _auto_mittel_zeichnen(self):
        """Nach jedem Neuzeichnen der Laeufe: automatischen Mittelwert rechnen, zeichnen (avg_color, Band min–max),
        seine Zeile oben in der Laufliste."""
        info, grund = self._auto_mittel_rechnen()
        if info is not None and not self._mw_kurven(AUTO_KEY, info["erg"], "@avg_color", "minmax"):
            info, grund = None, "Ø automatisch: ohne Leistung/Drehmoment"
        if info is None:
            self.plot.remove(AUTO_KEY)
        self._auto, self._auto_grund = info, grund
        self._auto_zeile()
        self._laeufe_kopf()

    def _auto_zeile_gewaehlt(self) -> bool:
        return any(it.data(0, AUTO_ROLLE) == "zeile" for it in self.run_tree.selectedItems())

    def _auto_zeile(self):
        """Kopf „Ø AUTOMATISCH“ und Zeile des automatischen Mittelwerts (bzw. warum es keinen gibt) oben in der
        Laufliste; „speichern“ in der letzten Spalte. Aus: nichts."""
        t = self.run_tree
        gewaehlt = self._auto_gewaehlt or self._auto_zeile_gewaehlt()
        self._auto_gewaehlt = False
        t.blockSignals(True)
        for i in reversed(range(t.topLevelItemCount())):
            if t.topLevelItem(i).data(0, AUTO_ROLLE):
                t.takeTopLevelItem(i)
        if not self.auto_mittel_an():
            t.blockSignals(False)
            return
        kopf = QtWidgets.QTreeWidgetItem(["Ø AUTOMATISCH  (nicht gespeichert, belegt keinen Platz)"])
        kopf.setData(0, AUTO_ROLLE, "kopf")
        kopf.setFlags(QtCore.Qt.ItemIsEnabled)
        f = kopf.font(0)
        f.setBold(True)
        kopf.setFont(0, f)
        kopf.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.farbe("text2"))))
        t.insertTopLevelItem(0, kopf)
        kopf.setFirstColumnSpanned(True)
        info = self._auto
        if info is None:
            it = QtWidgets.QTreeWidgetItem([self._auto_grund or "Ø automatisch: kein Mittelwert"])
            it.setData(0, AUTO_ROLLE, "grund")
            it.setFlags(QtCore.Qt.ItemIsEnabled)
            it.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.farbe("dim"))))
            it.setToolTip(0, AUTO_TIP)
            t.insertTopLevelItem(1, it)
            it.setFirstColumnSpanned(True)
            t.blockSignals(False)
            return
        erg = info["erg"]
        it = QtWidgets.QTreeWidgetItem(["", info["name"], zahl(erg.p_max, 1), zahl(erg.n_pmax), zahl(erg.m_max, 1),
                                        ""])
        it.setData(0, AUTO_ROLLE, "zeile")
        it.setIcon(1, farb_icon("@avg_color", True))
        for c in range(2, 5):
            it.setTextAlignment(c, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        f = it.font(1)
        f.setBold(True)
        it.setFont(1, f)
        quellen = [self._laeufe.get(i) for i in info["ids"]]
        tip = (f"{info['titel']}\nMittelwert aus " + ", ".join(self._run_label(q).split("  ")[0] for q in quellen if q)
               + f"\nBand = min–max der Quellläufe · nicht gespeichert, belegt keinen der {MESSPLAETZE} Plätze"
               + "\n„speichern“ (oder Rechtsklick) legt daraus einen Mittelwert-Lauf an")
        for c in range(5):
            it.setToolTip(c, tip)
        t.insertTopLevelItem(1, it)
        b = QtWidgets.QToolButton()
        b.setText("speichern")
        b.setToolTip("Als Mittelwert-Lauf speichern (wie Auswertung › Mittelwert bilden), danach über „＋“ wählbar")
        b.setAutoRaise(False)
        b.clicked.connect(lambda _c=False: self.auto_mittel_speichern())
        t.setItemWidget(it, 5, b)
        if gewaehlt:
            it.setSelected(True)
        t.blockSignals(False)

    def _auto_menue(self, pos: QtCore.QPoint):
        m = QtWidgets.QMenu(self)
        a_save = m.addAction("Als Mittelwert-Lauf speichern")
        a_save.setEnabled(self._auto is not None)
        a_aus = m.addAction(f"„{AUTO_TEXT}“ ausschalten")
        act = m.exec(pos)
        if act == a_save:
            self.auto_mittel_speichern()
        elif act == a_aus:
            self.auto_mittel_setzen(False)

    def auto_mittel_speichern(self, name: str = "") -> Optional[int]:
        """Automatischen Mittelwert als Mittelwert-Lauf speichern (mittelwert.speichern wie der Dialog der Auswertung:
        Fahrzeug/Setup des neuesten Quelllaufs, Schnellwerte, Notiz mit den Quellen, Band min–max). -> Lauf-ID."""
        info = self._auto
        if info is None:
            return None
        from .laufleiste import zeit_text
        from .mittelwert_dialog import mehrheit, zusatz_schreiben
        rows = [r for r in (self._laeufe.get(i) or self.db.run(i) for i in info["ids"]) if r is not None]
        setups = [self.db.lauf_setup(r) for r in rows]
        werte = [{"feld": f, "wert": mehrheit([s.get(f) for s in setups]), "vorher": ""}
                 for f in self.db.schnellwert_plaetze(info["vid"]) if f]
        notiz = "Quellen: " + ", ".join(zeit_text(r) for r in reversed(rows))
        rid = mittelwert.speichern(self.db, info["erg"], name.strip() or info["name"], vehicle_id=info["vid"],
                                   setup_id=info["sid"], notiz=notiz, setup_werte=werte)
        row = self.db.run(rid)
        if row is not None:
            zusatz_schreiben(row.ordner, band="minmax")
        self._reload_runs()
        self.statusBar().showMessage(f"„{info['name']}“ als Mittelwert-Lauf gespeichert – über „＋“ ins Diagramm "
                                     "legen oder in der Auswertung öffnen.", 8000)
        return rid

    def diagramm_laeufe(self) -> List[int]:
        """Lauf-IDs, die gerade im Diagramm stehen (ohne die laufende Messung)."""
        return list(self._gezeichnet)

    def _ref_key(self) -> Optional[str]:
        """Lauf fuer ERGEBNIS: laufende/letzte Messung, sonst der gewaehlte, sonst der neueste Lauf im Diagramm."""
        if LIVE_KEY in self.plot.data:
            return LIVE_KEY
        if self._auto_zeile_gewaehlt() and AUTO_KEY in self.plot.data:
            return AUTO_KEY
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
        elif key == AUTO_KEY:
            titel = f"ERGEBNIS  (Ø automatisch, {len(self._auto['ids'])} Läufe)" if self._auto else "ERGEBNIS"
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

    # ------------------------------------------------------------------ Werte am Cursor (rechte Spalte)
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
            if self._auto is not None:
                names[AUTO_KEY] = (self._auto["titel"], theme.laufarbe("@avg_color"), "Ø auto")
            names[LIVE_KEY] = ("laufende Messung", theme.farbe("live_curve"), "live")
            self._cursor_names = names
        return self._cursor_names

    def _neuester_lauf(self) -> Optional[str]:
        """Schluessel des neuesten Laufs im Diagramm (die eben gemessene, ungespeicherte Kurve zuerst; Mittelwerte
        nur, wenn sonst nichts da ist)."""
        if LIVE_KEY in self.plot.data:
            return LIVE_KEY
        laeufe = [self._laeufe[i] for i in self._gezeichnet if i in self._laeufe and str(i) in self.plot.data]
        echte = [r for r in laeufe if not r.ist_mittelwert] or laeufe
        return str(max(echte, key=lambda r: r.datum).id) if echte else None

    def lauf_werte_bei(self, key: str, n_rpm: float) -> Optional[Dict[str, Optional[float]]]:
        """Werte eines Laufs im Diagramm an der Drehzahl n_rpm je Live-Kanal ("n", "egt1", "lam", "v",
        "ecu:coolant" …; "afr" zusaetzlich). None ausserhalb des Laufs. Gespeicherte Laeufe liefern alle Kanaele
        (channels.run_channels), die ungespeicherte Kurve und Mittelwerte nur die des Diagramms."""
        d = self.plot.data.get(key)
        if d is None or not len(d["n"]):
            return None
        n = d["n"]
        ok = np.isfinite(n)
        if not ok.any() or n_rpm < np.min(n[ok]) or n_rpm > np.max(n[ok]):
            return None
        j = int(np.argmin(np.where(ok, np.abs(n - n_rpm), np.inf)))

        def wert(a):
            if a is None or j >= len(a):
                return None
            v = float(a[j])
            return v if math.isfinite(v) else None

        out: Dict[str, Optional[float]] = {"n": wert(n), "lam": wert(d.get("lam")), "egt1": wert(d.get("egt1")),
                                           "egt2": wert(d.get("egt2"))}
        for k, a in d.items():
            if k.startswith(EXTRA_PREFIX):
                name = k[len(EXTRA_PREFIX):]
                out[name if name.startswith(analog.PREFIX) else ECU_PREFIX + name] = wert(a)
        row = self._laeufe.get(int(key)) if key.isdigit() else None
        e = self._entry(int(key)) if row is not None and not row.ist_mittelwert else None
        if e is not None and e[1] is not None:
            from .rusefi import COLUMN_PREFIX
            run, res, ch, _p = e
            seg = res.segment
            for kanal, name in LAUF_KANAL.items():
                if name in ch:
                    out[kanal] = wert(np.asarray(ch[name].values, float)[seg])
            for spalte, a in run.get("columns", {}).items():
                if spalte.startswith(COLUMN_PREFIX):
                    out[ECU_PREFIX + spalte[len(COLUMN_PREFIX):]] = wert(np.asarray(a, float)[seg])
            for lk in analog.lauf_kanaele(run):
                out[analog.live_key(lk.kanal.eingang)] = wert(lk.werte[seg])
        for k in ("egt1", "egt2"):
            if out.get(k) is not None and out[k] <= 0:
                out[k] = None                          # kein Sensor
        lam = out.get("lam")
        if lam is not None and lam <= 0:
            out["lam"] = lam = None
        out["afr"] = lam * 14.7 if lam is not None else None
        return out

    def _cursor_left(self):
        self._cursor_inside = False
        if self._cursor_lauf is not None:
            self._cursor_lauf = None
            self._fahrzeug_titel()
            self._kacheln_zeigen(alle=True)

    def _cursor_moved(self, n_rpm: float):
        """Maus im Diagramm (die Fähnchen zeichnet DynoPlot): solange kein Lauf laeuft, zeigen die Live-Kacheln die
        Werte des neuesten Laufs an der Cursor-Drehzahl."""
        self._cursor_n = n_rpm
        self._cursor_inside = getattr(self.plot, "_cursor_inside", False)
        key = self._neuester_lauf() if self._cursor_inside and not self._lauf_aktiv() else None
        werte = self.lauf_werte_bei(key, n_rpm) if key is not None else None
        if key is None:
            if self._cursor_lauf is not None:
                self._cursor_left()
            return
        self._cursor_lauf = key
        name, farbe, _k = self._cursor_namen().get(key, (key, theme.farbe("live_curve"), key))
        zeit = "eben gemessen" if key == LIVE_KEY else (name.split("  ")[0][-5:] if ":" in name else name)
        bei = f"bei {zahl(werte['n'])} 1/min" if werte else "außerhalb des Laufs"
        self.live_titel.setText(f"<span style='color:{theme.laufarbe(farbe)}'>■</span> LAUF {zeit}  {bei}")
        self.live_titel.setToolTip(f"Werte von {name} am Cursor – Maus aus dem Diagramm: wieder live")
        self._kacheln_lauf(werte or {})

    def _kacheln_lauf(self, werte: Dict[str, Optional[float]]):
        """Live-Kacheln und Drehzahl mit den Werten eines Laufs (statt live), Quelle „Lauf“."""
        self._kachel_cache.clear()
        lam_mode = self.page_settings.lambda_radio.isChecked()
        for k in [self.k_drehzahl] + self.live_kacheln:
            kanal = k.kanal
            if not kanal:
                continue
            v = werte.get(kanal)
            text = zahl(werte.get("afr"), 2) if kanal == "lam" and not lam_mode and v is not None else None
            k.set_wert(v, text)
            k.set_quelle("" if k is self.k_drehzahl else "Lauf")
            if v is not None:
                b = k.balken
                b.set_wert(min(max(float(v), b.lo), b.hi))
        self.k_drehzahl.set_unter("Rolle " + (f"{zahl(werte['v'], 1)} km/h" if werte.get("v") is not None else "–"),
                                  "Zündung " + (zahl(werte["ign"]) if werte.get("ign") else "–"))

    # ------------------------------------------------------------------ Liste: Kontextmenue, Knoepfe
    def _run_context_menu(self, pos):
        it = self.run_tree.itemAt(pos)
        if it is not None and it.data(0, AUTO_ROLLE):
            self._auto_menue(self.run_tree.viewport().mapToGlobal(pos))
            return
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
            # ohne Meldung sah es aus, als ginge Löschen nicht (Testrückmeldung 10.11.)
            if self.run_tree.selectedItems():
                text = ("Der markierte Eintrag ist kein gespeicherter Lauf (z. B. Ø automatisch oder eine "
                        "Überschrift) und lässt sich nicht löschen.")
            else:
                text = ("Kein Lauf markiert. Lauf in der Liste anklicken (mehrere mit Strg oder Umschalt), "
                        "dann Löschen.")
            QtWidgets.QMessageBox.information(self, "Löschen", text)
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
