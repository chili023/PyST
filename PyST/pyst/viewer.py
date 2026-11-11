"""
Auswerter im Stil von MegaLogViewer (Reiter Auswertung, Entwurf v3 vom 08.11.2026).

Aufbau:
- Kopfzeile: X-Achse, Felder, nur Kurvenbereich, Layout (Speichern / Speichern unter … / Löschen),
  rechts Importieren (Menü: Dateien … / Ordner mit Unterordnern …), PDF-Bericht …, CSV-Export …
- links die SeitenLeiste „Läufe & Kanäle“ (32-px-Streifen, Overlay nach 300 ms, Anpinnen, Zustand gespeichert):
  Läufe auswählen (Fahrzeug -> Läufe nach Datum oder Setup, auch Mittelwerte) -> „Hinzufügen“, In der Auswertung,
  Kanäle je Feld. Fahrzeug- und Setup-Daten lassen sich direkt bearbeiten.
- Mitte: Laufleiste „ANGEZEIGTE LÄUFE“ (laufleiste.py) und darunter bis zu 4 Felder übereinander mit gemeinsamer
  X-Achse (Zeit oder Motordrehzahl). Jeder Kanal wird für sich auf die Feldhöhe skaliert (wie MLV), Kanäle gleicher
  Einheit teilen sich eine Skala. Ein Lauf: Farbe je Kanal. Mehrere Läufe: Farbe je Lauf, Linienart je Kanal.
  Mittelwert-Läufe: eigene Farbe (avg_color), dicke Linie, Band min–max bzw. ± σ halbtransparent.
- rechts Reiter „Werte am Cursor“ (cursorwerte.py: Kacheln mit den Werten aller angezeigten Läufe) und
  „Setup-Vergleich“ (Setup-Einträge nebeneinander, Unterschiede markiert).
- Cursor mit Maus oder Pfeiltasten (Schritt = ein Telegramm des ersten angezeigten Laufs).
- „Mittelwert bilden …“ (mittelwert_dialog.py) aus den markierten Läufen; Mittelwerte haben keine Rohdaten
  (CSV-Export liefert die gemittelten Kurven, Ausrollversuch/Kanäle des Hauptfensters bekommen sie nicht).
"""
import json
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import mittelwert as mw
from . import analog, physics
from . import theme
from .channels import Channel
from .cursorwerte import CursorSpalte, KachelDaten, bereich_runden, dez_fuer, wert_bei
from .db import SETUP_FIELDS, Database
from .dialogs import EditDialog
from .kacheln import Kanal, SeitenLeiste, farb_icon, zahl
from .lauf_dialog import LaufSetupDialog
from .laufleiste import Eintrag, LaufLeiste, datum_text, reihenfolge, schnellwert_zellen, zeit_text
from .mittelwert_dialog import (BAND_ARTEN, BAND_STANDARD, MittelwertDialog, band_text, fuell_band,
                                kanaele_aus_mittelwert, zusatz_lesen, zusatz_schreiben)
from .plots import run_color_basis
from .umbruch import UmbruchLayout

PALETTE = ["#c62828", "#1f5fbf", "#2e7d32", "#ef6c00", "#6a1b9a", "#00838f", "#ad1457", "#4e342e",
           "#9e9d24", "#37474f", "#d84315", "#0277bd", "#558b2f", "#8e24aa", "#00695c", "#f9a825"]
STYLES = [QtCore.Qt.SolidLine, QtCore.Qt.DashLine, QtCore.Qt.DotLine, QtCore.Qt.DashDotLine]
STYLE_MARK = ["──", "╌╌", "┈┈", "─·"]
DEFAULT_PANES = [["Drehzahl Motor", "Drehzahl Zündung"], ["Leistung", "Drehmoment"],
                 ["Übersetzung gefiltert", "Übersetzung ECU/Rolle gefiltert", "Übersetzung eingestellt"],
                 ["Lambda", "EGT 1", "EGT 2", "TPS (ECU)"]]
MAX_PANES = 4
X_ACHSEN = [("Zeit", "Zeit"), ("Motordrehzahl", "Drehzahl Motor")]     # (Anzeige, Kanal)
FELD_HOEHEN = [5, 3, 2, 2]                                             # Hoehenverhaeltnis der Felder
LINIE, LINIE_MW = 2.2, 3.6                                             # Linienbreite Lauf / Mittelwert
BAND_ALPHA = 70
# Live-Kanal je Fahrzeug (db.live_plaetze) -> Kanal der Auswertung: Balkenbereich und Zonen der Kacheln
LIVE_ZU_KANAL = {"egt1": "EGT 1", "egt2": "EGT 2", "lam": "Lambda", "v": "Geschwindigkeit", "n": "Drehzahl Motor",
                 "ign": "Drehzahl Zündung", "rate": "Messfrequenz"}
HINWEIS = "Maus = Cursor · ←/→ = ein Telegramm weiter · Mausrad = Zoom · ziehen = verschieben"


# ============================================================================================== reine Funktionen
def lauf_bezeichnungen(rows: Sequence) -> Dict[int, str]:
    """Kurzbezeichnung je Lauf fuer Kacheln und Chips: „16:24“, Mittelwert „Ø Ref.“ (mehrere: „Ø Ref. 2“ …);
    gleiche Uhrzeit an verschiedenen Tagen -> „26.05. 16:24“."""
    out: Dict[int, str] = {}
    zeiten = [zeit_text(r) for r in rows if not r.ist_mittelwert]
    k = 0
    for r in rows:
        if r.ist_mittelwert:
            k += 1
            out[r.id] = "Ø Ref." if k == 1 else f"Ø Ref. {k}"
        else:
            z = zeit_text(r)
            out[r.id] = f"{datum_text(r)[:6]} {z}" if zeiten.count(z) > 1 else z
    return out


def laufwerte_am_cursor(laeufe: Sequence[tuple], x_at: Optional[float]) -> List[Tuple[str, str, Optional[float]]]:
    """laeufe: [(Basisfarbe, Bezeichnung, x, y, interpolieren)] -> [(Basisfarbe, Bezeichnung, Wert am Cursor)];
    Laeufe ohne Daten an der Stelle bekommen None („–“)."""
    out = []
    for farbe, bez, x, y, interp in laeufe:
        v = None if x_at is None else wert_bei(x, y, x_at, interpolieren=interp)
        out.append((farbe, bez, v))
    return out


class _Teiler(QtWidgets.QSplitter):
    """Teiler Mitte | rechte Spalte. Ein gespeicherter Stand der alten Auswertung (drei Spalten) wuerde die rechte
    Spalte riesig machen -> dann die Standardaufteilung."""
    RECHTS_MAX = 760

    def restoreState(self, state) -> bool:
        ok = super().restoreState(state)
        s = self.sizes()
        if len(s) == 2 and (s[1] > self.RECHTS_MAX or s[1] < 200):
            self.setSizes([max(600, sum(s) - 400), 400])
        return ok


class LogViewer(QtWidgets.QWidget):
    db_changed = QtCore.Signal()               # Fahrzeug/Setup/Lauf bearbeitet -> Hauptfenster aktualisiert

    def __init__(self, db: Database, load_channels: Callable[[int], Optional[tuple]]):
        """load_channels(run_id) -> (Kanaele, Kurvenbereich[, Marker]) des Laufs (vom Hauptfenster, mit Cache).
        Fuer Mittelwert-Laeufe wird es nicht aufgerufen (keine Rohdaten), die laedt die Auswertung selbst."""
        super().__init__()
        self.db = db
        self.load_channels = load_channels
        self.selected: List[int] = []           # Laeufe in der Auswertung (Reihenfolge = Hinzufuegen)
        self.hidden: set = set()                # in der Auswertung, aber ausgeblendet
        self.markiert: set = set()              # Spalte „mark.“ (fuer „Mittelwert bilden …“)
        self.data: Dict[int, tuple] = {}        # run_id -> (Kanaele, Kurvenbereich[, Marker])
        self.baender: Dict[int, dict] = {}      # Mittelwert: Kanal -> {"minmax": (u, o), "sigma": (u, o)}
        self.mw_info: Dict[int, dict] = {}      # Mittelwert: {"res": MittelwertErgebnis, "band": art}
        self.rows: Dict[int, object] = {}       # run_id -> RunRow (bei jedem Neuladen aufgefrischt)
        self.bez: Dict[int, str] = {}           # run_id -> „16:24“ / „Ø Ref.“
        self.cursor_idx = 0
        self._cursor_frei: Optional[float] = None    # Maus: X frei ueber alle Laeufe; None = Telegramm des ersten
        self.color_of: Dict[str, str] = {}
        # Layout: Kanaele je Feld bleiben erhalten, auch wenn ein geladener Lauf einen Kanal nicht hat
        self.pane_channels: List[List[str]] = [list(p) for p in DEFAULT_PANES]
        self.kachel_wahl: Dict[str, str] = self._meta_json("viewer_kacheln", {})
        self._applying = False
        self._bereiche: Dict[str, Optional[tuple]] = {}
        self._live: Optional[list] = None
        self._ladefehler: Dict[int, str] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(5)
        root.addLayout(self._build_bar())

        center = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(center)
        cl.setContentsMargins(4, 0, 0, 0)
        cl.setSpacing(4)
        self.leiste = LaufLeiste()
        cl.addWidget(self.leiste)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(theme.farbe("plot"))
        theme.ganz_neu_zeichnen(self.glw)                # keine Reste der Cursorlinie (Mac/Retina)
        cl.addWidget(self.glw, 1)
        self.hint = theme.rolle(QtWidgets.QLabel(HINWEIS), "neben")
        self.hint.setWordWrap(True)
        cl.addWidget(self.hint)

        self.seitenleiste = SeitenLeiste("Läufe & Kanäle", self._build_left(), center, breite=400)
        self.seitenleiste.set_zustand(self._meta_json("viewer_seitenleiste", {}))
        self.seitenleiste.angepinnt_geaendert.connect(
            lambda _an: self.db.set_meta("viewer_seitenleiste", json.dumps(self.seitenleiste.zustand())))
        self.seitenleiste.breite_geaendert.connect(
            lambda _b: self.db.set_meta("viewer_seitenleiste", json.dumps(self.seitenleiste.zustand())))

        self.right_tabs = QtWidgets.QTabWidget()
        self.cursor = CursorSpalte()
        self.setup_table = QtWidgets.QTableWidget(0, 1)
        self.setup_table.verticalHeader().setVisible(False)
        self.setup_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.right_tabs.addTab(self.cursor, "Werte am Cursor")
        self.right_tabs.addTab(self.setup_table, "Setup-Vergleich")
        self.right_tabs.setMinimumWidth(300)
        split = _Teiler()
        split.setChildrenCollapsible(False)
        split.addWidget(self.seitenleiste)
        split.addWidget(self.right_tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1500, 400])
        self.split = split
        root.addWidget(split, 1)

        self.plots: List[pg.PlotItem] = []
        self.lines: List[pg.InfiniteLine] = []
        # rechte Spalte „Werte am Cursor“ wie in Messen: bei jeder Mausbewegung sofort nur die Zahlen tauschen
        # (_cursor_werte). Bereiche, Kanalauswahl und Maxima baut _update_cursor nur, wenn sich Laeufe/Felder aendern.
        # Frueher startete jede Bewegung einen 25-ms-Timer neu: bei laufender Maus blieb rechts alles stehen und
        # sprang erst beim Anhalten (Testrueckmeldung 10.11.2026).
        self._cursor_plan: Optional[dict] = None
        theme.bei_wechsel(self, self._thema, sofort=False)
        self.glw.scene().sigMouseMoved.connect(self._mouse_moved)
        for sig in (self.xaxis.currentIndexChanged, self.only_seg.toggled, self.panes_spin.valueChanged):
            sig.connect(lambda _=None: (self._layout_changed(), self._redraw()))
        self.layout_combo.activated.connect(lambda _i: self._layout_chosen())
        self.layout_save_btn.clicked.connect(self._layout_save)
        self.layout_save_as_btn.clicked.connect(self._layout_save_as)
        self.layout_del_btn.clicked.connect(self._layout_delete)
        self.leiste.sichtbar_geaendert.connect(self._set_sichtbar)
        self.leiste.markiert_geaendert.connect(self._set_markiert)
        self.leiste.notiz_geaendert.connect(self._notiz)
        self.leiste.setup_gewaehlt.connect(self._setup_dialog)
        self.leiste.quellen_gewaehlt.connect(self._quellen_menue)
        self.leiste.entfernt.connect(self._entfernen)
        self.leiste.mittelwert_gewaehlt.connect(self._mittelwert_bilden)
        self.cursor.kanal_gewaehlt.connect(self._kachel_gewaehlt)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        cur = self._meta_json("viewer_layout", None)
        if cur:
            self.apply_layout(cur, redraw=False)
        self._fill_layout_combo()
        self.reload()

    def _meta_json(self, key: str, default):
        try:
            v = json.loads(self.db.get_meta(key, "null"))
        except ValueError:
            return default
        if v is None or (default is not None and not isinstance(v, type(default))):
            return default
        return v

    # ------------------------------------------------------------ Kopfzeile
    def _build_bar(self) -> UmbruchLayout:
        """Kopfzeile; bei schmalem Fenster bricht sie in Gruppen um (Import/Export in die zweite Zeile)."""
        bar = UmbruchLayout(abstand=6)
        self.xaxis = QtWidgets.QComboBox()
        for text, kanal in X_ACHSEN:
            self.xaxis.addItem(text, kanal)
        self.xaxis.setMinimumWidth(150)
        self.panes_spin = QtWidgets.QSpinBox()
        self.panes_spin.setRange(1, MAX_PANES)
        self.panes_spin.setValue(4)
        self.only_seg = QtWidgets.QCheckBox("nur Kurvenbereich")
        for label, w_ in (("X-Achse", self.xaxis), ("Felder", self.panes_spin)):
            bar.add_gruppe(QtWidgets.QLabel(label), w_)
        bar.add(self.only_seg)
        bar.addSpacing(16)
        self.layout_combo = QtWidgets.QComboBox()
        self.layout_combo.setMinimumWidth(200)
        self.layout_combo.setToolTip("Gespeicherte Auswerte-Layouts (Felder, Kanäle, X-Achse)")
        self.layout_save_btn = QtWidgets.QPushButton("Speichern")
        self.layout_save_as_btn = QtWidgets.QPushButton("Speichern unter …")
        self.layout_del_btn = QtWidgets.QPushButton("Löschen")
        # Wunschbreite der Layout-Auswahl 270 bei genug Platz
        bar.add_gruppe(QtWidgets.QLabel("Layout"), self.layout_combo, breite={self.layout_combo: 270})
        bar.add_gruppe(self.layout_save_btn, self.layout_save_as_btn, self.layout_del_btn)
        bar.addStretch(1)
        # Import und Export (vom Hauptfenster verbunden), mit vollem Text
        # Menue am Knopf: einzelne Dateien oder ein ganzer Ordner mit Unterordnern (ordner_import)
        self.import_btn = QtWidgets.QPushButton("Importieren")
        self.import_btn.setToolTip("Alte Messungen in die Datenbank übernehmen: LabVIEW-XML (PST 3.x), PyST-Läufe "
                                   "(lauf.json, .pyst), Terminal-Mitschnitte – einzelne Dateien oder ein ganzer "
                                   "Ordner mit Unterordnern")
        menue = QtWidgets.QMenu(self.import_btn)
        self.import_dateien = menue.addAction("Dateien …")
        self.import_ordner = menue.addAction("Ordner mit Unterordnern …")
        self.import_btn.setMenu(menue)
        self.pdf_btn = QtWidgets.QPushButton("PDF-Bericht …")
        self.pdf_btn.setToolTip("Leistungsdiagramm mit den im Reiter Messen angehakten Läufen und deren Setups")
        self.csv_knopf = QtWidgets.QPushButton("CSV-Export …")
        self.csv_knopf.setToolTip("Alle Kanäle des markierten Laufs als CSV (Excel); beim Mittelwert die gemittelten "
                                  "Kurven")
        # Das Hauptfenster verbindet csv_btn (exportiert Rohdaten ueber den Lauf-Cache). Der sichtbare Knopf prueft
        # vorher, ob ein Mittelwert markiert ist – der hat keine Rohdaten und wird hier exportiert.
        self.csv_btn = QtWidgets.QPushButton("CSV-Export …", self)
        self.csv_btn.hide()
        self.csv_knopf.clicked.connect(self._csv)
        knoepfe = (self.import_btn, self.pdf_btn, self.csv_knopf)
        # Wunschbreite 130 bei genug Platz, sonst die Textbreite
        bar.add_gruppe(*knoepfe, breite={b: 130 for b in knoepfe})
        return bar

    # ------------------------------------------------------------ Layout
    def layout_state(self) -> dict:
        return {"felder": self.panes_spin.value(), "x": self.xaxis.currentData(),
                "nur_kurve": self.only_seg.isChecked(), "kanaele": [list(p) for p in self.pane_channels]}

    def apply_layout(self, lay: dict, redraw: bool = True):
        self._applying = True
        try:
            self.panes_spin.setValue(int(lay.get("felder", 4)))
            x = lay.get("x", "Zeit")
            i = self.xaxis.findData(x)
            if i < 0:
                i = self.xaxis.findText(x)
            self.xaxis.setCurrentIndex(max(0, i))
            self.only_seg.setChecked(bool(lay.get("nur_kurve", False)))
            chans = lay.get("kanaele") or DEFAULT_PANES
            self.pane_channels = [list(chans[i]) if i < len(chans) else [] for i in range(MAX_PANES)]
        finally:
            self._applying = False
        self._layout_changed()
        if redraw:
            self._fill_lists()
            self._redraw()

    def _layout_changed(self):
        """Aktuelles Layout merken (bleibt ueber das Beenden hinaus erhalten)."""
        if self._applying:
            return
        self.db.set_meta("viewer_layout", json.dumps(self.layout_state(), ensure_ascii=False))
        self._update_layout_dirty()

    def _layouts(self) -> dict:
        try:
            return json.loads(self.db.get_meta("viewer_layouts", "{}")) or {}
        except ValueError:
            return {}

    def _fill_layout_combo(self):
        cur = self.db.get_meta("viewer_layout_name", "")
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        self.layout_combo.addItem("(ungespeichert)", "")
        for name in sorted(self._layouts(), key=str.lower):
            self.layout_combo.addItem(name, name)
        i = self.layout_combo.findData(cur)
        self.layout_combo.setCurrentIndex(max(0, i))
        self.layout_combo.blockSignals(False)
        self._update_layout_dirty()

    def _update_layout_dirty(self):
        name = self.layout_combo.currentData() if hasattr(self, "layout_combo") else ""
        saved = self._layouts().get(name) if name else None
        state = self.layout_state()
        if saved is not None:                  # alte Layouts: X-Achse als Text, Anzeigen der Kachelzeile
            saved = dict(saved)
            if self.xaxis.findData(saved.get("x")) < 0 and self.xaxis.findText(str(saved.get("x"))) >= 0:
                saved["x"] = self.xaxis.itemData(self.xaxis.findText(saved["x"]))
            saved = {k: saved.get(k) for k in state}
        dirty = bool(name) and saved != state
        self.layout_save_btn.setEnabled(bool(name) and dirty)
        self.layout_del_btn.setEnabled(bool(name))
        self.layout_combo.setToolTip("Änderungen noch nicht im Layout gespeichert" if dirty else
                                     "Gespeicherte Auswerte-Layouts (Felder, Kanäle, X-Achse)")

    def _layout_chosen(self):
        name = self.layout_combo.currentData()
        self.db.set_meta("viewer_layout_name", name or "")
        if name and name in self._layouts():
            self.apply_layout(self._layouts()[name])
        self._update_layout_dirty()

    def _store_layout(self, name: str):
        lays = self._layouts()
        lays[name] = self.layout_state()
        self.db.set_meta("viewer_layouts", json.dumps(lays, ensure_ascii=False))
        self.db.set_meta("viewer_layout_name", name)
        self._fill_layout_combo()

    def _layout_save(self):
        name = self.layout_combo.currentData()
        if name:
            self._store_layout(name)
        else:
            self._layout_save_as()

    def _layout_save_as(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Layout speichern", "Name:",
                                                  text=self.layout_combo.currentData() or "")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._layouts() and name != self.layout_combo.currentData():
            if QtWidgets.QMessageBox.question(self, "Layout", f"„{name}“ überschreiben?") \
                    != QtWidgets.QMessageBox.Yes:
                return
        self._store_layout(name)

    def _layout_delete(self):
        name = self.layout_combo.currentData()
        if not name or QtWidgets.QMessageBox.question(self, "Layout", f"Layout „{name}“ löschen?") \
                != QtWidgets.QMessageBox.Yes:
            return
        lays = self._layouts()
        lays.pop(name, None)
        self.db.set_meta("viewer_layouts", json.dumps(lays, ensure_ascii=False))
        self.db.set_meta("viewer_layout_name", "")
        self._fill_layout_combo()

    def _pane_changed(self, i: int, it: QtWidgets.QListWidgetItem):
        name = it.text()
        cur = [n for n in self.pane_channels[i] if n != name]
        if it.checkState() == QtCore.Qt.Checked:
            cur.append(name)
        self.pane_channels[i] = cur
        self._layout_changed()
        self._redraw()

    def _markierter(self) -> Optional[int]:
        """Markierter Lauf: zuerst in "In der Auswertung", sonst in der Auswahlliste, sonst der erste (auch
        Mittelwerte)."""
        it = self.sel_list.currentItem()
        if it is not None:
            return it.data(QtCore.Qt.UserRole)
        it = self.pick_tree.currentItem()
        if it is not None and it.data(0, QtCore.Qt.UserRole) is not None:
            return it.data(0, QtCore.Qt.UserRole)
        reihe = self._reihe()
        return reihe[0] if reihe else None

    def _ist_mw(self, rid) -> bool:
        row = self.rows.get(rid) or self.db.run(rid)
        return bool(row is not None and row.ist_mittelwert)

    def current_run_id(self) -> Optional[int]:
        """Markierter Lauf mit Rohdaten (fuer CSV-Export und Ausrollversuch des Hauptfensters); Mittelwerte
        zaehlen nicht – dann der erste angezeigte Lauf mit Rohdaten."""
        rid = self._markierter()
        if rid is not None and not self._ist_mw(rid):
            return rid
        return next((r for r in self._reihe() if not self._ist_mw(r)), None)

    # ------------------------------------------------------------ Laufauswahl (linke Leiste)
    def _build_left(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        pick = QtWidgets.QGroupBox("Läufe auswählen")
        pl = QtWidgets.QVBoxLayout(pick)
        form = QtWidgets.QFormLayout()
        self.vehicle_combo = QtWidgets.QComboBox()
        self.sort_combo = QtWidgets.QComboBox()
        self.sort_combo.addItems(["nach Datum", "nach Setup"])
        form.addRow("Fahrzeug", self.vehicle_combo)
        form.addRow("Sortierung", self.sort_combo)
        pl.addLayout(form)
        self.pick_tree = QtWidgets.QTreeWidget()
        self.pick_tree.setHeaderLabels(["Lauf", "PS", "Setup"])
        self.pick_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.pick_tree.itemDoubleClicked.connect(lambda it, _c: self._add([it]))
        pl.addWidget(self.pick_tree, 1)
        hb = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Hinzufügen ↓")
        self.edit_vehicle_btn = QtWidgets.QPushButton("Fahrzeug bearbeiten …")
        self.edit_vehicle_btn.setToolTip("Fahrzeug / Setup des markierten Laufs bearbeiten")
        hb.addWidget(self.add_btn)
        hb.addWidget(self.edit_vehicle_btn)
        pl.addLayout(hb)
        lay.addWidget(pick, 3)

        sel = QtWidgets.QGroupBox("In der Auswertung (Häkchen = anzeigen)")
        sl = QtWidgets.QVBoxLayout(sel)
        self.sel_list = QtWidgets.QListWidget()
        self.sel_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        sl.addWidget(self.sel_list, 1)
        hb = QtWidgets.QHBoxLayout()
        self.remove_btn = QtWidgets.QPushButton("Entfernen")
        self.clear_btn = QtWidgets.QPushButton("Alle entfernen")
        self.edit_sel_btn = QtWidgets.QPushButton("Bearbeiten …")
        for b in (self.remove_btn, self.clear_btn, self.edit_sel_btn):
            hb.addWidget(b)
        sl.addLayout(hb)
        lay.addWidget(sel, 2)

        self.tabs = QtWidgets.QTabWidget()
        self.lists: List[QtWidgets.QListWidget] = []
        for i in range(MAX_PANES):
            lw = QtWidgets.QListWidget()
            lw.itemChanged.connect(lambda it, i=i: self._pane_changed(i, it))
            self.lists.append(lw)
            self.tabs.addTab(lw, f"Feld {i + 1}")
        lay.addWidget(theme.rolle(QtWidgets.QLabel("Kanäle je Feld"), "titel"))
        lay.addWidget(self.tabs, 3)

        self.vehicle_combo.currentIndexChanged.connect(lambda _: self._fill_pick())
        self.sort_combo.currentIndexChanged.connect(lambda _: self._fill_pick())
        self.add_btn.clicked.connect(lambda: self._add(self.pick_tree.selectedItems()))
        self.edit_vehicle_btn.clicked.connect(self._edit_from_pick)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn.clicked.connect(self._clear)
        self.edit_sel_btn.clicked.connect(self._edit_from_selection)
        self.sel_list.itemChanged.connect(self._visibility_changed)
        self.sel_list.customContextMenuRequested.connect(self._sel_menu)
        return w

    def reload(self):
        """Fahrzeuge und Laeufe neu aus der Datenbank lesen (nach Aenderungen)."""
        cur = self.vehicle_combo.currentData()
        self.vehicle_combo.blockSignals(True)
        self.vehicle_combo.clear()
        self.vehicle_combo.addItem("alle Fahrzeuge", -1)
        self.vehicle_combo.addItem("ohne Fahrzeug", 0)
        for r in self.db.vehicles():
            self.vehicle_combo.addItem(r["name"], r["id"])
        i = self.vehicle_combo.findData(cur)
        self.vehicle_combo.setCurrentIndex(i if i >= 0 else 0)
        self.vehicle_combo.blockSignals(False)
        existing = {r.id for r in self.db.runs(mittelwert=True)}
        self.selected = [rid for rid in self.selected if rid in existing]
        self.data = {rid: d for rid, d in self.data.items() if rid in existing}
        self.markiert &= existing
        self._alles()

    def _alles(self):
        self._fill_pick()
        self._fill_selection()
        self._reload_data()

    def invalidate(self, rid: int):
        """Lauf wurde neu gerechnet -> Kanaele neu laden."""
        if rid in self.data:
            self.data.pop(rid)
            self._reload_data()

    def _setup_names(self) -> Dict[int, str]:
        names = {}
        for v in self.db.vehicles():
            for s in self.db.setups(v["id"]):
                names[s["id"]] = s["name"] or "(ohne Namen)"
        return names

    def _basis(self, row) -> str:
        """Basisfarbe eines Laufs (theme.laufarbe macht sie themengerecht); Mittelwert: avg_color."""
        if row.ist_mittelwert:
            return row.farbe or "@avg_color"
        return run_color_basis(row)

    def _farbe(self, rid: int) -> str:
        row = self.rows.get(rid) or self.db.run(rid)
        return theme.laufarbe(self._basis(row)) if row is not None else theme.farbe("text")

    def _fill_pick(self):
        vid = self.vehicle_combo.currentData()
        runs = self.db.runs(mittelwert=True)                    # nach Datum absteigend, mit Mittelwerten
        if vid == 0:
            runs = [r for r in runs if not r.vehicle_id]
        elif vid and vid > 0:
            runs = [r for r in runs if r.vehicle_id == vid]
        setups = self._setup_names()
        self.pick_tree.clear()
        by_setup = self.sort_combo.currentIndex() == 1
        groups: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        for row in runs:
            dauer = row.art == "dauermessung" or row.quelle == "Dauermessung"
            it = QtWidgets.QTreeWidgetItem([row.name if row.ist_mittelwert else datum_text(row),
                                            "Dauer" if dauer else f"{row.p_max:.1f}",
                                            setups.get(row.setup_id, "")])
            it.setData(0, QtCore.Qt.UserRole, row.id)
            tip = f"{row.name}\n{row.notiz}" if row.notiz else row.name
            if row.ist_mittelwert:
                tip = f"Mittelwert-Lauf vom {datum_text(row)}\n" + tip
            it.setToolTip(0, tip)
            it.setIcon(0, farb_icon(self._basis(row), row.ist_mittelwert))
            if row.id in self.selected:
                f = it.font(0)
                f.setItalic(True)
                it.setFont(0, f)
            if by_setup:
                key = setups.get(row.setup_id, "— ohne Setup —")
                parent = groups.get(key)
                if parent is None:
                    parent = QtWidgets.QTreeWidgetItem([key])
                    f = parent.font(0)
                    f.setBold(True)
                    parent.setFont(0, f)
                    groups[key] = parent
                    self.pick_tree.addTopLevelItem(parent)
                parent.addChild(it)
            else:
                self.pick_tree.addTopLevelItem(it)
        self.pick_tree.setRootIsDecorated(by_setup)
        self.pick_tree.expandAll()
        for c in range(3):
            self.pick_tree.resizeColumnToContents(c)

    def _add(self, items):
        new = []
        for it in items:
            rid = it.data(0, QtCore.Qt.UserRole)
            if rid is None:                                     # Setup-Gruppe -> alle Laeufe darin
                new += [it.child(k).data(0, QtCore.Qt.UserRole) for k in range(it.childCount())]
            else:
                new.append(rid)
        self.add_runs(new)

    def add_runs(self, ids: Sequence[int]):
        """Laeufe in die Auswertung nehmen (hinten anhaengen, sichtbar)."""
        added = False
        for rid in ids:
            if rid is not None and rid not in self.selected and self.db.run(rid) is not None:
                self.selected.append(rid)
                added = True
            self.hidden.discard(rid)
        if added:
            self._alles()

    def show_run(self, rid: int):
        """Von aussen (Doppelklick in der Laufliste): Lauf hinzufuegen und anzeigen."""
        if rid not in self.selected:
            self.selected.insert(0, rid)
        self.hidden.discard(rid)
        self._alles()

    def _fill_selection(self):
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        setups = self._setup_names()
        self.sel_list.blockSignals(True)
        self.sel_list.clear()
        for rid in self.selected:
            row = self.db.run(rid)
            if row is None:
                continue
            dauer = row.art == "dauermessung" or row.quelle == "Dauermessung"
            text = " · ".join(x for x in (vehicles.get(row.vehicle_id, ""), setups.get(row.setup_id, ""),
                                          row.name if row.ist_mittelwert else datum_text(row),
                                          "Dauermessung" if dauer else f"{row.p_max:.1f} PS")
                              if x)
            it = QtWidgets.QListWidgetItem(farb_icon(self._basis(row), row.ist_mittelwert), text)
            it.setData(QtCore.Qt.UserRole, rid)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Unchecked if rid in self.hidden else QtCore.Qt.Checked)
            self.sel_list.addItem(it)
        self.sel_list.blockSignals(False)

    def _visibility_changed(self, it):
        self._set_sichtbar(it.data(QtCore.Qt.UserRole), it.checkState() == QtCore.Qt.Checked)

    def _set_sichtbar(self, rid: int, an: bool):
        if an:
            self.hidden.discard(rid)
        else:
            self.hidden.add(rid)
        self._fill_selection()
        self._fill_leiste()
        self._redraw()

    def _set_markiert(self, rid: int, an: bool):
        if an:
            self.markiert.add(rid)
        else:
            self.markiert.discard(rid)
        for e in self.leiste.eintraege:                 # Leiste hat ihren Eintrag schon geaendert
            if e.rid == rid:
                e.markiert = an

    def _entfernen(self, rid: int):
        if rid in self.selected:
            self.selected.remove(rid)
        self.markiert.discard(rid)
        self._alles()

    def _remove_selected(self):
        for it in self.sel_list.selectedItems():
            rid = it.data(QtCore.Qt.UserRole)
            if rid in self.selected:
                self.selected.remove(rid)
        self._alles()

    def _clear(self):
        self.selected = []
        self.markiert = set()
        self._alles()

    # ------------------------------------------------------------ Bearbeiten
    def _edit(self, vid, sid):
        dlg = EditDialog(self.db, vid, sid, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.db_changed.emit()
            self.reload()

    def _edit_from_pick(self):
        items = [it for it in self.pick_tree.selectedItems() if it.data(0, QtCore.Qt.UserRole) is not None]
        if items:
            row = self.db.run(items[0].data(0, QtCore.Qt.UserRole))
            self._edit(row.vehicle_id, row.setup_id)
            return
        vid = self.vehicle_combo.currentData()
        self._edit(vid if vid and vid > 0 else None, None)

    def _edit_from_selection(self):
        it = self.sel_list.currentItem()
        if it is None:
            return
        row = self.db.run(it.data(QtCore.Qt.UserRole))
        self._edit(row.vehicle_id, row.setup_id)

    def _sel_menu(self, pos):
        it = self.sel_list.itemAt(pos)
        if it is None:
            return
        row = self.db.run(it.data(QtCore.Qt.UserRole))
        m = QtWidgets.QMenu(self)
        a_setup = None if row.ist_mittelwert else m.addAction("Setup und Notiz des Laufs …")
        a_edit = m.addAction("Fahrzeug / Setup bearbeiten …")
        assign = m.addMenu("Setup zuordnen")
        setup_actions = {}
        if row.vehicle_id:
            for s in self.db.setups(row.vehicle_id):
                setup_actions[assign.addAction(s["name"] or "(ohne Namen)")] = s["id"]
        new_setup = assign.addAction("Neues Setup …")
        a_remove = m.addAction("Aus der Auswertung entfernen")
        act = m.exec(self.sel_list.viewport().mapToGlobal(pos))
        if act is None:
            return
        if act == a_setup:
            self._setup_dialog(row.id)
        elif act == a_edit:
            self._edit(row.vehicle_id, row.setup_id)
        elif act in setup_actions:
            self.db.update_run(row.id, setup_id=setup_actions[act])
            self.db_changed.emit()
            self.reload()
        elif act == new_setup:
            dlg = EditDialog(self.db, row.vehicle_id, None, self, "Neues Setup für diesen Lauf")
            if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.sid:
                self.db.update_run(row.id, vehicle_id=dlg.vid, setup_id=dlg.sid)
                self.db_changed.emit()
                self.reload()
        elif act == a_remove:
            self._entfernen(row.id)

    def _notiz(self, rid: int, text: str):
        self.db.update_run(rid, notiz=text)
        if rid in self.rows:
            self.rows[rid].notiz = text
        self._fill_setup_table()
        self.db_changed.emit()

    def _vorher(self, rid: int):
        """Lauf in der Zeile darueber (Vergleich im Setup-Dialog)."""
        reihe = self._reihe()
        i = reihe.index(rid) if rid in reihe else -1
        return self.rows.get(reihe[i - 1]) if i > 0 else None

    def _setup_dialog(self, rid: int):
        row = self.db.run(rid)
        if row is None or row.ist_mittelwert:
            return
        dlg = LaufSetupDialog(self.db, row, self._vorher(rid), self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.geaendert:
            self.db_changed.emit()
            self.reload()

    # ------------------------------------------------------------ Mittelwert
    def _mittelwert_bilden(self):
        ids = [rid for rid in self._reihe() if rid in self.markiert and rid in self.data and not self._ist_mw(rid)]
        if len(ids) < mw.MIN_LAEUFE:
            QtWidgets.QMessageBox.information(self, "Mittelwert bilden", "Bitte mindestens 2 Läufe in der Spalte "
                                              "„mark.“ markieren.")
            return
        quellen = [(self.rows[rid], self.data[rid][0], self.data[rid][1], self._basis(self.rows[rid])) for rid in ids]
        vid = next((self.rows[r].vehicle_id for r in ids if self.rows[r].vehicle_id), None)
        dlg = MittelwertDialog(self.db, quellen, self.db.schnellwert_plaetze(vid), self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.rid:
            self.mittelwert_uebernehmen(dlg.rid, ids)

    def mittelwert_uebernehmen(self, rid: int, quellen: Sequence[int] = ()):
        """Neuer Mittelwert-Lauf: oben in die Auswertung, Markierung der Quellen weg."""
        self.markiert -= set(quellen)
        if rid not in self.selected:
            self.selected.insert(0, rid)
        self.hidden.discard(rid)
        self.db_changed.emit()
        self.reload()

    def _band_setzen(self, rid: int, art: str):
        info = self.mw_info.setdefault(rid, {})
        info["band"] = art
        row = self.rows.get(rid)
        if row is not None:
            zusatz_schreiben(row.ordner, band=art)
        self._fill_leiste()
        self._redraw()

    def _quellen_tip(self, row) -> str:
        info = self.mw_info.get(row.id, {})
        res = info.get("res")
        namen = []
        for q in row.quellen:
            r = self.db.run(q)
            namen.append(zeit_text(r) if r is not None else f"Lauf {q} (gelöscht)")
        t = "Quellen: " + (", ".join(namen) or "–")
        if res is not None:
            t += f" – Raster {zahl(res.raster)} 1/min, oben {zahl(res.oben_weg)} 1/min weg"
        return t + f", Band {band_text(info.get('band', BAND_STANDARD))}"

    def _quellen_menue(self, rid: int, knopf=None):
        row = self.rows.get(rid) or self.db.run(rid)
        if row is None:
            return
        m = QtWidgets.QMenu(self)
        m.setToolTipsVisible(True)
        kopf = m.addAction("Quellläufe – Klick holt den Lauf in die Auswertung")
        kopf.setEnabled(False)
        for q in row.quellen:
            r = self.db.run(q)
            if r is None:
                a = m.addAction(f"Lauf {q} (gelöscht)")
                a.setEnabled(False)
                continue
            a = m.addAction(farb_icon(self._basis(r)), f"{datum_text(r)} · {zahl(r.p_max, 1)} PS"
                            + ("  ✓" if q in self.selected else ""))
            a.triggered.connect(lambda _c=False, q=q: self.add_runs([q]))
        m.addSeparator()
        kopf = m.addAction("Streuband im Diagramm")
        kopf.setEnabled(False)
        grp = QtGui.QActionGroup(m)
        aktuell = self.mw_info.get(rid, {}).get("band", BAND_STANDARD)
        for art, text in BAND_ARTEN:
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(art == aktuell)
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, art=art: self._band_setzen(rid, art))
        res = self.mw_info.get(rid, {}).get("res")
        if res is not None:
            m.addSeparator()
            a = m.addAction(f"Raster {zahl(res.raster)} 1/min · oben {zahl(res.oben_weg)} 1/min weg · "
                            f"gemittelt {zahl(res.n[0] if len(res.n) else 0)}–{zahl(res.n[-1] if len(res.n) else 0)}"
                            " 1/min")
            a.setEnabled(False)
        if knopf is not None:
            m.exec(knopf.mapToGlobal(QtCore.QPoint(0, knopf.height())))
        return m

    # ------------------------------------------------------------ Export
    def _csv(self):
        rid = self._markierter()
        if rid is None or not self._ist_mw(rid):
            self.csv_btn.click()                       # Rohdaten ueber das Hauptfenster
            return
        if rid not in self.data:
            return
        row = self.rows.get(rid) or self.db.run(rid)
        QtWidgets.QMessageBox.information(
            self, "CSV-Export", f"„{row.name}“ ist ein Mittelwert-Lauf ohne Rohdaten (Telegramme).\nExportiert werden "
                                "die gemittelten Kurven auf dem Drehzahlraster, dazu min, max und σ je Kanal.")
        import os
        path, flt = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSV-Export", os.path.join(os.path.expanduser("~"), f"{row.name.replace('·', '-')}.csv"),
            "CSV für Excel deutsch (*.csv);;CSV international, Dezimalpunkt (*.csv)")
        if path:
            from .report import export_csv
            export_csv(path, self.mittelwert_kanaele(rid), decimal_comma="deutsch" in flt)

    def mittelwert_kanaele(self, rid: int) -> Dict[str, Channel]:
        """Kanaele eines Mittelwerts mit min/max/σ als eigene Spalten (CSV-Export)."""
        ch = self.data[rid][0]
        out: Dict[str, Channel] = {}
        for name, c in ch.items():
            out[name] = c
            b = self.baender.get(rid, {}).get(name)
            if b:
                mn, mx = b.get("minmax") or (None, None)
                su, so = b.get("sigma") or (None, None)
                if mn is not None:
                    out[f"{name} min"] = Channel(mn, c.unit)
                    out[f"{name} max"] = Channel(mx, c.unit)
                if su is not None:
                    out[f"{name} σ"] = Channel((so - su) / 2.0, c.unit)
        return out

    # ------------------------------------------------------------ Daten
    def _load(self, rid: int) -> Optional[tuple]:
        row = self.rows.get(rid) or self.db.run(rid)
        if row is None:
            return None
        if not row.ist_mittelwert:
            return self.load_channels(rid)
        try:
            res = mw.laden(row)
        except (OSError, ValueError) as exc:
            self._ladefehler[rid] = str(exc)
            return None
        z = zusatz_lesen(row.ordner)
        ch, baender = kanaele_aus_mittelwert(res, z.get("einheiten"))
        self.baender[rid] = baender
        self.mw_info[rid] = {"res": res, "band": z.get("band", BAND_STANDARD)}
        return ch, None, []

    def _reload_data(self):
        self.rows = {rid: r for rid in self.selected for r in [self.db.run(rid)] if r is not None}
        for rid in self.selected:
            if rid not in self.data and rid in self.rows:
                d = self._load(rid)
                if d is not None:
                    self.data[rid] = d
        self._fill_lists()
        self._fill_leiste()
        self._redraw()
        self._fill_setup_table()

    def _reihe(self) -> List[int]:
        """Laeufe der Auswertung in Anzeigereihenfolge (Mittelwerte oben, dann alt -> neu)."""
        return [r.id for r in reihenfolge([self.rows[rid] for rid in self.selected if rid in self.rows])]

    def _runs_shown(self) -> List[int]:
        return [rid for rid in self._reihe() if rid not in self.hidden and rid in self.data]

    def _fill_lists(self):
        names: List[str] = []
        for rid in self.selected:
            if rid in self.data:
                for n in self.data[rid][0]:
                    if n != "Zeit" and n not in names:
                        names.append(n)
        for i, lw in enumerate(self.lists):
            checked = set(self.pane_channels[i])
            lw.blockSignals(True)
            lw.clear()
            for n in names:
                it = QtWidgets.QListWidgetItem(n)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Checked if n in checked else QtCore.Qt.Unchecked)
                lw.addItem(it)
            lw.blockSignals(False)
        for n in names:
            self.color_of.setdefault(n, PALETTE[len(self.color_of) % len(PALETTE)])
        self._alle_namen = names

    def _fill_leiste(self):
        reihe = self._reihe()
        rows = [self.rows[rid] for rid in reihe]
        self.bez = lauf_bezeichnungen(rows)
        vid = next((r.vehicle_id for r in rows if r.vehicle_id), None)
        felder = [f for f in self.db.schnellwert_plaetze(vid) if f]     # Spalten = belegte Plaetze des Fahrzeugs
        setups = self._setup_names()
        zellen = schnellwert_zellen([self.db.lauf_setup(r) for r in rows], felder)
        eintraege = []
        for r, z in zip(rows, zellen):
            dauer = r.art == "dauermessung" or r.quelle == "Dauermessung"
            tip = r.name + (f"\n{r.notiz}" if r.notiz else "")
            if r.id in self._ladefehler:
                tip += f"\nLässt sich nicht laden: {self._ladefehler[r.id]}"
            eintraege.append(Eintrag(
                rid=r.id, farbe=self._basis(r), mittelwert=r.ist_mittelwert,
                lauf=r.name if r.ist_mittelwert else datum_text(r), kurz=self.bez.get(r.id, ""),
                setup=setups.get(r.setup_id, ""), zellen=z,
                pmax="Dauermessung" if dauer else f"<b>{zahl(r.p_max, 1)}</b> PS @ {zahl(r.n_pmax)}",
                mmax="" if dauer else f"<b>{zahl(r.m_max, 1)}</b> Nm", notiz=r.notiz,
                sichtbar=r.id not in self.hidden, markiert=r.id in self.markiert, tooltip=tip,
                quellen_tip=self._quellen_tip(r) if r.ist_mittelwert else ""))
        vehicles = {v["id"]: v["name"] for v in self.db.vehicles()}
        vnamen = list(dict.fromkeys(vehicles[r.vehicle_id] for r in rows if r.vehicle_id in vehicles))
        info = ""
        if vnamen:
            info = f"· Fahrzeug{'e' if len(vnamen) > 1 else ''}: {', '.join(vnamen)} "
        info += "· gelb = Setup-Wert anders als im Lauf darüber"
        self.leiste.set_daten(eintraege, felder, info)
        shown = self._runs_shown()
        n = len([r for r in reihe if r not in self.hidden])
        self.seitenleiste.set_streifen_text(f"Läufe & Kanäle  ({n} angezeigt)")
        self.seitenleiste.set_zusatz(f"{n} angezeigt")
        self.seitenleiste.set_streifen_farben([self._basis(self.rows[r]) for r in shown])

    # ------------------------------------------------------------ Zeichnen
    def _selected_channels(self, pane: int) -> List[str]:
        lw = self.lists[pane]
        return [lw.item(k).text() for k in range(lw.count()) if lw.item(k).checkState() == QtCore.Qt.Checked]

    def _slice(self, rid):
        seg = self.data[rid][1]
        return seg if (self.only_seg.isChecked() and seg is not None) else slice(None)

    def _x_key(self) -> str:
        return self.xaxis.currentData() or "Zeit"

    def _x(self, rid) -> Optional[np.ndarray]:
        ch = self.data[rid][0]
        key = self._x_key()
        c = ch.get(key)
        if c is None:
            return None
        x = c.values[self._slice(rid)]
        if key == "Zeit" and len(x):
            x = x - x[0]
        return x

    def _range(self, name: str, runs: List[int]):
        vals = []
        for rid in runs:
            c = self.data[rid][0].get(name)
            if c is not None:
                v = c.values[self._slice(rid)]
                vals.append(v[np.isfinite(v)])
        allv = np.concatenate(vals) if vals else np.array([])
        if not len(allv):
            return None
        lo, hi = float(np.min(allv)), float(np.max(allv))
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        return lo, hi

    def _unit(self, name: str) -> str:
        for rid in self.selected:
            if rid in self.data and name in self.data[rid][0]:
                return self.data[rid][0][name].unit
        return ""

    def _kfarbe(self, name: str) -> str:
        """Kanalfarbe (ein Lauf) im aktiven Thema."""
        return theme.kurvenfarbe(self.color_of.get(name, "#000"))

    def _thema(self, _thema: str):
        self.glw.setBackground(theme.farbe("plot"))
        if getattr(self, "plots", None) is not None:
            self._fill_pick()
            self._fill_selection()
            self._redraw()
            self._fill_setup_table()

    def _band(self, rid: int, name: str):
        art = self.mw_info.get(rid, {}).get("band", BAND_STANDARD)
        if not art:
            return None
        return self.baender.get(rid, {}).get(name, {}).get(art)

    def _redraw(self):
        self.glw.clear()
        self.plots, self.lines = [], []
        self._bereiche, self._live = {}, None
        self._cursor_plan = None
        runs = self._runs_shown()
        multi = len(runs) > 1
        colors = {rid: self._farbe(rid) for rid in runs}
        # Mittelwerte zuletzt zeichnen (liegen oben)
        reihe = [r for r in runs if not self._ist_mw(r)] + [r for r in runs if self._ist_mw(r)]
        prev = None
        n_panes = self.panes_spin.value()
        for p in range(n_panes):
            pi = self.glw.addPlot(row=p, col=0)
            theme.plotitem_stil(pi)
            pi.setMenuEnabled(False)
            pi.hideButtons()
            pi.showGrid(x=True, y=True, alpha=0.35)
            pi.getAxis("left").setStyle(showValues=False)
            pi.getAxis("left").setWidth(12)
            for ax in ("top", "right"):                         # geschlossener Rahmen
                pi.showAxis(ax)
                pi.getAxis(ax).setStyle(showValues=False, tickLength=0)
            pi.setYRange(-0.05, 1.05, padding=0)
            pi.setDownsampling(auto=True, mode="peak")         # lange Dauermessungen
            pi.setClipToView(True)
            pi.vb.setMouseEnabled(x=True, y=False)
            if prev is not None:
                pi.setXLink(prev)
            if p < n_panes - 1:
                pi.getAxis("bottom").setStyle(showValues=False)
            prev = pi
            names = self._selected_channels(p)
            if multi:
                title = "  ".join(f"{STYLE_MARK[i % 4]} {n}" for i, n in enumerate(names))
            else:
                title = "  ".join(f"<span style='color:{self._kfarbe(n)}'>{n}</span>" for n in names)
            pi.setTitle(f"<b>{title}</b>" if title else "", size="10pt", color=theme.farbe("text"))
            ranges = {}                                         # gleiche Einheit -> gemeinsame Skala
            for n in names:
                r = self._range(n, runs)
                if r is None:
                    continue
                key = self._unit(n) or n
                o = ranges.get(key)
                ranges[key] = r if o is None else (min(o[0], r[0]), max(o[1], r[1]))
            for ci, n in enumerate(names):
                rng = ranges.get(self._unit(n) or n)
                if rng is None:
                    continue
                lo, hi = rng
                for rid in reihe:
                    c = self.data[rid][0].get(n)
                    x = self._x(rid)
                    if c is None or x is None:
                        continue
                    ist_mw = self._ist_mw(rid)
                    color = colors[rid] if multi else self._kfarbe(n)
                    if ist_mw:
                        b = self._band(rid, n)
                        if b is not None:
                            fuell_band(pi, x, (b[0] - lo) / (hi - lo), (b[1] - lo) / (hi - lo), colors[rid],
                                       BAND_ALPHA)
                    y = (c.values[self._slice(rid)] - lo) / (hi - lo)
                    style = STYLES[ci % 4] if multi else QtCore.Qt.SolidLine
                    pi.plot(x, y, pen=pg.mkPen(color, width=LINIE_MW if ist_mw else LINIE, style=style),
                            connect="finite")
            # Marker der Dauermessung (Text nur im obersten Feld)
            if self._x_key() == "Zeit" and not self.only_seg.isChecked():
                for rid in runs:
                    d = self.data[rid]
                    for t, text in (d[2] if len(d) > 2 else []):
                        pi.addItem(pg.InfiniteLine(t, angle=90, pen=pg.mkPen(theme.kurvenfarbe("#2e7d32"), width=1.2),
                                                   label=text if p == 0 else None,
                                                   labelOpts={"position": 0.92, "color": theme.farbe("ok_fg")}),
                                   ignoreBounds=True)
            line = pg.InfiniteLine(angle=90, movable=False,
                                   pen=pg.mkPen(theme.farbe("cursor"), width=1.2, style=QtCore.Qt.DashLine))
            pi.addItem(line, ignoreBounds=True)
            self.plots.append(pi)
            self.lines.append(line)
        # Fahne unten an der Cursorlinie (unterstes Feld): genaue Drehzahl bzw. Zeit am Cursor
        self.cursor_fahne = None
        if self.lines:
            T = theme.tokens()
            self.cursor_fahne = pg.InfLineLabel(self.lines[-1], "", movable=False, position=0.04,
                                                anchors=[(0.5, 1), (0.5, 1)], color=T["text"],
                                                fill=pg.mkBrush(T["panel"]), border=pg.mkPen(T["border"]))
            f = self.cursor_fahne.textItem.font()
            f.setBold(True)
            self.cursor_fahne.textItem.setFont(f)
        for p, f in enumerate(FELD_HOEHEN[:n_panes]):          # oberes Feld groesser (Entwurf: 5 : 3 : 2)
            self.glw.ci.layout.setRowStretchFactor(p, f)
        if self.plots:
            self.plots[-1].setLabel("bottom", "Zeit [s]" if self._x_key() == "Zeit" else "Motordrehzahl [1/min]",
                                    color=theme.farbe("plot_axis"))
        hinweis = HINWEIS
        if self._x_key() == "Zeit" and any(self._ist_mw(r) for r in runs):
            hinweis += " · Ø-Läufe gibt es nur über der Motordrehzahl (X-Achse umstellen)"
        self.hint.setText(hinweis)
        self._place_cursor()

    # ------------------------------------------------------------ Cursor
    def _mouse_moved(self, pos):
        for pi in self.plots:
            if pi.sceneBoundingRect().contains(pos):
                self._set_cursor_x(pi.vb.mapSceneToView(pos).x(), frei=True)
                return

    def _primary(self) -> Optional[int]:
        """Lauf, an dessen Telegrammen der Cursor springt: der erste angezeigte mit Rohdaten, sonst ein Mittelwert."""
        runs = [r for r in self._runs_shown() if self._x(r) is not None]
        echte = [r for r in runs if not self._ist_mw(r)]
        return (echte or runs or [None])[0]

    def _x_bereich(self) -> Optional[Tuple[float, float]]:
        """Kleinstes und groesstes X aller angezeigten Laeufe (der Cursor darf ueberall dazwischen stehen)."""
        lo, hi = math.inf, -math.inf
        for rid in self._runs_shown():
            xa = self._x(rid)
            if xa is not None and len(xa):
                ok = np.isfinite(xa)
                if ok.any():
                    lo, hi = min(lo, float(np.min(xa[ok]))), max(hi, float(np.max(xa[ok])))
        return (lo, hi) if lo <= hi else None

    def _set_cursor_x(self, x: float, frei: bool = False):
        """Cursor an X. frei=True (Maus): genau dort, ueber den ganzen Bereich aller Laeufe – auch wenn der erste
        Lauf vorher endet; sonst auf das naechste Telegramm des ersten Laufs."""
        rid = self._primary()
        xa = self._x(rid) if rid is not None else None
        if xa is None or not len(xa) or not np.isfinite(xa).any():
            return
        self.cursor_idx = int(np.nanargmin(np.abs(xa - x)))
        b = self._x_bereich() if frei else None
        self._cursor_frei = min(max(float(x), b[0]), b[1]) if b else None
        self._place_cursor()

    def cursor_x(self) -> Optional[float]:
        if self._cursor_frei is not None:
            return self._cursor_frei
        rid = self._primary()
        xa = self._x(rid) if rid is not None else None
        if xa is None or not len(xa):
            return None
        v = float(xa[max(0, min(len(xa) - 1, self.cursor_idx))])
        return v if np.isfinite(v) else None

    def _place_cursor(self):
        rid = self._primary()
        xa = self._x(rid) if rid is not None else None
        if xa is not None and len(xa):
            self.cursor_idx = max(0, min(len(xa) - 1, self.cursor_idx))
        x = self.cursor_x()
        if x is not None and np.isfinite(x):
            for line in self.lines:
                line.setPos(x)
            if getattr(self, "cursor_fahne", None) is not None:
                zeit = self._x_key() == "Zeit"
                self.cursor_fahne.setText(f"{zahl(x, 2)} s" if zeit else f"{zahl(x)} 1/min")
        if self._cursor_plan is None:
            self._update_cursor()
        else:
            self._cursor_werte()

    def keyPressEvent(self, ev: QtGui.QKeyEvent):
        if ev.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            if self._cursor_frei is not None:               # von der Mausstelle aus auf Telegramme des ersten Laufs
                self._set_cursor_x(self._cursor_frei)
            self.cursor_idx += -1 if ev.key() == QtCore.Qt.Key_Left else 1
            self._place_cursor()
        else:
            super().keyPressEvent(ev)

    def _kachel_kanaele(self) -> List[Tuple[str, str]]:
        """[(Standard, gewaehlt)]: Standard = Kanaele der Felder (ohne Doppelte), gewaehlt per Titel ▾."""
        std: List[str] = []
        for p in range(self.panes_spin.value()):
            for n in self._selected_channels(p):
                if n not in std:
                    std.append(n)
        return [(n, self.kachel_wahl.get(n, n)) for n in std]

    def _kachel_gewaehlt(self, i: int, kanal: str):
        paare = self._kachel_kanaele()
        if i < len(paare):
            std = paare[i][0]
            if kanal == std:
                self.kachel_wahl.pop(std, None)
            else:
                self.kachel_wahl[std] = kanal
            self.db.set_meta("viewer_kacheln", json.dumps(self.kachel_wahl, ensure_ascii=False))
        self._update_cursor()

    def _balken(self, name: str, runs: List[int]) -> Tuple[float, float, list]:
        """Balkenbereich und Zonen einer Kachel: aus der Live-Belegung des Fahrzeugs (Messen), sonst gerundet aus
        den angezeigten Daten."""
        if self._live is None:
            vid = next((self.rows[r].vehicle_id for r in runs if r in self.rows and self.rows[r].vehicle_id), None)
            self._live = self.db.live_plaetze(vid)
        for p in self._live:
            if LIVE_ZU_KANAL.get(p["kanal"]) == name:
                return float(p["min"]), float(p["max"]), [tuple(z) for z in p["zonen"]]
        if name not in self._bereiche:                  # nur der Kurvenbereich (ohne Anlauf und Ausrollen)
            vals = []
            for rid in runs:
                c = self.data[rid][0].get(name)
                if c is not None:
                    seg = self.data[rid][1]
                    v = np.asarray(c.values[seg if seg is not None else slice(None)], float)
                    vals.append(v[np.isfinite(v)])
            allv = np.concatenate(vals) if vals else np.zeros(0)
            self._bereiche[name] = (float(allv.min()), float(allv.max())) if len(allv) else None
        r = self._bereiche[name]
        lo, hi = bereich_runden(*r) if r else (0.0, 1.0)
        return lo, hi, []

    def _update_cursor(self):
        """Rechte Spalte „Werte am Cursor“ fuellen."""
        runs = self._runs_shown()
        prim = self._primary()
        x_at = self.cursor_x()
        # X-Achse
        zeit = self._x_key() == "Zeit"
        xs = [self._x(r) for r in runs]
        xs = [x[np.isfinite(x)] for x in xs if x is not None and len(x)]
        xs = [x for x in xs if len(x)]
        lo, hi = bereich_runden(min(float(x.min()) for x in xs), max(float(x.max()) for x in xs)) if xs else (0, 1)
        self.cursor.set_x("Zeit am Cursor" if zeit else "Motordrehzahl am Cursor", "s" if zeit else "1/min", x_at,
                          lo, hi, 2 if zeit else 0)
        # Kanaele
        daten = []
        plan_kacheln = []
        for i, (_std, name) in enumerate(self._kachel_kanaele()):
            if not name:
                daten.append(KachelDaten(""))
                continue
            laeufe = self._laufwerte(name, runs, prim, x_at)
            plan_kacheln.append((i, name))
            unit = self._unit(name)
            blo, bhi, zonen = self._balken(name, runs)
            daten.append(KachelDaten(name, unit, dez_fuer(unit, bhi - blo), blo, bhi, zonen, laeufe))
        kanaele = [Kanal(n, n, self._unit(n), "ECU" if n.endswith("(ECU)") else
                         analog.GRUPPE if n.endswith(analog.SUFFIX) else "PST", dez_fuer(self._unit(n)))
                   for n in getattr(self, "_alle_namen", [])]
        self.cursor.set_kacheln(daten, kanaele)
        # Maxima je Lauf
        maxima = {"p_max": [], "n_pmax": [], "m_max": [], "n_mmax": []}
        for rid in runs:
            row = self.rows[rid]
            dauer = row.art == "dauermessung" or row.quelle == "Dauermessung"
            for k in maxima:
                v = getattr(row, k)
                maxima[k].append((self._basis(row), self.bez.get(rid, ""), None if dauer or not v else float(v)))
        self.cursor.set_maxima(maxima)
        self._cursor_plan = {"runs": list(runs), "dez": 2 if zeit else 0, "kacheln": plan_kacheln}

    def _laufwerte(self, name: str, runs: Sequence[int], prim: Optional[int],
                   x_at: Optional[float]) -> List[Tuple[str, str, Optional[float]]]:
        """[(Basisfarbe, Bezeichnung, Wert am Cursor)] eines Kanals fuer die angezeigten Laeufe."""
        laeufe = []
        for rid in runs:
            c = self.data[rid][0].get(name)
            x = self._x(rid)
            y = None if c is None else c.values[self._slice(rid)]
            if rid == prim and self._cursor_frei is None and y is not None and len(y) and x_at is not None:
                v = float(y[min(self.cursor_idx, len(y) - 1)])
                laeufe.append((self._basis(self.rows[rid]), self.bez.get(rid, ""), v if np.isfinite(v) else None))
            else:
                laeufe += laufwerte_am_cursor([(self._basis(self.rows[rid]), self.bez.get(rid, ""), x, y,
                                                self._ist_mw(rid))], x_at)
        return laeufe

    def _cursor_werte(self):
        """Bei jeder Mausbewegung (wie Messen): nur die Zahlen am Cursor – X-Kachel und Laufzeilen der Kacheln."""
        plan = self._cursor_plan
        runs = self._runs_shown()
        if plan is None or plan["runs"] != runs:
            self._update_cursor()
            return
        x_at = self.cursor_x()
        k = self.cursor.x_kachel
        k.wert.setText(zahl(x_at, plan["dez"]))
        k.balken.set_marken([(x_at, "@cursor")] if x_at is not None else [])
        prim = self._primary()
        for i, name in plan["kacheln"]:
            if i < len(self.cursor.kacheln):
                self.cursor.kacheln[i].set_laeufe(self._laufwerte(name, runs, prim, x_at))

    # ------------------------------------------------------------ Setup-Vergleich
    def _fill_setup_table(self):
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        setups = self._setup_names()
        rows_data = []
        for rid in self._reihe():
            row = self.rows.get(rid) or self.db.run(rid)
            if row is None:
                continue
            s = self.db.lauf_setup(row)
            p = row.params or {}
            d = {"Fahrzeug": vehicles.get(row.vehicle_id, ""), "Setup": setups.get(row.setup_id, ""),
                 "Datum": datum_text(row), "Pmax": f"{row.p_max:.1f} PS bei {row.n_pmax:.0f}",
                 "Mmax": f"{row.m_max:.1f} Nm bei {row.n_mmax:.0f}", "Notiz Lauf": row.notiz}
            if row.ist_mittelwert:
                d["Mittelwert aus"] = ", ".join(datum_text(r) for r in (self.db.run(q) for q in row.quellen)
                                                if r is not None)
            for _g, fields in SETUP_FIELDS:
                for key, label, _t in fields:
                    d[label] = str(s.get(key, "") or "")
            if p and not row.ist_mittelwert:
                d["Übersetzung (Rechnung)"] = f"{p.get('ratio', 0):.3f}"
                d["Trägheit J"] = f"{p.get('inertia', 0):.2f}"
                pp = physics.DynoParams(**{k: v for k, v in p.items() if k in physics.DynoParams.__dataclass_fields__})
                d["Filter"] = physics.filter_text(pp, row.rate)
                d["Klima"] = f"{p.get('temp_c', 0):.1f} °C, {p.get('p_mbar', 0):.0f} mbar"
            rows_data.append((row, d))
        keys: List[str] = []
        for _row, d in rows_data:
            for k in d:
                if k not in keys:
                    keys.append(k)
        keys = [k for k in keys if any(d.get(k) for _r, d in rows_data)]
        self.setup_table.setColumnCount(1 + len(rows_data))
        self.setup_table.setHorizontalHeaderLabels(["Eintrag"] + [self.bez.get(r.id) or datum_text(r)
                                                                  for r, _ in rows_data])
        for k, (row, _d) in enumerate(rows_data):
            self.setup_table.horizontalHeaderItem(k + 1).setForeground(QtGui.QColor(theme.laufarbe(self._basis(row))))
        self.setup_table.setRowCount(len(keys))
        diff_bg = QtGui.QColor(theme.farbe("changed_bg"))
        diff_fg = QtGui.QColor(theme.farbe("changed_text"))
        for r, key in enumerate(keys):
            vals = [d.get(key, "") for _row, d in rows_data]
            # Mittelwerte ohne Eintrag (Rechnung, Klima …) zaehlen nicht als Unterschied
            vgl = [v for (row, _d), v in zip(rows_data, vals) if v or not row.ist_mittelwert]
            differs = len(set(vgl)) > 1 and key not in ("Datum", "Pmax", "Mmax", "Notiz Lauf", "Mittelwert aus")
            it = QtWidgets.QTableWidgetItem(key)
            if differs:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.setup_table.setItem(r, 0, it)
            for k, v in enumerate(vals):
                cell = QtWidgets.QTableWidgetItem(v)
                if differs:
                    cell.setBackground(diff_bg)
                    cell.setForeground(diff_fg)
                self.setup_table.setItem(r, k + 1, cell)
        # feste, schmale Laufspalten mit Zeilenumbruch, damit mehrere Laeufe nebeneinander passen
        self.setup_table.setWordWrap(True)
        self.setup_table.resizeColumnToContents(0)
        self.setup_table.setColumnWidth(0, min(self.setup_table.columnWidth(0), 170))
        for k in range(len(rows_data)):
            self.setup_table.setColumnWidth(k + 1, 115)
        self.setup_table.resizeRowsToContents()
