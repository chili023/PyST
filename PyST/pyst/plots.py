"""
Leistungsdiagramm: oben Leistung (links) + Drehmoment (rechts), unten Lambda/AFR (links) + EGT (rechts),
gemeinsame Drehzahlachse, Cursor-Linie.

Achsen: Die rechte Achse bekommt eine runde Teilung, die linke Achse wird so skaliert, dass ihre
Striche auf denselben Gitterlinien liegen (dafuer darf sie "krumme" Werte haben).
Waehrend eines Laufs kann die Skalierung eingefroren werden (freeze), damit sie wie beim vorherigen
Lauf stehen bleibt.
"""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore

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


# Achsgrenzen, die der Benutzer festlegen kann (0 = automatisch)
LIMIT_KEYS = ("n_lo", "n_hi", "ps_lo", "ps_hi", "nm_lo", "nm_hi")


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

    def set_labels(self, left: str, right: str, left_color: str, right_color: str):
        self.pi.setLabel("left", left, color=left_color)
        self.pi.setLabel("right", right, color=right_color)

    def _sync(self):
        self.vb2.setGeometry(self.pi.vb.sceneBoundingRect())
        self.vb2.linkedViewChanged(self.pi.vb, self.vb2.XAxis)

    def set_scales(self, left: Tuple[float, float, float], right: Tuple[float, float, float], margin: float = 0.04):
        """(min, max, schritt) je Achse; Gitter folgt der linken Achse. Beide Achsen bekommen denselben
        relativen Rand, damit die Beschriftung oben/unten nicht abgeschnitten wird und die Striche fluchten."""
        for (lo, hi, st), setter, axis in ((left, self.pi.setYRange, "left"), (right, self.vb2.setYRange, "right")):
            span = hi - lo
            setter(lo - margin * span, hi + margin * span, padding=0)
            k = int(round(span / st))
            self.pi.getAxis(axis).setTicks([[(lo + i * st, _fmt(lo + i * st, st)) for i in range(k + 1)], []])


class DynoPlot(pg.GraphicsLayoutWidget):
    cursor_moved = QtCore.Signal(float)          # Drehzahl unter der Maus
    cursor_left = QtCore.Signal()                # Maus hat das Diagramm verlassen

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
        self.lambda_mode = True                    # False = AFR
        self.items: Dict[str, Dict[str, Tuple[object, object]]] = {}   # Lauf -> Rolle -> (Linie, Besitzer)
        self._styles: Dict[str, tuple] = {}
        self.data: Dict[str, Dict[str, np.ndarray]] = {}
        self.frozen = False
        self.last_scale: Optional[dict] = None
        self.limits: Dict[str, float] = {k: 0.0 for k in LIMIT_KEYS}
        self.cursor_lines = []
        for plot in (self.top.pi, self.bottom.pi):
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#555555", width=1, style=QtCore.Qt.DashLine))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self.cursor_lines.append(line)
        self.scene().sigMouseMoved.connect(self._mouse_moved)
        self.apply_scale(self.default_scale())

    # ------------------------------------------------------------ Kurven
    def set_curves(self, key: str, n, ps, nm, lam=None, egt1=None, egt2=None, color="#000000",
                   width: float = 2.0, rescale: bool = True):
        """Kurven eines Laufs setzen. Existiert der Lauf schon (z.B. die Live-Kurve), werden nur die Daten
        der vorhandenen Linien ersetzt – kein Neuaufbau, damit die Anzeige fluessig bleibt."""
        n = np.asarray(n, float)
        thin = max(1.0, width - 0.5)
        store = {"n": n, "ps": np.asarray(ps, float), "nm": np.asarray(nm, float)}
        want = {"ps": (self.top.pi, store["ps"], QtCore.Qt.SolidLine, width),
                "nm": (self.top.vb2, store["nm"], QtCore.Qt.DashLine, width)}
        if lam is not None and len(lam) and np.isfinite(lam).any():
            lam = np.asarray(lam, float)
            store["lam"] = lam
            want["lam"] = (self.bottom.pi, lam if self.lambda_mode else lam * 14.7, QtCore.Qt.SolidLine, thin)
        for role, egt, style in (("egt1", egt1, QtCore.Qt.DashLine), ("egt2", egt2, QtCore.Qt.DotLine)):
            if egt is None:
                continue
            egt = np.asarray(egt, float)
            store[role] = egt
            if len(egt) and np.nanmax(np.nan_to_num(egt)) > 0:
                want[role] = (self.bottom.vb2, egt, style, thin)

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
                item = pg.PlotCurveItem(n, y, pen=pg.mkPen(color, width=wd, style=style), connect="finite")
                owner.addItem(item)
                cur[role] = (item, owner)
        self.items[key] = cur
        self._styles[key] = (color, width)
        self.data[key] = store
        if rescale and not self.frozen:
            self.auto_scale()

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

    def set_limits(self, limits: Dict[str, float]):
        """Feste Achsgrenzen (0 = automatisch), z.B. {"n_lo": 5000, "ps_lo": 5}. Wirkt sofort."""
        self.limits = {k: float(limits.get(k, 0.0) or 0.0) for k in LIMIT_KEYS}
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
        lam = (0.7, 1.3, 0.1) if self.lambda_mode else (10.0, 16.0, 1.0)
        k = int(round((lam[1] - lam[0]) / lam[2]))
        egt_step = nice_step(max(100.0, sc.get("egt", 800.0) * 1.05) / k)
        self.bottom.set_scales(lam, (0.0, egt_step * k, egt_step))

    def freeze(self, on: bool = True):
        """Skalierung festhalten (waehrend des Laufs)."""
        self.frozen = on
        if on and self.last_scale:
            self.apply_scale(self.last_scale)

    def set_lambda_mode(self, lam: bool):
        self.lambda_mode = lam
        self.bottom.set_labels("Lambda λ" if lam else "AFR", "EGT [°C]", "#2e7d32", "#ef6c00")
        if self.last_scale:
            self.apply_scale(self.last_scale)

    # ------------------------------------------------------------ Cursor
    def _mouse_moved(self, pos):
        for plot in (self.top.pi, self.bottom.pi):
            if plot.sceneBoundingRect().contains(pos):
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
        for k in ("lam", "egt1", "egt2"):
            if k in d and len(d[k]) > j:
                out[k] = float(d[k][j])
        return out
