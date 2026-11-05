"""
Unterreiter „Tasten“: Tasten-Setup der Funk-Zange, und das große Band über den Reitern für Hinweise an den Fahrer.
Regeln und Übertragung stehen in tasten.py.
"""
import time
from typing import Callable, Dict

from PySide6 import QtCore, QtWidgets

from . import tasten
from .hw_page import _Bg


class TastenBand(QtWidgets.QLabel):
    """Über allen Reitern, groß genug für den Blick vom Motorrad; verschwindet von selbst."""

    def __init__(self):
        super().__init__()
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setWordWrap(True)
        self.farbe = ""
        self.hide()
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def zeige(self, text: str, farbe: str, dauer_s: float = 0.0):
        """dauer_s = 0: bleibt, bis der nächste Text kommt."""
        self.setText(text)
        if farbe != self.farbe:                    # läuft im Sekundenzähler, neu gestalten kostet
            self.farbe = farbe
            self.setStyleSheet(f"background: {farbe}; color: white; font-size: 26px; font-weight: 700; padding: 10px;")
        self.show()
        self.timer.stop()
        if dauer_s:
            self.timer.start(int(dauer_s * 1000))


class TastenPage(QtWidgets.QScrollArea):
    geaendert = QtCore.Signal()

    def __init__(self, settings: dict, save_settings: Callable[[], None], get_link: Callable[[], object]):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.get_link = get_link
        self.setup = tasten.laden(settings.get("zange_tasten"))
        self.sync = tasten.TastenSync(tasten.packen(self.setup))
        self.bg = _Bg()
        self._nr = 0
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        root = QtWidgets.QVBoxLayout(inner)
        top = QtWidgets.QHBoxLayout()
        root.addLayout(top)

        g = QtWidgets.QGroupBox("Einstellbar (außerhalb des Blitzmodus)")
        form = QtWidgets.QFormLayout(g)
        self.combos: Dict[str, QtWidgets.QComboBox] = {}
        for geste, text in tasten.GESTEN:
            c = QtWidgets.QComboBox()
            for aktion, atext in tasten.AKTIONEN:
                c.addItem(atext, aktion)
            c.setCurrentIndex(c.findData(self.setup[geste]))
            c.currentIndexChanged.connect(self._auswahl)
            form.addRow(text, c)
            self.combos[geste] = c
        self.b_standard = QtWidgets.QPushButton("Standard wiederherstellen")
        self.b_standard.clicked.connect(self.standard)
        form.addRow(self.b_standard)
        top.addWidget(g, 1)

        f = QtWidgets.QGroupBox("Fest in der Firmware, auch ohne PC")
        ff = QtWidgets.QFormLayout(f)
        for was, wirkung in tasten.FEST:
            ff.addRow(was, QtWidgets.QLabel(wirkung))
        top.addWidget(f, 1)

        self.doppelt = QtWidgets.QLabel("")
        self.doppelt.setWordWrap(True)
        self.doppelt.setStyleSheet("color: #ef6c00; font-weight: 600;")
        root.addWidget(self.doppelt)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        hint = QtWidgets.QLabel("Kurz = beim Loslassen, lang = 1,5 s gehalten, löst einmal aus. Ist ein PC verbunden, gilt dieses "
                                "Setup, sonst der Standard. Die Zange speichert es nicht: Nach einem Neustart schickt PyST es "
                                "neu. Neuer Lauf und Übersetzung brauchen PyST, Lambda die Basis: Fehlt das, zeigt die Zange "
                                "das Fehlerbild (3 × lang). Übersetzung messen: Drehzahl halten, Taste startet 5 s Messung, "
                                "erst ein zweiter Druck innerhalb von 30 s übernimmt den Wert.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        root.addStretch(1)
        self._zeige()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(200)

    # ------------------------------------------------------------------ Setup
    def _auswahl(self, *_):
        self.setup = {g: c.currentData() for g, c in self.combos.items()}
        self.settings["zange_tasten"] = dict(self.setup)
        self.save_settings()
        self.sync.setzen(tasten.packen(self.setup))
        self._zeige()
        self.geaendert.emit()

    def standard(self):
        for g, c in self.combos.items():
            c.blockSignals(True)
            c.setCurrentIndex(c.findData(tasten.STANDARD[g]))
            c.blockSignals(False)
        self._auswahl()

    def _zeige(self):
        self.doppelt.setText(tasten.doppelt_text(self.setup))
        text, farbe = self.sync.text()
        self.status.setText(text)
        self.status.setStyleSheet(f"font-weight: 600; color: {farbe};")

    # ------------------------------------------------------------------ Zange
    def zange_zustand(self, state: str):
        """Aus der Statusleiste: ok, weg, usb oder leer."""
        self.sync.zange(state in ("ok", "usb"))
        self._zeige()

    def on_event(self, ev):
        if ev.kind == "ZANGE" and ev.args[:1] and ev.args[0] in ("verbunden", "gekoppelt", "neustart"):
            self.sync.neu()
            self._zeige()

    def led(self, muster: int):
        """Rückmeldung an der Zange. _Bg verwirft gleiche Schlüssel, jede Antwort muss aber hinaus."""
        link = self.get_link()
        if link is None or not link.is_basis:
            return
        self._nr += 1
        self.bg.submit(f"led{self._nr}", lambda: link.zange_led(muster), lambda _r, _e: None)

    def poll(self):
        self.bg.poll()
        link = self.get_link()
        if link is None:
            return
        wert = self.sync.faellig(time.time())
        if wert is None:
            return
        self._zeige()
        self._nr += 1
        self.bg.submit(f"setup{self._nr}", lambda: link.zange_tasten(wert),
                       lambda res, err: self._antwort(wert, [] if err else res))

    def _antwort(self, wert: int, lines):
        self.sync.antwort(time.time(), wert, lines or [])
        self._zeige()
