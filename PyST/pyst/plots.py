"""
Leistungsdiagramm: oben Leistung (links) + Drehmoment (rechts), unten Lambda/AFR (links) + EGT (rechts),
gemeinsame Drehzahlachse, Cursor-Linie.

Achsen: Die rechte Achse bekommt eine runde Teilung, die linke Achse wird so skaliert, dass ihre
Striche auf denselben Gitterlinien liegen (dafuer darf sie "krumme" Werte haben).
Waehrend eines Laufs kann die Skalierung eingefroren werden (freeze), damit sie wie beim vorherigen
Lauf stehen bleibt.

Unteres Diagramm: frei belegbar (set_lower) – je Achse bis zu 4 Kanaele (λ/EGT vom Board, ECU-Kanaele),
Strichart = Kanal, Farbe = Lauf; ganz ausblendbar (set_lower_visible).

Zusatzkanaele (z.B. rusEFI) werden ohne eigene Achse ueber das Leistungsdiagramm gelegt: jeder in
einer eigenen ViewBox, die ihren Wertebereich auf die volle Hoehe streckt. Farbe = Lauf, Strichart =
Kanal; die Legende oben links nennt Strichart und Bereich.
"""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

GRID_ALPHA = 60
# 20 gut unterscheidbare Farben (auf weissem Grund lesbar)
RUN_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf",
              "#bcbd22", "#7f7f7f", "#393b79", "#ad494a", "#637939", "#e6550d", "#6b6ecf", "#843c39",
              "#d6616b", "#3182bd", "#31a354", "#756bb1"]
# Farben der alten Version, die automatisch vergeben wurden (werden beim Start zurueckgesetzt)
OLD_AUTO_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#e377c2", "#17becf",
                   "#bcbd22", "#7f7f7f"]


def run_color(row) -> str:
    """Farbe eines Laufs: selbst gewaehlte Farbe aus der Datenbank, sonst fest aus der Lauf-Nummer.
    Schritt 7 durch 20 Farben -> aufeinanderfolgende Laeufe bekommen deutlich verschiedene Farben,
    eine Farbe wiederholt sich erst nach 20 Laeufen."""
    return row.farbe or RUN_COLORS[(row.id * 7) % len(RUN_COLORS)]


def nice_step(raw: float) -> float:
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    e = math.floor(math.log10(raw))
    f = raw / 10 ** e
    for n in (1.0, 2.0, 2.5, 5.0, 10.0):
        if f <= n + 1e-9:
            return n * 10 ** e
    return 10.0 * 10 ** e


def _fmt(v: float, step: float) -> str:
    dec = 0 if step >= 1 else (1 if step >= 0.1 else 2)
    if abs(step - round(step)) > 1e-6 and step > 1:
        dec = 1
    return f"{v:.{dec}f}"


def aligned_scale(max_right: float, max_left: float, intervals: int = 6,
                  min_right: float = 0.0, min_left: float = 0.0,
                  fixed_right: bool = False, fixed_left: bool = False) -> Tuple[float, float, float, float, int]:
    """Rechte Achse rund, linke Achse passend. -> (rechts_oben, rechts_schritt, links_oben, links_schritt, k)
    min_*: untere Grenze der Achse. fixed_*: max_* ist eine feste Grenze (kein Rand von 6 %); die linke
    Achse endet dann genau dort, die rechte auf dem naechsten runden Strich."""
    span_r = max(1e-6, (max_right - min_right) * (1.0 if fixed_right else 1.06))
    step_r = nice_step(span_r / intervals)
    k = max(2, int(math.ceil(span_r / step_r - 1e-9)))
    top_r = min_right + k * step_r
    span_l = max(1e-6, (max_left - min_left) * (1.0 if fixed_left else 1.06))
    step_l = span_l / k
    if not fixed_left:
        # linke Teilung auf 2 signifikante Stellen aufrunden ("krumm", aber lesbar)
        mag = 10 ** math.floor(math.log10(step_l))
        step_l = math.ceil(step_l / mag * 10) / 10 * mag
    top_l = min_left + k * step_l
    return top_r, step_r, top_l, step_l, k


# Stricharten der Zusatzkanaele (Strich/Luecke in Linienbreiten) und ihre Darstellung in der Legende
# (Leistung ist durchgezogen, Drehmoment gestrichelt -> hier nur Punkt- und Strichpunkt-Muster)
OVERLAY_DASHES = [[1, 2], [8, 3, 1.5, 3], [8, 3, 1.5, 3, 1.5, 3], [1, 6]]
OVERLAY_SAMPLES = ["┈┈┈┈┈", "━·━·━", "━··━··", "· · · ·"]
OVERLAY_ALPHA = 190
MAX_OVERLAYS = len(OVERLAY_DASHES)
LOWER_STYLES = [QtCore.Qt.SolidLine, QtCore.Qt.DashLine, QtCore.Qt.DotLine, QtCore.Qt.DashDotLine]
LOWER_MARKS = ["──", "╌╌", "┈┈", "─·"]
MAX_LOWER = len(LOWER_STYLES)               # Kanaele je Achse im unteren Diagramm
LOWER_DEFAULT = {"left": ["lam"], "right": ["egt1", "egt2"]}
EGT_KEYS = ("egt1", "egt2")
EXTRA_PREFIX = "x:"             # Schluessel der Zusatzkanaele in DynoPlot.data


def _nice_range(lo: float, hi: float) -> Tuple[float, float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return 0.0, 1.0
    if hi - lo < 1e-9:
        pad = max(abs(hi) * 0.1, 1.0)
        lo, hi = lo - pad, hi + pad
    step = nice_step((hi - lo) / 5)
    return math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step


def fmt_value(v: float) -> str:
    """Kompakte Zahl fuer Anzeigen/Legende."""
    if v is None or not math.isfinite(v):
        return "–"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


# Achsgrenzen, die der Benutzer festlegen kann (0 = automatisch)
LIMIT_KEYS = ("n_lo", "n_hi", "ps_lo", "ps_hi", "nm_lo", "nm_hi", "lam_lo", "lam_hi", "egt_lo", "egt_hi")
LAMBDA_DEFAULT = (0.7, 1.3)          # untere Achse links, in λ (im AFR-Modus x 14,7)


def _even_intervals(span: float, prefer=(4, 3, 5, 6, 2)) -> int:
    """Anzahl Intervalle, bei der span/k eine runde Teilung ist (sonst 4)."""
    for k in prefer:
        st = span / k
        if abs(nice_step(st) - st) < 1e-6 * max(1.0, st):
            return k
    return 4


FIELD_H = 15


class AxisField(QtWidgets.QLineEdit):
    """Eingabefeld fuer eine Achsgrenze direkt am Achsende. Leer = automatisch (grau der aktuelle Wert)."""

    def __init__(self, parent, key: str, tip: str, align):
        super().__init__(parent)
        self.key = key
        self.setToolTip(tip + "\nZahl + Enter = feste Grenze, leer = automatisch")
        self.setAlignment(align | QtCore.Qt.AlignVCenter)
        self.setFixedSize(52, FIELD_H)
        self.setStyleSheet(
            "QLineEdit { border: 1px solid transparent; border-radius: 3px; background: #ffffff;"
            " font-size: 11px; padding: 0 2px; color: #000; }"
            "QLineEdit:hover, QLineEdit:focus { border: 1px solid #8a8a8a; background: #ffffff; }")

    def set_fixed(self, text: str):
        self.setText(text)
        f = self.font()
        f.setBold(bool(text))
        self.setFont(f)


class DualAxisPlot:
    """Ein PlotItem mit zweiter Y-Achse (rechts) in eigener ViewBox."""

    def __init__(self, layout: pg.GraphicsLayout, row: int, left_label: str, right_label: str,
                 left_color: str, right_color: str):
        self.pi = layout.addPlot(row=row, col=0)
        self.pi.showGrid(x=True, y=True, alpha=GRID_ALPHA / 255)
        self.pi.setMenuEnabled(False)
        self.pi.hideButtons()
        self.pi.showAxis("right")
        self.pi.showAxis("top")                     # geschlossener Rahmen
        self.pi.getAxis("top").setStyle(showValues=False, tickLength=0)
        self.vb2 = pg.ViewBox()
        self.vb2.setMenuEnabled(False)
        self.pi.scene().addItem(self.vb2)
        self.pi.getAxis("right").linkToView(self.vb2)
        self.vb2.setXLink(self.pi)
        self.pi.vb.sigResized.connect(self._sync)
        for ax in ("left", "right"):
            self.pi.getAxis(ax).setWidth(58)
            self.pi.getAxis(ax).enableAutoSIPrefix(False)
        self.set_labels(left_label, right_label, left_color, right_color)
        self.pi.vb.setMouseEnabled(x=True, y=False)
        self.vb2.setMouseEnabled(x=True, y=False)
        self.end_labels = True          # False: Beschriftung am oberen/unteren Ende weglassen (dort sitzen Felder)
        self._scales = None

    def set_labels(self, left: str, right: str, left_color: str, right_color: str):
        self.pi.setLabel("left", left, color=left_color)
        self.pi.setLabel("right", right, color=right_color)

    def _sync(self):
        self.vb2.setGeometry(self.pi.vb.sceneBoundingRect())
        self.vb2.linkedViewChanged(self.pi.vb, self.vb2.XAxis)

    def set_scales(self, left: Tuple[float, float, float], right: Tuple[float, float, float], margin: float = 0.04):
        """(min, max, schritt) je Achse; Gitter folgt der linken Achse. Beide Achsen bekommen denselben
        relativen Rand, damit die Beschriftung oben/unten nicht abgeschnitten wird und die Striche fluchten."""
        self._scales = (left, right, margin)
        for (lo, hi, st), setter, axis in ((left, self.pi.setYRange, "left"), (right, self.vb2.setYRange, "right")):
            span = hi - lo
            setter(lo - margin * span, hi + margin * span, padding=0)
            k = int(round(span / st))
            label = lambda i: _fmt(lo + i * st, st) if self.end_labels or 0 < i < k else ""
            self.pi.getAxis(axis).setTicks([[(lo + i * st, label(i)) for i in range(k + 1)], []])

    def set_end_labels(self, on: bool):
        self.end_labels = on
        if self._scales:
            self.set_scales(*self._scales)


class DynoPlot(pg.GraphicsLayoutWidget):
    cursor_moved = QtCore.Signal(float)          # Drehzahl unter der Maus
    cursor_left = QtCore.Signal()                # Maus hat das Diagramm verlassen
    limits_changed = QtCore.Signal(dict)         # Achsgrenzen in den Feldern geaendert

    P_COLOR, M_COLOR = "#c62828", "#1f5fbf"

    def __init__(self):
        super().__init__()
        self.setBackground("w")
        self.top = DualAxisPlot(self.ci, 0, "Leistung [PS]", "Drehmoment [Nm]", self.P_COLOR, self.M_COLOR)
        self.bottom = DualAxisPlot(self.ci, 1, "Lambda λ", "EGT [°C]", "#2e7d32", "#ef6c00")
        self.bottom.pi.setXLink(self.top.pi)
        self.top.pi.getAxis("bottom").setStyle(showValues=False)
        self.bottom.pi.setLabel("bottom", "Motordrehzahl [1/min]")
        self.ci.layout.setRowStretchFactor(0, 5)
        self.ci.layout.setRowStretchFactor(1, 2)
        self.ci.layout.setVerticalSpacing(16)      # Platz fuer die Grenzfelder zwischen den Diagrammen
        self.lambda_mode = True                    # False = AFR
        self.lower = {k: list(v) for k, v in LOWER_DEFAULT.items()}
        self.lower_labels: Dict[str, Tuple[str, str]] = {"lam": ("Lambda", "λ"), "egt1": ("EGT 1", "°C"),
                                                         "egt2": ("EGT 2", "°C")}
        self.lower_visible = True
        self.items: Dict[str, Dict[str, Tuple[object, object]]] = {}   # Lauf -> Rolle -> (Linie, Besitzer)
        self._styles: Dict[str, tuple] = {}
        self.data: Dict[str, Dict[str, np.ndarray]] = {}
        self.frozen = False
        self.last_scale: Optional[dict] = None
        self.limits: Dict[str, float] = {k: 0.0 for k in LIMIT_KEYS}
        self.overlays: List[Tuple[str, str, str]] = []        # (Kanal, Bezeichnung, Einheit)
        self.ov_boxes: Dict[str, pg.ViewBox] = {}
        self.ov_ranges: Dict[str, Tuple[float, float]] = {}
        self.ov_legend = QtWidgets.QGraphicsTextItem(self.top.pi.vb)
        self.ov_legend.setPos(4, 22)                 # unter dem Knopf "Kurven"
        self.ov_legend.setZValue(1000)
        self.top.pi.vb.sigResized.connect(self._sync_overlays)
        self.cursor_lines = []
        for plot in (self.top.pi, self.bottom.pi):
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#555555", width=1, style=QtCore.Qt.DashLine))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self.cursor_lines.append(line)
        self.scene().sigMouseMoved.connect(self._mouse_moved)
        self._build_fields()
        for plot in (self.top, self.bottom):
            plot.end_labels = False
        self.apply_scale(self.default_scale())

    # ------------------------------------------------------------ Kurven
    def set_curves(self, key: str, n, ps, nm, lam=None, egt1=None, egt2=None, color="#000000",
                   width: float = 2.0, rescale: bool = True, extra: Optional[Dict[str, np.ndarray]] = None):
        """Kurven eines Laufs setzen. Existiert der Lauf schon (z.B. die Live-Kurve), werden nur die Daten
        der vorhandenen Linien ersetzt – kein Neuaufbau, damit die Anzeige fluessig bleibt.
        extra: Zusatzkanaele {Kanal: Werte zu n}; gezeichnet werden die in set_overlays gewaehlten,
        alle anderen stehen fuer Cursor-Werte bereit."""
        n = np.asarray(n, float)
        thin = max(1.0, width - 0.5)
        store = {"n": n, "ps": np.asarray(ps, float), "nm": np.asarray(nm, float)}
        want = {"ps": (self.top.pi, store["ps"], QtCore.Qt.SolidLine, width),
                "nm": (self.top.vb2, store["nm"], QtCore.Qt.DashLine, width)}
        for role, y in (("lam", lam), ("egt1", egt1), ("egt2", egt2)):
            if y is not None:
                store[role] = np.asarray(y, float)
        for name, y in (extra or {}).items():
            y = np.asarray(y, float)
            store[EXTRA_PREFIX + name] = y
            if name in self.ov_boxes:
                want[EXTRA_PREFIX + name] = (self.ov_boxes[name], y, None, 1.6)
        want.update(self._lower_want(store, thin))

        if self._styles.get(key) != (color, width):
            self.remove(key)
        cur = self.items.get(key, {})
        for role in [r for r in cur if r not in want]:
            item, owner = cur.pop(role)
            owner.removeItem(item)
        for role, (owner, y, style, wd) in want.items():
            if role in cur:
                cur[role][0].setData(n, y)
            else:
                item = pg.PlotCurveItem(n, y, pen=self._pen(role, color, wd, style), connect="finite")
                owner.addItem(item)
                cur[role] = (item, owner)
        self.items[key] = cur
        self._styles[key] = (color, width)
        self.data[key] = store
        if any(r.startswith(EXTRA_PREFIX) for r in want):
            self._scale_overlays()
        if rescale and not self.frozen:
            self.auto_scale()

    def _lower_want(self, store: Dict[str, np.ndarray], width: float) -> dict:
        """Linien des unteren Diagramms: Rolle "lo:<Seite>:<Kanal>" -> (Besitzer, Werte, Strichart, Breite)."""
        want = {}
        if not self.lower_visible:
            return want
        for side, owner in (("left", self.bottom.pi), ("right", self.bottom.vb2)):
            for i, key in enumerate(self.lower[side]):
                y = store.get(key)
                if y is None or not len(y) or not np.isfinite(y).any():
                    continue
                if key in EGT_KEYS and np.nanmax(np.nan_to_num(y)) <= 0:
                    continue                                   # kein Sensor
                if key == "lam" and not self.lambda_mode:
                    y = y * 14.7
                want[f"lo:{side}:{key}"] = (owner, y, LOWER_STYLES[i % MAX_LOWER], width)
        return want

    def _pen(self, role: str, color: str, width: float, style):
        if role.startswith(EXTRA_PREFIX):
            names = [o[0] for o in self.overlays]
            name = role[len(EXTRA_PREFIX):]
            c = pg.mkColor(color)
            c.setAlpha(OVERLAY_ALPHA)
            pen = pg.mkPen(c, width=width)
            pen.setDashPattern(OVERLAY_DASHES[names.index(name) % MAX_OVERLAYS] if name in names else [2, 2])
            return pen
        return pg.mkPen(color, width=width, style=style)

    # ------------------------------------------------------------ unteres Diagramm
    def set_lower(self, left: List[str], right: List[str], labels: Dict[str, Tuple[str, str]]):
        """Belegung des unteren Diagramms. Schluessel: "lam", "egt1", "egt2" (Board) oder "x:<ECU-Kanal>".
        labels: Schluessel -> (Bezeichnung, Einheit). Aendert sich die Belegung, gelten die Grenzen unten
        wieder automatisch."""
        new = {"left": list(left)[:MAX_LOWER], "right": list(right)[:MAX_LOWER]}
        self.lower_labels.update(labels)
        changed = new != self.lower
        self.lower = new
        if changed:
            lim = dict(self.limits)
            for k in ("lam_lo", "lam_hi", "egt_lo", "egt_hi"):
                lim[k] = 0.0
            self.set_limits(lim)
            self.limits_changed.emit(dict(self.limits))
        self._rebuild_lower()

    def set_lower_visible(self, on: bool):
        self.lower_visible = on
        lay = self.ci.layout
        for item in (self.bottom.pi, self.bottom.vb2):
            item.setVisible(on)
        lay.setRowMinimumHeight(1, 0)
        lay.setRowMaximumHeight(1, 16777215 if on else 0)
        lay.setRowStretchFactor(1, 2 if on else 0)
        top_x = self.top.pi.getAxis("bottom")
        top_x.setStyle(showValues=not on)
        self.top.pi.setLabel("bottom", "" if on else "Motordrehzahl [1/min]")
        for k in ("lam_lo", "lam_hi", "egt_lo", "egt_hi"):
            self.fields[k].setVisible(on)
        self._rebuild_lower()
        QtCore.QTimer.singleShot(0, self._place_fields)

    def _rebuild_lower(self):
        for key, cur in self.items.items():
            for role in [r for r in cur if r.startswith("lo:")]:
                item, owner = cur.pop(role)
                owner.removeItem(item)
            store = self.data.get(key)
            if store is None:
                continue
            color, width = self._styles.get(key, ("#000000", 2.0))
            for role, (owner, y, style, wd) in self._lower_want(store, max(1.0, width - 0.5)).items():
                item = pg.PlotCurveItem(store["n"], y, pen=self._pen(role, color, wd, style), connect="finite")
                owner.addItem(item)
                cur[role] = (item, owner)
        self._lower_axis_labels()
        if self.last_scale:
            self.apply_scale(self.last_scale)

    def lower_title(self, key: str) -> Tuple[str, str]:
        if key == "lam":
            return ("Lambda", "λ") if self.lambda_mode else ("AFR", "")
        return self.lower_labels.get(key, (key, ""))

    def _lower_axis_labels(self):
        def text(side):
            parts = []
            for i, key in enumerate(self.lower[side]):
                label, unit = self.lower_title(key)
                mark = LOWER_MARKS[i % MAX_LOWER] + " " if len(self.lower[side]) > 1 else ""
                parts.append(f"{mark}{label}" + (f" [{unit}]" if unit and unit != "λ" else (" λ" if unit else "")))
            return "   ".join(parts)
        self.bottom.set_labels(text("left"), text("right"), "#2e7d32", "#ef6c00")
        self.bottom.pi.getAxis("left").setStyle(showValues=bool(self.lower["left"]))
        self.bottom.pi.getAxis("right").setStyle(showValues=bool(self.lower["right"]))

    def _lower_range(self, side: str):
        vals = [d[k] for d in self.data.values() for k in self.lower[side] if k in d and len(d[k])]
        vals = [v[np.isfinite(v)] for v in vals]
        vals = np.concatenate(vals) if vals else np.array([])
        if side == "left" and self.lower["left"] == ["lam"] and not self.lambda_mode:
            vals = vals * 14.7
        if all(k in EGT_KEYS for k in self.lower[side]):
            vals = vals[vals > 0]
        return (float(vals.min()), float(vals.max())) if len(vals) else None

    # ------------------------------------------------------------ Zusatzkanaele
    def set_overlays(self, defs: List[Tuple[str, str, str]]):
        """Zusatzkanaele im Leistungsdiagramm: [(Kanal, Bezeichnung, Einheit)], hoechstens MAX_OVERLAYS."""
        defs = list(defs)[:MAX_OVERLAYS]
        if defs == self.overlays:
            return
        for cur in self.items.values():
            for role in [r for r in cur if r.startswith(EXTRA_PREFIX)]:
                item, owner = cur.pop(role)
                owner.removeItem(item)
        for vb in self.ov_boxes.values():
            self.top.pi.scene().removeItem(vb)
        self.ov_boxes = {}
        self.overlays = defs
        for name, _label, _unit in defs:
            vb = pg.ViewBox()
            vb.setMenuEnabled(False)
            vb.setMouseEnabled(x=True, y=False)
            self.top.pi.scene().addItem(vb)
            vb.setXLink(self.top.pi)
            self.ov_boxes[name] = vb
        for key, store in self.data.items():
            color, width = self._styles.get(key, ("#000000", 2.0))
            for name, vb in self.ov_boxes.items():
                y = store.get(EXTRA_PREFIX + name)
                if y is None:
                    continue
                role = EXTRA_PREFIX + name
                item = pg.PlotCurveItem(store["n"], y, pen=self._pen(role, color, 1.6, None), connect="finite")
                vb.addItem(item)
                self.items.setdefault(key, {})[role] = (item, vb)
        self._sync_overlays()
        self._scale_overlays()

    def _sync_overlays(self):
        for vb in self.ov_boxes.values():
            vb.setGeometry(self.top.pi.vb.sceneBoundingRect())
            vb.linkedViewChanged(self.top.pi.vb, vb.XAxis)

    def _scale_overlays(self):
        lines = []
        for i, (name, label, unit) in enumerate(self.overlays):
            vals = [d[EXTRA_PREFIX + name] for d in self.data.values() if EXTRA_PREFIX + name in d]
            finite = np.concatenate([v[np.isfinite(v)] for v in vals]) if vals else np.array([])
            lo, hi = _nice_range(float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
            old = self.ov_ranges.get(name)
            if self.frozen and old:                      # waehrend des Laufs nur erweitern
                lo, hi = min(lo, old[0]), max(hi, old[1])
            self.ov_ranges[name] = (lo, hi)
            span = hi - lo
            self.ov_boxes[name].setYRange(lo - 0.04 * span, hi + 0.04 * span, padding=0)
            u = f" {unit}" if unit else ""
            lines.append(f"<span style='font-family: monospace;'>{OVERLAY_SAMPLES[i]}</span> "
                         f"{label}: {fmt_value(lo)} … {fmt_value(hi)}{u}")
        self.ov_legend.setHtml("<div style='background: rgba(255,255,255,200); color: #333; font-size: 11px;'>"
                               + "<br>".join(lines) + "</div>" if lines else "")

    def remove(self, key: str):
        for item, owner in self.items.pop(key, {}).values():
            owner.removeItem(item)
        self._styles.pop(key, None)
        self.data.pop(key, None)

    def clear_all(self):
        for key in list(self.items):
            self.remove(key)

    # ------------------------------------------------------------ Skalierung
    def default_scale(self, n_lo: float = 2000, n_hi: float = 12000) -> dict:
        return {"x": (n_lo, n_hi), "ps": 40.0, "nm": 25.0, "egt": 800.0}

    def auto_scale(self):
        if not self.data:
            return
        xs, ps, nm, egt = [], [0.0], [0.0], [0.0]
        for d in self.data.values():
            if len(d["n"]):
                xs += [float(np.nanmin(d["n"])), float(np.nanmax(d["n"]))]
                ps.append(float(np.nanmax(d["ps"])))
                nm.append(float(np.nanmax(d["nm"])))
                for k in ("egt1", "egt2"):
                    if k in d and len(d[k]):
                        egt.append(float(np.nanmax(d[k])))
        if not xs:
            return
        step = 500.0
        x = (math.floor(min(xs) / step) * step, math.ceil(max(xs) / step) * step)
        self.apply_scale({"x": x, "ps": max(ps), "nm": max(nm), "egt": max(egt) or 800.0})
        if self.overlays:
            self._scale_overlays()

    # ------------------------------------------------------------ Grenzfelder an den Achsen
    def _lam_factor(self) -> float:
        """Grenzen unten links sind in λ gespeichert, solange dort nur λ liegt (Anzeige ggf. als AFR)."""
        lam_only = self.lower["left"] == ["lam"] or not self.lower["left"]
        return 1.0 if self.lambda_mode or not lam_only else 14.7

    def _build_fields(self):
        L, R = QtCore.Qt.AlignRight, QtCore.Qt.AlignLeft
        tips = {"n_lo": ("Drehzahl von [1/min]", R), "n_hi": ("Drehzahl bis [1/min]", L),
                "ps_lo": ("Leistung von [PS]", L), "ps_hi": ("Leistung bis [PS]", L),
                "nm_lo": ("Drehmoment von [Nm]", R), "nm_hi": ("Drehmoment bis [Nm]", R),
                "lam_lo": ("Unten links von", L), "lam_hi": ("Unten links bis", L),
                "egt_lo": ("Unten rechts von", R), "egt_hi": ("Unten rechts bis", R)}
        self.fields: Dict[str, AxisField] = {}
        for key, (tip, align) in tips.items():
            fld = AxisField(self, key, tip, align)
            fld.editingFinished.connect(lambda k=key: self._field_edited(k))
            self.fields[key] = fld
        self.auto_btn = QtWidgets.QToolButton(self)
        self.auto_btn.setText("auto")
        self.auto_btn.setToolTip("Alle Achsgrenzen zurück auf automatisch")
        self.auto_btn.setStyleSheet("QToolButton { font-size: 11px; padding: 0 4px; }")
        self.auto_btn.clicked.connect(self.reset_limits)
        # Auswahlmenues oben links in beiden Diagrammen (Inhalt fuellt das Hauptfenster)
        self.top_btn, self.top_menu = self._menu_button("Kurven", "Kurven im Leistungsdiagramm")
        self.bottom_btn, self.bottom_menu = self._menu_button("Kurven", "Kanäle im unteren Diagramm")
        for plot in (self.top, self.bottom):
            plot.pi.vb.sigResized.connect(self._place_fields)

    def _menu_button(self, text: str, tip: str):
        btn = QtWidgets.QToolButton(self)
        btn.setText(text + " ▾")
        btn.setToolTip(tip)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        btn.setStyleSheet("QToolButton { font-size: 11px; padding: 0px 4px; background: rgba(255,255,255,220);"
                          " border: 1px solid #c4c4c4; border-radius: 3px; }"
                          "QToolButton::menu-indicator { image: none; }")
        btn.setFixedHeight(FIELD_H + 1)
        menu = QtWidgets.QMenu(btn)
        btn.setMenu(menu)
        return btn, menu

    def _field_edited(self, key: str):
        fld = self.fields[key]
        text = fld.text().strip().replace(",", ".")
        try:
            val = float(text) if text else 0.0
        except ValueError:
            val = self.limits.get(key, 0.0)
        if key.startswith("lam_") and val:
            val /= self._lam_factor()
        if val == self.limits.get(key, 0.0):
            self._show_limits()
            return
        lim = dict(self.limits)
        lim[key] = max(0.0, val)
        self.set_limits(lim)
        self.limits_changed.emit(dict(self.limits))

    def reset_limits(self):
        self.set_limits({})
        self.limits_changed.emit(dict(self.limits))

    def _show_limits(self):
        for key, fld in getattr(self, "fields", {}).items():
            v = self.limits.get(key, 0.0)
            if key.startswith("lam_"):
                v *= self._lam_factor()
            fld.set_fixed("" if not v else f"{v:.4g}")

    def _place_fields(self):
        def rect(item):
            # geometry() statt sceneBoundingRect(): mit Gitter reicht der Umriss der Achsen ueber das ganze Diagramm
            r = item.parentItem().mapRectToScene(item.geometry())
            return self.mapFromScene(r).boundingRect()
        h = FIELD_H
        # Felder genau auf den Endstrichen (verdecken dessen Beschriftung und zeigen den Wert selbst)
        for plot, lo, hi, rlo, rhi in ((self.top, "ps_lo", "ps_hi", "nm_lo", "nm_hi"),
                                       (self.bottom, "lam_lo", "lam_hi", "egt_lo", "egt_hi")):
            vb = rect(plot.pi.vb)
            la, ra = rect(plot.pi.getAxis("left")), rect(plot.pi.getAxis("right"))
            xl, xr = la.right() - 54, ra.left() + 2
            y_top, y_bot = vb.top() - h // 2, vb.bottom() - h // 2
            self.fields[hi].move(xl, y_top)
            self.fields[lo].move(xl, y_bot)
            self.fields[rhi].move(xr, y_top)
            self.fields[rlo].move(xr, y_bot)
        # Drehzahl: in der Zeile der Achsbeschriftung, links/rechts frei
        xplot = self.bottom if self.lower_visible else self.top
        vb = rect(xplot.pi.vb)
        ax = rect(xplot.pi.getAxis("bottom"))
        y = ax.bottom() - h - 2
        self.fields["n_lo"].move(vb.left(), y)
        self.fields["n_hi"].move(vb.right() - 52, y)
        self.auto_btn.setFixedHeight(h + 2)
        self.auto_btn.move(4, y - 1)
        for plot, btn in ((self.top, self.top_btn), (self.bottom, self.bottom_btn)):
            vb = rect(plot.pi.vb)
            btn.move(vb.left() + 3, vb.top() + 3)
        self.bottom_btn.setVisible(self.lower_visible)

    def render_image(self):
        """Bild fuer den PDF-Bericht: mit allen Achsbeschriftungen (die Felder sind nicht Teil der Szene)."""
        from . import report
        for plot in (self.top, self.bottom):
            plot.set_end_labels(True)
        try:
            return report.render_plot(self.scene())
        finally:
            for plot in (self.top, self.bottom):
                plot.set_end_labels(False)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QtCore.QTimer.singleShot(0, self._place_fields)

    def set_limits(self, limits: Dict[str, float]):
        """Feste Achsgrenzen (0 = automatisch), z.B. {"n_lo": 5000, "ps_lo": 5}. Wirkt sofort."""
        self.limits = {k: float(limits.get(k, 0.0) or 0.0) for k in LIMIT_KEYS}
        self._show_limits()
        if self.frozen and self.last_scale:
            self.apply_scale(self.last_scale)
        elif self.data:
            self.auto_scale()
        else:
            self.apply_scale(self.last_scale or self.default_scale())

    def apply_scale(self, sc: dict):
        self.last_scale = dict(sc)
        lim = self.limits
        x_lo = lim["n_lo"] or sc["x"][0]
        x_hi = lim["n_hi"] or sc["x"][1]
        if x_hi <= x_lo:
            x_hi = x_lo + 1000.0
        self.top.pi.setXRange(x_lo, x_hi, padding=0)
        ps_lo, nm_lo = lim["ps_lo"], lim["nm_lo"]
        ps_hi = lim["ps_hi"] if lim["ps_hi"] > ps_lo else max(sc["ps"] or 1.0, ps_lo + 1.0)
        nm_hi = lim["nm_hi"] if lim["nm_hi"] > nm_lo else max(sc["nm"] or 1.0, nm_lo + 1.0)
        nm_top, nm_step, ps_top, ps_step, _ = aligned_scale(
            nm_hi, ps_hi, min_right=nm_lo, min_left=ps_lo,
            fixed_right=lim["nm_hi"] > nm_lo, fixed_left=lim["ps_hi"] > ps_lo)
        self.top.set_scales((ps_lo, ps_top, ps_step), (nm_lo, nm_top, nm_step))
        # unten links: nur λ -> feste Enden (Standard 0,7 … 1,3); sonst aus den Daten. Rechts folgt mit
        # derselben Anzahl Intervalle (Gitter fluchtet).
        if self.lower["left"] == ["lam"] or not self.lower["left"]:
            f = self._lam_factor()
            lam_lo = (lim["lam_lo"] or LAMBDA_DEFAULT[0]) * f
            lam_hi = (lim["lam_hi"] or LAMBDA_DEFAULT[1]) * f
            if lam_hi <= lam_lo:
                lam_hi = lam_lo + 0.2 * f
            k = _even_intervals(lam_hi - lam_lo)
            lam_step = (lam_hi - lam_lo) / k
        else:
            rng = self._lower_range("left") or (0.0, 1.0)
            lam_lo, lam_hi = lim["lam_lo"] or min(0.0, rng[0]), lim["lam_hi"]
            if lam_hi > lam_lo and lim["lam_lo"]:
                k = _even_intervals(lam_hi - lam_lo)
                lam_step = (lam_hi - lam_lo) / k
            else:
                k = 4
                if lam_hi > lam_lo:
                    lam_step = (lam_hi - lam_lo) / k
                else:
                    lam_step = nice_step(max(1e-6, rng[1] * 1.05 - lam_lo) / k)
                    if not lim["lam_lo"]:
                        lam_lo = math.floor(lam_lo / lam_step + 1e-9) * lam_step
                    lam_hi = lam_lo + k * lam_step
        rng = self._lower_range("right")
        egt_like = all(key in EGT_KEYS for key in self.lower["right"])
        egt_lo = lim["egt_lo"] or (0.0 if egt_like or rng is None else min(0.0, rng[0]))
        if lim["egt_hi"] > egt_lo:
            egt_step = (lim["egt_hi"] - egt_lo) / k
        else:
            top = rng[1] * 1.05 if rng else (800.0 if egt_like else 1.0)
            span = max(100.0, top - egt_lo) if egt_like else max(1e-6, top - egt_lo)
            egt_step = nice_step(span / k)
            if not lim["egt_lo"] and rng is not None and not egt_like:
                egt_lo = math.floor(egt_lo / egt_step + 1e-9) * egt_step
                egt_step = nice_step(max(1e-6, top - egt_lo) / k)
        egt_top = egt_lo + egt_step * k
        self.bottom.set_scales((lam_lo, lam_hi, lam_step), (egt_lo, egt_top, egt_step))
        eff = {"n_lo": (x_lo, 500), "n_hi": (x_hi, 500), "ps_lo": (ps_lo, ps_step), "ps_hi": (ps_top, ps_step),
               "nm_lo": (nm_lo, nm_step), "nm_hi": (nm_top, nm_step), "lam_lo": (lam_lo, lam_step),
               "lam_hi": (lam_hi, lam_step), "egt_lo": (egt_lo, egt_step), "egt_hi": (egt_top, egt_step)}
        for key, (val, st) in eff.items():
            self.fields[key].setPlaceholderText(_fmt(val, st))

    def freeze(self, on: bool = True):
        """Skalierung festhalten (waehrend des Laufs)."""
        self.frozen = on
        if on and self.last_scale:
            self.apply_scale(self.last_scale)

    def set_lambda_mode(self, lam: bool):
        self.lambda_mode = lam
        self._show_limits()
        self._rebuild_lower()

    # ------------------------------------------------------------ Cursor
    def _mouse_moved(self, pos):
        for plot in (self.top.pi, self.bottom.pi):
            if plot.isVisible() and plot.sceneBoundingRect().contains(pos):
                x = plot.vb.mapSceneToView(pos).x()
                for line in self.cursor_lines:
                    line.setPos(x)
                    line.setVisible(True)
                self._cursor_inside = True
                self.cursor_moved.emit(float(x))
                return
        self._leave()

    def _leave(self):
        if getattr(self, "_cursor_inside", False):
            self._cursor_inside = False
            for line in self.cursor_lines:
                line.setVisible(False)
            self.cursor_left.emit()

    def leaveEvent(self, ev):
        self._leave()
        super().leaveEvent(ev)

    def values_at(self, key: str, n_rpm: float) -> Optional[Dict[str, float]]:
        d = self.data.get(key)
        if d is None or not len(d["n"]):
            return None
        n = d["n"]
        if n_rpm < np.nanmin(n) or n_rpm > np.nanmax(n):
            return None
        j = int(np.nanargmin(np.abs(n - n_rpm)))
        out = {"n": float(n[j]), "ps": float(d["ps"][j]), "nm": float(d["nm"][j])}
        for k in d:
            if (k in ("lam", "egt1", "egt2") or k.startswith(EXTRA_PREFIX)) and len(d[k]) > j:
                out[k] = float(d[k][j])
        return out
