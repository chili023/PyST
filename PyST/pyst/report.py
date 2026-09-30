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


def export_pdf(path: str, plot_image: QtGui.QImage, title: str, rows: List[Dict[str, str]], notes: List[str]):
    """rows: je Lauf {farbe, name, fahrzeug, ergebnis, klima, details}; plot_image = gerendertes Diagramm."""
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

    # Kopf
    font(16, True)
    head_h = 9 * mm
    p.drawText(QtCore.QRectF(0, 0, W, head_h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
    font(9)
    p.drawText(QtCore.QRectF(0, 0, W, head_h), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
               "PyST · " + _dt.datetime.now().strftime("%d.%m.%Y %H:%M"))
    y = head_h + 2 * mm

    # Diagramm
    max_h = H * 0.60
    scale = min(W / plot_image.width(), max_h / plot_image.height())
    tw, th = plot_image.width() * scale, plot_image.height() * scale
    p.drawImage(QtCore.QRectF((W - tw) / 2, y, tw, th), plot_image)
    y += th + 3 * mm

    # Tabelle
    cols = [("", 0.025), ("Lauf", 0.19), ("Fahrzeug / Setup", 0.30), ("Ergebnis", 0.19), ("Klima", 0.12),
            ("Details", 0.175)]
    keys = ["name", "fahrzeug", "ergebnis", "klima", "details"]
    flags = QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap
    font(8, True)
    x = 0.0
    hh = 5 * mm
    for label, frac in cols:
        p.drawText(QtCore.QRectF(x, y, W * frac, hh), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
        x += W * frac
    y += hh
    p.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(W, y))
    y += 1 * mm
    font(7.5)
    for r in rows:
        # Zeilenhoehe = hoechste Zelle
        h = 0.0
        x = W * cols[0][1]
        for (label, frac), key in zip(cols[1:], keys):
            rect = p.boundingRect(QtCore.QRectF(x, y, W * frac - 2 * mm, 1e6), flags, r.get(key, ""))
            h = max(h, rect.height())
            x += W * frac
        if y + h > H - 8 * mm:
            break
        p.fillRect(QtCore.QRectF(0.5 * mm, y + 0.8 * mm, 3.5 * mm, 2.2 * mm), QtGui.QColor(r.get("farbe", "#000")))
        x = W * cols[0][1]
        for (label, frac), key in zip(cols[1:], keys):
            p.drawText(QtCore.QRectF(x, y, W * frac - 2 * mm, h), flags, r.get(key, ""))
            x += W * frac
        y += h + 1.5 * mm
        p.setPen(QtGui.QColor("#cccccc"))
        p.drawLine(QtCore.QPointF(0, y - 0.8 * mm), QtCore.QPointF(W, y - 0.8 * mm))
        p.setPen(QtGui.QColor("#000000"))
    font(7)
    for line in notes:
        p.drawText(QtCore.QRectF(0, H - 6 * mm, W, 6 * mm), QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, line)
    p.end()


def render_plot(scene, width_px: int = 1800) -> QtGui.QImage:
    """Diagramm hochaufgeloest als Bild (pyqtgraph skaliert Schrift und Linien korrekt mit)."""
    from pyqtgraph.exporters import ImageExporter
    ex = ImageExporter(scene)
    ex.parameters()["width"] = width_px
    ex.parameters()["antialias"] = True
    return ex.export(toBytes=True)
