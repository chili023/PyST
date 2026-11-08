"""
Statusleiste unten (44 px): drei farbige Felder „PST Basis“ | „PST Zange/Mega“ | „PST/Lambda ECU“, je mit Symbol
(✔ ⚠ ✖ –), Geraetename und Text – die Farbe steht nie allein. Rechts Kurzmeldungen und der Umschalter
☀ Tag | ☾ Nacht | Auto.

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
        return theme.WARNUNG, "im Bootloader – Firmware unter Einstellungen › Firmware aufspielen"
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


class ThemaUmschalter(QtWidgets.QWidget):
    """☀ Tag | ☾ Nacht | Auto – setzt theme.manager().setze(wahl); zeigt die gespeicherte Wahl."""

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.knoepfe: Dict[str, QtWidgets.QToolButton] = {}
        self.gruppe = QtWidgets.QButtonGroup(self)
        self.gruppe.setExclusive(True)
        for wahl, text in ((theme.TAG, "☀ Tag"), (theme.NACHT, "☾ Nacht"), (theme.AUTO, "Auto")):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolTip("Darstellung (auch im Menü Ansicht › Darstellung)" +
                         ("; Auto folgt dem Farbschema des Systems" if wahl == theme.AUTO else ""))
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
                            f" color: {T['accent_text'] if on else T['text']}; border: 1px solid {T['border']};"
                            f" padding: 5px 10px; font-size: 13px; font-weight: {700 if on else 500}; {rad} }}")


class Statusleiste(QtWidgets.QWidget):
    """Inhalt der Statusleiste des Hauptfensters: drei StatusFelder, Kurzmeldung, ThemaUmschalter.
    einbauen(statusbar): als dauerhaftes Widget einsetzen; Kurzmeldungen (showMessage) erscheinen rechts."""

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
        self.umschalter = ThemaUmschalter()
        lay.addWidget(self.umschalter)
        self.setFixedHeight(HOEHE)
        self.zust: Dict[str, Tuple[str, str]] = {}
        self.set_zustaende(zustaende(Eingang()))

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
