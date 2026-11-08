"""
Kleine Bausteine der Einstellungen (Entwurf 08.11.2026, Runde 3): Zustandschip, Kopfkarte mit Balken, Kippschalter,
Hinweisflaeche. Alle Farben kommen aus dem Thema (theme.py) und folgen einem Wechsel Tag/Nacht.
"""
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

NEUTRAL, AKZENT = "neutral", "akzent"
SYMBOL = {theme.GUT: "✔", theme.WARNUNG: "⚠", theme.FEHLER: "✖", theme.AUS: "–", NEUTRAL: "–"}


def chip_farben(stufe: str, thema: Optional[str] = None):
    """(Hintergrund, Schrift) einer Zustandsflaeche: ok/warn/err/aus, neutral (grau) oder akzent (blau)."""
    T = theme.tokens(thema)
    if stufe in (theme.GUT, theme.WARNUNG, theme.FEHLER):
        bg, fg, _s = theme.status_farben(stufe, thema)
        return bg, fg
    if stufe == AKZENT:
        return T["accent"], T["accent_text"]
    return T["track"], T["text"]


class Chip(QtWidgets.QLabel):
    """Farbiges Schildchen, z. B. „✔ erkannt · USB“, „neu“, „Update (USB)“."""

    def __init__(self, text: str = "", stufe: str = NEUTRAL, groesse: int = 13, breit: bool = False):
        super().__init__(text)
        self.stufe = stufe
        self.groesse = groesse
        self.breit = breit
        if not breit:
            self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.setWordWrap(breit)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def set_zustand(self, stufe: str, text: Optional[str] = None):
        if text is not None:
            self.setText(text)
        if stufe != self.stufe:
            self.stufe = stufe
            self._stil()

    def _stil(self):
        bg, fg = chip_farben(self.stufe)
        pad = "6px 8px" if self.breit else "3px 8px"
        self.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; font-size: {self.groesse}px; font-weight: 700;"
                           f" border-radius: 4px; padding: {pad}; }}")


def hinweis(text: str, stufe: str = theme.WARNUNG) -> Chip:
    """Breite Hinweisflaeche (gelb/rot), z. B. „Zange per USB anschließen – vorher vom Zündkabel nehmen“."""
    return Chip(text, stufe, 13, breit=True)


class Balken(QtWidgets.QProgressBar):
    """Schmaler Balken 0…100 in Zonenfarbe (Signal, Akku, Log)."""

    def __init__(self):
        super().__init__()
        self.setRange(0, 100)
        self.setTextVisible(False)
        self.setFixedHeight(8)
        self.zone = "zone_ok"
        theme.bei_wechsel(self, lambda _t: self._stil())

    def set_balken(self, wert: Optional[float], zone: str = "zone_ok"):
        self.setVisible(wert is not None)
        if wert is not None:
            self.setValue(int(max(0, min(100, wert))))
        if zone != self.zone:
            self.zone = zone
            self._stil()

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(f"QProgressBar {{ background: {T['track']}; border: none; border-radius: 3px; }}"
                           f"QProgressBar::chunk {{ background: {T[self.zone]}; border-radius: 3px; }}")


class Karte(QtWidgets.QFrame):
    """Kopfkarte (Zange: Verbindung, Signal, Akku …): Titel, Wert groß mit Einheit, Balken, Zeile darunter."""

    def __init__(self, titel: str, balken: bool = False):
        super().__init__()
        self.setObjectName("einstKarte")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 6)
        lay.setSpacing(2)
        self.titel = QtWidgets.QLabel(titel)
        theme.rolle(self.titel, "neben")
        lay.addWidget(self.titel)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.symbol = QtWidgets.QLabel("")
        self.symbol.hide()
        self.wert = QtWidgets.QLabel("–")
        self.einheit = QtWidgets.QLabel("")
        theme.rolle(self.einheit, "neben")
        row.addWidget(self.symbol)
        row.addWidget(self.wert)
        row.addWidget(self.einheit, 0, QtCore.Qt.AlignBottom)
        row.addStretch(1)
        lay.addLayout(row)
        self.balken = Balken()
        self.balken.setVisible(False)
        self.mit_balken = balken
        lay.addWidget(self.balken)
        self.unten = QtWidgets.QLabel("")
        theme.rolle(self.unten, "neben")
        self.unten.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.unten)
        self.chip = Chip("", theme.WARNUNG, 12)
        self.chip.hide()
        lay.addWidget(self.chip)
        lay.addStretch(1)
        self.stufe = ""
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.setStyleSheet(f"QFrame#einstKarte {{ background: {T['panel']}; border: 1px solid {T['border']};"
                           " border-radius: 5px; }")
        farbe = {theme.GUT: T["zone_ok"], theme.WARNUNG: T["zone_warn"], theme.FEHLER: T["zone_err"]}.get(
            self.stufe, T["dim"])
        self.symbol.setStyleSheet(f"color: {farbe}; font-size: 18px; font-weight: 700;")
        wert = T["dim"] if self.stufe == theme.AUS else T["value"]
        self.wert.setStyleSheet(f"color: {wert}; font-size: 21px; font-weight: 700;")

    def set_wert(self, wert: str, einheit: str = "", unten: str = "", stufe: str = "",
                 balken: Optional[float] = None, zone: str = "zone_ok", chip: str = ""):
        self.wert.setText(wert)
        self.einheit.setText(einheit)
        self.unten.setText(unten)
        self.unten.setVisible(bool(unten) and not chip)
        self.chip.setText(chip)
        self.chip.setVisible(bool(chip))
        self.symbol.setText(SYMBOL.get(stufe, "") if stufe in (theme.GUT, theme.WARNUNG, theme.FEHLER, theme.AUS) else "")
        self.symbol.setVisible(bool(self.symbol.text()))
        if self.mit_balken:
            self.balken.set_balken(balken, zone)
        if stufe != self.stufe:
            self.stufe = stufe
            self._stil()

    def text(self) -> str:
        """Wert und Einheit (Tests, Statusleiste)."""
        return f"{self.wert.text()} {self.einheit.text()}".strip()


class Schalter(QtWidgets.QAbstractButton):
    """Kippschalter (Expertenmodus): an = Akzentfarbe, Knopf rechts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(46, 24)
        theme.bei_wechsel(self, lambda _t: self.update(), sofort=False)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(46, 24)

    def paintEvent(self, _ev):
        T = theme.tokens()
        on = self.isChecked()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(1, 1, 44, 22)
        p.setPen(QtGui.QPen(QtGui.QColor(T["accent"] if on else T["border_strong"]), 1.5))
        p.setBrush(QtGui.QColor(T["accent"] if on else T["track"]))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(T["accent_text"] if on else T["border_strong"]))
        p.drawEllipse(QtCore.QRectF(25 if on else 4, 4, 16, 16))
        p.end()


def gross_knopf(text: str, primaer: bool = False) -> QtWidgets.QPushButton:
    """Großer Knopf der Firmware-Karten („Aktualisieren“, „Flashen“, „Aktuell ✔“)."""
    b = QtWidgets.QPushButton(text)
    b.setMinimumSize(210, 34)
    b.setStyleSheet("font-size: 17px; font-weight: 700;")
    if primaer:
        theme.rolle(b, "primaer")
    return b


def setze_primaer(b: QtWidgets.QPushButton, primaer: bool):
    """Knopf zwischen Primär (blau) und normal umschalten, Stylesheet der Anwendung neu anwenden."""
    neu = "primaer" if primaer else ""
    if (b.property("rolle") or "") != neu:
        theme.rolle(b, neu)


class AutoLabel(QtWidgets.QLabel):
    """Hinweiszeile, die ohne Text verschwindet (kein leerer Abstand)."""

    def setText(self, text: str):
        super().setText(text)
        self.setVisible(bool(text))


def abschnitt(text: str) -> QtWidgets.QLabel:
    """Fette Zwischenzeile in einer Gruppe („per Funk (über die Basis)“)."""
    l = QtWidgets.QLabel(text)
    theme.rolle(l, "titel")
    return l


def neben(text: str, wrap: bool = True) -> QtWidgets.QLabel:
    l = QtWidgets.QLabel(text)
    l.setWordWrap(wrap)
    theme.rolle(l, "neben")
    return l
