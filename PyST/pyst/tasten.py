"""
Tasten der Funk-Zange: Setup, Zustand der Übertragung und die Aktionen Neuer Lauf und Übersetzung messen. Ohne Qt.

Der Blitzmodus und alles Lebensnotwendige sind fest in der Firmware (FEST), damit die Zange ohne PC läuft. Die sechs
Gesten hier gelten außerhalb des Blitzmodus. Die Zange hält das Setup nur im RAM und startet mit STANDARD: PyST schickt es
beim Verbinden, bei jedem neuen „Zange verbunden“, nach einem Neustart der Zange und bei einer Änderung erneut.

Übertragung: ein Wert, je Geste ein Halbbyte (firmware/zange-c3/src/core/ztasten.h). Über die Basis '#ZANGE SET tasten <wert>',
direkt per USB '#SET tasten <wert>'. Bestätigt ist das Setup, wenn die Zange quittiert hat.

Rückmeldung an der Zange: '#ZANGE LED <muster>'. Die Zange erwartet nach Neuer Lauf und Übersetzung eine Antwort, sonst zeigt
sie das Fehlerbild.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import farben
from . import physics

GESTEN: Tuple[Tuple[str, str], ...] = (("t1_kurz", "T1 kurz"), ("t2_kurz", "T2 kurz"), ("t3_kurz", "T3 kurz"),
                                       ("t3_lang", "T3 lang"), ("t4_kurz", "T4 kurz"), ("t4_lang", "T4 lang"))
# Reihenfolge = Wert in der Firmware (zt_aktion_t)
AKTIONEN: Tuple[Tuple[str, str], ...] = (("nichts", "Nichts"), ("marker", "Marker"),
                                         ("aufnahme", "Aufnahme Zange Start/Stopp"), ("lauf", "Neuer Lauf"),
                                         ("uebersetzung", "Übersetzung messen"), ("lambda", "Lambda ein/aus"))
AKTION_TEXT = dict(AKTIONEN)
GESTE_TEXT = dict(GESTEN)
STANDARD: Dict[str, str] = {"t1_kurz": "marker", "t2_kurz": "aufnahme", "t3_kurz": "lauf", "t3_lang": "uebersetzung",
                            "t4_kurz": "nichts", "t4_lang": "lambda"}
FEST: Tuple[Tuple[str, str], ...] = (
    ("T1 halten", "einschalten (Hardware)"), ("T1 lang, 2 s", "ausschalten"), ("T1 + T2, 3 s", "koppeln"),
    ("T2 lang, 1,5 s", "Blitzmodus ein/aus"), ("Blitzmodus: T1 kurz", "Marker"),
    ("Blitzmodus: T2 kurz", "Aufnahme Zange Start/Stopp"), ("Blitzmodus: T3/T4 kurz", "φ −/+ 0,5 °KW"),
    ("Blitzmodus: T3/T4 lang", "φ −/+ 5 °KW"))

LED_KEIN, LED_LAEUFT, LED_FERTIG, LED_OK, LED_FEHLER = range(5)        # pf_led_t
ZANGE_VERSION, BASIS_VERSION = "0.1.3", "0.2.3"


def laden(d: Optional[dict]) -> Dict[str, str]:
    """Aus den Einstellungen; Fehlendes und Unbekanntes wird Standard."""
    d = d if isinstance(d, dict) else {}
    return {g: d[g] if d.get(g) in AKTION_TEXT else STANDARD[g] for g, _t in GESTEN}


def packen(setup: Dict[str, str]) -> int:
    namen = [a for a, _t in AKTIONEN]
    return sum(namen.index(setup[g]) << (4 * i) for i, (g, _t) in enumerate(GESTEN))


def entpacken(wert: int) -> Dict[str, str]:
    return {g: AKTIONEN[(wert >> (4 * i)) & 0xF][0] for i, (g, _t) in enumerate(GESTEN)}


def doppelt(setup: Dict[str, str]) -> List[Tuple[str, List[str]]]:
    """Aktionen auf mehreren Gesten: [(Aktion, [Gesten])]. Marker und Nichts dürfen mehrfach."""
    out = []
    for a, _t in AKTIONEN:
        gesten = [g for g, _x in GESTEN if setup[g] == a]
        if a not in ("marker", "nichts") and len(gesten) > 1:
            out.append((a, gesten))
    return out


def doppelt_text(setup: Dict[str, str]) -> str:
    return "\n".join(f"„{AKTION_TEXT[a]}“ liegt auf " + " und ".join(GESTE_TEXT[g] for g in gs) + "."
                     for a, gs in doppelt(setup))


def geste_von(setup: Dict[str, str], aktion: str) -> str:
    """Beschriftung der ersten Geste mit dieser Aktion, für Hinweise an den Fahrer."""
    return next((t for g, t in GESTEN if setup[g] == aktion), "Taste")


# ---------------------------------------------------------------------------------------------- Übertragung
class TastenSync:
    """Wann das Setup zur Zange muss und ob sie es bestätigt hat. Der Aufrufer fragt faellig() und meldet antwort()."""
    AUS, OFFEN, UNTERWEGS, OK, ALT, FEHLER = "aus", "offen", "unterwegs", "ok", "alt", "fehler"
    WIEDERHOLEN_S = 2.0
    VERSUCHE = 5

    def __init__(self, wert: int):
        self.wert = wert
        self.da = False
        self.zustand = self.AUS
        self.grund = ""
        self.versuche = 0
        self.naechster = 0.0

    def _offen(self):
        self.zustand = self.OFFEN if self.da else self.AUS
        self.versuche = 0
        self.naechster = 0.0

    def setzen(self, wert: int):
        """Änderung im Menü."""
        self.wert = wert
        self._offen()

    def zange(self, da: bool):
        """Zange erreichbar? Jede neue Verbindung schickt das Setup."""
        if da != self.da:
            self.da = da
            self._offen()

    def neu(self):
        """Zange meldet sich neu (verbunden, gekoppelt, Neustart): RAM-Setup kann weg sein."""
        self.da = True
        self._offen()

    def faellig(self, now: float) -> Optional[int]:
        if self.zustand == self.OFFEN and now >= self.naechster:
            self.zustand = self.UNTERWEGS
            self.versuche += 1
            return self.wert
        return None

    def antwort(self, now: float, wert: int, lines: List[str]):
        if self.zustand != self.UNTERWEGS:
            return                                     # Verbindung inzwischen weg oder Wert neu: gilt nicht mehr
        if wert != self.wert:
            self._offen()
            return
        last = lines[-1] if lines else ""
        if last.startswith("#OK"):
            self.zustand = self.OK
            self.grund = ""
        elif "unbekannt" in last:
            self.zustand = self.ALT
            self.grund = last
        elif self.versuche >= self.VERSUCHE:
            self.zustand = self.FEHLER
            self.grund = last or "keine Antwort"
        else:
            self.zustand = self.OFFEN                  # belegt, keine Antwort: gleich noch einmal
            self.naechster = now + self.WIEDERHOLEN_S

    def text(self) -> Tuple[str, str]:
        """Für die Anzeige: Text, Farbe."""
        if self.zustand == self.OK:
            return "Die Zange hat das Setup bestätigt.", farben.GUT
        if self.zustand in (self.OFFEN, self.UNTERWEGS):
            return "Wird an die Zange geschickt …", farben.WARNUNG
        if self.zustand == self.ALT:
            return f"Firmware zu alt für das Setup: Zange ab {ZANGE_VERSION}, Basis ab {BASIS_VERSION}.", farben.FEHLER
        if self.zustand == self.FEHLER:
            return f"Die Zange hat das Setup nicht bestätigt ({self.grund}).", farben.FEHLER
        return "Keine Zange verbunden. Das Setup geht beim Verbinden hinaus, bis dahin gilt der Standard.", farben.NEUTRAL


# ---------------------------------------------------------------------------------------------- Sperren
def sperre(verbunden: bool, lauf: bool, oszi: bool, flash: bool, dialog: bool = False, pruefung: bool = False,
           messung: bool = False) -> str:
    """Warum eine Tasten-Aktion gerade nicht geht; leer = sie geht. Gilt für Neuer Lauf und Übersetzung."""
    if flash:
        return "Firmware wird geflasht"
    if not verbunden:
        return "Messboard nicht verbunden"
    if lauf:
        return "Ein Lauf läuft schon"
    if oszi:
        return "Oszi-Aufnahme läuft"
    if pruefung:
        return "Prüfung läuft"
    if dialog:
        return "Ein Dialog ist offen"
    if messung:
        return "Übersetzung wird gerade gemessen"
    return ""


# ---------------------------------------------------------------------------------------------- Übersetzung
MESS_S = 5.0
BESTAETIGEN_S = 30.0
STREUUNG_MAX = 0.02               # Übersetzung: Standardabweichung, wie im Dialog
DREHZAHL_MAX = 0.03               # Rolle: Standardabweichung, bezogen auf den Mittelwert
ANTEIL_MIN = 0.8                  # so viele Proben müssen beide Drehzahlen haben
PROBEN_MIN = 10
ECU_MAX = 0.03                    # Zündabnehmer gegen rusEFI, wie im Dialog
DOPPELT_TOLERANZ = 0.05


def auswerten(n_motor, n_rolle, n_ecu=None, bisher: float = 0.0) -> Tuple[Optional[float], str]:
    """Proben einer Messung -> (Übersetzung, Fehler). Kein Wert, wenn die Messung nicht taugt."""
    nm, nr = np.asarray(n_motor, float), np.asarray(n_rolle, float)
    if len(nm) < PROBEN_MIN:
        return None, "Zu wenige Messwerte"
    ok = np.isfinite(nm) & np.isfinite(nr) & (nm > 0) & (nr > 1)
    if ok.sum() < max(PROBEN_MIN, ANTEIL_MIN * len(nm)):
        return None, "Motor oder Rolle liefern keine Drehzahl"
    nm, nr = nm[ok], nr[ok]
    if float(np.std(nr)) > DREHZAHL_MAX * float(np.mean(nr)):
        return None, "Drehzahl nicht stabil"
    wert = physics.trimmed_ratio(nm, nr)
    if wert is None or wert <= 0:
        return None, "Zu wenige Messwerte"
    if float(np.std(nm / nr)) > STREUUNG_MAX * wert:
        return None, "Streuung zu groß: Drehzahl nicht stabil oder Signal unsauber"
    if n_ecu is not None:
        ne = np.asarray(n_ecu, float)[ok]
        fein = np.isfinite(ne) & (ne > 0)
        if fein.sum() >= PROBEN_MIN:
            ecu = physics.trimmed_ratio(ne[fein], nr[fein])
            if ecu and abs(wert / ecu - 1.0) > ECU_MAX:
                return None, (f"Zündabnehmer und rusEFI weichen um {100 * (wert / ecu - 1):+.0f} % ab: "
                              "zählt die Zange doppelt? Impulse je Umdrehung prüfen")
    if bisher > 0:
        for faktor in (2.0, 0.5):
            if abs(wert / bisher / faktor - 1.0) < DOPPELT_TOLERANZ:
                return None, (f"Wert ist das {'Doppelte' if faktor > 1 else 'Halbe'} von bisher: zählt die Zange doppelt? "
                              "Impulse je Umdrehung prüfen, sonst im Dialog Übersetzung eintragen")
    return wert, ""


@dataclass
class Meldung:
    """Was bei tick() passiert ist."""
    art: str                      # fertig, fehler, verworfen
    text: str = ""


class UebersetzungMessung:
    """Fahrer hält Drehzahl, Taste misst MESS_S, zweites Drücken übernimmt. Ohne zweites Drücken ändert sich nichts."""
    AUS, MISST, WARTET = "aus", "misst", "wartet"

    def __init__(self, mess_s: float = MESS_S, bestaetigen_s: float = BESTAETIGEN_S):
        self.mess_s = mess_s
        self.bestaetigen_s = bestaetigen_s
        self.zustand = self.AUS
        self.bisher = 0.0
        self.wert: Optional[float] = None
        self.t_ende = 0.0
        self._nm: List[float] = []
        self._nr: List[float] = []
        self._ne: List[float] = []

    @property
    def aktiv(self) -> bool:
        return self.zustand != self.AUS

    def taste(self, now: float, bisher: float) -> Tuple[str, Optional[float]]:
        """Druck auf die Geste: ('start'|'laeuft'|'uebernehmen', Wert beim Übernehmen)."""
        if self.zustand == self.MISST:
            return "laeuft", None
        if self.zustand == self.WARTET:
            wert = self.wert
            self.abbrechen()
            return "uebernehmen", wert
        self.zustand = self.MISST
        self.bisher = bisher
        self.wert = None
        self.t_ende = now + self.mess_s
        self._nm, self._nr, self._ne = [], [], []
        return "start", None

    def probe(self, n_motor: float, n_rolle: float, n_ecu: Optional[float] = None):
        if self.zustand == self.MISST:
            self._nm.append(n_motor)
            self._nr.append(n_rolle)
            self._ne.append(float("nan") if n_ecu is None else n_ecu)

    def tick(self, now: float) -> Optional[Meldung]:
        if self.zustand == self.MISST and now >= self.t_ende:
            wert, fehler = auswerten(self._nm, self._nr, self._ne, self.bisher)
            if wert is None:
                self.abbrechen()
                return Meldung("fehler", fehler)
            self.wert = wert
            self.zustand = self.WARTET
            self.t_ende = now + self.bestaetigen_s
            return Meldung("fertig")
        if self.zustand == self.WARTET and now >= self.t_ende:
            self.abbrechen()
            return Meldung("verworfen", "Keine Bestätigung, Messung verworfen")
        return None

    def abbrechen(self):
        self.zustand = self.AUS
        self.wert = None

    def rest_s(self, now: float) -> int:
        return max(0, int(np.ceil(self.t_ende - now)))
