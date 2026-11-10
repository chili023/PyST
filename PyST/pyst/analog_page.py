"""
Einstellungen › Analogeingänge: Kanaele der Analogeingaenge benennen und umrechnen (analog.py).

Oben das Geraet (PyST-Basis, STM32 F446, Arduino Mega; vorgewaehlt das verbundene), darunter je Eingang eine Zeile:
Haken aktiv, Name, Einheit (in der Tabelle editierbar), Umrechnung, Spannung und Wert live. Die Umrechnung des gewaehlten
Eingangs steht darunter (keine, 2-Punkt, Faktor/Offset, Balkengrenzen, Nachkommastellen, Vorlagen). Jede Aenderung
wird sofort gespeichert (einstellungen.json "analog") und meldet sich mit `geaendert` (Live-Kacheln, Diagramm).

Live-Werte: Geraete mit '#CAP;…;ai=' bekommen beim Verbinden '#AI 10' (Vorschlag PST-Diag 1), die PyST-Basis wird
etwa jede Sekunde mit '#STAT' gefragt, solange die Seite offen ist oder Basis-Kanaele aktiv sind.
"""
import math
import time
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import analog, theme
from . import einst_widgets as ew
from .bg import Bg, waiting

SP_EINGANG, SP_AKTIV, SP_NAME, SP_EINHEIT, SP_UMR, SP_U, SP_WERT, SP_HINWEIS = range(8)
SPALTEN = ["Eingang", "Aktiv", "Name", "Einheit", "Umrechnung", "Spannung", "Wert", "Hinweis"]
STAT_ALLE_S = 1.0                    # Basis: '#STAT' hoechstens so oft
TABELLE_ZEILEN = 8                   # so viele Zeilen ohne Rollen (Mega: 16 Eingaenge)
OHNE_WERTE_S = 3.0                   # verbunden, aber so lange keine Werte: „Firmware liefert keine Werte“


def _zahl_feld(lo: float = -1e6, hi: float = 1e6, dez: int = 3) -> QtWidgets.QDoubleSpinBox:
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dez)
    s.setKeyboardTracking(False)
    s.setMinimumWidth(90)
    return s


def _opt_text(x: Optional[float]) -> str:
    return "" if x is None else f"{x:g}".replace(".", ",")


def _opt_wert(text: str) -> Optional[float]:
    t = text.strip().replace(",", ".")
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class AnalogPage(QtWidgets.QScrollArea):
    geaendert = QtCore.Signal()      # Konfiguration geaendert (gespeichert)

    def __init__(self, settings: dict, save: Callable[[], None], get_link: Callable[[], object],
                 busy: Callable[[], bool] = lambda: False):
        super().__init__()
        self.settings, self.save, self.get_link, self.busy = settings, save, get_link, busy
        analog.laden(settings)
        self.bg = Bg()
        self.link = None
        self._ai_gesendet = False
        self._last_stat = 0.0
        self._verbunden_t = 0.0
        self._laedt = False                       # Felder werden gerade befuellt: keine Aenderung melden
        self.key = analog.BASIS
        self.liste: List[analog.Kanal] = analog.kanaele(self.key)
        self._melden = QtCore.QTimer(self)        # geaendert gesammelt melden (Tippen im Namen)
        self._melden.setSingleShot(True)
        self._melden.setInterval(300)
        self._melden.timeout.connect(self.geaendert.emit)

        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        root = QtWidgets.QVBoxLayout(inner)
        root.setSpacing(8)
        # Kopf: Geraet, Zustand, Messbereich
        kopf = QtWidgets.QHBoxLayout()
        kopf.addWidget(QtWidgets.QLabel("Gerät"))
        self.geraet_box = QtWidgets.QComboBox()
        for k in analog.REIHENFOLGE:
            self.geraet_box.addItem(analog.GERAETE[k].name, k)
        self.geraet_box.setMinimumWidth(170)
        kopf.addWidget(self.geraet_box)
        self.chip = ew.Chip("nicht verbunden", ew.NEUTRAL)
        kopf.addWidget(self.chip)
        self.bereich_label = ew.neben("", wrap=False)
        kopf.addWidget(self.bereich_label, 1)
        root.addLayout(kopf)
        self.hinweis_label = ew.neben("")
        root.addWidget(self.hinweis_label)
        # Tabelle: eine Zeile je Eingang
        self.tab = QtWidgets.QTableWidget(0, len(SPALTEN))
        self.tab.setHorizontalHeaderLabels(SPALTEN)
        self.tab.verticalHeader().setVisible(False)
        self.tab.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tab.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tab.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed
                                 | QtWidgets.QAbstractItemView.SelectedClicked)
        hh = self.tab.horizontalHeader()
        for c in (SP_EINGANG, SP_AKTIV, SP_EINHEIT, SP_UMR, SP_U, SP_WERT):
            hh.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(SP_NAME, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(SP_HINWEIS, QtWidgets.QHeaderView.Stretch)
        self.tab.setColumnWidth(SP_NAME, 170)
        self.tab.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        root.addWidget(self.tab)
        root.addWidget(ew.neben("Haken setzen, Name und Einheit in der Tabelle eintragen (Doppelklick). Aktive Kanäle "
                                "gibt es in den Live-Kacheln (▾, Gruppe „Analog“), im unteren Diagramm (Kurven ▾) und in "
                                "der Auswertung. In roh.csv bleiben die Rohspannungen, lauf.json merkt sich die "
                                "Umrechnung des Laufs."))
        root.addWidget(self._build_umrechnung())
        self.neu_box = QtWidgets.QCheckBox("Gespeicherte Läufe mit der aktuellen Umrechnung zeigen "
                                           "(sonst mit der Umrechnung, die beim Lauf galt)")
        self.neu_box.setChecked(analog.neu_rechnen())
        root.addWidget(self.neu_box)
        root.addStretch(1)

        self.geraet_box.currentIndexChanged.connect(self._geraet_gewaehlt)
        self.tab.itemChanged.connect(self._zelle_geaendert)
        self.tab.currentCellChanged.connect(lambda r, *_a: self._zeile_zeigen(r))
        self.neu_box.toggled.connect(self._neu_rechnen)
        theme.bei_wechsel(self, lambda _t: self._live_zeigen(), sofort=False)
        self._fuellen()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)

    # ------------------------------------------------------------------ Aufbau
    def _build_umrechnung(self) -> QtWidgets.QGroupBox:
        self.box_umr = QtWidgets.QGroupBox("Umrechnung")
        row = QtWidgets.QHBoxLayout(self.box_umr)
        row.setSpacing(16)
        # links: Art und Parameter
        links = QtWidgets.QFormLayout()
        self.art_box = QtWidgets.QComboBox()
        self.art_box.setMaximumWidth(220)
        for k, t in analog.ARTEN:
            self.art_box.addItem(t, k)
        links.addRow("Art", self.art_box)
        self.f_u1, self.f_w1, self.f_u2, self.f_w2 = (_zahl_feld() for _ in range(4))
        for f in (self.f_u1, self.f_u2):
            f.setSuffix(" V")
        self.zeile_p1 = QtWidgets.QHBoxLayout()
        self.zeile_p2 = QtWidgets.QHBoxLayout()
        for lay, u, w in ((self.zeile_p1, self.f_u1, self.f_w1), (self.zeile_p2, self.f_u2, self.f_w2)):
            lay.addWidget(u)
            lay.addWidget(QtWidgets.QLabel("="))
            lay.addWidget(w)
            lay.addStretch(1)
        self.lab_p1, self.lab_p2 = QtWidgets.QLabel("Punkt 1"), QtWidgets.QLabel("Punkt 2")
        links.addRow(self.lab_p1, self.zeile_p1)
        links.addRow(self.lab_p2, self.zeile_p2)
        self.f_a, self.f_b = _zahl_feld(dez=4), _zahl_feld(dez=4)
        self.zeile_ab = QtWidgets.QHBoxLayout()
        self.zeile_ab.addWidget(QtWidgets.QLabel("W ="))
        self.zeile_ab.addWidget(self.f_a)
        self.zeile_ab.addWidget(QtWidgets.QLabel("· U +"))
        self.zeile_ab.addWidget(self.f_b)
        self.zeile_ab.addStretch(1)
        self.lab_ab = QtWidgets.QLabel("Faktor/Offset")
        links.addRow(self.lab_ab, self.zeile_ab)
        self.fehler_label = ew.AutoLabel("")
        theme.stufe(self.fehler_label, "err")
        links.addRow(self.fehler_label)
        row.addLayout(links, 3)
        # Mitte: Balken und Stellen
        mitte = QtWidgets.QFormLayout()
        self.f_lo, self.f_hi = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        for f in (self.f_lo, self.f_hi):
            f.setPlaceholderText("automatisch")
            f.setMaximumWidth(120)
        mitte.addRow("Balken von", self.f_lo)
        mitte.addRow("bis", self.f_hi)
        self.f_dez = QtWidgets.QSpinBox()
        self.f_dez.setRange(0, 4)
        mitte.addRow("Nachkommastellen", self.f_dez)
        row.addLayout(mitte, 2)
        # rechts: Vorlagen und Probe
        rechts = QtWidgets.QVBoxLayout()
        self.b_vorlage = QtWidgets.QPushButton("Vorlage ▾")
        self.b_vorlage.setToolTip("Gängige Sensoren als Ausfüllhilfe: Umrechnung, Einheit und Balken eintragen")
        m = QtWidgets.QMenu(self.b_vorlage)
        for text, werte in analog.VORLAGEN:
            m.addAction(text).triggered.connect(lambda _c=False, w=werte: self._vorlage(w))
        self.b_vorlage.setMenu(m)
        rechts.addWidget(self.b_vorlage, 0, QtCore.Qt.AlignLeft)
        self.probe_label = ew.neben("")
        rechts.addWidget(self.probe_label)
        rechts.addStretch(1)
        row.addLayout(rechts, 2)
        self.art_box.currentIndexChanged.connect(self._umr_geaendert)
        for f in (self.f_u1, self.f_w1, self.f_u2, self.f_w2, self.f_a, self.f_b):
            f.valueChanged.connect(self._umr_geaendert)
        self.f_dez.valueChanged.connect(self._umr_geaendert)
        for f in (self.f_lo, self.f_hi):
            f.editingFinished.connect(self._umr_geaendert)
        return self.box_umr

    # ------------------------------------------------------------------ Geraet und Tabelle
    def waehle_geraet(self, key: str):
        i = self.geraet_box.findData(key)
        if i >= 0:
            self.geraet_box.setCurrentIndex(i)

    def _geraet_gewaehlt(self, _i: int):
        self.key = self.geraet_box.currentData()
        self.liste = analog.kanaele(self.key)
        self._fuellen()

    def _fuellen(self):
        g = analog.GERAETE[self.key]
        self.bereich_label.setText(f"{len(g.eingaenge)} Eingänge, {g.bereich}")
        self.hinweis_label.setText(g.hinweis)
        self._laedt = True
        self.tab.setRowCount(len(self.liste))
        for r, (e, k) in enumerate(zip(g.eingaenge, self.liste)):
            it = QtWidgets.QTableWidgetItem(e.pin)
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.tab.setItem(r, SP_EINGANG, it)
            it = QtWidgets.QTableWidgetItem("")
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if k.aktiv else QtCore.Qt.Unchecked)
            self.tab.setItem(r, SP_AKTIV, it)
            for c, text, tip in ((SP_NAME, k.name, "Name des Kanals, z. B. Öldruck"),
                                 (SP_EINHEIT, k.einheit, "Einheit des umgerechneten Werts, z. B. bar")):
                it = QtWidgets.QTableWidgetItem(text)
                it.setToolTip(tip)
                self.tab.setItem(r, c, it)
            for c in (SP_UMR, SP_U, SP_WERT, SP_HINWEIS):
                it = QtWidgets.QTableWidgetItem("")
                it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                if c in (SP_U, SP_WERT):
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.tab.setItem(r, c, it)
            self.tab.item(r, SP_HINWEIS).setText(e.fest)
            if e.fest:
                self.tab.item(r, SP_HINWEIS).setToolTip(e.fest + " – dann misst der Eingang das AFR (Lambda › "
                                                       "AFR-Eingang), ein eigener Kanal wäre doppelt")
            self._umr_text(r)
        self._laedt = False
        self._tabelle_hoehe()
        r = max(0, self.tab.currentRow())
        self.tab.setCurrentCell(r, SP_NAME)
        self._zeile_zeigen(r)
        self._live_zeigen()

    def _tabelle_hoehe(self):
        """So hoch wie die Zeilen (Basis 2, F446 2), beim Mega hoechstens TABELLE_ZEILEN, dann rollen."""
        n = min(self.tab.rowCount(), TABELLE_ZEILEN)
        h = self.tab.horizontalHeader().sizeHint().height() + 2 * self.tab.frameWidth()
        h += sum(self.tab.rowHeight(r) for r in range(n)) if n else 30
        self.tab.setFixedHeight(h + 2)

    def _umr_text(self, r: int):
        k = self.liste[r]
        it = self.tab.item(r, SP_UMR)
        fehler = k.fehler()
        it.setText(("⚠ " if fehler else "") + k.umrechnung_text())
        it.setToolTip(fehler)

    def _zelle_geaendert(self, it: QtWidgets.QTableWidgetItem):
        if self._laedt:
            return
        r, c = it.row(), it.column()
        if not 0 <= r < len(self.liste):
            return
        k = self.liste[r]
        if c == SP_AKTIV:
            k.aktiv = it.checkState() == QtCore.Qt.Checked
        elif c == SP_NAME:
            k.name = it.text().strip()[:40]
        elif c == SP_EINHEIT:
            k.einheit = it.text().strip()[:12]
            self._umr_text(r)
            if r == self.tab.currentRow():
                self._probe()
        else:
            return
        self._speichern()

    # ------------------------------------------------------------------ Umrechnung des gewaehlten Eingangs
    def _zeile_zeigen(self, r: int):
        if not 0 <= r < len(self.liste):
            self.box_umr.setEnabled(False)
            return
        self.box_umr.setEnabled(True)
        k = self.liste[r]
        e = analog.GERAETE[self.key].eingaenge[r]
        self.box_umr.setTitle(f"Umrechnung – {e.pin}" + (f" ({k.name})" if k.name else ""))
        self._laedt = True
        self.art_box.setCurrentIndex(max(0, self.art_box.findData(k.art)))
        for f, v in ((self.f_u1, k.u1), (self.f_w1, k.w1), (self.f_u2, k.u2), (self.f_w2, k.w2), (self.f_a, k.a),
                     (self.f_b, k.b)):
            f.setValue(v)
        self.f_lo.setText(_opt_text(k.lo))
        self.f_hi.setText(_opt_text(k.hi))
        self.f_dez.setValue(k.dez)
        self._laedt = False
        self._art_zeigen()
        self._probe()

    def _art_zeigen(self):
        art = self.art_box.currentData()
        zwei, fak = art == analog.ZWEIPUNKT, art == analog.FAKTOR
        for w in (self.lab_p1, self.lab_p2):
            w.setVisible(zwei)
        for lay in (self.zeile_p1, self.zeile_p2):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is not None:
                    w.setVisible(zwei)
        self.lab_ab.setVisible(fak)
        for i in range(self.zeile_ab.count()):
            w = self.zeile_ab.itemAt(i).widget()
            if w is not None:
                w.setVisible(fak)

    def _umr_geaendert(self, *_a):
        if self._laedt:
            return
        r = self.tab.currentRow()
        if not 0 <= r < len(self.liste):
            return
        k = self.liste[r]
        k.art = self.art_box.currentData()
        k.u1, k.w1, k.u2, k.w2 = self.f_u1.value(), self.f_w1.value(), self.f_u2.value(), self.f_w2.value()
        k.a, k.b = self.f_a.value(), self.f_b.value()
        k.lo, k.hi = _opt_wert(self.f_lo.text()), _opt_wert(self.f_hi.text())
        k.dez = self.f_dez.value()
        self._art_zeigen()
        self._umr_text(r)
        self._probe()
        self._speichern()

    def _vorlage(self, werte: dict):
        r = self.tab.currentRow()
        if not 0 <= r < len(self.liste):
            return
        k = self.liste[r]
        k.vorlage(werte)
        self._laedt = True
        self.tab.item(r, SP_EINHEIT).setText(k.einheit)
        self._laedt = False
        self._umr_text(r)
        self._zeile_zeigen(r)
        self._speichern()

    def _probe(self):
        """Beispiel der Umrechnung (und Fehler U1 = U2) unter den Vorlagen."""
        r = self.tab.currentRow()
        if not 0 <= r < len(self.liste):
            return
        k = self.liste[r]
        fehler = k.fehler()
        self.fehler_label.setText(fehler)
        if fehler:
            self.probe_label.setText("")
            return
        e = analog.GERAETE[self.key].eingaenge[r]
        einh = k.einheit_text()
        lo, hi = k.bereich(e.u_max)
        punkte = (k.u1, k.u2) if k.art == analog.ZWEIPUNKT else (0.0, e.u_max / 2, e.u_max)
        teile = [f"{analog.zahl(u, 2)} V → {analog.zahl(k.wert(u), k.dez)} {einh}".rstrip() for u in punkte]
        if k.art == analog.ZWEIPUNKT:
            teile.append(f"{analog.zahl(e.u_max, 1)} V → {analog.zahl(k.wert(e.u_max), k.dez)} {einh}".rstrip()
                         + " (Ende Messbereich)")
        self.probe_label.setText("Beispiel:\n" + "\n".join(teile) +
                                 f"\nBalken {analog.zahl(lo, k.dez)} … {analog.zahl(hi, k.dez)} {einh}".rstrip())

    # ------------------------------------------------------------------ Speichern
    def _speichern(self):
        analog.speichern(self.settings, self.key, self.liste)
        self.save()
        self._live_zeigen()
        self._melden.start()

    def _neu_rechnen(self, an: bool):
        analog.set_neu_rechnen(self.settings, an)
        self.save()
        self._melden.start()

    # ------------------------------------------------------------------ Live
    def _geraet_link(self) -> Optional[str]:
        return analog.geraet_von_link(self.link)

    def poll(self):
        self.bg.poll()
        link = self.get_link()
        now = time.time()
        if link is not self.link:
            self.link, self._ai_gesendet, self._last_stat = link, False, 0.0
            self._verbunden_t = now
            g = self._geraet_link()
            if g is not None and g != self.key:
                self.waehle_geraet(g)                       # vorausgewaehlt: das verbundene Geraet
        if link is not None and not waiting() and not self.busy():
            if analog.caps_eingaenge(getattr(link, "caps", {})) and not self._ai_gesendet:
                self._ai_gesendet = True
                self.bg.submit("ai_ein", link.analog_ein, lambda _r, _e: None)
            elif (getattr(link, "is_basis", False) and not analog.caps_eingaenge(link.caps)
                  and now - self._last_stat >= STAT_ALLE_S and (self.isVisible() or analog.aktive(analog.BASIS))):
                self._last_stat = now
                self.bg.submit("ai_stat", link.analog_stat, lambda _r, _e: None)
        if self.isVisible():
            self._live_zeigen()

    def _live_zeigen(self):
        link = self.link
        g = self._geraet_link()
        dieses = link is not None and g == self.key
        werte = link.analog_frisch() if dieses else {}
        einh = getattr(link, "analog_einheit", {}) if dieses else {}
        if not dieses:
            self.chip.set_zustand(ew.NEUTRAL, "nicht verbunden" if link is None or g is None else
                                  f"verbunden: {analog.GERAETE[g].name}")
        elif werte:
            self.chip.set_zustand(theme.GUT, "✔ verbunden · live")
        elif time.time() - self._verbunden_t > OHNE_WERTE_S:
            self.chip.set_zustand(theme.WARNUNG, "verbunden · keine Analogwerte (Firmware)")
        else:
            self.chip.set_zustand(ew.NEUTRAL, "verbunden · warte auf Werte")
        dim = QtGui.QBrush(QtGui.QColor(theme.farbe("dim")))
        normal = QtGui.QBrush(QtGui.QColor(theme.farbe("text")))
        g_info = analog.GERAETE[self.key]
        vorher, self._laedt = self._laedt, True          # Live-Zellen sind keine Eingabe
        for r, (e, k) in enumerate(zip(g_info.eingaenge, self.liste)):
            iu, iw = self.tab.item(r, SP_U), self.tab.item(r, SP_WERT)
            if iu is None or iw is None:
                continue
            u = werte.get(e.id)
            ok = u is not None and math.isfinite(u)
            iu.setText(f"{analog.zahl(u, 3)} {einh.get(e.id, 'V')}" if ok else "–")
            w = k.wert(u) if ok else None
            iw.setText(f"{analog.zahl(w, k.dez)} {k.einheit_text()}".rstrip() if w is not None else "–")
            b = normal if ok else dim
            iu.setForeground(b)
            iw.setForeground(b if k.aktiv or not ok else dim)
        self._laedt = vorher

    def close_page(self):
        self.timer.stop()
