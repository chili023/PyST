"""
Rechte Spalte der Auswertung, Reiter „Werte am Cursor“ (Entwurf v3): kleine Kacheln wie in Messen.

- oben „Motordrehzahl am Cursor“ (bzw. der Wert der X-Achse) gross, mit Balken und Marke
- je Kanal der Felder eine Kachel (kacheln.WertKachel, groesse=KLEIN) im Mehrlauf-Modus: Farbstrich, Zeit bzw.
  „Ø Ref.“, Wert; je Lauf eine Marke auf dem Balken. Der Kanal laesst sich per Titel ▾ waehlen.
- unten „Pmax je Lauf“ und „Mmax je Lauf“

Reine Funktionen (getestet): wert_bei, bereich_runden, dez_fuer.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PySide6 import QtCore, QtWidgets

from . import theme
from .kacheln import KLEIN, Kanal, WertKachel, ZonenBalken, _kachel_css, zahl

LaufWert = Tuple[str, str, Optional[float]]      # (Basisfarbe, Bezeichnung, Wert)


# ============================================================================================== reine Funktionen
def wert_bei(x, y, x_at: float, interpolieren: bool = False) -> Optional[float]:
    """Wert von y an der Stelle x_at. Ausserhalb der Daten (x_at kleiner als das kleinste bzw. groesser als das
    groesste x) -> None, damit kein Randwert als Cursorwert erscheint. interpolieren=True fuer steigendes x
    (Mittelwert auf dem Drehzahlraster), sonst der naechste Punkt."""
    if x is None or y is None:
        return None
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = min(len(x), len(y))
    if n == 0 or x_at is None or not math.isfinite(x_at):
        return None
    x, y = x[:n], y[:n]
    ok = np.isfinite(x)
    if not ok.any():
        return None
    lo, hi = float(np.min(x[ok])), float(np.max(x[ok]))
    tol = (hi - lo) * 1e-6
    if x_at < lo - tol or x_at > hi + tol:
        return None
    if interpolieren:
        m = ok & np.isfinite(y)
        if m.sum() < 2:
            return None
        xs, ys = x[m], y[m]
        if xs[0] > xs[-1]:
            xs, ys = xs[::-1], ys[::-1]
        if x_at < xs[0] or x_at > xs[-1]:
            return None
        v = float(np.interp(x_at, xs, ys))
    else:
        d = np.where(ok, np.abs(x - x_at), np.inf)
        v = float(y[int(np.argmin(d))])
    return v if math.isfinite(v) else None


def bereich_runden(lo: float, hi: float) -> Tuple[float, float]:
    """Balkenbereich auf runde Werte (Schritt aus plots.nice_step, etwa 4 Teile)."""
    from .plots import nice_step
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return 0.0, 1.0
    if hi - lo < 1e-9:
        span = abs(lo) * 0.1 or 1.0
        lo, hi = lo - span, hi + span
    st = nice_step((hi - lo) / 4)
    a, b = math.floor(lo / st + 1e-9) * st, math.ceil(hi / st - 1e-9) * st
    if b <= a:
        b = a + st
    return round(a, 10), round(b, 10)


def dez_fuer(einheit: str, spanne: float = 100.0) -> int:
    """Nachkommastellen eines Kanals in den Kacheln (wie im Entwurf: PS/Nm 1, λ 2, Übersetzung 3, °C 0)."""
    feste = {"PS": 1, "Nm": 1, "λ": 2, "AFR": 1, "°C": 0, "1/min": 0, "nKW/nR": 3, "km/h": 1, "s": 2, "Hz": 1,
             "0/1": 0, "%": 1}
    if einheit in feste:
        return feste[einheit]
    if spanne < 10:
        return 2
    return 1 if spanne < 100 else 0


@dataclass
class KachelDaten:
    kanal: str                                   # Kanalname der Auswertung (z.B. "Leistung"), "" = leer
    einheit: str = ""
    dez: int = 1
    lo: float = 0.0
    hi: float = 1.0
    zonen: List[Tuple[float, float, str]] = field(default_factory=list)
    laeufe: List[LaufWert] = field(default_factory=list)


# ============================================================================================== Widget
class _XKachel(QtWidgets.QFrame):
    """„Motordrehzahl am Cursor“: Titel, grosser Wert mit Einheit, Balken mit Cursormarke."""

    def __init__(self):
        super().__init__()
        self.setObjectName("xkachel")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 2)
        lay.setSpacing(0)
        hb = QtWidgets.QHBoxLayout()
        hb.setSpacing(3)
        self.titel = QtWidgets.QLabel("Motordrehzahl am Cursor")
        self.wert = QtWidgets.QLabel("–")
        self.wert.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        self.einheit = QtWidgets.QLabel("1/min")
        self.einheit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        hb.addWidget(self.titel)
        hb.addStretch(1)
        hb.addWidget(self.wert)
        hb.addWidget(self.einheit)
        lay.addLayout(hb)
        self.balken = ZonenBalken(hoehe=10, marke=True)
        lay.addWidget(self.balken)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(_kachel_css("xkachel"))
        self.titel.setStyleSheet(f"color: {T['text']}; font-size: 14px; font-weight: 700;")
        self.wert.setStyleSheet(f"color: {T['value']}; font-size: 26px; font-weight: 700;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 13px; padding-bottom: 4px;")


class CursorSpalte(QtWidgets.QWidget):
    """Inhalt des Reiters „Werte am Cursor“. kanal_gewaehlt(index, kanal): Titel ▾ einer Kanalkachel."""
    kanal_gewaehlt = QtCore.Signal(int, str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(5)
        self.x_kachel = _XKachel()
        lay.addWidget(self.x_kachel)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        inner = QtWidgets.QWidget()
        self.kachel_lay = QtWidgets.QVBoxLayout(inner)
        self.kachel_lay.setContentsMargins(0, 0, 0, 0)
        self.kachel_lay.setSpacing(5)
        self.kachel_lay.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)
        self.leer_hinweis = theme.rolle(QtWidgets.QLabel("Keine Läufe angezeigt – links unter „Läufe & Kanäle“ "
                                                         "Läufe hinzufügen."), "neben")
        self.leer_hinweis.setWordWrap(True)
        lay.addWidget(self.leer_hinweis)
        unten = QtWidgets.QHBoxLayout()
        unten.setSpacing(5)
        self.pmax = WertKachel(groesse=KLEIN, kanaele=[Kanal("p_max", "Pmax je Lauf", "PS", dez=1),
                                                       Kanal("n_pmax", "Drehzahl bei Pmax", "1/min")])
        self.mmax = WertKachel(groesse=KLEIN, kanaele=[Kanal("m_max", "Mmax je Lauf", "Nm", dez=1),
                                                       Kanal("n_mmax", "Drehzahl bei Mmax", "1/min")])
        for k, s in ((self.pmax, "p_max"), (self.mmax, "m_max")):
            k.set_kanal(s)
            k.balken.hide()
            k.kanal_gewaehlt.connect(lambda _s, k=k: (k.balken.hide(), self._maxima_zeigen()))
            unten.addWidget(k)
        lay.addLayout(unten)
        self.kacheln: List[WertKachel] = []
        self._maxima: dict = {}

    # ------------------------------------------------------------ X-Achse
    def set_x(self, titel: str, einheit: str, wert: Optional[float], lo: float, hi: float, dez: int = 0):
        k = self.x_kachel
        k.titel.setText(titel)
        k.einheit.setText(einheit)
        k.wert.setText(zahl(wert, dez))
        k.balken.set_bereich(lo, hi, dez=dez)
        k.balken.set_marken([(wert, "@cursor")] if wert is not None else [])

    # ------------------------------------------------------------ Kanaele
    def set_kacheln(self, daten: Sequence[KachelDaten], kanaele: Sequence[Kanal]):
        """Je Eintrag eine Kachel (wiederverwendet); kanaele = Auswahl im Titel ▾."""
        while len(self.kacheln) < len(daten):
            i = len(self.kacheln)
            k = WertKachel(groesse=KLEIN)
            k.set_menue_hinweis("Standard: die Kanäle der Felder")
            k.kanal_gewaehlt.connect(lambda s, i=i: self.kanal_gewaehlt.emit(i, s))
            self.kachel_lay.insertWidget(i, k)
            self.kacheln.append(k)
        for i, k in enumerate(self.kacheln):
            if i >= len(daten):
                k.hide()
                continue
            d = daten[i]
            k.show()
            k.set_kanaele(kanaele)
            if k.kanal != d.kanal or k.dez != d.dez:
                k.set_kanal(d.kanal, titel=d.kanal, einheit=d.einheit)
                k.dez = d.dez
            k.set_quelle(d.einheit)                      # rechts oben die Einheit (wie im Entwurf)
            k.set_bereich(d.lo, d.hi, zonen=d.zonen, dez=d.dez if d.hi - d.lo < 10 else 0)
            k.set_laeufe(d.laeufe if d.kanal else [])
        self.leer_hinweis.setVisible(not daten)

    # ------------------------------------------------------------ Maxima
    def set_maxima(self, maxima: dict):
        """maxima: {"p_max"|"n_pmax"|"m_max"|"n_mmax": [(Basisfarbe, Bezeichnung, Wert)]}"""
        self._maxima = dict(maxima)
        self._maxima_zeigen()

    def _maxima_zeigen(self):
        for k in (self.pmax, self.mmax):
            k.dez = 0 if k.kanal.startswith("n_") else 1
            info = k.kanal_info()
            k.set_quelle(info.einheit if info else "")
            k.set_laeufe(self._maxima.get(k.kanal, []) if k.kanal else [])
            k.balken.hide()
