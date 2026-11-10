"""
Export: PDF-Bericht (Diagramm + Ergebnistabelle + Setup-Daten) und CSV.
"""
import csv
import datetime as _dt
from typing import Dict, List

import numpy as np
from PySide6 import QtCore, QtGui

from .channels import Channel


def export_csv(path: str, channels: Dict[str, Channel], decimal_comma: bool = True):
    """Alle Kanaele eines Laufs als Spalten. decimal_comma=True: Excel (deutsch), ';' + ','."""
    names = list(channels)
    n = max((len(c.values) for c in channels.values()), default=0)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([f"{k} [{channels[k].unit}]" if channels[k].unit else k for k in names])
        for i in range(n):
            row = []
            for k in names:
                v = channels[k].values
                x = v[i] if i < len(v) else np.nan
                s = "" if not np.isfinite(x) else f"{x:.6g}"
                row.append(s.replace(".", ",") if decimal_comma else s)
            w.writerow(row)


def export_pdf(path: str, plot_image: QtGui.QImage, title: str, rows: List[Dict[str, str]], notes: List[str],
               untertitel: str = ""):
    """rows: je Lauf {farbe, name, fahrzeug, ergebnis, klima, details}; plot_image = gerendertes Diagramm.
    untertitel: Zeile unter dem Titel (woher das Diagramm kommt, Achsen, Kurven). Passt die Tabelle nicht auf die
    erste Seite, geht sie auf weiteren Seiten weiter (Kopf der Tabelle wiederholt). notes: Fusszeilen je Seite."""
    writer = QtGui.QPdfWriter(path)
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageOrientation(QtGui.QPageLayout.Landscape)
    writer.setPageMargins(QtCore.QMarginsF(12, 10, 12, 10), QtGui.QPageLayout.Millimeter)
    writer.setResolution(300)
    writer.setTitle(title)
    p = QtGui.QPainter(writer)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    W, H = writer.width(), writer.height()
    mm = writer.resolution() / 25.4

    def font(pt, bold=False):
        f = QtGui.QFont("Helvetica")
        f.setPointSizeF(pt)
        f.setBold(bold)
        p.setFont(f)

    flags = QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap
    stand = "PyST · " + _dt.datetime.now().strftime("%d.%m.%Y %H:%M")

    def fuss() -> float:
        """Fusszeilen von unten nach oben; liefert die Oberkante."""
        font(7)
        unten = H
        for line in reversed(notes):
            rect = p.boundingRect(QtCore.QRectF(0, 0, W, 1e6), flags, line)
            unten -= rect.height()
            p.drawText(QtCore.QRectF(0, unten, W, rect.height()), flags, line)
            unten -= 0.5 * mm
        return unten

    # Kopf
    font(16, True)
    head_h = 9 * mm
    p.drawText(QtCore.QRectF(0, 0, W, head_h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
    font(9)
    p.drawText(QtCore.QRectF(0, 0, W, head_h), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, stand)
    y = head_h + 1 * mm
    if untertitel:
        font(8)
        rect = p.boundingRect(QtCore.QRectF(0, y, W, 1e6), flags, untertitel)
        p.drawText(QtCore.QRectF(0, y, W, rect.height()), flags, untertitel)
        y += rect.height() + 1 * mm
    y += 1 * mm

    # Diagramm
    if plot_image is not None and not plot_image.isNull():
        max_h = H * 0.60
        scale = min(W / plot_image.width(), max_h / plot_image.height())
        tw, th = plot_image.width() * scale, plot_image.height() * scale
        p.drawImage(QtCore.QRectF((W - tw) / 2, y, tw, th), plot_image)
        y += th + 3 * mm

    # Tabelle
    cols = [("", 0.025), ("Lauf", 0.19), ("Fahrzeug / Setup", 0.30), ("Ergebnis", 0.19), ("Klima", 0.12),
            ("Details", 0.175)]
    keys = ["name", "fahrzeug", "ergebnis", "klima", "details"]

    def tabellenkopf(y0: float) -> float:
        font(8, True)
        x = 0.0
        hh = 5 * mm
        for label, frac in cols:
            p.drawText(QtCore.QRectF(x, y0, W * frac, hh), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
            x += W * frac
        y0 += hh
        p.drawLine(QtCore.QPointF(0, y0), QtCore.QPointF(W, y0))
        font(7.5)
        return y0 + 1 * mm

    unten = fuss() - 2 * mm
    y = tabellenkopf(y)
    for r in rows:
        # Zeilenhoehe = hoechste Zelle
        h = 0.0
        x = W * cols[0][1]
        for (label, frac), key in zip(cols[1:], keys):
            rect = p.boundingRect(QtCore.QRectF(x, y, W * frac - 2 * mm, 1e6), flags, r.get(key, ""))
            h = max(h, rect.height())
            x += W * frac
        if y + h > unten:
            writer.newPage()                       # weiter auf der naechsten Seite
            font(9)
            p.drawText(QtCore.QRectF(0, 0, W, 6 * mm), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       f"{title} (Fortsetzung)")
            p.drawText(QtCore.QRectF(0, 0, W, 6 * mm), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, stand)
            unten = fuss() - 2 * mm
            y = tabellenkopf(7 * mm)
        p.fillRect(QtCore.QRectF(0.5 * mm, y + 0.8 * mm, 3.5 * mm, 2.2 * mm), QtGui.QColor(r.get("farbe", "#000")))
        x = W * cols[0][1]
        for (label, frac), key in zip(cols[1:], keys):
            p.drawText(QtCore.QRectF(x, y, W * frac - 2 * mm, h), flags, r.get(key, ""))
            x += W * frac
        y += h + 1.5 * mm
        p.setPen(QtGui.QColor("#cccccc"))
        p.drawLine(QtCore.QPointF(0, y - 0.8 * mm), QtCore.QPointF(W, y - 0.8 * mm))
        p.setPen(QtGui.QColor("#000000"))
    p.end()


def render_plot(scene, width_px: int = 1800) -> QtGui.QImage:
    """Diagramm hochaufgeloest als Bild (pyqtgraph skaliert Schrift und Linien korrekt mit)."""
    from pyqtgraph.exporters import ImageExporter
    ex = ImageExporter(scene)
    ex.parameters()["width"] = width_px
    ex.parameters()["antialias"] = True
    return ex.export(toBytes=True)
