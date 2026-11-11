"""
Statusleiste unten (44 px): drei farbige Felder „PST Basis“ | „PST Zange/Mega“ | „PST/Lambda ECU“, je mit Symbol
(✔ ⚠ ✖ –), Geraetename und Text – die Farbe steht nie allein. Rechts Kurzmeldungen, der Knopf „PDF-Bericht“
(PdfKnopf: Bericht von dem, was gerade im Diagramm steht, Signal Statusleiste.pdf_gewuenscht), daneben „Lauf speichern“
(SpeichernKnopf, Ctrl+S: nur aktiv mit ungespeichertem Lauf, Signal speichern_gewuenscht) und der Umschalter
☀ | ☾ | ◐ (Tag, Nacht, Automatisch) als kleine Piktogramm-Knoepfe.

Ablauf: Das Hauptfenster sammelt reihum eine Momentaufnahme (Eingang) aus Verbindung, Zange, Lambda und ECU,
zustaende(eingang) macht daraus {Feld: (Stufe, Text)} (rein, ohne Qt testbar), Statusleiste.set_zustaende zeigt es.
anzeige(zustaende) liefert dazu Farben und Symbole (rein).
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

# (Schluessel, Geraetename) der drei Felder
FELDER: List[Tuple[str, str]] = [("basis", "PST Basis"), ("zange", "PST Zange/Mega"), ("ecu", "PST/Lambda ECU")]
HOEHE = 44
_RANG = {theme.AUS: 0, theme.GUT: 1, theme.WARNUNG: 2, theme.FEHLER: 3}
RSSI_SCHWACH = -80.0                   # dBm: darunter Warnung
AKKU_LEER_PCT = 20.0


@dataclass
class Eingang:
    """Momentaufnahme fuer die Statusleiste (alles optional, Standard = nichts verbunden)."""
    verbunden: bool = False            # Messboard (Basis/Mega/F446) offen
    board: str = ""                    # "basis" | "mega" | "f446" | "zange_usb"
    weg: str = ""                      # Verbindung weg: Text (z.B. "Verbindung weg, PyST verbindet neu …")
    anmeldung_fehler: str = ""         # Anmeldung an der Basis abgelehnt
    port: str = ""                     # Port des Messboards (SIM…, tcp://…, /dev/…, COM3)
    fw: str = ""                       # Firmware-Version bzw. erste Zeile der Antwort auf 'v'
    hz: float = 0.0                    # aktuelle Messfrequenz (0 = kein Datenstrom)
    bootloader: bool = False
    zange_state: str = ""              # Signal von hw_page: ok | weg | usb | "" (unbekannt)
    zange: Optional[object] = None     # basis.ZangeStatus der Hauptzange
    zangen_weitere: int = 0
    lam: Optional[object] = None       # basis.LambdaStatus (None = keine Basis/unbekannt)
    ecu: Optional[dict] = None         # None = keine ECU; sonst {"fehler": str, "ini": bool, "passt": bool, "hz": float}


def port_art(port: str) -> str:
    p = (port or "").lower()
    if p.startswith("sim"):
        return "Simulator"
    if p.startswith("tcp://"):
        return "WLAN"
    return "USB" if p else ""


def _fw(fw: str) -> str:
    """Kurzform der Firmware fuer die Statusleiste: nur die Versionsnummer, sonst der bereinigte Text."""
    fw = (fw or "").strip().strip(";,").strip()
    m = re.search(r"\d+\.\d+(?:\.\d+)?", fw)
    if m:
        return "FW " + m.group(0)
    fw = fw[:24]
    return f"FW {fw}" if fw and not fw.lower().startswith("fw") else fw


def _zahl(x: float, dez: int = 0) -> str:
    return f"{x:.{dez}f}".replace(".", ",")


def _ok(x) -> bool:
    return x is not None and x == x


def basis_zustand(e: Eingang) -> Tuple[str, str]:
    if e.anmeldung_fehler:
        return theme.FEHLER, "Anmeldung an der Basis gescheitert"
    if e.weg and not e.verbunden:
        return theme.WARNUNG, e.weg
    if not e.verbunden:
        return theme.FEHLER, "nicht verbunden"
    if e.board in ("mega", "f446"):
        return theme.AUS, "nicht benutzt – Messboard " + ("Arduino Mega" if e.board == "mega" else "STM32 (F446)")
    if e.board == "zange_usb":
        return theme.AUS, "nicht verbunden (Zange per USB)"
    if e.bootloader:
        return theme.WARNUNG, "im Bootloader – Firmware unter Einstellungen › Software & Firmware aufspielen"
    teile = ["verbunden " + port_art(e.port)]
    if e.fw:
        teile.append(_fw(e.fw))
    if e.hz > 0.5:
        teile.append(f"{e.hz:.0f} Hz")
    return theme.GUT, " · ".join(t.strip() for t in teile)


def zange_zustand(e: Eingang) -> Tuple[str, str]:
    if e.verbunden and e.board in ("mega", "f446"):
        name = "Arduino Mega" if e.board == "mega" else "STM32 (F446)"
        teile = [f"{name} verbunden {port_art(e.port)}".strip()]
        if e.fw:
            teile.append(_fw(e.fw))
        if e.hz > 0.5:
            teile.append(f"{e.hz:.0f} Hz")
        return theme.GUT, " · ".join(teile)
    if e.board == "zange_usb" or e.zange_state == "usb":
        return theme.WARNUNG, "Zange per USB – nur Auslesen/Testen, nicht am laufenden Motor"
    if not e.verbunden:
        return theme.AUS, "keine Basis verbunden"
    z = e.zange
    if e.zange_state == "weg" or (z is not None and not getattr(z, "connected", True)):
        return theme.FEHLER, "nicht verbunden – Zange aus oder nicht gekoppelt"
    if e.zange_state != "ok" or z is None:
        return theme.AUS, "Zange wird abgefragt …"
    stufe = theme.GUT
    nr = f"Zange {z.id}" if getattr(z, "id", 0) else "Zange"
    rssi = getattr(z, "rssi", float("nan"))
    teile = [f"{nr}: Funk ok" + (f" ({_zahl(rssi)} dBm)".replace("-", "−") if _ok(rssi) else "")]
    if _ok(rssi) and rssi < RSSI_SCHWACH:
        stufe = theme.WARNUNG
        teile[0] = f"{nr}: Funk schwach ({_zahl(rssi)} dBm)".replace("-", "−")
    mv, pct = getattr(z, "akku_mv", float("nan")), getattr(z, "akku_pct", float("nan"))
    if _ok(mv):
        teile.append(f"Akku {_zahl(mv / 1000, 1)} V")
    elif _ok(pct):
        teile.append(f"Akku {pct:.0f} %")
    if _ok(pct) and pct < AKKU_LEER_PCT and not getattr(z, "vbus", False):
        stufe = theme.WARNUNG
        teile.append("laden!")
    if not getattr(z, "sync_ok", True):
        stufe = theme.WARNUNG
        teile.append("kein Sync")
    if e.zangen_weitere:
        teile.append(f"+{e.zangen_weitere} weitere")
    return stufe, " · ".join(teile)


def lambda_teil(lam) -> Tuple[str, str]:
    """(Stufe, Text) der Lambda-Heizung; ("", "") = unbekannt/keine Basis."""
    if lam is None or not getattr(lam, "known", False):
        return "", ""
    if lam.low_voltage:
        return theme.FEHLER, "Lambda: 12 V zu niedrig"
    rest = getattr(lam, "rest_s", 0)
    if lam.st == "heizt":
        return theme.GUT, "Lambda heizt"
    if lam.st == "scharf":
        return theme.WARNUNG, f"Lambda bereit · aus in {rest // 60}:{rest % 60:02d}"
    return theme.AUS, "Lambda aus"


def ecu_teil(ecu: Optional[dict]) -> Tuple[str, str]:
    if ecu is None:
        return theme.AUS, "ECU nicht verbunden"
    if ecu.get("fehler"):
        return theme.FEHLER, f"ECU Fehler – {ecu['fehler']}"
    if not ecu.get("ini", True):
        return theme.WARNUNG, "ECU verbunden – keine passende .ini"
    if not ecu.get("passt", True):
        return theme.WARNUNG, "ECU: .ini passt nicht zur Firmware"
    hz = ecu.get("hz", 0.0) or 0.0
    return theme.GUT, "ECU verbunden" + (f" · {hz:.0f} Hz" if hz > 0.5 else "")


def schlimmste(*stufen: str) -> str:
    """Schwerste Stufe (err > warn > ok > aus); leere Stufen zaehlen nicht."""
    da = [s for s in stufen if s]
    return max(da, key=lambda s: _RANG.get(s, 0)) if da else theme.AUS


def lambda_ecu_zustand(e: Eingang) -> Tuple[str, str]:
    ls, lt = lambda_teil(e.lam)
    es, et = ecu_teil(e.ecu)
    stufe = schlimmste(ls, es)
    return stufe, " · ".join(t for t in (lt, et) if t)


def zustaende(e: Eingang) -> Dict[str, Tuple[str, str]]:
    """Momentaufnahme -> {Feld: (Stufe, Text)} fuer die drei Felder."""
    return {"basis": basis_zustand(e), "zange": zange_zustand(e), "ecu": lambda_ecu_zustand(e)}


@dataclass
class FeldAnzeige:
    schluessel: str
    name: str
    stufe: str
    symbol: str
    text: str
    hintergrund: str
    schrift: str


def anzeige(zust: Dict[str, Tuple[str, str]], thema: Optional[str] = None) -> List[FeldAnzeige]:
    """{Feld: (Stufe, Text)} -> Anzeige je Feld mit Farben und Symbol aus dem Thema (fehlendes Feld = aus, „–“)."""
    out = []
    for key, name in FELDER:
        stufe, text = zust.get(key, (theme.AUS, "–"))
        stufe = stufe if stufe in theme.STUFEN else theme.AUS
        bg, fg, sym = theme.status_farben(stufe, thema)
        out.append(FeldAnzeige(key, name, stufe, sym, text, bg, fg))
    return out


# =============================================================================================== Widgets
def kurz_text(text: str, fm: QtGui.QFontMetrics, platz: int) -> str:
    """Text fuer platz Pixel: passt er nicht, fallen hintere Teile „ · …“ ganz weg (kein halber Wert), erst zuletzt
    wird mit „…“ gekuerzt. Der volle Text steht im Tooltip."""
    if platz <= 0 or fm.horizontalAdvance(text) <= platz:
        return text
    teile = text.split(" · ")
    for n in range(len(teile) - 1, 0, -1):
        t = " · ".join(teile[:n]) + " …"
        if fm.horizontalAdvance(t) <= platz:
            return t
    return fm.elidedText(teile[0], QtCore.Qt.ElideRight, platz)


class StatusFeld(QtWidgets.QFrame):
    """Farbige Flaeche mit Symbol, Name (fett) und Text. Auch als Laufzustand neben START verwendbar:
    StatusFeld(name="") und set_zustand("warn", "GAS WEG")."""

    def __init__(self, name: str = "", hoehe: int = 36, schrift_px: int = 14):
        super().__init__()
        self.setObjectName("statusfeld")
        self.stufe, self.name, self.schrift_px = theme.AUS, name, schrift_px
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 2, 10, 2)
        lay.setSpacing(8)
        self.symbol = QtWidgets.QLabel("–")
        self.name_label = QtWidgets.QLabel(name)
        self.name_label.setVisible(bool(name))
        self.text_label = QtWidgets.QLabel("")
        self.text_label.setTextFormat(QtCore.Qt.PlainText)
        self.text_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.text_label.installEventFilter(self)
        self._voll = ""
        lay.addWidget(self.symbol)
        lay.addWidget(self.name_label)
        lay.addWidget(self.text_label, 1)
        self.setFixedHeight(hoehe)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumWidth(120)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def set_zustand(self, stufe: str, text: str):
        stufe = stufe if stufe in theme.STUFEN else theme.AUS
        if (stufe, text) == (self.stufe, self._voll) and self.styleSheet():
            return
        self.stufe = stufe
        self._voll = text
        self.setToolTip(f"{self.name}: {text}" if self.name else text)
        self._stil()

    def text(self) -> str:
        """Voller Text (angezeigt wird er bei wenig Platz gekuerzt, siehe kurz_text)."""
        return self._voll

    def _kuerzen(self):
        f = QtGui.QFont(self.text_label.font())
        f.setPixelSize(self.schrift_px)
        f.setWeight(QtGui.QFont.DemiBold)
        self.text_label.setText(kurz_text(self._voll, QtGui.QFontMetrics(f), self.text_label.width()))

    def eventFilter(self, obj, ev):
        if obj is self.text_label and ev.type() == QtCore.QEvent.Resize:
            self._kuerzen()
        return super().eventFilter(obj, ev)

    def _stil(self):
        bg, fg, sym = theme.status_farben(self.stufe)
        self.symbol.setText(sym)
        px = self.schrift_px
        self.setStyleSheet(f"QFrame#statusfeld {{ background: {bg}; border-radius: 4px; }}"
                           f"QLabel {{ color: {fg}; background: transparent; font-size: {px}px; }}")
        self.symbol.setStyleSheet(f"font-size: {px + 6}px; font-weight: 800;")
        self.name_label.setStyleSheet("font-weight: 800;")
        self.text_label.setStyleSheet("font-weight: 600;")
        self._kuerzen()


# Piktogramme des Umschalters: (Wahl, Zeichen, Tooltip)
THEMA_KNOEPFE = ((theme.TAG, "☀", "Tag (hell)"), (theme.NACHT, "☾", "Nacht (dunkel)"),
                 (theme.AUTO, "◐", "Automatisch (folgt dem Farbschema des Systems)"))
KNOPF_PX = 30                          # Breite eines Piktogramm-Knopfs


class ThemaUmschalter(QtWidgets.QWidget):
    """☀ | ☾ | ◐ (Tag, Nacht, Automatisch) als kleine Piktogramm-Knoepfe – setzt theme.manager().setze(wahl);
    die gespeicherte Wahl ist hervorgehoben (Akzentflaeche), der Text steht im Tooltip."""

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.knoepfe: Dict[str, QtWidgets.QToolButton] = {}
        self.gruppe = QtWidgets.QButtonGroup(self)
        self.gruppe.setExclusive(True)
        for wahl, zeichen, text in THEMA_KNOEPFE:
            b = QtWidgets.QToolButton()
            b.setText(zeichen)
            b.setCheckable(True)
            b.setFixedSize(KNOPF_PX, HOEHE - 12)
            b.setToolTip(f"Darstellung: {text} – auch im Menü Ansicht › Darstellung")
            b.setAccessibleName(f"Darstellung {text}")
            b.clicked.connect(lambda _c=False, w=wahl: theme.manager().setze(w))
            self.gruppe.addButton(b)
            self.knoepfe[wahl] = b
            lay.addWidget(b)
        theme.verbinde(theme.manager().wahl_geaendert, self, self._zeigen)
        theme.bei_wechsel(self, lambda _t: self._zeigen())

    def _zeigen(self, *_a):
        T = theme.tokens()
        wahl = theme.manager().wahl
        keys = list(self.knoepfe)
        for i, (key, b) in enumerate(self.knoepfe.items()):
            on = key == wahl
            b.setChecked(on)
            rad = ("border-top-left-radius: 4px; border-bottom-left-radius: 4px;" if i == 0 else
                   "border-top-right-radius: 4px; border-bottom-right-radius: 4px;" if i == len(keys) - 1 else "")
            b.setStyleSheet(f"QToolButton {{ background: {T['accent'] if on else T['panel']};"
                            f" color: {T['accent_text'] if on else T['text']};"
                            f" border: 1px solid {T['accent_border'] if on else T['border']};"
                            f" padding: 0px; font-size: 16px; font-weight: {800 if on else 500}; {rad} }}")


def pdf_symbol(farbe: str, px: int = 18) -> QtGui.QIcon:
    """Kleines Blatt mit Eselsohr und Zeilen (Symbol des Knopfs „PDF-Bericht“) in der Schriftfarbe."""
    pm = QtGui.QPixmap(px * 2, px * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(farbe), 1.4)
    p.setPen(pen)
    w, h, ohr = px * 0.62, px * 0.84, px * 0.22
    x0, y0 = (px - w) / 2, (px - h) / 2
    blatt = QtGui.QPolygonF([QtCore.QPointF(x0, y0), QtCore.QPointF(x0 + w - ohr, y0),
                             QtCore.QPointF(x0 + w, y0 + ohr), QtCore.QPointF(x0 + w, y0 + h),
                             QtCore.QPointF(x0, y0 + h)])
    p.drawPolygon(blatt)
    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x0 + w - ohr, y0), QtCore.QPointF(x0 + w - ohr, y0 + ohr),
                                    QtCore.QPointF(x0 + w, y0 + ohr)]))
    for k in range(3):
        y = y0 + h * (0.45 + 0.17 * k)
        p.drawLine(QtCore.QPointF(x0 + w * 0.2, y), QtCore.QPointF(x0 + w * (0.8 if k < 2 else 0.55), y))
    p.end()
    return QtGui.QIcon(pm)


class PdfKnopf(QtWidgets.QToolButton):
    """„PDF-Bericht“ mit Blatt-Symbol: Bericht von dem, was gerade im Diagramm zu sehen ist (das Hauptfenster
    entscheidet je Reiter, gui.on_pdf_aktiv)."""

    def __init__(self):
        super().__init__()
        self.setText("PDF-Bericht")
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setFixedHeight(HOEHE - 12)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("PDF-Bericht von dem, was gerade im Diagramm zu sehen ist: Läufe, Referenzen und "
                        "Mittelwerte (auch Ø automatisch), Achsgrenzen, Kurven, Fahrzeug/Setup, Klima, Ergebnisse.\n"
                        "Im Reiter Auswertung deren Felder, sonst das Diagramm aus Messen.")
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        T = theme.tokens()
        self.setIcon(pdf_symbol(T["text"]))
        self.setIconSize(QtCore.QSize(18, 18))
        self.setStyleSheet(f"QToolButton {{ background: {T['panel']}; color: {T['text']}; border: 1px solid "
                           f"{T['border']}; border-radius: 4px; padding: 0px 8px 0px 4px; font-size: 13px; "
                           f"font-weight: 600; }} QToolButton:hover {{ border-color: {T['accent_border']}; }}")


def speichern_symbol(farbe: str, px: int = 18) -> QtGui.QIcon:
    """Diskette mit Haekchen (Symbol des Knopfs „Lauf speichern“) in der Schriftfarbe."""
    pm = QtGui.QPixmap(px * 2, px * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtGui.QPen(QtGui.QColor(farbe), 1.4))
    s, ecke = px * 0.78, px * 0.2
    x0 = y0 = (px - s) / 2
    p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x0, y0), QtCore.QPointF(x0 + s - ecke, y0),
                                   QtCore.QPointF(x0 + s, y0 + ecke), QtCore.QPointF(x0 + s, y0 + s),
                                   QtCore.QPointF(x0, y0 + s)]))
    p.drawRect(QtCore.QRectF(x0 + s * 0.25, y0, s * 0.42, s * 0.28))          # Schieber oben
    pen = QtGui.QPen(QtGui.QColor(farbe), 1.8)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x0 + s * 0.27, y0 + s * 0.66),                # Haekchen
                                    QtCore.QPointF(x0 + s * 0.44, y0 + s * 0.82),
                                    QtCore.QPointF(x0 + s * 0.75, y0 + s * 0.47)]))
    p.end()
    return QtGui.QIcon(pm)


class SpeichernKnopf(QtWidgets.QToolButton):
    """„Lauf speichern“ mit Disketten-Symbol neben „PDF-Bericht“: aktiv nur, wenn eine ungespeicherte Kurve da ist
    (set_aktiv), sonst grau mit Tooltip „Kein ungespeicherter Lauf“. Schmale Statusleiste: nur „Speichern“."""
    TEXT, TEXT_KURZ = "Lauf speichern", "Speichern"
    TIP_AN = "Letzten Lauf speichern (Ctrl+S) – mit Notiz und Setup-Werten, danach steht er in der Liste Läufe"
    TIP_AUS = "Kein ungespeicherter Lauf"

    def __init__(self):
        super().__init__()
        self.setText(self.TEXT)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setFixedHeight(HOEHE - 12)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAccessibleName("Lauf speichern")
        self.set_aktiv(False)
        theme.bei_wechsel(self, lambda _t: self._stil())

    def set_aktiv(self, an: bool):
        """Aktiv/grau mit passendem Tooltip (laeuft im Takt des Laufs mit: nur bei einer Aenderung neu gestalten)."""
        if bool(an) == self.isEnabled() and self.styleSheet():
            return
        self.setEnabled(bool(an))
        self.setToolTip(self.TIP_AN if an else self.TIP_AUS)
        self._stil()

    def set_kurz(self, kurz: bool):
        t = self.TEXT_KURZ if kurz else self.TEXT
        if self.text() != t:
            self.setText(t)

    def _stil(self):
        T = theme.tokens()
        an = self.isEnabled()
        self.setIcon(speichern_symbol(T["text"] if an else T["dim"]))
        self.setIconSize(QtCore.QSize(18, 18))
        self.setStyleSheet(f"QToolButton {{ background: {T['panel']}; color: {T['text']}; border: 1px solid "
                           f"{T['accent_border'] if an else T['border']}; border-radius: 4px; padding: 0px 8px 0px 4px;"
                           f" font-size: 13px; font-weight: {700 if an else 600}; }}"
                           f" QToolButton:disabled {{ color: {T['dim']}; }}"
                           f" QToolButton:hover {{ border-color: {T['accent_border']}; }}")


class Statusleiste(QtWidgets.QWidget):
    """Inhalt der Statusleiste des Hauptfensters: drei StatusFelder, Kurzmeldung, PdfKnopf, ThemaUmschalter.
    einbauen(statusbar): als dauerhaftes Widget einsetzen; Kurzmeldungen (showMessage) erscheinen rechts.
    pdf_gewuenscht: Knopf „PDF-Bericht“ gedrueckt (das Hauptfenster erzeugt den Bericht); speichern_gewuenscht:
    Knopf „Lauf speichern“ gedrueckt."""
    pdf_gewuenscht = QtCore.Signal()
    speichern_gewuenscht = QtCore.Signal()
    KURZ_UNTER = 1400                      # schmaler: „Speichern“ statt „Lauf speichern“

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        self.felder: Dict[str, StatusFeld] = {}
        for key, name in FELDER:
            f = StatusFeld(name)
            self.felder[key] = f
            lay.addWidget(f, 1)
        self.meldung = QtWidgets.QLabel("")
        self.meldung.setMinimumWidth(80)
        self.meldung.setMaximumWidth(320)
        self.meldung.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        theme.rolle(self.meldung, "neben")
        lay.addWidget(self.meldung)
        self.pdf_knopf = PdfKnopf()
        self.pdf_knopf.clicked.connect(lambda _c=False: self.pdf_gewuenscht.emit())
        lay.addWidget(self.pdf_knopf)
        self.speichern_knopf = SpeichernKnopf()
        self.speichern_knopf.clicked.connect(lambda _c=False: self.speichern_gewuenscht.emit())
        lay.addWidget(self.speichern_knopf)
        lay.addSpacing(2)
        self.umschalter = ThemaUmschalter()
        lay.addWidget(self.umschalter)
        self.setFixedHeight(HOEHE)
        self.zust: Dict[str, Tuple[str, str]] = {}
        self.set_zustaende(zustaende(Eingang()))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.speichern_knopf.set_kurz(self.width() < self.KURZ_UNTER)

    def einbauen(self, sb: QtWidgets.QStatusBar):
        sb.setSizeGripEnabled(False)
        sb.addPermanentWidget(self, 1)
        sb.messageChanged.connect(self.zeige_meldung)

    def zeige_meldung(self, text: str):
        fm = self.meldung.fontMetrics()
        self.meldung.setText(fm.elidedText(text, QtCore.Qt.ElideRight, self.meldung.maximumWidth()))
        self.meldung.setToolTip(text)

    def set_zustaende(self, zust: Dict[str, Tuple[str, str]]):
        self.zust = dict(zust)
        for a in anzeige(zust):
            self.felder[a.schluessel].set_zustand(a.stufe, a.text)

    def feld(self, key: str) -> StatusFeld:
        return self.felder[key]
