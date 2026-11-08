"""
Gemeinsame Bausteine der neuen Oberflaeche (Messen, Auswertung, Einstellungen), alle mit Farben aus theme.py:

- ZonenBalken   Balken mit Skala und Zonen (gruen/gelb/rot/blau), Fuellung oder Marke, mehrere Laufmarken
- WertKachel    Titel mit ▾-Menue zur Kanalwahl (gruppiert nach Quelle PST/ECU, „leer lassen“), Quelle rechts oben,
                grosser Wert mit Einheit, Balken, Platznummer; Groessen "gross" (Messen) und "klein" (Auswertung);
                Modus "mehrere Laeufe" (Zeilen je Lauf + Marken); Zustand "fehlt" („ECU fehlt“, Wert „–“)
- SeitenLeiste  links einklappbar: 32-px-Streifen mit senkrechtem Titel, Overlay nach 300 ms Verweilen ueber dem
                Nachbarn (verdeckt, schiebt nicht), Klick daneben/Maus weg klappt zu, „Anpinnen“ schiebt den Nachbarn
- Symbole       pin_icon, schloss_icon/schloss_pixmap, farb_icon (Laufarbe, Ø fuer Mittelwert)
- zahl(x, dez)  deutsche Zahl mit Tausenderpunkt und Komma
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

LEER = ""                         # Kanal-Schluessel fuer „leer lassen“
GROSS, KLEIN = "gross", "klein"
QUELLEN = ("PST", "ECU")          # Reihenfolge der Gruppen im Auswahlmenue


def zahl(x: Optional[float], dez: int = 0) -> str:
    """12345.6 -> "12.345,6"; None/NaN -> "–"."""
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "–"
    s = f"{x:,.{max(0, dez)}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Kanal:
    """Waehlbarer Kanal einer Kachel. schluessel z.B. "egt1", "lam", "ecu:CLT"."""
    schluessel: str
    titel: str
    einheit: str = ""
    quelle: str = "PST"           # Gruppe im Menue: "PST" oder "ECU"
    dez: int = 0
    lo: float = 0.0               # Vorschlag Balkenbereich
    hi: float = 100.0


Zone = Tuple[float, float, str]   # (von, bis, stufe) mit stufe in ok | warn | err | cold


# =============================================================================================== Balken
class ZonenBalken(QtWidgets.QWidget):
    """Balken mit Zonen und Skala. set_wert: Fuellung bis zum Wert (Farbe der Zone) bzw. Marke (marke=True, z.B.
    Lambda im Sollband). set_marken([(wert, farbe)]): je Lauf eine Marke (Auswertung, mehrere Laeufe).
    set_fehlt(True): grauer Balken ohne Wert."""

    def __init__(self, lo: float = 0.0, hi: float = 100.0, zonen: Optional[Sequence[Zone]] = None,
                 ticks: Optional[Sequence[float]] = None, hoehe: int = 14, marke: bool = False, dez: int = 0,
                 skala: bool = True):
        super().__init__()
        self.lo, self.hi = float(lo), float(hi)
        self.zonen: List[Zone] = list(zonen or [])
        self.ticks: List[float] = list(ticks) if ticks is not None else []
        self.marke, self.dez, self.bar_h, self.skala = marke, dez, hoehe, skala
        self.wert: Optional[float] = None
        self.marken: List[Tuple[float, str]] = []
        self.fehlt = False
        self.setFixedHeight(hoehe + (18 if skala else 3))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        theme.bei_wechsel(self, lambda _t: self.update(), sofort=False)

    # ---- Einstellen
    def set_bereich(self, lo: float, hi: float, ticks: Optional[Sequence[float]] = None, dez: Optional[int] = None):
        self.lo, self.hi = float(lo), float(hi) if hi > lo else float(lo) + 1.0
        self.ticks = list(ticks) if ticks is not None else []
        if dez is not None:
            self.dez = dez
        self.update()

    def set_zonen(self, zonen: Optional[Sequence[Zone]]):
        self.zonen = [(float(a), float(b), str(s)) for a, b, s in (zonen or [])]
        self.update()

    def set_wert(self, wert: Optional[float]):
        self.wert = None if wert is None or not math.isfinite(wert) else float(wert)
        self.update()

    def set_marken(self, marken: Sequence[Tuple[float, str]]):
        self.marken = [(float(v), c) for v, c in marken if v is not None and math.isfinite(v)]
        self.update()

    def set_fehlt(self, on: bool):
        self.fehlt = on
        self.update()

    # ---- Rechnen
    def auto_ticks(self) -> List[float]:
        if self.ticks:
            return self.ticks
        from .plots import nice_step
        st = nice_step((self.hi - self.lo) / 4)
        first = math.ceil(self.lo / st - 1e-9) * st
        out, v = [], first
        while v <= self.hi + 1e-9:
            out.append(round(v, 10))
            v += st
        return out

    def zonen_stufe(self, v: float) -> str:
        """Stufe der Zone, in der v liegt (ausserhalb aller Zonen: ok)."""
        for a, b, s in self.zonen:
            if a <= v <= b:
                return s
        return "ok"

    def _x(self, v: float, w: float) -> float:
        return 5 + (min(max(v, self.lo), self.hi) - self.lo) / (self.hi - self.lo) * (w - 10)

    def paintEvent(self, _ev):
        T = theme.tokens()
        nacht = theme.ist_nacht()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w = self.width()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(T["track"]))
        p.drawRoundedRect(QtCore.QRectF(5, 1, w - 10, self.bar_h), 3, 3)
        if not self.fehlt:
            for a, b, s in self.zonen:
                col = QtGui.QColor(T[theme.ZONEN.get(s, "zone_ok")])
                col.setAlpha(70 if nacht else 60)
                p.setBrush(col)
                p.drawRect(QtCore.QRectF(self._x(a, w), 1, self._x(b, w) - self._x(a, w), self.bar_h))
        if self.wert is not None and not self.marken and not self.fehlt:
            zc = QtGui.QColor(T[theme.ZONEN.get(self.zonen_stufe(self.wert), "zone_ok")])
            if self.marke:
                x = self._x(self.wert, w)
                p.setBrush(QtGui.QColor(T["value"]))
                p.drawRoundedRect(QtCore.QRectF(x - 3.5, 0, 7, self.bar_h + 2), 2, 2)
                p.setBrush(zc)
                p.drawRoundedRect(QtCore.QRectF(x - 2, 1.5, 4, self.bar_h - 1), 1, 1)
            else:
                p.setBrush(zc)
                p.drawRoundedRect(QtCore.QRectF(5, 1, self._x(self.wert, w) - 5, self.bar_h), 3, 3)
        if not self.fehlt:
            for v, col in self.marken:
                x = self._x(v, w)
                p.setBrush(QtGui.QColor(T["value"]))
                p.drawRoundedRect(QtCore.QRectF(x - 4, -0.5, 8, self.bar_h + 3), 2, 2)
                p.setBrush(QtGui.QColor(theme.laufarbe(col)))
                p.drawRoundedRect(QtCore.QRectF(x - 2.5, 1, 5, self.bar_h), 1.5, 1.5)
        if self.skala:
            f = p.font()
            f.setPixelSize(theme.ACHSE_PX)
            p.setFont(f)
            p.setPen(QtGui.QColor(T["text2"]))
            fm = p.fontMetrics()
            ticks = self.auto_ticks()
            for i, t in enumerate(ticks):
                x = self._x(t, w)
                p.drawLine(QtCore.QPointF(x, self.bar_h + 1), QtCore.QPointF(x, self.bar_h + 4))
                txt = zahl(t, self.dez)
                tw = fm.horizontalAdvance(txt)
                tx = x - 1 if i == 0 else (x - tw + 1 if i == len(ticks) - 1 else x - tw / 2)
                p.drawText(QtCore.QPointF(max(0.0, min(tx, w - tw)), self.bar_h + 17), txt)
        p.end()


# =============================================================================================== Kachel
def _kachel_css(objname: str, border_tok: str = "border") -> str:
    T = theme.tokens()
    return (f"QFrame#{objname} {{ background: {T['panel']}; border: 1px solid {T[border_tok]}; border-radius: 5px; }}"
            "QLabel { background: transparent; border: none; }")


class WertKachel(QtWidgets.QFrame):
    """Kachel fuer einen Kanal. Ablauf fuer den Aufrufer:
        k = WertKachel(platz=1, groesse=GROSS, kanaele=[Kanal("egt1", "EGT 1", "°C", "PST"), …])
        k.kanal_gewaehlt.connect(...)        # Auswahl im ▾-Menue (Schluessel, LEER = leer lassen) -> speichern
        k.set_kanal("egt1"); k.set_bereich(200, 1000, zonen=[(850, 899, "warn"), (900, 1000, "err")])
        k.set_wert(846)                      # live; None = „–“
        k.set_fehlt("ECU fehlt")             # Kanal gerade nicht verfuegbar
        k.set_laeufe([(farbe, "16:24", 29.7), …])   # mehrere Laeufe: Zeilen + Marken; [] = wieder Einzelwert
    """
    kanal_gewaehlt = QtCore.Signal(str)

    def __init__(self, platz: int = 0, groesse: str = GROSS, kanaele: Sequence[Kanal] = (),
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("wertkachel")
        self.platz, self.groesse = platz, groesse
        self.kanaele: List[Kanal] = list(kanaele)
        self.kanal: str = LEER
        self.dez = 0
        self.hervorgehoben = False
        self.menue_hinweis = ""
        self._fehlt_text = ""
        self._quelle_text = ""
        self.wert: Optional[float] = None
        self.laeufe: List[Tuple[str, str, Optional[float]]] = []
        gross = groesse == GROSS
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(0)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(4)
        self.nr = QtWidgets.QLabel(str(platz) if platz else "")
        self.nr.setFixedSize(18, 18)
        self.nr.setAlignment(QtCore.Qt.AlignCenter)
        self.nr.setVisible(bool(platz))
        head.addWidget(self.nr)
        self.titel = QtWidgets.QToolButton()
        self.titel.setAutoRaise(True)
        self.titel.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.titel.setToolTip("Kanal wählen")
        self.menue = QtWidgets.QMenu(self.titel)
        self.menue.aboutToShow.connect(self._menue_fuellen)
        self.titel.setMenu(self.menue)
        head.addWidget(self.titel)
        head.addStretch(1)
        self.quelle = QtWidgets.QLabel("")
        head.addWidget(self.quelle)
        lay.addLayout(head)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(3)
        row.addStretch(1)
        self.wert_label = QtWidgets.QLabel("–")
        self.wert_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        row.addWidget(self.wert_label)
        self.einheit = QtWidgets.QLabel("")
        self.einheit.setMinimumWidth(24 if gross else 18)
        self.einheit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        row.addWidget(self.einheit)
        self.wert_zeile = QtWidgets.QWidget()
        self.wert_zeile.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.wert_zeile)
        # mehrere Laeufe: Zeilen je Lauf (Farbstrich, Bezeichnung, Wert)
        self.lauf_box = QtWidgets.QWidget()
        self.lauf_grid = QtWidgets.QGridLayout(self.lauf_box)
        self.lauf_grid.setContentsMargins(0, 2, 0, 2)
        self.lauf_grid.setHorizontalSpacing(6)
        self.lauf_grid.setVerticalSpacing(0)
        self.lauf_grid.setColumnStretch(1, 1)
        self.lauf_box.hide()
        lay.addWidget(self.lauf_box)
        self.balken = ZonenBalken(hoehe=14 if gross else 10)
        lay.addWidget(self.balken)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self._zeige_kanal()

    # ------------------------------------------------------------ Stil
    def _stil(self):
        T = theme.tokens()
        gross = self.groesse == GROSS
        self.setStyleSheet(_kachel_css("wertkachel", "zone_warn" if self.hervorgehoben else "border"))
        self.nr.setStyleSheet(f"QLabel {{ background: {T['track']}; border-radius: 9px; color: {T['text']};"
                              " font-size: 12px; font-weight: 700; }")
        self.titel.setStyleSheet(f"QToolButton {{ color: {T['text']}; font-size: {14 if gross else 13}px; font-weight: 700;"
                                 " border: none; padding: 0 2px; background: transparent; }"
                                 "QToolButton::menu-indicator { image: none; }")
        self.quelle.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")
        wert_farbe = T["dim"] if (self._fehlt_text or self.wert is None) else T["value"]
        self.wert_label.setStyleSheet(f"color: {wert_farbe}; font-size: {30 if gross else 20}px; font-weight: 700;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 13px; padding-bottom: {5 if gross else 3}px;")
        self._laufzeilen()

    # ------------------------------------------------------------ Kanal
    def set_kanaele(self, kanaele: Sequence[Kanal]):
        self.kanaele = list(kanaele)
        self._zeige_kanal()

    def kanal_info(self, schluessel: Optional[str] = None) -> Optional[Kanal]:
        s = self.kanal if schluessel is None else schluessel
        return next((k for k in self.kanaele if k.schluessel == s), None)

    def set_kanal(self, schluessel: str, titel: Optional[str] = None, einheit: Optional[str] = None,
                  quelle: Optional[str] = None):
        """Kanal zeigen (ohne Signal). Titel/Einheit/Quelle aus der Kanalliste, sonst die Angaben."""
        self.kanal = schluessel or LEER
        k = self.kanal_info()
        self._titel = titel if titel is not None else (k.titel if k else (schluessel or "leer"))
        self._einheit = einheit if einheit is not None else (k.einheit if k else "")
        self._quelle_text = quelle if quelle is not None else (k.quelle if k else "")
        if k is not None:
            self.dez = k.dez
        self._fehlt_text = ""
        self._zeige_kanal()

    def _zeige_kanal(self):
        leer = not self.kanal
        if not hasattr(self, "_titel"):
            self._titel, self._einheit = "leer", ""
        self.titel.setText(f"{'leer' if leer else self._titel}  ▾")
        self.einheit.setText("" if leer else self._einheit)
        self.quelle.setText(self._fehlt_text or self._quelle_text)
        self.wert_zeile.setVisible(not leer and not self.laeufe)
        self.balken.setVisible(not leer)
        self._stil()

    def set_menue_hinweis(self, text: str):
        """Hinweis unten im Auswahlmenue, z.B. „Auswahl gilt für Aprilia RS 125“."""
        self.menue_hinweis = text

    def _menue_fuellen(self):
        m = self.menue
        m.clear()
        gruppen: Dict[str, List[Kanal]] = {}
        for k in self.kanaele:
            gruppen.setdefault(k.quelle or "PST", []).append(k)
        reihe = [q for q in QUELLEN if q in gruppen] + [q for q in gruppen if q not in QUELLEN]
        for q in reihe:
            m.addSection(q)
            for k in gruppen[q]:
                a = m.addAction(k.titel + (f" [{k.einheit}]" if k.einheit else ""))
                a.setCheckable(True)
                a.setChecked(k.schluessel == self.kanal)
                a.triggered.connect(lambda _c=False, s=k.schluessel: self.waehle(s))
        m.addSeparator()
        a = m.addAction("leer lassen")
        a.setCheckable(True)
        a.setChecked(not self.kanal)
        a.triggered.connect(lambda _c=False: self.waehle(LEER))
        if self.menue_hinweis:
            h = m.addAction(self.menue_hinweis)
            h.setEnabled(False)

    def waehle(self, schluessel: str):
        """Wie eine Auswahl im Menue: Kanal setzen und kanal_gewaehlt senden."""
        self.set_kanal(schluessel)
        k = self.kanal_info()
        if k is not None:
            self.set_bereich(k.lo, k.hi, dez=k.dez)
        self.set_wert(None)
        self.kanal_gewaehlt.emit(self.kanal)

    # ------------------------------------------------------------ Werte
    def set_bereich(self, lo: float, hi: float, zonen: Optional[Sequence[Zone]] = None,
                    ticks: Optional[Sequence[float]] = None, dez: Optional[int] = None, marke: Optional[bool] = None):
        """Balkenbereich, Zonen [(von, bis, "ok"|"warn"|"err"|"cold")], Skala; marke=True: Marke statt Fuellung."""
        self.balken.set_bereich(lo, hi, ticks, dez)
        if zonen is not None:
            self.balken.set_zonen(zonen)
        if marke is not None:
            self.balken.marke = marke

    def set_wert(self, wert: Optional[float], text: Optional[str] = None):
        """Einzelwert; text statt der formatierten Zahl (z.B. "heizt auf")."""
        self.wert = None if wert is None or not isinstance(wert, (int, float)) or not math.isfinite(wert) else float(wert)
        if self._fehlt_text:
            self._fehlt_text = ""
            self.quelle.setText(self._quelle_text)
            self.balken.set_fehlt(False)
        self.wert_label.setText(text if text is not None else zahl(self.wert, self.dez))
        self.balken.set_wert(self.wert)
        self._stil()

    def set_fehlt(self, text: str = "ECU fehlt"):
        """Kanal nicht verfuegbar: Wert „–“, grauer Balken, text rechts oben statt der Quelle."""
        self._fehlt_text = text
        self.wert = None
        self.quelle.setText(text)
        self.wert_label.setText("–")
        self.balken.set_wert(None)
        self.balken.set_fehlt(True)
        self._stil()

    @property
    def fehlt(self) -> bool:
        return bool(self._fehlt_text)

    def set_quelle(self, text: str):
        self._quelle_text = text
        if not self._fehlt_text:
            self.quelle.setText(text)

    def set_hervorgehoben(self, on: bool):
        self.hervorgehoben = on
        self._stil()

    def wert_text(self) -> str:
        return self.wert_label.text()

    # ------------------------------------------------------------ mehrere Laeufe
    def set_laeufe(self, laeufe: Sequence[Tuple[str, str, Optional[float]]]):
        """Modus „mehrere Laeufe“: [(Laufarbe, Bezeichnung, Wert)] untereinander, je Lauf eine Marke auf dem
        Balken. Leere Liste = zurueck zum Einzelwert."""
        self.laeufe = [(c, t, v) for c, t, v in laeufe]
        self.lauf_box.setVisible(bool(self.laeufe) and bool(self.kanal))
        self.wert_zeile.setVisible(not self.laeufe and bool(self.kanal))
        self.balken.set_marken([(v, c) for c, _t, v in self.laeufe if v is not None])
        self._laufzeilen()

    def _laufzeilen(self):
        g = self.lauf_grid
        while g.count():
            it = g.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()          # sonst bleiben Reste sichtbar, bis deleteLater greift
                it.widget().deleteLater()
        if not self.laeufe:
            return
        T = theme.tokens()
        for r, (farbe, text, wert) in enumerate(self.laeufe):
            strich = QtWidgets.QLabel()
            strich.setFixedSize(16, 5)
            strich.setStyleSheet(f"background: {theme.laufarbe(farbe)}; border-radius: 1px;")
            name = QtWidgets.QLabel(text)
            name.setStyleSheet(f"color: {T['text']}; font-size: 13px;")
            val = QtWidgets.QLabel(zahl(wert, self.dez))
            val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            val.setStyleSheet(f"color: {T['value'] if wert is not None else T['dim']}; font-size: 14px; font-weight: 700;")
            g.addWidget(strich, r, 0, QtCore.Qt.AlignVCenter)
            g.addWidget(name, r, 1)
            g.addWidget(val, r, 2)

    def lauf_texte(self) -> List[Tuple[str, str]]:
        """[(Bezeichnung, Wert-Text)] der Laufzeilen (fuer Tests)."""
        return [(t, zahl(v, self.dez)) for _c, t, v in self.laeufe]


# =============================================================================================== Symbole
def _pix(draw, size: int = 20) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    draw(p)
    p.end()
    return pm


def pin_icon(angepinnt: bool) -> QtGui.QIcon:
    """Pinnadel: schraeg = nicht angepinnt, senkrecht in Akzentfarbe = angepinnt."""
    col = QtGui.QColor(theme.farbe("accent_border") if angepinnt else theme.farbe("text"))

    def d(p):
        p.translate(10, 10)
        if not angepinnt:
            p.rotate(40)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QtCore.QPointF(0, -5), 4.2, 4.2)
        p.drawRect(QtCore.QRectF(-1.6, -3, 3.2, 5))
        p.drawRect(QtCore.QRectF(-5, 1.5, 10, 2))
        p.setPen(QtGui.QPen(col, 1.5))
        p.drawLine(QtCore.QPointF(0, 3.5), QtCore.QPointF(0, 9))
    return QtGui.QIcon(_pix(d))


def schloss_pixmap(gesperrt: bool, size: int = 16, farbe: Optional[str] = None) -> QtGui.QPixmap:
    col = QtGui.QColor(farbe or theme.farbe("text" if gesperrt else "text2"))

    def d(p):
        s = size / 16
        p.scale(s, s)
        p.setPen(QtGui.QPen(col, 1.8))
        p.setBrush(QtCore.Qt.NoBrush)
        if gesperrt:
            p.drawArc(QtCore.QRectF(4, 1.5, 8, 9), 0, 180 * 16)
            p.drawLine(QtCore.QPointF(4, 6), QtCore.QPointF(4, 8))
            p.drawLine(QtCore.QPointF(12, 6), QtCore.QPointF(12, 8))
        else:
            p.drawArc(QtCore.QRectF(7, 0.5, 8, 9), 0, 180 * 16)
            p.drawLine(QtCore.QPointF(15, 5), QtCore.QPointF(15, 7))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QtCore.QRectF(2.5, 7.5, 11, 8), 1.5, 1.5)
    return _pix(d, size)


def schloss_icon(gesperrt: bool) -> QtGui.QIcon:
    return QtGui.QIcon(schloss_pixmap(gesperrt))


def farb_icon(farbe: str, mittelwert: bool = False, size: int = 14) -> QtGui.QIcon:
    """Farbfeld eines Laufs (Farbe im aktiven Thema); mittelwert=True: mit Ø-Zeichen."""
    c = theme.laufarbe(farbe)

    def d(p):
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(c))
        p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), 2, 2)
        if mittelwert:
            f = p.font()
            f.setPixelSize(size - 3)
            f.setBold(True)
            p.setFont(f)
            hell = theme.leuchtdichte(c) > 0.35
            p.setPen(QtGui.QColor("#000000" if hell else "#ffffff"))
            p.drawText(QtCore.QRectF(0, 0, size, size), QtCore.Qt.AlignCenter, "Ø")
    return QtGui.QIcon(_pix(d, size))


# =============================================================================================== Seitenleiste
class _Streifen(QtWidgets.QAbstractButton):
    """Eingeklappte Leiste: 32 px breit, ▶ oben, senkrechter Titel, darunter Farbfelder (z.B. sichtbare Laeufe)."""
    BREITE = 32

    def __init__(self, text: str):
        super().__init__()
        self.setText(text)
        self.farben: List[str] = []
        self.setFixedWidth(self.BREITE)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        theme.bei_wechsel(self, lambda _t: self.update(), sofort=False)

    def sizeHint(self):
        return QtCore.QSize(self.BREITE, 200)

    def paintEvent(self, _ev):
        T = theme.tokens()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtGui.QColor(T["accent_border"] if self.underMouse() else T["border"]))
        p.setBrush(QtGui.QColor(T["panel"]))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor(T["text"]))
        p.drawText(QtCore.QRectF(0, 8, self.width(), 20), QtCore.Qt.AlignCenter, "▶")
        tw = p.fontMetrics().horizontalAdvance(self.text())
        p.save()
        p.translate(self.width() / 2 + 5, 34)
        p.rotate(90)
        p.drawText(QtCore.QPointF(0, 0), self.text())
        p.restore()
        y = 34 + tw + 12
        for c in self.farben:
            if y + 12 > self.height() - 4:
                break
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(theme.laufarbe(c)))
            p.drawRoundedRect(QtCore.QRectF(self.width() / 2 - 6, y, 12, 12), 2, 2)
            y += 16
        p.end()


class SeitenLeiste(QtWidgets.QWidget):
    """Linke Leiste mit Nachbar (z.B. Diagramm). Aufbau beim Aufrufer:
        leiste = SeitenLeiste("Läufe", inhalt=liste_widget, nachbar=diagramm, breite=372)
        leiste.set_angepinnt(settings.get("laeufe_angepinnt", False))
        leiste.angepinnt_geaendert.connect(lambda an: (settings.update(laeufe_angepinnt=an), speichern()))
        layout.addWidget(leiste)                 # statt des Nachbarn
        leiste.set_streifen_text("Läufe (3 von 6 sichtbar)"); leiste.set_streifen_farben([...])
    Nicht angepinnt: nur der Streifen; Maus 300 ms darauf -> Inhalt als Overlay ueber dem Nachbarn (verdeckt, schiebt
    nicht). Maus weg oder Klick daneben -> zu. Angepinnt: Inhalt links neben dem Nachbarn (Teiler verschiebbar)."""
    angepinnt_geaendert = QtCore.Signal(bool)
    offen_geaendert = QtCore.Signal(bool)          # Overlay auf/zu (nicht beim Anpinnen)
    VERZOEGERUNG_MS = 300                          # Verweilen auf dem Streifen bis zum Aufklappen
    ZU_MS = 300                                    # Maus ausserhalb, bis es zuklappt

    def __init__(self, titel: str, inhalt: QtWidgets.QWidget, nachbar: QtWidgets.QWidget, breite: int = 372,
                 angepinnt: bool = False, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.titel_text = titel
        self.breite = breite
        self.inhalt = inhalt
        self.nachbar = nachbar
        self._angepinnt = False
        self._offen = False
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.streifen = _Streifen(titel)
        self.streifen.setToolTip(f"{titel}: Maus darauf zeigt die Liste, Klick öffnet sie sofort")
        lay.addWidget(self.streifen)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        lay.addWidget(self.splitter, 1)
        # Feld mit Kopf (Titel, Zusatz, Anpinnen) und Inhalt; liegt angepinnt im Teiler, sonst als Overlay auf self
        self.feld = QtWidgets.QFrame()
        self.feld.setObjectName("seitenleiste")
        fl = QtWidgets.QVBoxLayout(self.feld)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.setSpacing(6)
        kopf = QtWidgets.QHBoxLayout()
        kopf.setSpacing(6)
        self.kopf_titel = QtWidgets.QLabel(titel)
        self.kopf_zusatz = QtWidgets.QLabel("")
        kopf.addWidget(self.kopf_titel)
        kopf.addWidget(self.kopf_zusatz)
        kopf.addStretch(1)
        self.kopf = kopf                           # weitere Knoepfe: leiste.kopf.insertWidget(…)
        self.pin = QtWidgets.QToolButton()
        self.pin.setCheckable(True)
        self.pin.setIconSize(QtCore.QSize(20, 20))
        self.pin.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.pin.toggled.connect(self.set_angepinnt)
        kopf.addWidget(self.pin)
        fl.addLayout(kopf)
        fl.addWidget(inhalt, 1)
        self.schatten = None
        self.splitter.addWidget(nachbar)
        self.feld.setParent(self)
        self.feld.hide()
        self.auf_timer = QtCore.QTimer(self)
        self.auf_timer.setSingleShot(True)
        self.auf_timer.setInterval(self.VERZOEGERUNG_MS)
        self.auf_timer.timeout.connect(self.oeffnen)
        self.zu_timer = QtCore.QTimer(self)
        self.zu_timer.setSingleShot(True)
        self.zu_timer.setInterval(self.ZU_MS)
        self.zu_timer.timeout.connect(self._zu_wenn_draussen)
        self.streifen.installEventFilter(self)
        self.feld.installEventFilter(self)
        self.streifen.clicked.connect(self.oeffnen)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self._pin_anzeigen()
        if angepinnt:
            self.set_angepinnt(True)

    # ------------------------------------------------------------ Darstellung
    def _stil(self):
        T = theme.tokens()
        rand = T["accent_border"] if not self._angepinnt else T["border"]
        breite = 2 if not self._angepinnt else 1
        self.feld.setStyleSheet(f"QFrame#seitenleiste {{ background: {T['menu'] if not self._angepinnt else T['panel']};"
                                f" border: {breite}px solid {rand}; border-radius: 5px; }}")
        self.kopf_titel.setStyleSheet(f"color: {T['text']}; font-size: 15px; font-weight: 700; background: transparent;")
        self.kopf_zusatz.setStyleSheet(f"color: {T['text2']}; font-size: 13px; background: transparent;")
        self.pin.setStyleSheet(f"QToolButton {{ color: {T['text']}; border: 1px solid {T['border']}; border-radius: 4px;"
                               f" padding: 2px 8px; font-size: 13px; background: {T['panel']}; }}"
                               f"QToolButton:checked {{ border-color: {T['accent_border']}; }}")
        self.pin.setIcon(pin_icon(self._angepinnt))
        self.streifen.update()

    def _pin_anzeigen(self):
        self.pin.blockSignals(True)
        self.pin.setChecked(self._angepinnt)
        self.pin.blockSignals(False)
        self.pin.setText("angepinnt" if self._angepinnt else "Anpinnen")
        self.pin.setToolTip("Lösen: Liste klappt wieder ein und liegt dann über dem Nachbarn" if self._angepinnt
                            else "Anpinnen: Liste bleibt offen und schiebt den Nachbarn zur Seite")
        self._stil()

    def set_streifen_text(self, text: str):
        """Senkrechter Text im eingeklappten Streifen, z.B. „Läufe (3 von 6 sichtbar)“."""
        self.streifen.setText(text)
        self.streifen.update()

    def set_streifen_farben(self, farben: Sequence[str]):
        """Farbfelder unter dem Text (z.B. die sichtbaren Laeufe)."""
        self.streifen.farben = list(farben)
        self.streifen.update()

    def set_zusatz(self, text: str):
        """Text neben dem Titel im Kopf (z.B. „3 von 6 sichtbar“)."""
        self.kopf_zusatz.setText(text)

    # ------------------------------------------------------------ Zustand
    def ist_angepinnt(self) -> bool:
        return self._angepinnt

    def ist_offen(self) -> bool:
        """Inhalt sichtbar (Overlay offen oder angepinnt)."""
        return self._angepinnt or self._offen

    def zustand(self) -> dict:
        """Zum Speichern: {"angepinnt": bool, "breite": int}."""
        return {"angepinnt": self._angepinnt, "breite": self.breite}

    def set_zustand(self, d: Optional[dict]):
        d = d or {}
        if d.get("breite"):
            self.breite = int(d["breite"])
        self.set_angepinnt(bool(d.get("angepinnt", False)))

    def set_angepinnt(self, an: bool):
        an = bool(an)
        if an == self._angepinnt:
            self._pin_anzeigen()
            return
        self.auf_timer.stop()
        self.zu_timer.stop()
        if an:
            self._schliessen(still=True)
            self.feld.setGraphicsEffect(None)
            self.schatten = None
            self.splitter.insertWidget(0, self.feld)
            self.feld.show()
            self.splitter.setSizes([self.breite, max(1, self.splitter.width() - self.breite)])
            self.streifen.hide()
        else:
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 50:
                self.breite = sizes[0]
            self.feld.hide()
            self.feld.setParent(self)              # aus dem Teiler: Nachbar bekommt die volle Breite
            self.streifen.show()
        self._angepinnt = an
        self._pin_anzeigen()
        self.angepinnt_geaendert.emit(an)

    def oeffnen(self):
        """Overlay oeffnen (nur nicht angepinnt)."""
        self.auf_timer.stop()
        if self._angepinnt or self._offen:
            return
        self._offen = True
        self.schatten = QtWidgets.QGraphicsDropShadowEffect(self.feld)
        self.schatten.setBlurRadius(30)
        self.schatten.setOffset(6, 0)
        self.schatten.setColor(QtGui.QColor(0, 0, 0, 90 if not theme.ist_nacht() else 170))
        self.feld.setGraphicsEffect(self.schatten)
        self._platzieren()
        self.feld.show()
        self.feld.raise_()
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._stil()
        self.zu_timer.start()                      # Maus gar nicht erst hinein: trotzdem wieder zu
        self.offen_geaendert.emit(True)

    def schliessen(self):
        self._schliessen()

    def _schliessen(self, still: bool = False):
        self.auf_timer.stop()
        self.zu_timer.stop()
        if not self._offen:
            return
        self._offen = False
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if not self._angepinnt:
            self.feld.hide()
        if not still:
            self.offen_geaendert.emit(False)

    def _platzieren(self):
        w = min(self.breite, max(120, self.width() - 40))
        self.feld.setGeometry(0, 0, w, self.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._offen and not self._angepinnt:
            self._platzieren()

    def _maus_drin(self) -> bool:
        pos = self.feld.mapFromGlobal(QtGui.QCursor.pos())
        return self.feld.rect().contains(pos)

    def _zu_wenn_draussen(self):
        """Maus weg: zu, ausser die Maus ist (wieder) drin oder ein Menue/Dialog aus der Liste ist offen."""
        if not self._offen or self._angepinnt:
            return
        app = QtWidgets.QApplication.instance()
        if self._maus_drin() or app.activePopupWidget() is not None or app.activeModalWidget() is not None:
            return
        self._schliessen()

    # ------------------------------------------------------------ Ereignisse
    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.streifen and not self._angepinnt:
            if t == QtCore.QEvent.Enter:
                self.auf_timer.start()
            elif t == QtCore.QEvent.Leave and not self._offen:
                self.auf_timer.stop()
        elif obj is self.feld and self._offen:
            if t == QtCore.QEvent.Enter:
                self.zu_timer.stop()
            elif t == QtCore.QEvent.Leave:
                self.zu_timer.start()
        if self._offen and not self._angepinnt and t == QtCore.QEvent.MouseButtonPress and isinstance(obj, QtWidgets.QWidget):
            app = QtWidgets.QApplication.instance()
            drin = obj is self.feld or self.feld.isAncestorOf(obj) or obj is self.streifen
            if not drin and app.activePopupWidget() is None and obj.window() is self.window():
                self._schliessen()                 # Klick daneben (z.B. in die Grafik)
        return False
