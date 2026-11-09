"""
Unterreiter „Software & Firmware“ (Entwurf 08.11.2026, Kunde/Experte; Schlüssel der Seite weiter "firmware"): oben die
Kachel „PyST (Software)“ mit installierter und neuester Version (update.latest_pyst, nur Hinweis, installiert wird
nichts), darunter die Firmware der Geräte aus dem öffentlichen Firmware-Repo (GitHub chili023/PyST, Branch firmware,
manifest.json) oder aus einer eigenen Datei. Prüfungen und Befehlszeilen in flash.py, Download und Prüfsummen in update.py.

- Angeschlossen (FlashPage._erkennen): Verbindung des Hauptfensters (Basis USB/WLAN, Zange per Funk bzw. gekoppelt oder
  USB, Mega, F446) oder ein passender Port bzw. das Laufwerk NODE_F446RE. Ports werden nur aufgelistet, nie geöffnet;
  solange der Reiter sichtbar ist alle 2 s und bei jedem Verbindungswechsel (verbindung_geaendert).
- Kunde (Standard): vier Karten Basis, Zange, Arduino Mega, STM32 (F446) mit erkannt/installiert/verfügbar und einem
  großen Knopf. Nicht angeschlossene Karten sind über das Thema ausgegraut (Token dim/win), zeigen nur die verfügbare
  Version, ihr Knopf ist aus und sagt im Tooltip, was zu tun ist. Das Basis-Update nimmt den Basis-ESP mit. Darunter
  der Ablauf mit Fortschritt.
- Expertenmodus (Schalter, gemerkt in "firmware_experte"): Tabelle aller Bausteine (dazu Basis-ESP und Lambda F042),
  Port, eigene Datei mit „erzwingen“, Erstflash-Wege (ST-Link, ESP-Brücke, esptool, avrdude, ST-Link/DFU), Zange per
  USB und das Protokoll – der frühere Inhalt dieses Unterreiters (FlashRow je Ziel) steckt unter „Eigene Datei“.
  Nicht angeschlossene Zeilen sind nur abgeblendet und bleiben bedienbar (Erstflash leerer Platinen).
"""
import os
import re
import time
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import einst_widgets as ew
from . import farben
from . import theme
from . import flash, update
from .flash import FlashCancelled, FlashError
from .bg import Bg, wait_in_thread

QUELLE = "GitHub chili023/PyST › firmware"
PYST_SEITE = "https://github.com/chili023/PyST"
PYST_RELEASES = f"{PYST_SEITE}/releases"
# Kundenansicht: was zu tun ist, solange ein Gerät nicht angeschlossen ist (Tooltip des Knopfs)
ANSCHLIESSEN = {"basis": "Basis per USB anschließen",
                "zange": "Zange per USB anschließen (vorher vom Zündkabel nehmen) oder über die Basis koppeln",
                "mega": "Arduino Mega per USB anschließen",
                "f446": "Nucleo (STM32 F446) per USB anschließen"}


class UsbWorker(QtCore.QThread):
    """USB-Update im eigenen Thread, die Oberflaeche bleibt bedienbar. FLASHER ist der Name der Klasse in flash.py
    (beim Start nachgeschlagen, Tests ersetzen sie dort)."""
    FLASHER = "UsbFlasher"
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    done = QtCore.Signal(str, str)                # Status (ok, abbruch, fehler), Text

    def __init__(self, update, parent=None):
        super().__init__(parent)
        self.update = update
        self.stop = False

    def run(self):
        f = getattr(flash, self.FLASHER)(self.update, log=self.log.emit, progress=self.progress.emit,
                                         cancelled=lambda: self.stop)
        try:
            self.done.emit("ok", f.run())
        except FlashCancelled as exc:
            self.done.emit("abbruch", str(exc))
        except (FlashError, OSError, ValueError) as exc:
            self.done.emit("fehler", str(exc))


class ZangeWorker(UsbWorker):
    """Zange per USB ueber #UPD (flash.ZangeFlasher)."""
    FLASHER = "ZangeFlasher"


class BasisEspWorker(UsbWorker):
    """Basis-ESP ueber #ESPUPD am Messport der Basis (flash.BasisEspFlasher)."""
    FLASHER = "BasisEspFlasher"


class LambdaWorker(UsbWorker):
    """F042 ueber die Basis per CAN (flash.LambdaFlasher)."""
    FLASHER = "LambdaFlasher"


class F446Worker(UsbWorker):
    """F446: .bin auf das Laufwerk NODE_F446RE kopieren (flash.F446Flasher), update = Abbild."""
    FLASHER = "F446Flasher"


WORKER = {flash.ZANGE: ZangeWorker, flash.BASIS_ESP: BasisEspWorker, flash.LAMBDA_F042: LambdaWorker,
          flash.F446: F446Worker}
USB_TARGETS = (flash.BASIS_STM, flash.ZANGE, flash.BASIS_ESP, flash.LAMBDA_F042)     # ohne externes Werkzeug
DATEI_ZIELE = (flash.ZANGE, flash.BASIS_STM, flash.BASIS_STLINK, flash.BASIS_ESP, flash.LAMBDA_F042, flash.MEGA,
               flash.F446, flash.F446_STLINK)
MUSTER = {flash.BASIS_STM: "Update-Datei (*.pystfw)", flash.ZANGE: "Update-Datei (*.pystfw)",
          flash.BASIS_ESP: "Update-Datei (*.pystfw)", flash.LAMBDA_F042: "Wideband-Abbild (*.bin)",
          flash.MEGA: "Mega-Firmware (*.hex)", flash.F446: "F446-Firmware (*.bin)",
          flash.F446_STLINK: "F446-Firmware (*.bin)"}

# Kundenansicht: Karte -> (Titel, Untertitel, Update-Ziele in Reihenfolge, Knopftext)
KARTEN = {
    "basis": ("Basis", "Messplatine PyST (Basis STM32 · Basis-ESP · Lambda)", (flash.BASIS_STM, flash.BASIS_ESP),
              "Aktualisieren"),
    "zange": ("Zange", "Funk-Zange am Zündkabel", (flash.ZANGE,), "Aktualisieren"),
    "mega": ("Arduino Mega", "ältere Messplatine PST 3.x", (flash.MEGA,), "Flashen"),
    "f446": ("STM32 (F446)", "ältere Messplatine PST-STM32", (flash.F446,), "Flashen"),
}
# Expertenmodus: Zeile -> (Name, Update-Ziel, Erstflash-Ziel, Erstflash-Text, Weg, Dateiformat)
ZEILEN = (
    ("basis_stm", "Basis STM32", flash.BASIS_STM, flash.BASIS_STLINK, "Erstflash ST-Link …", "USB (Update-Datei)",
     ".pystfw"),
    ("basis_esp", "Basis-ESP", flash.BASIS_ESP, flash.BASIS_ESP_BRIDGE, "Erstflash ESP-Brücke …", "USB über die Basis",
     ".pystfw"),
    ("lambda", "Lambda (F042)", flash.LAMBDA_F042, None, "", "CAN über die Basis", ".bin"),
    ("zange", "Zange", flash.ZANGE, flash.ZANGE_ESPTOOL, "Erstflash esptool …", "USB an der Zange", ".pystfw"),
    ("mega", "Arduino Mega", flash.MEGA, None, "", "USB · avrdude", ".hex"),
    ("f446", "STM32 (F446)", flash.F446, flash.F446_STLINK, "Erstflash ST-Link …", "Laufwerk, st-flash, DFU", ".bin"),
)
ERSTFLASH = (("Basis STM32", "ST-Link (SWD), Gesamtdatei", flash.BASIS_STLINK),
             ("Basis-ESP", "ESP-Brücke über die Basis (esptool)", flash.BASIS_ESP_BRIDGE),
             ("Zange", "esptool über USB-C", flash.ZANGE_ESPTOOL),
             ("Arduino Mega", "avrdude, Arduino-Bootloader, 115200 Bd", flash.MEGA),
             ("STM32 (F446)", "ST-Link (st-flash) oder USB-DFU (BOOT0)", flash.F446_STLINK))
WEG_LANG = {"mega": "USB · avrdude, Arduino-Bootloader, 115200 Bd",
            "f446": "USB-Laufwerk NODE_F446RE, st-flash (ST-Link) oder USB-DFU (BOOT0)"}
INBETRIEBNAHME = ("Inbetriebnahme neue Basis: 1. STM32 per ST-Link → 2. Basis-ESP per Brücke → 3. Updates per USB → "
                  "4. Lambda über die Basis → 5. Zange einmal per esptool")


class FlashRow(QtWidgets.QGroupBox):
    """Ein Flash-Ziel: Ordner, Stand der Firmware, Hinweis, Knopf (Expertenmodus › Eigene Datei)."""
    start = QtCore.Signal(str)
    load = QtCore.Signal(str)

    def __init__(self, target: flash.Target, folder: str, changed: Callable[[str, str], None]):
        super().__init__(target.name)
        self.target = target
        self.changed = changed
        lay = QtWidgets.QGridLayout(self)
        self.path = QtWidgets.QLineEdit(folder)
        self.is_file = target.key in DATEI_ZIELE
        self.path.setPlaceholderText("Firmware-Datei wählen" if self.is_file else "Ordner mit dem Build wählen")
        self.b_pick = QtWidgets.QPushButton("Datei wählen …" if self.is_file else "Ordner …")
        self.b_go = QtWidgets.QPushButton("Flashen")
        self.info = QtWidgets.QLabel("")
        self.info.setWordWrap(True)
        self.versions = QtWidgets.QLabel("")
        self.versions.setStyleSheet("font-weight: 600;")
        self.remote = QtWidgets.QLabel("")
        self.remote.hide()
        self.b_load = QtWidgets.QPushButton("Herunterladen")
        self.b_load.hide()
        self.b_load.clicked.connect(lambda: self.load.emit(target.key))
        self.current, self.stale = "", False
        hint = QtWidgets.QLabel(target.hint)
        hint.setWordWrap(True)
        theme.rolle(hint, "neben")
        lay.addWidget(QtWidgets.QLabel("Datei" if self.is_file else "Build-Ordner"), 0, 0)
        lay.addWidget(self.path, 0, 1)
        lay.addWidget(self.b_pick, 0, 2)
        lay.addWidget(self.b_go, 0, 3)
        lay.addWidget(self.versions, 1, 0, 1, 4)
        lay.addWidget(self.remote, 2, 0, 1, 3)
        lay.addWidget(self.b_load, 2, 3)
        lay.addWidget(self.info, 3, 0, 1, 4)
        lay.addWidget(hint, 4, 0, 1, 4)
        lay.setColumnStretch(1, 1)
        self._seen = None
        self._info = ("", "", "")
        self.b_pick.clicked.connect(self.pick)
        self.path.editingFinished.connect(self._edited)
        self.b_go.clicked.connect(lambda: self.start.emit(target.key))
        self.refresh()

    def pick(self):
        title = f"Firmware {self.target.name}"
        if self.is_file:
            pattern = MUSTER.get(self.target.key, "Gesamtdatei (*.bin)")
            d = QtWidgets.QFileDialog.getOpenFileName(self, title, self.path.text(), pattern)[0]
        else:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, title, self.path.text())
        if d:
            self.path.setText(d)
            self._edited()

    def _edited(self):
        self.changed(self.target.key, self.path.text().strip())
        self.refresh()

    def _stamp(self):
        """Merkmal der Datei bzw. des Ordners: nur wenn es sich aendert, lohnt erneutes Lesen samt SHA-256."""
        path = self.path.text().strip()
        try:
            st = os.stat(path)
            files = sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(path)) \
                if os.path.isdir(path) else []
        except OSError:
            return None
        return path, st.st_mtime_ns, st.st_size, files

    def _image_info(self):
        """-> (Beschreibung, Version, Farbe)"""
        stamp = self._stamp()
        if stamp is not None and stamp == self._seen:
            return self._info
        try:
            image = flash.load_image(self.target.key, self.path.text().strip())
            text = image.summary() + (f"\n{image.warning}" if image.warning else "")
            info = text, flash.image_version(image), farben.WARNUNG if image.warning else farben.GUT
        except (FlashError, OSError, ValueError) as exc:
            info = str(exc), "", farben.FEHLER
        self._seen, self._info = stamp, info
        return info

    @property
    def file_version(self) -> str:
        return self._image_info()[1]

    def refresh(self):
        text, new, color = self._image_info()
        if self.target.key == flash.LAMBDA_F042:
            self.versions.setText("Version: der F042 meldet nur das Protokoll, geprüft wird nach dem Update (0xA0)")
        else:
            line, state = flash.version_text(self.current, new, self.stale)
            self.versions.setText(line)
            self.versions.setStyleSheet("font-weight: 600; color: %s;" % {
                "neu": farben.WARNUNG, "gleich": farben.GUT, "aelter": farben.FEHLER}.get(state, theme.farbe("text")))
        port = flash.target_port(self.target.key)
        if self.target.key != flash.BASIS_STLINK:
            text += f"\nPort: {port or 'nicht gefunden'}"
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")


class FirmwareKarte(QtWidgets.QFrame):
    """Kundenansicht: ein Gerät mit erkannt/nicht angeschlossen, installiert, verfügbar und großem Knopf."""
    los = QtCore.Signal(str)

    def __init__(self, key: str, titel: str, unter: str, knopf: str):
        super().__init__()
        self.key = key
        self.knopf_text = knopf
        self.angeschlossen = True
        self.setObjectName("fwKarte")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(4)
        kopf = QtWidgets.QHBoxLayout()
        self.titel = QtWidgets.QLabel(titel)
        self.erkannt = ew.Chip("– nicht angeschlossen", ew.NEUTRAL)
        kopf.addWidget(self.titel)
        kopf.addSpacing(8)
        kopf.addWidget(self.erkannt)
        kopf.addStretch(1)
        lay.addLayout(kopf)
        self.unter = ew.neben(unter, wrap=False)
        lay.addWidget(self.unter)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.addWidget(ew.neben("installiert", False), 0, 0)
        grid.addWidget(ew.neben("verfügbar", False), 0, 1)
        self.installiert = QtWidgets.QLabel("–")
        self.verfuegbar = QtWidgets.QLabel("–")
        self.installiert.setMinimumWidth(200)
        self.neu = ew.Chip("neu", theme.WARNUNG, 12)
        self.neu.hide()
        v = QtWidgets.QHBoxLayout()
        v.setSpacing(4)
        v.addWidget(self.verfuegbar)
        v.addWidget(self.neu, 0, QtCore.Qt.AlignVCenter)
        v.addStretch(1)
        grid.addWidget(self.installiert, 1, 0)
        grid.addLayout(v, 1, 1)
        self.b_los = ew.gross_knopf(knopf)
        grid.addWidget(self.b_los, 1, 3, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        self.hinweis = ew.hinweis("", theme.WARNUNG)
        self.hinweis.hide()
        lay.addWidget(self.hinweis)
        self.unten = ew.neben("")
        lay.addWidget(self.unten)
        lay.addStretch(1)
        self.b_los.clicked.connect(lambda: self.los.emit(self.key))
        theme.bei_wechsel(self, lambda _t: self._stil())

    def _stil(self):
        """Angeschlossen: Kachelfläche, normale Schrift. Sonst ausgegraut: Fensterfläche, Rand gestrichelt, Titel und
        Zahlen in „dim“ (Kontrast laut theme.KONTRAST_PAARE mindestens 4,5:1)."""
        T = theme.tokens()
        an = self.angeschlossen
        self.setStyleSheet(f"QFrame#fwKarte {{ background: {T['panel' if an else 'win']}; border: 1px "
                           f"{'solid' if an else 'dashed'} {T['border']}; border-radius: 5px; }}")
        self.titel.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {T['text' if an else 'dim']};")
        for l in (self.installiert, self.verfuegbar):
            l.setStyleSheet(f"font-size: 28px; font-weight: 700; color: {T['value' if an else 'dim']};")

    def set_angeschlossen(self, an: bool, tipp: str = ""):
        """Ausgrauen über das Thema; tipp sagt, was zu tun ist (Tooltip von Karte und Knopf)."""
        self.setToolTip("" if an else f"Nicht angeschlossen – {tipp}" if tipp else "Nicht angeschlossen")
        if an != self.angeschlossen:
            self.angeschlossen = an
            self._stil()

    def zeige(self, erkannt: str, installiert: str, verfuegbar: str, zustand: str, unten: str = "",
              hinweis: str = "", tipp: str = ""):
        """zustand: neu (Update verfügbar, Knopf blau), aktuell (Knopf „Aktuell ✔“ ausgegraut), flashen (Knopf
        normal: Version unbekannt), leer (nichts verfügbar). Ohne erkannt ist die Karte ausgegraut, zeigt nur die
        verfügbare Version und der Knopf ist aus (tipp: was zu tun ist)."""
        an = bool(erkannt)
        self.set_angeschlossen(an, tipp)
        self.erkannt.set_zustand(theme.GUT if an else ew.NEUTRAL,
                                 f"✔ erkannt · {erkannt}" if an else "– nicht angeschlossen")
        self.installiert.setText((installiert if an else "") or "–")
        self.verfuegbar.setText(verfuegbar or "–")
        self.neu.setVisible(an and zustand == "neu")
        if an and zustand == "aktuell":
            self.b_los.setText("Aktuell ✔")
        else:
            self.b_los.setText(self.knopf_text)
        self.b_los.setEnabled(an and zustand in ("neu", "flashen"))
        self.b_los.setToolTip("" if an else tipp)
        ew.setze_primaer(self.b_los, an and zustand == "neu")
        self.unten.setText(unten)
        self.unten.setVisible(bool(unten))
        self.hinweis.setText(hinweis)
        self.hinweis.setVisible(bool(hinweis))


class SoftwareKarte(FirmwareKarte):
    """Kachel „PyST (Software)“: installierte Version, neueste aus `main` (update.latest_pyst), Datenordner und der
    Weg zum Update (git pull bzw. ZIP von der Release-Seite). Der Knopf öffnet nur die Release-Seite."""

    def __init__(self):
        super().__init__("pyst", "PyST (Software)", "Programm am PC", "Release-Seite …")
        self.b_los.setToolTip(PYST_RELEASES)
        self.b_los.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(PYST_RELEASES)))

    def zeige_pyst(self, lokal: str, remote: Optional[str], gesucht: str, ordner: str):
        """gesucht: "" (noch nicht), "suche", "fertig"; remote None = nicht erreichbar."""
        neu = bool(remote and update.is_newer(remote, lokal))
        self.unter.setText(f"Programm am PC · Datenordner {ordner}")
        self.installiert.setText(lokal or "–")
        self.verfuegbar.setText(remote or "–")
        self.neu.setVisible(neu)
        if neu:
            self.erkannt.set_zustand(theme.WARNUNG, f"Update verfügbar: {remote}")
        elif remote:
            self.erkannt.set_zustand(theme.GUT, "✔ aktuell")
        else:
            self.erkannt.set_zustand(ew.NEUTRAL, "✔ installiert")
        self.b_los.setText("Update laden …" if neu else self.knopf_text)
        ew.setze_primaer(self.b_los, neu)
        if neu:
            unten = (f"PyST {remote} ist da. Update: im PyST-Ordner „git pull“ – oder das ZIP von der Release-Seite "
                     f"laden und entpacken. Datenbank und Einstellungen in {ordner} bleiben erhalten.")
        elif remote:
            unten = f"PyST {lokal} ist aktuell (neueste Version auf GitHub: {remote})."
        elif gesucht == "fertig":
            unten = "Neueste Version nicht abrufbar (ohne Netz?) – „Jetzt nach Updates suchen“."
        elif gesucht == "suche":
            unten = "Suche nach der neuesten Version …"
        else:
            unten = "Noch nicht gesucht – „Jetzt nach Updates suchen“."
        self.unten.setText(unten)
        self.unten.setVisible(True)


class GeraeteZeile:
    """Expertenmodus: eine Zeile der Tabelle aller Bausteine (Widgets im Raster der Seite)."""

    def __init__(self, page: "FlashPage", grid: QtWidgets.QGridLayout, r: int, key: str, name: str, ziel: str,
                 erst: Optional[str], erst_text: str, weg: str, fmt: str):
        self.key, self.ziel, self.erst = key, ziel, erst
        self.angeschlossen = True
        n = QtWidgets.QLabel(name)
        n.setStyleSheet("font-weight: 700;")
        self.name = n
        self.port = QtWidgets.QComboBox()
        self.port.setMinimumWidth(150)          # die Tabelle passt so auch im Fenster 1280 px ohne Querscrollen
        self.port.setEnabled(key in ("mega", "f446"))
        self.port.setToolTip("Port für avrdude (Mega) bzw. der Nucleo; die anderen sucht PyST selbst"
                             if key in ("mega", "f446") else "wird beim Flashen selbst gesucht")
        weg_l = QtWidgets.QLabel(weg)
        weg_l.setToolTip(WEG_LANG.get(key, weg))
        fmt_l = ew.neben(fmt, False)
        self.geraet = QtWidgets.QLabel("–")
        self.verfuegbar = QtWidgets.QLabel("–")
        for l in (self.geraet, self.verfuegbar):
            l.setStyleSheet("font-weight: 700;")
        # abblenden: Rolle der Texte -> „dim“ (Stylesheet der Anwendung), sonst die eigene Rolle
        self._texte = ((n, ""), (weg_l, ""), (fmt_l, "neben"), (self.geraet, ""), (self.verfuegbar, ""))
        self.zustand = ew.Chip("–", ew.NEUTRAL, 12)
        self.b_los = QtWidgets.QPushButton("Flashen" if key in ("mega", "f446") else "Aktualisieren")
        self.b_datei = QtWidgets.QPushButton("Datei wählen …")
        self.erzwingen = QtWidgets.QCheckBox()
        self.erzwingen.setToolTip("Auch gleiche oder ältere Version bzw. Datei mit ungeprüfter Signatur flashen")
        self.erzwingen.toggled.connect(lambda _on: page._anzeigen())
        for c, w in enumerate((n, self.port, weg_l, fmt_l, self.geraet, self.verfuegbar, self.zustand, self.b_los,
                               self.b_datei)):
            grid.addWidget(w, r, c)
        if erst:
            self.b_erst = QtWidgets.QPushButton("Erstflash …")      # Weg im Tooltip, die Tabelle bleibt schmal
            self.b_erst.setToolTip(erst_text.replace(" …", "") + " (leere oder verkonfigurierte Platine)")
            self.b_erst.clicked.connect(lambda: page.erstflash(erst))
            grid.addWidget(self.b_erst, r, 9)
        grid.addWidget(self.erzwingen, r, 10, QtCore.Qt.AlignCenter)
        self.b_los.clicked.connect(lambda: page.aktualisieren([ziel], frage=True, erzwingen=self.erzwingen.isChecked()))
        self.b_datei.clicked.connect(lambda: page.datei_waehlen(ziel))

    def set_angeschlossen(self, an: bool):
        """Nicht angeschlossen: Texte abblenden, Knöpfe bleiben bedienbar (Erstflash, eigene Datei)."""
        if an == self.angeschlossen:
            return
        self.angeschlossen = an
        for l, rolle in self._texte:
            theme.rolle(l, rolle if an else "dim")
        self.name.setToolTip("" if an else "Nicht angeschlossen – Erstflash und eigene Datei gehen trotzdem")

    def set_ports(self, ports: List[str], text: str):
        if self.key in ("mega", "f446"):
            cur = self.port.currentData()
            items = [(p, p) for p in ports] or [("", "– (nicht gefunden)")]
            if [self.port.itemData(i) for i in range(self.port.count())] != [d for d, _t in items]:
                self.port.clear()
                for d, t in items:
                    self.port.addItem(t, d)
                i = self.port.findData(cur)
                self.port.setCurrentIndex(max(0, i))
        elif self.port.count() != 1 or self.port.itemText(0) != text:
            self.port.clear()
            self.port.addItem(text)


class FlashPage(QtWidgets.QScrollArea):
    pyst_update = QtCore.Signal(str)         # neue PyST-Version in main
    flash_done = QtCore.Signal()             # Port ist wieder frei: automatisches Verbinden darf wieder
    zange_verfuegbar = QtCore.Signal(str)    # neueste Zangen-Firmware laut Manifest (Karte Firmware unter Zange)

    def __init__(self, settings: dict, save_settings: Callable[[], None], get_link: Callable[[], object],
                 disconnect: Callable[[], None]):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.get_link = get_link
        self.get_info = lambda: ""               # Antwort auf 'v' (Mega/F446), setzt das Hauptfenster
        self.get_zange = lambda: ""              # „Zange 1“ (Hauptzange per Funk), setzt das Hauptfenster
        self.get_zange_state = lambda: ""        # Zange laut Seite Zange: ok | weg | usb | "", setzt das Hauptfenster
        self.disconnect = disconnect
        self.proc = None
        self.worker = None
        self.bg = Bg()
        self.remote: Dict[str, update.Entry] = {}
        self.stand = ""                          # Zeit der letzten Abfrage des Manifests
        self.pyst_neu = ""
        self.pyst_lokal = ""
        self.pyst_remote: Optional[str] = None   # neueste PyST-Version aus main, None = unbekannt
        self.pyst_gesucht = ""                   # "", "suche", "fertig"
        self.datenordner = os.path.join(os.path.expanduser("~"), "PyST")
        self._link_zuletzt = None                # Verbindungswechsel erkennen (verbindung_geaendert)
        self.installiert: Dict[str, str] = {}
        self.erkannt: Dict[str, str] = {}        # Karte/Zeile -> „USB“, „Funk (Zange 1)“ …, leer = nicht da
        self.ports: Dict[str, List[str]] = {}
        self.queue: List[str] = []               # Ziele, die noch drankommen (Basis: STM, dann ESP)
        self._ablauf_erzwingen = False
        self.update_base = update.FIRMWARE_URL
        self.download_dir = os.path.join(os.path.expanduser("~"), "PyST", "firmware_updates")
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        self.root = QtWidgets.QVBoxLayout(inner)
        self.root.setSpacing(8)
        self.root.addLayout(self._build_kopf())
        self.pyst_karte = SoftwareKarte()        # Kunde und Experte: immer zuoberst
        self.root.addWidget(self.pyst_karte)
        self._pyst_anzeigen()
        self.kunde = self._build_kunde()
        self.experte = self._build_experte(settings)
        self.ablauf = self._build_ablauf()
        self.root.addWidget(self.kunde)
        self.root.addWidget(self.experte, 1)
        self.root.addStretch(0)
        self.schalter.setChecked(bool(settings.get("firmware_experte", False)))
        self.schalter.toggled.connect(self.set_experte)
        self.set_experte(self.schalter.isChecked(), speichern=False)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2000)

    # ------------------------------------------------------------------ Aufbau
    def _build_kopf(self):
        row = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("Software und Firmware")
        t.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.quelle = ew.neben(f"Quelle: {QUELLE} · noch nicht gesucht", False)
        self.b_suchen = QtWidgets.QPushButton("Jetzt nach Updates suchen")
        self.b_suchen.clicked.connect(self.suchen)
        self.schalter = ew.Schalter()
        self.schalter.setToolTip("Alle Bausteine einzeln, eigene Datei, Port, Erstflash und Protokoll")
        self.schalter_text = QtWidgets.QLabel("Expertenmodus")
        row.addWidget(t)
        row.addSpacing(24)
        row.addWidget(self.quelle)
        row.addStretch(1)
        row.addWidget(self.b_suchen)
        row.addSpacing(24)
        row.addWidget(self.schalter_text)
        row.addWidget(self.schalter)
        return row

    def _build_kunde(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        self.karten: Dict[str, FirmwareKarte] = {}
        for i, (key, (titel, unter, _z, knopf)) in enumerate(KARTEN.items()):
            k = FirmwareKarte(key, titel, unter, knopf)
            k.los.connect(self.karte_los)
            grid.addWidget(k, i // 2, i % 2)
            self.karten[key] = k
        lay.addLayout(grid)
        self.kunde_ablauf = QtWidgets.QVBoxLayout()
        lay.addLayout(self.kunde_ablauf)
        lay.addWidget(ew.neben("Mehr (einzelne Bausteine, eigene Datei, Port, Erstflash, Protokoll): Schalter "
                               "„Expertenmodus“ oben rechts."))
        return w

    def _build_ablauf(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Ablauf")
        lay = QtWidgets.QVBoxLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("bereit")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.zuletzt = ew.Chip("", theme.GUT)
        self.zuletzt.setVisible(bool(self.settings.get("firmware_zuletzt")))
        self.zuletzt.setText(self.settings.get("firmware_zuletzt", ""))
        self.b_alle = QtWidgets.QPushButton("Alle verfügbaren Updates (0) …")
        self.b_alle.setEnabled(False)
        self.b_alle.clicked.connect(self.alle)
        row.addWidget(self.status, 1)
        row.addWidget(self.zuletzt)
        row.addSpacing(12)
        row.addWidget(self.b_alle)
        lay.addLayout(row)
        line = QtWidgets.QHBoxLayout()
        self.bar = QtWidgets.QProgressBar()
        self.bar.hide()
        self.b_cancel = QtWidgets.QPushButton("Abbrechen")
        self.b_cancel.hide()
        self.b_cancel.clicked.connect(self._cancel)
        line.addWidget(self.bar, 1)
        line.addWidget(self.b_cancel)
        lay.addLayout(line)
        lay.addWidget(ew.neben("Während eines Updates erscheint hier der Fortschritt. Nichts abstecken, Platine nicht "
                               "ausschalten; danach verbindet sich PyST von selbst neu."))
        return g

    def _build_experte(self, settings: dict) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        # ---- Tabelle aller Bausteine
        g = QtWidgets.QGroupBox("Geräte (Expertenmodus: alle Bausteine einzeln)")
        grid = QtWidgets.QGridLayout(g)
        grid.setHorizontalSpacing(8)
        for c, text in enumerate(("Gerät", "Port", "Weg", "Datei", "auf dem Gerät", "verfügbar", "Zustand", "", "", "",
                                  "erzwingen")):
            if text:
                h = QtWidgets.QLabel(text)
                theme.rolle(h, "neben")
                h.setStyleSheet("font-weight: 700;")
                grid.addWidget(h, 0, c)
        self.zeilen: Dict[str, GeraeteZeile] = {}
        for r, z in enumerate(ZEILEN, 1):
            self.zeilen[z[0]] = GeraeteZeile(self, grid, r, *z)
        grid.setColumnStretch(2, 1)
        lay.addWidget(g)
        self.experte_ablauf = QtWidgets.QVBoxLayout()
        lay.addLayout(self.experte_ablauf)
        # ---- Eigene Datei (die Flash-Ziele von früher, je eines sichtbar) | Erstflash
        mitte = QtWidgets.QHBoxLayout()
        g = QtWidgets.QGroupBox("Eigene Datei (statt Firmware-Repo)")
        el = QtWidgets.QVBoxLayout(g)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Gerät"))
        self.ziel_wahl = QtWidgets.QComboBox()
        for key, t in flash.TARGETS.items():
            self.ziel_wahl.addItem(t.name, key)
        row.addWidget(self.ziel_wahl, 1)
        el.addLayout(row)
        self.ziel_stapel = QtWidgets.QStackedWidget()
        folders: Dict[str, str] = settings.setdefault("flash_ordner", {})
        for key, esptool in ((flash.ZANGE, flash.ZANGE_ESPTOOL), (flash.BASIS_ESP, flash.BASIS_ESP_BRIDGE)):
            old = folders.get(key, "")
            if old and esptool not in folders and flash.is_esp_build(old):
                folders[esptool] = folders.pop(key)       # bis 1.5 hiess so der esptool-Weg
        self.rows: Dict[str, FlashRow] = {}
        for key, t in flash.TARGETS.items():
            row_w = FlashRow(t, folders.get(key, ""), self._folder_changed)
            row_w.start.connect(self.start)
            row_w.load.connect(self._download)
            self.ziel_stapel.addWidget(row_w)
            self.rows[key] = row_w
        self.ziel_wahl.currentIndexChanged.connect(self.ziel_stapel.setCurrentIndex)
        el.addWidget(self.ziel_stapel)
        self.erzwingen = QtWidgets.QCheckBox("Version erzwingen / Downgrade erlauben")
        self.erzwingen.setToolTip("Gleiche oder ältere Version flashen; Dateien mit unbekanntem Schlüssel nur so")
        el.addWidget(self.erzwingen)
        el.addWidget(ew.neben("Erlaubt: .pystfw (Basis, Basis-ESP, Zange) · .hex (Mega) · .bin (F446, Lambda). Die "
                              "Signatur wird geprüft; ohne geprüfte Signatur nur mit Haken „erzwingen“."))
        mitte.addWidget(g, 3)
        g = QtWidgets.QGroupBox("Erstflash (leere oder verkonfigurierte Platine)")
        gl = QtWidgets.QGridLayout(g)
        for r, (name, weg, ziel) in enumerate(ERSTFLASH):
            n = QtWidgets.QLabel(name)
            n.setStyleSheet("font-weight: 700;")
            b = QtWidgets.QPushButton("Starten …")
            b.clicked.connect(lambda _c=False, z=ziel: self.erstflash(z))
            gl.addWidget(n, r, 0)
            gl.addWidget(QtWidgets.QLabel(weg), r, 1)
            gl.addWidget(b, r, 2)
        gl.setColumnStretch(1, 1)
        gl.setRowStretch(len(ERSTFLASH), 1)
        mitte.addWidget(g, 2)
        lay.addLayout(mitte)
        # ---- Zange per USB
        g = QtWidgets.QGroupBox("Zange – Update per USB")
        zl = QtWidgets.QVBoxLayout(g)
        warn = QtWidgets.QLabel("⚠ " + flash.NO_USB_AT_ENGINE)
        warn.setWordWrap(True)
        theme.rolle(warn, "banner_fehler")
        zl.addWidget(warn)
        row = QtWidgets.QHBoxLayout()
        self.zange_datei = QtWidgets.QLabel("")
        theme.rolle(self.zange_datei, "neben")
        b = QtWidgets.QPushButton("Aktualisieren")
        theme.rolle(b, "primaer")
        b.clicked.connect(lambda: self.aktualisieren([flash.ZANGE], frage=True,
                                                     erzwingen=self.zeilen["zange"].erzwingen.isChecked()))
        self.b_zange_usb = b
        row.addWidget(self.zange_datei, 1)
        row.addWidget(b)
        zl.addLayout(row)
        lay.addWidget(g)
        # ---- Protokoll
        g = QtWidgets.QGroupBox("Protokoll")
        pl = QtWidgets.QVBoxLayout(g)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.log.setMinimumHeight(180)
        pl.addWidget(self.log, 1)
        row = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("Protokoll kopieren")
        b1.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.log.toPlainText()))
        b2 = QtWidgets.QPushButton("Protokoll speichern …")
        b2.clicked.connect(self.log_speichern)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addSpacing(16)
        row.addWidget(ew.neben(INBETRIEBNAHME), 1)
        pl.addLayout(row)
        lay.addWidget(g, 1)
        return w

    def set_experte(self, on: bool, speichern: bool = True):
        self.kunde.setVisible(not on)
        self.experte.setVisible(on)
        (self.experte_ablauf if on else self.kunde_ablauf).addWidget(self.ablauf)
        self.schalter_text.setStyleSheet("font-weight: 700;" if on else "")
        if speichern:
            self.settings["firmware_experte"] = bool(on)
            self.save_settings()

    @property
    def experte_an(self) -> bool:
        return self.schalter.isChecked()

    # ------------------------------------------------------------------ Protokoll, Zustand
    def _log(self, text: str):
        for line in str(text).replace("\r", "\n").splitlines():
            if line.strip():
                self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line.rstrip()}")

    def log_speichern(self, path: str = "") -> str:
        if not path:
            name = time.strftime("%y%m%d_%H%M%S") + "_Firmware-Protokoll.txt"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Protokoll speichern",
                                                            os.path.join(os.path.expanduser("~"), name),
                                                            "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.log.toPlainText() + "\n")
        return path

    def _status(self, text: str, color: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {color};")
        self._log(text)

    # ------------------------------------------------------------------ Updates suchen
    def check_updates(self, version: str):
        """Beim Programmstart: im Hintergrund nach Neuem suchen, ohne Netz oder Branch bleibt alles still."""
        self.pyst_lokal = version
        if "pyst" not in self.bg.pending:
            self.pyst_gesucht = "suche"
            self._pyst_anzeigen()
        self.bg.submit("pyst", update.latest_pyst, lambda v, e: self._pyst_seen(v, version))
        self.bg.submit("manifest", lambda: update.fetch_manifest(base=self.update_base), self._manifest)

    def suchen(self):
        """Knopf „Jetzt nach Updates suchen“."""
        from . import __version__
        self.quelle.setText(f"Quelle: {QUELLE} · suche …")
        self.check_updates(self.pyst_lokal or __version__)

    def _pyst_seen(self, remote, local: str):
        self.pyst_lokal = local
        self.pyst_remote = remote or None
        self.pyst_gesucht = "fertig"
        neu = bool(remote) and update.is_newer(remote, local)
        self.pyst_neu = remote if neu else ""
        self._pyst_anzeigen()
        if neu:
            self.pyst_update.emit(remote)

    def set_datenordner(self, ordner: str):
        """Datenordner (~/PyST) für die Kachel PyST, setzt das Hauptfenster."""
        self.datenordner = ordner
        self._pyst_anzeigen()

    def _pyst_anzeigen(self):
        from . import __version__
        home = os.path.expanduser("~")
        ordner = self.datenordner
        if ordner == home or ordner.startswith(home + os.sep):
            ordner = "~" + ordner[len(home):]
        self.pyst_karte.zeige_pyst(self.pyst_lokal or __version__, self.pyst_remote, self.pyst_gesucht, ordner)

    def _manifest(self, entries, err):
        self.remote = entries or {}
        self.stand = time.strftime("%d.%m.%Y %H:%M") if entries is not None else ""
        self.quelle.setText(f"Quelle: {QUELLE} · " + (f"Stand {self.stand}" if self.stand else
                                                      "nicht erreichbar (ohne Netz oder Branch)"))
        if entries:
            self._log(f"Firmware-Liste gelesen ({QUELLE}): " +
                      ", ".join(f"{k} {e.version}" for k, e in sorted(entries.items())))
        z = self.remote.get(flash.ZANGE)
        self.zange_verfuegbar.emit(z.version if z else "")
        self._show_remote()

    def _show_remote(self):
        for key, row in self.rows.items():
            e = self.remote.get(key)
            neu = e is not None and (not row.current or update.is_newer(e.version, row.current))
            row.remote.setVisible(e is not None)
            row.remote.setText(f"Online: {e.version}" + (" (Update verfügbar)" if neu else "") if e else "")
            row.b_load.setVisible(e is not None and row.is_file and key != flash.BASIS_STLINK)
        self._anzeigen()

    # ------------------------------------------------------------------ Geräte erkennen
    @staticmethod
    def _suche_ports() -> Dict[str, List[str]]:
        """Läuft im Hintergrund: Ports bzw. Laufwerke je Gerät (ohne sie zu öffnen)."""
        return {"basis": flash.basis_ports(), "zange": flash.zange_ports(), "mega": flash.mega_ports(),
                "f446": flash.f446_ports(), "nucleo": flash.nucleo_laufwerke()}

    def _erkennen(self, link) -> Dict[str, str]:
        """Karte/Zeile -> Art der Verbindung („USB“, „WLAN“, „Funk (Zange 1)“), leer = nicht da."""
        p = self.ports
        out = {k: "" for k in ("basis", "zange", "mega", "f446")}
        if link is not None and link.is_basis:
            out["basis"] = "WLAN" if link.is_tcp else "USB"
            if link.ign_source == "zange" or self.get_zange_state() == "ok":      # gekoppelt bzw. per Funk da
                out["zange"] = "Funk"
        elif link is not None and link.is_zange_usb:
            out["zange"] = "USB"
        elif link is not None:
            out["f446" if link.is_stm else "mega"] = "USB" + (" (Simulator)" if link.port.startswith("SIM") else "")
        if not out["basis"] and p.get("basis"):
            out["basis"] = "USB"
        if p.get("zange"):
            out["zange"] = "USB"
        if not out["mega"] and p.get("mega"):
            out["mega"] = "USB"
        if not out["f446"] and (p.get("f446") or p.get("nucleo")):
            out["f446"] = "USB"
        return out

    def _refresh(self):
        self.bg.poll()
        if self.busy or not self.isVisible():
            return
        self.bg.submit("ports", self._suche_ports, self._ports)
        link = self.get_link()
        if link is not None and not link.bootloader:
            info = self.get_info() or ""
            self.bg.submit("versionen", lambda: flash.device_versions(link, info), self._versions)
        else:
            for row in self.rows.values():
                row.stale = bool(row.current)
        for row in self.rows.values():
            row.refresh()
        self._anzeigen()

    def showEvent(self, event):
        """Reiter wird sichtbar: gleich die Ports auflisten, nicht erst nach dem nächsten Takt."""
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._refresh)

    def verbindung_geaendert(self, *_args):
        """Vom Hauptfenster bei Verbindungswechseln (Verbinden, Trennen, Zange per Funk da/weg): Karten neu zeichnen,
        bei neuer Verbindung auch Ports und Versionen neu lesen. Nur solange der Reiter sichtbar ist."""
        if self.busy or not self.isVisible():
            return
        link = self.get_link()
        if link is not self._link_zuletzt:
            self._link_zuletzt = link
            self._refresh()
        else:
            self._anzeigen()

    def _ports(self, ports, err):
        if err is None and ports is not None:
            self.ports = ports
            self._anzeigen()

    def _versions(self, found, err):
        if err is not None or found is None:
            return
        for key, row in self.rows.items():
            if key in found:
                row.current, row.stale = found[key], False
            row.refresh()
        self.installiert.update(found)
        self._show_remote()

    # ------------------------------------------------------------------ Anzeige Kunde/Experte
    def _verfuegbar(self, ziel: str) -> str:
        e = self.remote.get(ziel)
        return e.version if e else ""

    def _ist(self, ziel: str) -> str:
        row = self.rows.get(ziel)
        return self.installiert.get(ziel) or (row.current if row is not None else "")

    def _zustand(self, ziel: str, erkannt: bool) -> str:
        ist, neu = self._ist(ziel), self._verfuegbar(ziel)
        if not neu:
            return ""
        if ist and not update.is_newer(neu, ist):
            return "aktuell"
        if ist and erkannt:
            return "neu"
        return "flashen"

    def updates_offen(self) -> List[str]:
        """Ziele mit neuerer Firmware an erkannten Geräten (Knopf „Alle verfügbaren Updates“)."""
        er = self._erkennen(self.get_link())
        out = []
        for key, (_t, _u, ziele, _k) in KARTEN.items():
            if er.get(key) and (key != "zange" or er[key] == "USB"):      # Zange nur per USB
                out += [z for z in ziele if self._zustand(z, True) == "neu"]
        return out

    def _anzeigen(self):
        link = self.get_link()
        er = self._erkennen(link)
        self.erkannt = er
        for key, karte in self.karten.items():
            _t, _u, ziele, _k = KARTEN[key]
            haupt = ziele[0]
            zustand = self._zustand(haupt, bool(er[key]))
            if key == "basis" and zustand != "neu" and any(self._zustand(z, True) == "neu" for z in ziele[1:]) \
                    and er[key]:
                zustand = "neu"
            unten, hinweis = "", ""
            if not er[key]:
                unten = f"Nicht angeschlossen: {ANSCHLIESSEN[key]}; wird automatisch erkannt."
            elif key == "basis":
                esp = self._ist(flash.BASIS_ESP)
                unten = (f"Basis-ESP{f' ({esp})' if esp else ''} wird automatisch mit aktualisiert. "
                         "Den Lambda-Controller (F042) aktualisiert man einzeln im Expertenmodus.")
                if er[key] == "WLAN" and not self.ports.get("basis"):
                    hinweis = "⚠ Per WLAN verbunden – das Update geht nur per USB: Basis per USB anschließen."
            elif key == "zange":
                hinweis = "⚠ Zum Aktualisieren die Zange per USB anschließen – vorher vom Zündkabel nehmen!"
                name = self.get_zange() if er["zange"] == "Funk" else ""
                if name:
                    er["zange"] = f"Funk ({name})"
            elif zustand == "aktuell":
                unten = "Firmware ist aktuell. Flashen per USB, Dauer ca. 30 s."
            else:
                unten = "Flashen per USB, Dauer ca. 30 s. Die Messverbindung wird vorher getrennt."
            if not self._verfuegbar(haupt):
                unten = (unten + " " if unten else "") + ("Noch keine Firmware-Liste – „Jetzt nach Updates suchen“."
                                                         if not self.stand else "Im Firmware-Repo nicht vorhanden.")
            karte.zeige(er[key], self._ist(haupt), self._verfuegbar(haupt), zustand, unten, hinweis,
                        ANSCHLIESSEN[key])
            karte.b_los.setEnabled(karte.b_los.isEnabled() and not self.busy)
        # Expertenmodus: Tabelle
        p = self.ports
        port_text = {"basis_stm": (p.get("basis") or ["– (nicht gefunden)"])[0] + (" · PST Basis" if p.get("basis")
                                                                                  else ""),
                     "basis_esp": "über Basis", "lambda": "über Basis",
                     "zange": (p.get("zange") or [""])[0] or ("Funk · über Basis" if er["zange"] else "–")}
        for key, z in self.zeilen.items():
            z.set_ports(p.get(key, []) if key == "mega" else p.get("f446", []) if key == "f446" else [],
                        port_text.get(key, ""))
            ist, neu = self._ist(z.ziel), self._verfuegbar(z.ziel)
            z.geraet.setText(ist or ("Prot. 0xA0" if key == "lambda" and er["basis"] else "–"))
            z.verfuegbar.setText(neu or "–")
            da = bool(er.get({"basis_stm": "basis", "basis_esp": "basis", "lambda": "basis"}.get(key, key)))
            z.set_angeschlossen(da)
            zu = self._zustand(z.ziel, da)
            if key == "lambda":
                z.zustand.set_zustand(ew.NEUTRAL, "Version nicht lesbar" if da else "nicht angeschlossen")
            elif not da:
                z.zustand.set_zustand(ew.NEUTRAL, "nicht angeschlossen")
            elif zu == "neu":
                z.zustand.set_zustand(theme.WARNUNG, "Update (USB)" if key == "zange" else "Update")
            elif zu == "aktuell":
                z.zustand.set_zustand(theme.GUT, "aktuell")
            else:
                z.zustand.set_zustand(ew.NEUTRAL, "erkannt" if da else "–")
            moeglich = zu == "neu" or bool(neu and z.erzwingen.isChecked()) or key in ("mega", "f446") and bool(neu)
            z.b_los.setVisible(moeglich or key in ("mega", "f446"))
            z.b_los.setEnabled(not self.busy and moeglich)
            ew.setze_primaer(z.b_los, zu == "neu")
        offen = self.updates_offen()
        self.b_alle.setText(f"Alle verfügbaren Updates ({len(offen)}) …")
        self.b_alle.setEnabled(bool(offen) and not self.busy)
        zp = self.rows[flash.ZANGE].path.text().strip()
        self.zange_datei.setText(f"bereit · Datei {os.path.basename(zp)} (eigene)" if zp else
                                 f"bereit · Firmware-Repo {self._verfuegbar(flash.ZANGE) or '–'}")

    # ------------------------------------------------------------------ Bedienung
    def karte_los(self, key: str):
        """Großer Knopf einer Karte: Basis = STM, danach ESP (wenn neuer); sonst das eine Ziel."""
        _t, _u, ziele, _k = KARTEN[key]
        offen = [z for z in ziele if self._zustand(z, True) in ("neu", "flashen") and self._verfuegbar(z)]
        if key in ("mega", "f446"):
            offen = offen[:1] or ([ziele[0]] if self._verfuegbar(ziele[0]) else [])
        self.aktualisieren(offen, frage=True)

    def alle(self):
        self.aktualisieren(self.updates_offen(), frage=True)

    def datei_waehlen(self, ziel: str):
        """Zeile „Datei wählen …“: Ziel unter Eigene Datei zeigen und die Datei wählen lassen."""
        self.ziel_wahl.setCurrentIndex(self.ziel_wahl.findData(ziel))
        self.rows[ziel].pick()

    def erstflash(self, ziel: str):
        """Erstflash-Weg: ohne gewählte Datei bzw. Build-Ordner erst wählen lassen, dann starten (mit Rückfrage)."""
        self.ziel_wahl.setCurrentIndex(self.ziel_wahl.findData(ziel))
        row = self.rows[ziel]
        if not row.path.text().strip():
            row.pick()
            if not row.path.text().strip():
                return
        self.start(ziel)

    def aktualisieren(self, ziele: List[str], frage: bool = True, erzwingen: bool = False):
        """Ziele nacheinander aus dem Firmware-Repo laden (Größe + SHA-256, signierte zusätzlich Signatur) und flashen."""
        if self.busy or not ziele:
            return
        if flash.ZANGE in ziele and flash.zange_port() is None and not getattr(self.get_link(), "is_zange_usb", False):
            self._status("Zange per USB anschließen (vorher vom Zündkabel nehmen) – dann „Aktualisieren“.",
                         farben.WARNUNG)
            return
        fehlt = [z for z in ziele if z not in self.remote]
        if fehlt:
            self._status("Keine Firmware im Repo für: " + ", ".join(flash.TARGETS[z].name for z in fehlt) +
                         " – „Jetzt nach Updates suchen“ oder Expertenmodus › Eigene Datei.", farben.WARNUNG)
            return
        if frage:
            text = "\n".join(f"{flash.TARGETS[z].name}: {self._ist(z) or '?'} → {self.remote[z].version}" for z in ziele)
            if QtWidgets.QMessageBox.question(
                    self, "Firmware aktualisieren", f"Jetzt aktualisieren?\n\n{text}\n\nNichts abstecken, nicht "
                                                    "ausschalten. Die Messverbindung wird getrennt.") != \
                    QtWidgets.QMessageBox.Yes:
                return
        self.queue = list(ziele)
        self._ablauf_erzwingen = erzwingen
        self._naechstes()

    def _naechstes(self):
        if not self.queue or self.busy:
            return
        key = self.queue[0]
        e = self.remote[key]
        self._status(f"Lade {e.file} …", farben.WARNUNG)
        self.bg.submit("laden", lambda: update.download(e, self.download_dir, self.update_base),
                       lambda path, err: self._geladen(key, path, err))

    def _geladen(self, key: str, path, err):
        self._downloaded(key, path, err)
        if err is not None:
            self.queue = []
            return
        self.queue.pop(0)
        self.start(key, frage=False, erzwingen=self._ablauf_erzwingen)

    def _ablauf_weiter(self, ok: bool):
        """Nach einem Ziel: nächstes aus der Warteschlange (Basis-ESP nach dem STM), nach einem Fehler nichts mehr."""
        if not ok:
            self.queue = []
            return
        if self.queue:
            QtCore.QTimer.singleShot(3000, self._naechstes)

    def _download(self, key: str):
        e = self.remote.get(key)
        if e is None or self.busy:
            return
        self._status(f"Lade {e.file} …", farben.WARNUNG)
        self.bg.submit("laden", lambda: update.download(e, self.download_dir, self.update_base),
                       lambda path, err: self._downloaded(key, path, err))

    def _downloaded(self, key: str, path, err):
        if err is not None:
            self._status(f"Download fehlgeschlagen: {err}", farben.FEHLER)
            return
        row = self.rows[key]
        row.path.setText(path)
        self._folder_changed(key, path)
        row.refresh()
        self._status(f"{os.path.basename(path)} geladen und geprüft, bereit zum Flashen.", farben.GUT)

    def _folder_changed(self, key: str, folder: str):
        self.settings.setdefault("flash_ordner", {})[key] = folder
        self.save_settings()

    @property
    def busy(self) -> bool:
        """Solange geflasht wird, darf sich das automatische Verbinden den Port nicht nehmen."""
        return self.proc is not None or self.worker is not None

    def _set_busy(self, busy: bool):
        for row in self.rows.values():
            row.b_go.setEnabled(not busy)
        self.b_suchen.setEnabled(not busy)
        if not busy:
            self._anzeigen()
        else:
            for k in self.karten.values():
                k.b_los.setEnabled(False)
            for z in self.zeilen.values():
                z.b_los.setEnabled(False)
            self.b_alle.setEnabled(False)

    def _port_fuer(self, key: str) -> Optional[str]:
        """Mega/F446: der in der Tabelle gewählte Port, sonst der einzige gefundene."""
        zeile = {flash.MEGA: "mega", flash.F446: "f446", flash.F446_STLINK: "f446"}.get(key)
        if zeile and self.zeilen[zeile].port.currentData():
            return self.zeilen[zeile].port.currentData()
        return flash.target_port(key)

    def _erzwingen_noetig(self, key: str, image) -> str:
        """Grund, warum nur mit „erzwingen“ geflasht wird (Datei nicht neuer als das Gerät, Signatur ungeprüft)."""
        warn = getattr(image, "warning", "")
        if isinstance(warn, str) and warn:
            return "Signatur ungeprüft: " + warn
        neu = getattr(image, "version", "")
        ist = self._ist(key)
        if isinstance(neu, str) and neu and ist and not update.is_newer(neu, ist):
            return f"Die Datei ({neu}) ist nicht neuer als das Gerät ({ist})"
        return ""

    def start(self, key: str, frage: bool = True, erzwingen: Optional[bool] = None):
        if self.busy:
            return
        row = self.rows[key]
        t = flash.TARGETS[key]
        port = self._port_fuer(key)
        if self.get_link() is not None:
            self.disconnect()                     # Port freigeben; danach neu suchen, der Name kann wechseln
        try:
            image, cmd = flash.build_command(key, row.path.text().strip(), port)
        except (FlashError, OSError, ValueError) as exc:
            self._status(str(exc), farben.FEHLER)
            self.queue = []
            return
        if erzwingen is None:
            zeile = next((z for z in self.zeilen.values() if key in (z.ziel, z.erst)), None)
            erzwingen = self.erzwingen.isChecked() or bool(zeile and zeile.erzwingen.isChecked())
        grund = self._erzwingen_noetig(key, image)
        if grund and not erzwingen:
            self._status(f"{t.name}: {grund}. Nur mit Haken „erzwingen“ (Expertenmodus).", farben.WARNUNG)
            self.queue = []
            return
        warning = f"{image.warning}\n\n" if image.warning else ""
        text = f"{t.name} jetzt flashen?\n\n{image.summary()}\n\n{warning}{t.hint}"
        if frage and QtWidgets.QMessageBox.question(self, "Flashen", text) != QtWidgets.QMessageBox.Yes:
            return
        if key == flash.BASIS_ESP_BRIDGE:         # erst nach der Rueckfrage: die Bruecke wartet nur 30 s auf esptool
            try:
                wait_in_thread(lambda: flash.esp_bridge_on(flash.basis_port()))
            except FlashError as exc:
                self._status(str(exc), farben.FEHLER)
                self.flash_done.emit()
                return
        if key in USB_TARGETS or (key == flash.F446 and not cmd):
            self.run_usb(key, image)
        else:
            self.run(key, cmd)

    def run_usb(self, key: str, image):
        self._log(f"––– {flash.TARGETS[key].name} –––")
        what = "Zange nicht ausschalten, nicht abstecken" if key == flash.ZANGE else \
            "Nucleo nicht abstecken" if key == flash.F446 else "Basis nicht vom Strom trennen"
        self._status(f"{flash.TARGETS[key].name}: läuft, {what} …", farben.WARNUNG)
        self._set_busy(True)
        is_lambda = key == flash.LAMBDA_F042
        if is_lambda:
            n = len(image.lambda_app)
        elif key == flash.F446:
            n = os.path.getsize(image.main)
        else:
            n = image.update.image_len
        self.bar.setRange(0, n)
        self.bar.setValue(0)
        self.bar.show()
        self.b_cancel.setEnabled(True)
        self.b_cancel.show()
        if is_lambda:
            self.worker = LambdaWorker(image.lambda_app, self)
        elif key == flash.F446:
            self.worker = F446Worker(image, self)
        else:
            self.worker = WORKER.get(key, UsbWorker)(image.update, self)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(lambda done, _n: self.bar.setValue(done))
        self.worker.done.connect(lambda state, text: self._usb_done(key, state, text))
        self.worker.start()

    def _cancel(self):
        if self.worker is not None:
            self.worker.stop = True
            self.b_cancel.setEnabled(False)
        self.queue = []

    def _zuletzt(self, key: str, version: str):
        text = f"✔ zuletzt: {flash.TARGETS[key].name} auf {version or 'neue Version'} aktualisiert · " \
               f"{time.strftime('%d.%m. %H:%M')}"
        self.zuletzt.setText(text)
        self.zuletzt.show()
        self.settings["firmware_zuletzt"] = text
        self.save_settings()

    def _usb_done(self, key: str, state: str, text: str):
        self.worker.wait()
        self.worker = None
        self.flash_done.emit()
        self.bar.hide()
        self.b_cancel.hide()
        self._set_busy(False)
        t = flash.TARGETS[key]
        if state == "ok" and key == flash.LAMBDA_F042:
            self._status(f"{t.name}: geflasht, 0x190-Telegramme mit Version {text} kamen wieder.", farben.GUT)
        elif state == "ok" and key in (flash.ZANGE, flash.BASIS_ESP):
            self._status(f"{t.name}: Version {text} läuft und ist als gültig markiert.", farben.GUT)
        elif state == "ok" and key == flash.F446:
            self._status(f"{t.name}: Version {text or '?'} geflasht.", farben.GUT)
        elif state == "ok":
            self._status(f"{t.name}: Version {text} läuft.", farben.GUT)
        elif state == "abbruch" and key == flash.LAMBDA_F042:
            self._status(f"{t.name}: {text}", farben.WARNUNG)
        elif state == "abbruch":
            self._status(f"{t.name}: abgebrochen, alte Firmware bleibt.", farben.WARNUNG)
        else:
            self._status(f"{t.name}: {text}", farben.FEHLER)
        if state == "ok":
            self._zuletzt(key, text)
        self._ablauf_weiter(state == "ok")

    def run(self, key: str, cmd):
        self._log(f"––– {flash.TARGETS[key].name} –––")
        self._log(" ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        self._status(f"{flash.TARGETS[key].name}: läuft …", farben.WARNUNG)
        self._set_busy(True)
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(lambda code, _st: self._finished(key, code))
        self.proc.errorOccurred.connect(lambda _e: self._failed(key))
        self.proc.start(cmd[0], cmd[1:])

    def _read(self):
        self._log(bytes(self.proc.readAllStandardOutput()).decode("utf8", "replace"))

    def _finished(self, key: str, code: int):
        if self.proc is None:
            return
        self._read()
        self.proc = None
        self._set_busy(False)
        self.flash_done.emit()
        t = flash.TARGETS[key]
        if code == 0:
            nach = " 12 V einmal aus und ein." if key == flash.BASIS_STLINK else \
                " BOOT0 wieder auf 0 und Reset." if key == flash.F446_STLINK and "dfu" in t.name.lower() else ""
            self._status(f"{t.name}: geflasht.{nach}", farben.GUT)
            self._zuletzt(key, self.rows[key].file_version)
        else:
            self._status(f"{t.name}: Flashen fehlgeschlagen (Code {code}), siehe Ausgabe.", farben.FEHLER)
        self._ablauf_weiter(code == 0)

    def _failed(self, key: str):
        if self.proc is not None and self.proc.error() == QtCore.QProcess.FailedToStart:
            self.proc = None
            self._set_busy(False)
            self.flash_done.emit()
            self._status(f"{flash.TARGETS[key].name}: Werkzeug ließ sich nicht starten.", farben.FEHLER)
            self.queue = []
