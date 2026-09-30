"""
Auswerter im Stil von MegaLogViewer.

Laufauswahl: Fahrzeug waehlen -> dessen Laeufe (nach Datum oder nach Setup gruppiert) -> "Hinzufuegen".
Danach weitere Fahrzeuge/Laeufe dazunehmen. Fahrzeug- und Setup-Daten lassen sich direkt bearbeiten.

Anzeige:
- bis zu 4 Felder uebereinander, gemeinsame X-Achse (Zeit oder Motordrehzahl)
- beliebige Kanaele je Feld; jeder Kanal wird fuer sich auf die Feldhoehe skaliert (wie MLV),
  Kanaele gleicher Einheit teilen sich eine Skala; echte Werte in der Wertetabelle
- ein Lauf: Farbe je Kanal. Mehrere Laeufe: Farbe je Lauf, Linienart je Kanal
- Cursor mit Maus oder Pfeiltasten (Schritt = ein Telegramm des ersten Laufs)
- Setup-Vergleich: Setup-Eintraege der gewaehlten Laeufe nebeneinander, Unterschiede markiert
"""
from typing import Callable, Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .db import SETUP_FIELDS, Database
from . import physics
from .dialogs import EditDialog
import json

from .plots import run_color
from .widgets import GaugeBar

# Anzeigen am Cursor: (Schluessel, Titel, Einheit, Kanal, Nachkommastellen)
VIEW_GAUGES = [
    ("ps", "Leistung", "PS", "Leistung", 1), ("nm", "Drehmoment", "Nm", "Drehmoment", 1),
    ("n", "Motor (Rolle × i)", "1/min", "Drehzahl Motor", 0), ("ign", "Motor (Zündung)", "1/min", "Drehzahl Zündung", 0),
    ("v", "Rolle", "km/h", "Geschwindigkeit", 1), ("egt1", "EGT 1", "°C", "EGT 1", 0), ("egt2", "EGT 2", "°C", "EGT 2", 0),
    ("lam", "Lambda", "λ", "Lambda", 3), ("tps", "TPS", "%", "tps", 1),
    ("ratio", "Übersetzung", "", "Übersetzung gefiltert", 2), ("rate", "Messfrequenz", "Hz", "Messfrequenz", 2),
    ("t", "Zeit", "s", "Zeit", 2),
]

PALETTE = ["#c62828", "#1f5fbf", "#2e7d32", "#ef6c00", "#6a1b9a", "#00838f", "#ad1457", "#4e342e",
           "#9e9d24", "#37474f", "#d84315", "#0277bd", "#558b2f", "#8e24aa", "#00695c", "#f9a825"]
STYLES = [QtCore.Qt.SolidLine, QtCore.Qt.DashLine, QtCore.Qt.DotLine, QtCore.Qt.DashDotLine]
STYLE_MARK = ["──", "╌╌", "┈┈", "─·"]
DEFAULT_PANES = [["Drehzahl Motor", "Drehzahl Zündung"], ["Leistung", "Drehmoment"],
                 ["Übersetzung gefiltert", "Übersetzung eingestellt"], ["Lambda", "EGT 1", "EGT 2", "tps", "TPS"]]
MAX_PANES = 4


def _icon(color: str) -> QtGui.QIcon:
    pm = QtGui.QPixmap(12, 12)
    pm.fill(QtGui.QColor(color))
    return QtGui.QIcon(pm)


class LogViewer(QtWidgets.QWidget):
    db_changed = QtCore.Signal()               # Fahrzeug/Setup bearbeitet -> Hauptfenster aktualisiert

    def __init__(self, db: Database, load_channels: Callable[[int], Optional[tuple]]):
        """load_channels(run_id) -> (Kanaele, Kurvenbereich) des Laufs (vom Hauptfenster, mit Cache)."""
        super().__init__()
        self.db = db
        self.load_channels = load_channels
        self.selected: List[int] = []           # Laeufe in der Auswertung (Reihenfolge = Hinzufuegen)
        self.hidden: set = set()                # in der Auswertung, aber ausgeblendet
        self.data: Dict[int, tuple] = {}        # run_id -> (Kanaele, Kurvenbereich)
        self.cursor_idx = 0
        self.color_of: Dict[str, str] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        split = QtWidgets.QSplitter()
        split.addWidget(self._build_left())

        center = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        self.xaxis = QtWidgets.QComboBox()
        self.xaxis.addItems(["Zeit", "Drehzahl Motor"])
        self.panes_spin = QtWidgets.QSpinBox()
        self.panes_spin.setRange(1, MAX_PANES)
        self.panes_spin.setValue(4)
        self.only_seg = QtWidgets.QCheckBox("nur Kurvenbereich")
        for label, w_ in (("X-Achse", self.xaxis), ("Felder", self.panes_spin)):
            bar.addWidget(QtWidgets.QLabel(label))
            bar.addWidget(w_)
        bar.addWidget(self.only_seg)
        bar.addStretch(1)
        cl.addLayout(bar)
        try:
            hidden = json.loads(self.db.get_meta("viewer_gauges_hidden", '["egt2", "tps", "rate"]'))
        except ValueError:
            hidden = []
        self.gauge_run = QtWidgets.QLabel("")
        self.gauge_run.setMinimumWidth(90)
        self.gauge_run.setWordWrap(True)
        self.gauge_bar = GaugeBar([(k, t, u) for k, t, u, _c, _d in VIEW_GAUGES], hidden,
                                  lambda h: self.db.set_meta("viewer_gauges_hidden", json.dumps(sorted(h))),
                                  prefix=self.gauge_run)
        root.addWidget(self.gauge_bar)                          # volle Breite ueber allem
        root.addWidget(split, 1)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("w")
        cl.addWidget(self.glw, 1)
        hint = QtWidgets.QLabel("Maus = Cursor · ←/→ = ein Telegramm weiter · Mausrad = Zoom · ziehen = verschieben")
        hint.setStyleSheet("color: gray;")
        cl.addWidget(hint)
        split.addWidget(center)

        self.right_tabs = QtWidgets.QTabWidget()
        self.table = QtWidgets.QTableWidget(0, 1)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setup_table = QtWidgets.QTableWidget(0, 1)
        self.setup_table.verticalHeader().setVisible(False)
        self.setup_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.right_tabs.addTab(self.table, "Werte am Cursor")
        self.right_tabs.addTab(self.setup_table, "Setup-Vergleich")
        self.right_tabs.setMinimumWidth(240)
        split.addWidget(self.right_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 900, 320])

        self.plots: List[pg.PlotItem] = []
        self.lines: List[pg.InfiniteLine] = []
        self.glw.scene().sigMouseMoved.connect(self._mouse_moved)
        self.xaxis.currentIndexChanged.connect(self._redraw)
        self.only_seg.toggled.connect(self._redraw)
        self.panes_spin.valueChanged.connect(self._redraw)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.reload()

    # ------------------------------------------------------------ Laufauswahl
    def _build_left(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setMinimumWidth(300)
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
        self.edit_vehicle_btn = QtWidgets.QPushButton("Bearbeiten …")
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
            lw.itemChanged.connect(self._redraw)
            self.lists.append(lw)
            self.tabs.addTab(lw, f"Feld {i + 1}")
        lay.addWidget(QtWidgets.QLabel("<b>Kanäle je Feld</b>"))
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
        existing = {r.id for r in self.db.runs()}
        self.selected = [rid for rid in self.selected if rid in existing]
        self.data = {rid: d for rid, d in self.data.items() if rid in existing}
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

    def _fill_pick(self):
        vid = self.vehicle_combo.currentData()
        runs = self.db.runs()                                   # nach Datum absteigend
        if vid == 0:
            runs = [r for r in runs if not r.vehicle_id]
        elif vid and vid > 0:
            runs = [r for r in runs if r.vehicle_id == vid]
        setups = self._setup_names()
        self.pick_tree.clear()
        by_setup = self.sort_combo.currentIndex() == 1
        groups: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        for row in runs:
            it = QtWidgets.QTreeWidgetItem([row.datum.replace("T", " ")[:16], f"{row.p_max:.1f}",
                                            setups.get(row.setup_id, "")])
            it.setData(0, QtCore.Qt.UserRole, row.id)
            it.setToolTip(0, f"{row.name}\n{row.notiz}" if row.notiz else row.name)
            it.setIcon(0, _icon(run_color(row)))
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
        added = False
        for rid in new:
            if rid is not None and rid not in self.selected:
                self.selected.append(rid)
                added = True
        if added:
            self._fill_pick()
            self._fill_selection()
            self._reload_data()

    def show_run(self, rid: int):
        """Von aussen (Doppelklick in der Laufliste): Lauf hinzufuegen und anzeigen."""
        if rid not in self.selected:
            self.selected.insert(0, rid)
        self.hidden.discard(rid)
        self._fill_pick()
        self._fill_selection()
        self._reload_data()

    def _fill_selection(self):
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        setups = self._setup_names()
        self.sel_list.blockSignals(True)
        self.sel_list.clear()
        for rid in self.selected:
            row = self.db.run(rid)
            if row is None:
                continue
            text = " · ".join(x for x in (vehicles.get(row.vehicle_id, ""), setups.get(row.setup_id, ""),
                                          row.datum.replace("T", " ")[:16], f"{row.p_max:.1f} PS") if x)
            it = QtWidgets.QListWidgetItem(_icon(run_color(row)), text)
            it.setData(QtCore.Qt.UserRole, rid)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Unchecked if rid in self.hidden else QtCore.Qt.Checked)
            self.sel_list.addItem(it)
        self.sel_list.blockSignals(False)

    def _visibility_changed(self, it):
        rid = it.data(QtCore.Qt.UserRole)
        if it.checkState() == QtCore.Qt.Checked:
            self.hidden.discard(rid)
        else:
            self.hidden.add(rid)
        self._redraw()

    def _remove_selected(self):
        for it in self.sel_list.selectedItems():
            rid = it.data(QtCore.Qt.UserRole)
            if rid in self.selected:
                self.selected.remove(rid)
        self._fill_pick()
        self._fill_selection()
        self._reload_data()

    def _clear(self):
        self.selected = []
        self._fill_pick()
        self._fill_selection()
        self._reload_data()

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
        if act == a_edit:
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
            self.selected.remove(row.id)
            self._fill_pick()
            self._fill_selection()
            self._reload_data()

    # ------------------------------------------------------------ Daten
    def _reload_data(self):
        for rid in self.selected:
            if rid not in self.data:
                d = self.load_channels(rid)
                if d is not None:
                    self.data[rid] = d
        self._fill_lists()
        self._redraw()
        self._fill_setup_table()

    def _runs_shown(self) -> List[int]:
        return [rid for rid in self.selected if rid not in self.hidden and rid in self.data]

    def _fill_lists(self):
        names: List[str] = []
        for rid in self.selected:
            if rid in self.data:
                for n in self.data[rid][0]:
                    if n != "Zeit" and n not in names:
                        names.append(n)
        for i, lw in enumerate(self.lists):
            checked = {lw.item(k).text() for k in range(lw.count()) if lw.item(k).checkState() == QtCore.Qt.Checked}
            if not checked and lw.count() == 0:
                checked = set(DEFAULT_PANES[i])
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

    # ------------------------------------------------------------ Zeichnen
    def _selected_channels(self, pane: int) -> List[str]:
        lw = self.lists[pane]
        return [lw.item(k).text() for k in range(lw.count()) if lw.item(k).checkState() == QtCore.Qt.Checked]

    def _slice(self, rid):
        seg = self.data[rid][1]
        return seg if (self.only_seg.isChecked() and seg is not None) else slice(None)

    def _x(self, rid) -> Optional[np.ndarray]:
        ch = self.data[rid][0]
        key = "Zeit" if self.xaxis.currentText() == "Zeit" else "Drehzahl Motor"
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

    def _redraw(self):
        self.glw.clear()
        self.plots, self.lines = [], []
        runs = self._runs_shown()
        multi = len(runs) > 1
        colors = {rid: run_color(self.db.run(rid)) for rid in runs}
        prev = None
        n_panes = self.panes_spin.value()
        for p in range(n_panes):
            pi = self.glw.addPlot(row=p, col=0)
            pi.setMenuEnabled(False)
            pi.hideButtons()
            pi.showGrid(x=True, y=False, alpha=0.25)
            pi.getAxis("left").setStyle(showValues=False)
            pi.getAxis("left").setWidth(12)
            for ax in ("top", "right"):                         # geschlossener Rahmen
                pi.showAxis(ax)
                pi.getAxis(ax).setStyle(showValues=False, tickLength=0)
            pi.setYRange(-0.05, 1.05, padding=0)
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
                title = "  ".join(f"<span style='color:{self.color_of.get(n, '#000')}'>{n}</span>" for n in names)
            pi.setTitle(title, size="9pt")
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
                for rid in runs:
                    c = self.data[rid][0].get(n)
                    x = self._x(rid)
                    if c is None or x is None:
                        continue
                    y = (c.values[self._slice(rid)] - lo) / (hi - lo)
                    color = colors[rid] if multi else self.color_of.get(n, "#000")
                    style = STYLES[ci % 4] if multi else QtCore.Qt.SolidLine
                    pi.plot(x, y, pen=pg.mkPen(color, width=1.6, style=style), connect="finite")
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#444", width=1))
            pi.addItem(line, ignoreBounds=True)
            self.plots.append(pi)
            self.lines.append(line)
        if self.plots:
            self.plots[-1].setLabel("bottom", "Zeit [s]" if self.xaxis.currentText() == "Zeit"
                                    else "Motordrehzahl [1/min]")
        self._place_cursor()

    # ------------------------------------------------------------ Cursor
    def _mouse_moved(self, pos):
        for pi in self.plots:
            if pi.sceneBoundingRect().contains(pos):
                self._set_cursor_x(pi.vb.mapSceneToView(pos).x())
                return

    def _primary(self) -> Optional[int]:
        runs = self._runs_shown()
        return runs[0] if runs else None

    def _set_cursor_x(self, x: float):
        rid = self._primary()
        xa = self._x(rid) if rid is not None else None
        if xa is None or not len(xa):
            return
        self.cursor_idx = int(np.nanargmin(np.abs(xa - x)))
        self._place_cursor()

    def _place_cursor(self):
        rid = self._primary()
        xa = self._x(rid) if rid is not None else None
        if xa is not None and len(xa):
            self.cursor_idx = max(0, min(len(xa) - 1, self.cursor_idx))
            for line in self.lines:
                line.setPos(float(xa[self.cursor_idx]))
        self._update_table()

    def keyPressEvent(self, ev: QtGui.QKeyEvent):
        if ev.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            self.cursor_idx += -1 if ev.key() == QtCore.Qt.Key_Left else 1
            self._place_cursor()
        else:
            super().keyPressEvent(ev)

    def _value(self, rid: int, name: str, x_at: float, primary: bool) -> str:
        c = self.data[rid][0].get(name)
        x = self._x(rid)
        if c is None or x is None or not len(x):
            return ""
        j = self.cursor_idx if primary else int(np.nanargmin(np.abs(x - x_at)))
        j = min(j, len(x) - 1)
        v = c.values[self._slice(rid)][j]
        if not np.isfinite(v):
            return "–"
        dec = 3 if c.unit == "λ" else (2 if abs(v) < 100 else 0)
        return f"{v:.{dec}f} {c.unit}"

    def _update_table(self):
        runs = self._runs_shown()
        names = ["Zeit"]
        for p in range(self.panes_spin.value()):
            for n in self._selected_channels(p):
                if n not in names:
                    names.append(n)
        self.table.setColumnCount(1 + len(runs))
        self.table.setHorizontalHeaderLabels(["Kanal"] + [self.db.run(rid).datum.replace("T", " ")[5:16]
                                                         for rid in runs])
        for k, rid in enumerate(runs):
            self.table.horizontalHeaderItem(k + 1).setForeground(QtGui.QColor(run_color(self.db.run(rid))))
        self.table.setRowCount(len(names))
        x_at = 0.0
        if runs:
            xa = self._x(runs[0])
            if xa is not None and len(xa) > self.cursor_idx:
                x_at = float(xa[self.cursor_idx])
        self._update_gauges(runs)
        for r, n in enumerate(names):
            it = QtWidgets.QTableWidgetItem(n)
            if len(runs) <= 1:
                it.setForeground(QtGui.QColor(self.color_of.get(n, "#000")))
            self.table.setItem(r, 0, it)
            for k, rid in enumerate(runs):
                self.table.setItem(r, k + 1, QtWidgets.QTableWidgetItem(self._value(rid, n, x_at, k == 0)))
        self.table.resizeColumnToContents(0)
        for k in range(len(runs)):
            self.table.setColumnWidth(k + 1, 105)

    def _update_gauges(self, runs):
        """Anzeigenleiste: Werte des ersten sichtbaren Laufs am Cursor."""
        if not runs:
            self.gauge_run.setText("")
            for key in self.gauge_bar.keys():
                self.gauge_bar.set(key, "–")
            return
        rid = runs[0]
        row = self.db.run(rid)
        self.gauge_run.setText(f"<span style='color:{run_color(row)}'>■</span> {row.datum.replace('T', ' ')[5:16]}")
        ch = self.data[rid][0]
        sl = self._slice(rid)
        for key, _title, unit, name, dec in VIEW_GAUGES:
            c = ch.get(name) or (ch.get("TPS") if name == "tps" else None)
            if c is None:
                self.gauge_bar.set(key, "–")
                continue
            vals = c.values[sl]
            if not len(vals):
                self.gauge_bar.set(key, "–")
                continue
            v = vals[min(self.cursor_idx, len(vals) - 1)]
            if name == "Zeit":
                v = v - vals[0]
            ok = np.isfinite(v) and not (name.startswith("EGT") and v <= 0)
            if not ok:
                text = "–"
            elif dec == 0 and unit == "1/min":
                text = f"{v:,.0f}".replace(",", ".")          # wie im Reiter Messen: 9.972
            else:
                text = f"{v:.{dec}f}"
            self.gauge_bar.set(key, text)

    # ------------------------------------------------------------ Setup-Vergleich
    def _fill_setup_table(self):
        vehicles = {r["id"]: r["name"] for r in self.db.vehicles()}
        rows_data = []
        for rid in self.selected:
            row = self.db.run(rid)
            if row is None:
                continue
            s = (self.db.setup(row.setup_id) if row.setup_id else None) or {}
            p = row.params or {}
            d = {"Fahrzeug": vehicles.get(row.vehicle_id, ""), "Setup": s.get("name", ""),
                 "Datum": row.datum.replace("T", " ")[:16], "Pmax": f"{row.p_max:.1f} PS bei {row.n_pmax:.0f}",
                 "Mmax": f"{row.m_max:.1f} Nm bei {row.n_mmax:.0f}", "Notiz Lauf": row.notiz}
            for _g, fields in SETUP_FIELDS:
                for key, label, _t in fields:
                    d[label] = str(s.get(key, "") or "")
            if p:
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
        self.setup_table.setHorizontalHeaderLabels(["Eintrag"] + [r.datum.replace("T", " ")[5:16]
                                                                  for r, _ in rows_data])
        for k, (row, _d) in enumerate(rows_data):
            self.setup_table.horizontalHeaderItem(k + 1).setForeground(QtGui.QColor(run_color(row)))
        self.setup_table.setRowCount(len(keys))
        diff_bg = QtGui.QColor("#fff3b0")
        for r, key in enumerate(keys):
            vals = [d.get(key, "") for _row, d in rows_data]
            differs = len(set(vals)) > 1 and key not in ("Datum", "Pmax", "Mmax", "Notiz Lauf")
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
                self.setup_table.setItem(r, k + 1, cell)
        # feste, schmale Laufspalten mit Zeilenumbruch, damit mehrere Laeufe nebeneinander passen
        self.setup_table.setWordWrap(True)
        self.setup_table.resizeColumnToContents(0)
        self.setup_table.setColumnWidth(0, min(self.setup_table.columnWidth(0), 170))
        for k in range(len(rows_data)):
            self.setup_table.setColumnWidth(k + 1, 115)
        self.setup_table.resizeRowsToContents()
