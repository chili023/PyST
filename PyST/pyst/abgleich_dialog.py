"""
Fenster „Übersetzung live abgleichen“ (Übersetzungsart von Hand): Motor auf konstanter Drehzahl halten und die
Übersetzung nKW/nRolle verstellen, bis PST dieselbe Motordrehzahl zeigt wie der Drehzahlmesser des Motorrads.

Links ein runder Drehzahlmesser (Drehzahlmesser, QPainter, Farben aus theme), in der Mitte die Motordrehzahl aus
Rolle × Übersetzung als grosse Zahl mit Rolle (1/min, km/h) und Zünddrehzahl zum Vergleich, rechts die Übersetzung
(direkt editierbar) zwischen den Pfeilknoepfen +1 / +0,1 / +0,05 und −0,05 / −0,1 / −1 (Auto-Repeat beim Halten).
Tasten: Pfeil hoch/runter ±0,05, Bild hoch/runter ±0,1, Strg+Pfeil ±1. Verstellt wird nur zwischen ABGLEICH_MIN und
ABGLEICH_MAX (schritt_wert, auf 3 Nachkommastellen gerundet).

Jede Aenderung geht sofort in das Feld „ratio“ der Rolle/Lauf-Seite (dieselbe Einstellung wie im Popup
„Lauf-Einstellungen …“, beide zeigen sie mit) und – ausserhalb eines Laufs – in die Parameter des RunControllers,
damit auch die Drehzahl-Kachel gleich mitrechnet. „Übernehmen“ stellt die Übersetzungsart auf „von Hand“, meldet
uebernommen(wert) (das Hauptfenster speichert) und schliesst; „Abbrechen“ (auch Esc/Schliessen) stellt den Wert vom
Oeffnen wieder her. Nicht modal: das Hauptfenster laeuft weiter und liefert die Live-Werte (ctrl.live).
"""
import math
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .kacheln import zahl
from .runner import ACTIVE

ABGLEICH_MIN, ABGLEICH_MAX = 0.5, 50.0      # weiter wird mit den Pfeilen/Tasten nicht verstellt
SCHRITTE = (1.0, 0.1, 0.05)                 # Pfeilknoepfe, von oben nach unten (darunter dieselben negativ)
SKALA_STANDARD = 14000.0                    # Drehzahlmesser bis, wenn das Fahrzeug nichts eingestellt hat
VERALTET_S = 1.0                            # so lange ohne Telegramm: keine Rollendrehzahl
N_ROLLE_MIN = 1.0                           # darunter steht die Rolle [1/min]
KEIN_BOARD = "Keine Rollendrehzahl – Messboard verbinden"
KEIN_SIGNAL = "Keine Rollendrehzahl – Rolle steht oder kein Signal vom Messboard"
ANLEITUNG = ("Motor auf konstanter Drehzahl halten, Übersetzung verstellen, bis PST dieselbe Drehzahl zeigt wie "
             "der Drehzahlmesser des Motorrads.")
TASTEN_HINWEIS = "Tasten: ↑/↓ ±0,05 · Bild ↑/↓ ±0,1 · Strg+↑/↓ ±1"

Zone = Tuple[float, float, str]


def schritt_wert(wert: float, d: float, lo: float = ABGLEICH_MIN, hi: float = ABGLEICH_MAX) -> float:
    """wert + d auf 3 Nachkommastellen (6,85 + 0,05 = 6,900, keine Gleitkommareste). An der Grenze bleibt es dort,
    jenseits der Grenze wird in diese Richtung nicht weiter verstellt."""
    wert = round(float(wert), 3)
    if d > 0:
        return wert if wert >= hi else min(round(wert + d, 3), hi)
    if d < 0:
        return wert if wert <= lo else max(round(wert + d, 3), lo)
    return wert


def tasten_schritt(key: int, mod) -> float:
    """Schritt zu einer Taste (0 = keine Abgleich-Taste)."""
    strg = bool(mod & QtCore.Qt.ControlModifier)
    if key == QtCore.Qt.Key_Up:
        return 1.0 if strg else 0.05
    if key == QtCore.Qt.Key_Down:
        return -1.0 if strg else -0.05
    if key == QtCore.Qt.Key_PageUp:
        return 0.1
    if key == QtCore.Qt.Key_PageDown:
        return -0.1
    return 0.0


def skala_bereich(hi_fahrzeug: Optional[float], zonen: Sequence[Zone]) -> Tuple[float, List[Zone]]:
    """(Skalenende, Zonen) des Drehzahlmessers: Maximum der Drehzahl-Kachel des Fahrzeugs, sonst SKALA_STANDARD;
    Zonen wie die Kachel, die oberste (bis zum alten Ende) reicht bis zum Skalenende."""
    hi = float(hi_fahrzeug) if hi_fahrzeug else SKALA_STANDARD
    zz = [(float(a), float(b), str(s)) for a, b, s in zonen if float(a) < hi]
    ende = max((b for _a, b, _s in zz), default=0.0)
    return hi, [(a, hi if b >= ende else min(b, hi), s) for a, b, s in zz]


# =============================================================================================== Drehzahlmesser
class Drehzahlmesser(QtWidgets.QWidget):
    """Runder Drehzahlmesser: 270°-Skala von 0 bis hi (Ziffern in 1000 1/min), Teilstriche, Zonen wie die
    Drehzahl-Kachel als Band am Rand, Zeiger. set_wert(None): kein Zeiger."""
    START, BOGEN = 225.0, 270.0               # Grad (mathematisch, 0 = rechts, gegen den Uhrzeiger)

    def __init__(self, hi: float = SKALA_STANDARD, zonen: Optional[Sequence[Zone]] = None):
        super().__init__()
        self.hi = float(hi)
        self.zonen: List[Zone] = list(zonen or [])
        self.wert: Optional[float] = None
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setAccessibleName("Drehzahlmesser")
        theme.bei_wechsel(self, lambda _t: self.update(), sofort=False)

    def set_bereich(self, hi: float, zonen: Optional[Sequence[Zone]] = None):
        self.hi = float(hi) if hi > 0 else SKALA_STANDARD
        self.zonen = [(float(a), float(b), str(s)) for a, b, s in (zonen or [])]
        self.update()

    def set_wert(self, wert: Optional[float]):
        wert = None if wert is None or not math.isfinite(wert) else float(wert)
        if wert != self.wert:
            self.wert = wert
            self.update()

    def winkel(self, v: float) -> float:
        """Winkel [Grad] zu einer Drehzahl (0 -> 225°, hi -> -45°), ausserhalb der Skala am Anschlag."""
        f = min(max(v / self.hi, 0.0), 1.0)
        return self.START - self.BOGEN * f

    def teilung(self) -> Tuple[float, float]:
        """(grosse, kleine Teilung) [1/min]: 1000/500, bei grossen Skalen 2000/1000, bei kleinen 500/250."""
        if self.hi > 16000:
            return 2000.0, 1000.0
        if self.hi < 5000:
            return 500.0, 250.0
        return 1000.0, 500.0

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(420, 420)

    def paintEvent(self, _ev):
        T = theme.tokens()
        nacht = theme.ist_nacht()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        d = min(self.width(), self.height()) - 4
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = d / 2.0

        def punkt(grad: float, rad: float) -> QtCore.QPointF:
            a = math.radians(grad)
            return QtCore.QPointF(cx + rad * math.cos(a), cy - rad * math.sin(a))

        # Zifferblatt
        p.setPen(QtGui.QPen(QtGui.QColor(T["border_strong"]), 2))
        p.setBrush(QtGui.QColor(T["panel"]))
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        # Band: Spur und Zonen
        band = max(6.0, r * 0.07)
        rb = r * 0.90
        box = QtCore.QRectF(cx - rb, cy - rb, 2 * rb, 2 * rb)

        def bogen(a: float, b: float, farbe: QtGui.QColor):
            wa, wb = self.winkel(a), self.winkel(b)
            pen = QtGui.QPen(farbe, band)
            pen.setCapStyle(QtCore.Qt.FlatCap)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawArc(box, int(round(wb * 16)), int(round((wa - wb) * 16)))

        bogen(0.0, self.hi, QtGui.QColor(T["track"]))
        for a, b, s in self.zonen:
            col = QtGui.QColor(T[theme.ZONEN.get(s, "zone_ok")])
            if not nacht and s == "warn":
                col.setAlpha(220)
            bogen(max(a, 0.0), min(b, self.hi), col)
        # Teilstriche und Ziffern
        gross, klein = self.teilung()
        p.setPen(QtGui.QPen(QtGui.QColor(T["text"]), max(2.0, r * 0.012)))
        n = int(round(self.hi / klein))
        f = p.font()
        f.setPixelSize(max(11, int(r * 0.12)))
        f.setBold(True)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        for i in range(n + 1):
            v = i * klein
            if v > self.hi + 1e-6:
                break
            ist_gross = abs(v / gross - round(v / gross)) < 1e-6
            w = self.winkel(v)
            aussen = r * 0.84
            innen = r * (0.72 if ist_gross else 0.78)
            p.setPen(QtGui.QPen(QtGui.QColor(T["text"] if ist_gross else T["text2"]),
                                max(2.0, r * (0.018 if ist_gross else 0.01))))
            p.drawLine(punkt(w, innen), punkt(w, aussen))
            if ist_gross:
                txt = zahl(v / 1000.0, 0 if gross >= 1000 else 1)
                c = punkt(w, r * 0.59)
                tw, th = fm.horizontalAdvance(txt), fm.ascent()
                p.setPen(QtGui.QColor(T["text"]))
                p.drawText(QtCore.QPointF(c.x() - tw / 2, c.y() + th / 2 - 1), txt)
        # Einheit in der Luecke unten
        f2 = p.font()
        f2.setPixelSize(max(10, int(r * 0.075)))
        f2.setBold(False)
        p.setFont(f2)
        p.setPen(QtGui.QColor(T["text2"]))
        txt = "× 1000 1/min"
        tw = QtGui.QFontMetricsF(f2).horizontalAdvance(txt)
        p.drawText(QtCore.QPointF(cx - tw / 2, cy + r * 0.62), txt)
        # Zeiger
        if self.wert is not None:
            w = math.radians(self.winkel(self.wert))
            ux, uy = math.cos(w), -math.sin(w)            # Richtung (Bildschirm, y nach unten)
            nx, ny = -uy, ux
            spitze, hinten, breit = r * 0.86, r * 0.16, max(3.0, r * 0.035)
            poly = QtGui.QPolygonF([
                QtCore.QPointF(cx + ux * spitze, cy + uy * spitze),
                QtCore.QPointF(cx + nx * breit, cy + ny * breit),
                QtCore.QPointF(cx - ux * hinten, cy - uy * hinten),
                QtCore.QPointF(cx - nx * breit, cy - ny * breit)])
            p.setPen(QtGui.QPen(QtGui.QColor(T["panel"]), 1))
            p.setBrush(QtGui.QColor(T["egt_color"]))
            p.drawPolygon(poly)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(T["text"]))
        p.drawEllipse(QtCore.QPointF(cx, cy), r * 0.07, r * 0.07)
        p.end()


# =============================================================================================== Dialog
class AbgleichDialog(QtWidgets.QDialog):
    """seite: SettingsPage (fields["ratio"], ratio_art). ctrl(): aktueller RunController oder None (Hauptfenster).
    hi, zonen: Skala des Drehzahlmessers (skala_bereich)."""
    uebernommen = QtCore.Signal(float)

    def __init__(self, parent, seite, ctrl: Callable[[], Any], hi: float = SKALA_STANDARD,
                 zonen: Optional[Sequence[Zone]] = None):
        super().__init__(parent)
        self.setWindowTitle("Übersetzung live abgleichen")
        self.setModal(False)
        self.seite = seite
        self.feld: QtWidgets.QDoubleSpinBox = seite.fields["ratio"]
        self.ctrl = ctrl
        self.start_wert = round(self.feld.value(), 3)
        self._ctrl_gesehen = None
        self._empfangen = (-1, 0.0)                # (link.received, Zeitpunkt der letzten Aenderung)
        self.n_motor: Optional[float] = None
        self._offen = True                          # bis done(): Feld „ratio“ verbunden

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        self.titel = QtWidgets.QLabel("Übersetzung live abgleichen")
        lay.addWidget(self.titel)
        self.anleitung = QtWidgets.QLabel(ANLEITUNG)
        self.anleitung.setWordWrap(True)
        lay.addWidget(self.anleitung)

        mitte = QtWidgets.QHBoxLayout()
        mitte.setSpacing(16)
        lay.addLayout(mitte, 1)
        self.messer = Drehzahlmesser(hi, zonen)
        mitte.addWidget(self.messer, 5)

        self.anzeige = QtWidgets.QFrame()
        self.anzeige.setObjectName("abgleich_anzeige")
        al = QtWidgets.QVBoxLayout(self.anzeige)
        al.setContentsMargins(18, 14, 18, 14)
        al.setSpacing(4)
        self.l_motor = QtWidgets.QLabel("Motordrehzahl · Rolle × Übersetzung")
        al.addWidget(self.l_motor)
        self.zahl = QtWidgets.QLabel("–")
        self.zahl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.zahl.setAccessibleName("Motordrehzahl")
        al.addWidget(self.zahl)
        self.einheit = QtWidgets.QLabel("1/min")
        self.einheit.setAlignment(QtCore.Qt.AlignRight)
        al.addWidget(self.einheit)
        al.addSpacing(10)
        self.rolle = QtWidgets.QLabel("Rolle  –")
        self.zuendung = QtWidgets.QLabel("Zündung  –")
        self.zuendung.setToolTip("Motordrehzahl aus dem Zündabnehmer des Messboards (zum Vergleich)")
        al.addWidget(self.rolle)
        al.addWidget(self.zuendung)
        self.hinweis = QtWidgets.QLabel("")
        self.hinweis.setWordWrap(True)
        al.addWidget(self.hinweis)
        al.addStretch(1)
        self.tasten = QtWidgets.QLabel(TASTEN_HINWEIS)
        self.tasten.setWordWrap(True)
        al.addWidget(self.tasten)
        mitte.addWidget(self.anzeige, 5)

        spalte = QtWidgets.QVBoxLayout()
        spalte.setSpacing(6)
        self.l_i = QtWidgets.QLabel("Übersetzung nKW/nRolle")
        self.l_i.setAlignment(QtCore.Qt.AlignCenter)
        spalte.addWidget(self.l_i)
        self.knoepfe: dict = {}
        for d in SCHRITTE:
            spalte.addWidget(self._knopf(d))
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(self.feld.minimum(), self.feld.maximum())
        self.spin.setDecimals(3)
        self.spin.setSingleStep(0.05)
        # Knoepfe des Felds per Stylesheet auf 0 px (NoButtons laesst mit dem Stylesheet der Anwendung kein Textfeld)
        self.spin.setAlignment(QtCore.Qt.AlignCenter)
        self.spin.setKeyboardTracking(False)
        self.spin.setAccessibleName("Übersetzung")
        self.spin.setToolTip("Übersetzung nKW/nRolle, direkt eintragen (Enter) oder mit den Pfeilen verstellen")
        self.spin.setMinimumSize(170, 64)
        self.spin.setValue(self.feld.value())
        self.spin.valueChanged.connect(self._spin_geaendert)
        self.spin.installEventFilter(self)
        self.spin.lineEdit().installEventFilter(self)
        spalte.addWidget(self.spin)
        for d in reversed(SCHRITTE):
            spalte.addWidget(self._knopf(-d))
        spalte.addStretch(1)
        mitte.addLayout(spalte, 0)

        unten = QtWidgets.QHBoxLayout()
        self.art_hinweis = QtWidgets.QLabel("Übernehmen speichert die Übersetzung und stellt die Übersetzungsart auf "
                                            "„von Hand“.")
        self.art_hinweis.setWordWrap(True)
        theme.rolle(self.art_hinweis, "neben")
        unten.addWidget(self.art_hinweis, 1)
        self.b_ok = QtWidgets.QPushButton("Übernehmen")
        theme.rolle(self.b_ok, "primaer")
        self.b_abbrechen = QtWidgets.QPushButton("Abbrechen")
        for b in (self.b_ok, self.b_abbrechen):
            b.setAutoDefault(False)                  # Enter im Feld traegt nur ein, schliesst nicht
            b.setDefault(False)
            b.setMinimumSize(140, 44)
            unten.addWidget(b)
        self.b_ok.clicked.connect(self.uebernehmen)
        self.b_abbrechen.clicked.connect(self.reject)
        lay.addLayout(unten)

        self.feld.valueChanged.connect(self._feld_geaendert)
        theme.bei_wechsel(self, lambda _t: self._stil())
        self.resize(1180, 660)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.takt)
        self.timer.start(50)
        self.takt()
        self.spin.setFocus()

    # ------------------------------------------------------------------ Aufbau
    def _knopf(self, d: float) -> QtWidgets.QPushButton:
        betrag = zahl(abs(d), 0 if abs(d) >= 1 else (1 if abs(d) >= 0.1 else 2))
        txt = ("▲  +" if d > 0 else "▼  −") + betrag
        b = QtWidgets.QPushButton(txt)
        b.setFocusPolicy(QtCore.Qt.NoFocus)          # Tasten bleiben beim Feld (Pfeil hoch/runter)
        b.setAutoDefault(False)
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(400)
        b.setAutoRepeatInterval(120)
        b.setMinimumSize(170, 52)
        taste = {1.0: "Strg+↑", 0.1: "Bild ↑", 0.05: "↑"}[abs(d)].replace("↑", "↑" if d > 0 else "↓")
        b.setToolTip(f"Übersetzung {'+' if d > 0 else '−'}{betrag} ({taste}, gedrückt halten: wiederholt)")
        b.clicked.connect(lambda _c=False, d=d: self.schritt(d))
        self.knoepfe[d] = b
        return b

    def _stil(self):
        T = theme.tokens()
        self.titel.setStyleSheet(f"color: {T['text']}; font-size: 22px; font-weight: 800;")
        self.anleitung.setStyleSheet(f"color: {T['text']}; font-size: 16px;")
        self.anzeige.setStyleSheet(f"QFrame#abgleich_anzeige {{ background: {T['panel']};"
                                   f" border: 1px solid {T['border']}; border-radius: 6px; }}")
        self.l_motor.setStyleSheet(f"color: {T['text2']}; font-size: 16px; font-weight: 700;")
        self.einheit.setStyleSheet(f"color: {T['text2']}; font-size: 24px; font-weight: 700;")
        for lab in (self.rolle, self.zuendung):
            lab.setStyleSheet(f"color: {T['text']}; font-size: 19px;")
        self.hinweis.setStyleSheet(f"color: {T['warn_fg']}; font-size: 17px; font-weight: 700;")
        self.tasten.setStyleSheet(f"color: {T['text2']}; font-size: 14px;")
        self.l_i.setStyleSheet(f"color: {T['text2']}; font-size: 15px; font-weight: 700;")
        self.spin.setStyleSheet(f"QDoubleSpinBox {{ font-size: 34px; font-weight: 800; color: {T['input_text']};"
                                f" background: {T['field']}; border: 2px solid {T['accent_border']};"
                                " border-radius: 5px; padding: 2px 6px; min-height: 54px; }"
                                " QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }"
                                " QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { image: none; width: 0px; }")
        for b in self.knoepfe.values():               # min-height: das Stylesheet der Anwendung setzt 20 px
            b.setStyleSheet("QPushButton { font-size: 22px; font-weight: 800; min-height: 44px; min-width: 150px; }")
        for b in (self.b_ok, self.b_abbrechen):
            b.setStyleSheet("QPushButton { font-size: 15px; font-weight: 700; min-height: 34px; min-width: 120px; }")
        self._zahl_stil()

    def _zahl_stil(self):
        T = theme.tokens()
        farbe = T["value"] if self.n_motor is not None else T["dim"]
        self.zahl.setStyleSheet(f"color: {farbe}; font-size: 112px; font-weight: 800;")

    # ------------------------------------------------------------------ Uebersetzung
    def wert(self) -> float:
        return round(self.spin.value(), 3)

    def schritt(self, d: float):
        if not self.spin.isEnabled():
            return
        neu = schritt_wert(self.wert(), d)
        if neu != self.wert():
            self.spin.setValue(neu)

    def _spin_geaendert(self, v: float):
        if not self._offen:
            return
        v = round(v, 3)
        if round(self.feld.value(), 3) != v:
            self.feld.setValue(v)                    # -> _feld_geaendert, Popup und Rolle/Lauf-Seite
        else:
            self._feld_geaendert(v)

    def _feld_geaendert(self, v: float):
        """Feld „ratio“ geaendert (hier, im Popup oder auf der Rolle/Lauf-Seite)."""
        if round(self.spin.value(), 3) != round(v, 3):
            self.spin.blockSignals(True)
            self.spin.setValue(v)
            self.spin.blockSignals(False)
        c = self.ctrl()
        if c is not None and c.state not in ACTIVE:
            c.params.ratio = round(v, 3)             # Drehzahl-Kachel (Rolle × i) rechnet gleich mit
        self.takt()

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress:
            d = tasten_schritt(ev.key(), ev.modifiers())
            if d:
                self.schritt(d)
                return True
        elif ev.type() == QtCore.QEvent.Wheel and obj is self.spin:   # Mausrad: ±0,05 mit denselben Grenzen
            dy = ev.angleDelta().y()
            if dy:
                self.schritt(0.05 if dy > 0 else -0.05)
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        d = tasten_schritt(ev.key(), ev.modifiers())
        if d:
            self.schritt(d)
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ Live
    def live(self) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
        """(n Rolle, v [km/h], n Zündung, Hinweis) aus ctrl.live; None = nicht vorhanden."""
        c = self.ctrl()
        if c is None:
            return None, None, None, KEIN_BOARD
        if c is not self._ctrl_gesehen:              # neu verbunden: Datenstrom an (wie beim Einmessen)
            self._ctrl_gesehen = c
            self._empfangen = (-1, 0.0)
            if c.state not in ACTIVE and not c.link.streaming():
                c.link.start()
        now = time.time()
        rec = getattr(c.link, "received", None)
        if rec != self._empfangen[0]:
            self._empfangen = (rec, now)
        lv = c.live
        fin = lambda x: x is not None and isinstance(x, (int, float)) and math.isfinite(x)
        if now - self._empfangen[1] > VERALTET_S or not fin(lv.n_roll) or lv.n_roll <= N_ROLLE_MIN:
            return None, None, None, KEIN_SIGNAL
        ign = lv.n_meas if fin(lv.n_meas) and lv.n_meas > 0 else None
        return lv.n_roll, (lv.v_kmh if fin(lv.v_kmh) else None), ign, ""

    def takt(self):
        n_roll, v, ign, hinweis = self.live()
        n = n_roll * self.wert() if n_roll is not None else None
        war = self.n_motor is not None
        self.n_motor = n
        self.zahl.setText(zahl(n, 0))
        if war != (n is not None):
            self._zahl_stil()
        self.messer.set_wert(n)
        self.rolle.setText("Rolle  –" if n_roll is None else
                           f"Rolle  {zahl(n_roll, 0)} 1/min · {zahl(v, 1)} km/h")
        self.zuendung.setText(f"Zündung  {zahl(ign, 0)} 1/min" if ign is not None else "Zündung  –")
        self.hinweis.setText(hinweis)
        c = self.ctrl()
        frei = c is None or c.state not in ACTIVE
        for w in [self.spin, self.b_ok] + list(self.knoepfe.values()):
            if w.isEnabled() != frei:
                w.setEnabled(frei)
        if not frei:
            self.hinweis.setText("Lauf läuft – Übersetzung erst danach verstellen")

    # ------------------------------------------------------------------ Schliessen
    def uebernehmen(self):
        if not self._offen:
            return
        self.spin.interpretText()
        v = self.wert()
        if round(self.feld.value(), 3) != v:
            self.feld.setValue(v)
        art = self.seite.ratio_art
        i = art.findData("hand")
        if i >= 0 and art.currentIndex() != i:
            art.setCurrentIndex(i)
        self.uebernommen.emit(v)
        self.accept()

    def reject(self):
        """Abbrechen, Esc, Fenster schliessen: Wert vom Oeffnen zurueck."""
        if self._offen and round(self.feld.value(), 3) != self.start_wert:
            self.feld.setValue(self.start_wert)
        super().reject()

    def done(self, r):
        self.timer.stop()
        if self._offen:
            self._offen = False
            self.feld.valueChanged.disconnect(self._feld_geaendert)
        super().done(r)
