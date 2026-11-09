"""
Reiter „Kalibrierung“: Ausrollversuch ein/aus und Einstellungen, Ergebnis des letzten Ausrollversuchs
(Messpunkte + Fit als Verlustleistung oder Verlustmoment ueber der Motordrehzahl Rolle x i), Knopf „Übernehmen“
und die aktuell gueltigen Verluste. Gerechnet wird in ausrollen.py, uebernommen im Hauptfenster (Signal).
Knopf „Ausrollversuch (Assistent) …“ (Signal assistent): Ausrollversuch ohne Lauf mit abgehobenem Rad
(ausroll_assistent.py, Verluste der Rolle allein). Diagramm (VerlustDiagramm) und Werte (werte_html) nutzt der
Assistent mit.

Einstellungen in einstellungen.json unter "ausrollen": an, n_ende, t_max, quadratisch, letzte (Ergebnis mit
Messpunkten), uebernommen (zuletzt uebernommenes Ergebnis), assistent_ziel (Zieldrehzahl Rolle im Assistenten).
"""
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from . import physics
from . import theme
from .ausrollen import AusrollErgebnis, verlust_ps
from .runner import COAST_N_ENDE, COAST_T_MAX

ANZEIGE = (("ps", "Verlustleistung [PS]"), ("nm", "Verlustmoment an der Rolle [Nm]"))
FARBE_FIT = "#c62828"          # Basisfarben, je Thema in der Helligkeit angepasst (theme.kurvenfarbe)
FARBE_PUNKTE = "#1f5fbf"
FARBE_AKTUELL = "#7f7f7f"


ROLLE_ALLEIN_QUELLE = "Rolle allein"      # steht in der Quelle (AusrollErgebnis.quelle) eines Assistenten-Ergebnisses
ROLLE_ALLEIN = ("Verluste der Rolle allein (Lager, Luft) – Rad, Kette und Getriebeausgang sind nicht enthalten "
                "(Rad war abgehoben).")


def _komma(x: float, dec: int = 2) -> str:
    return f"{x:.{dec}f}".replace(".", ",") if x is not None and np.isfinite(x) else "–"


def werte_html(r: AusrollErgebnis) -> str:
    """Tabelle M0/M1/m2, Fit-Guete, Bereiche (Gruppe „Letzter Ausrollversuch“ und Assistent)."""
    if not r.ok and not len(r.punkte_n):
        return ""
    rows = [("M0 (n = 0)", f"{_komma(r.m0)} Nm"),
            ("M1", f"{_komma(r.m1)} Nm bei n1 = {r.n1:.0f} 1/min Rolle"),
            ("m2 (quadratisch)", f"{_komma(r.m2, 3)} Nm/(1000/min)²" if r.quadratisch else "– (linear)"),
            ("Fit-Güte", f"R² {_komma(r.r2, 4)}, RMS {_komma(r.rms_nm)} Nm"),
            ("Bereich Rolle", f"{r.n_bis:.0f} – {r.n_von:.0f} 1/min"),
            ("Bereich Motor", f"{r.n_bis * r.ratio:.0f} – {r.n_von * r.ratio:.0f} 1/min (i = {_komma(r.ratio, 3)})")]
    if r.rolle_allein:
        rows += [("Ausgewertet", f"{_komma(r.dauer_s, 1)} s, Anfang {_komma(r.verworfen_s, 1)} s verworfen"),
                 ("Gemessen", "Rolle allein, Rad abgehoben")]
    else:
        rows += [("Ausgewertet", f"{_komma(r.dauer_s, 1)} s, Übergang {_komma(r.verworfen_s, 1)} s verworfen"),
                 ("Kupplung erkannt über", r.kupplung or "–")]
    if r.ok:
        n_top = r.n_von
        rows.append(("Verlust oben", f"{_komma(float(r.mv(n_top)), 1)} Nm = "
                                     f"{_komma(float(verlust_ps(n_top, r.mv(n_top))), 1)} PS"))
    return "<table cellspacing=3>" + "".join(f"<tr><td>{a}</td><td><b>{b}</b></td></tr>" for a, b in rows) \
        + "</table>"


class VerlustDiagramm(QtWidgets.QWidget):
    """Auswahl „Anzeige“ (PS/Nm) und Diagramm: Messpunkte (Klassenmittel), Fit und die aktuell gueltigen Verluste
    ueber der Motordrehzahl (Rolle × i). zeige(ergebnis, aktuell) zeichnet neu."""
    anzeige_geaendert = QtCore.Signal(str)

    def __init__(self, anzeige: str = "ps", hoehe: int = 280):
        super().__init__()
        self.result: Optional[AusrollErgebnis] = None
        self.current: Optional[physics.DynoParams] = None
        pl = QtWidgets.QVBoxLayout(self)
        pl.setContentsMargins(0, 0, 0, 0)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QLabel("Anzeige"))
        self.anzeige = QtWidgets.QComboBox()
        for key, text in ANZEIGE:
            self.anzeige.addItem(text, key)
        self.anzeige.setCurrentIndex(max(0, self.anzeige.findData(anzeige)))
        hb.addWidget(self.anzeige)
        hb.addStretch(1)
        pl.addLayout(hb)
        self.plot = pg.PlotWidget(background=theme.farbe("plot"))
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.35)
        self.plot.setLabel("bottom", "Motordrehzahl (Rolle × i) [1/min]")
        self.plot.setMinimumHeight(hoehe)
        self.plot.addLegend(offset=(10, 10))
        self.c_punkte = self.plot.plot([], [], pen=None, symbol="o", symbolSize=6, symbolPen=None,
                                       symbolBrush=FARBE_PUNKTE, name="Messpunkte (Klassenmittel)")
        self.c_fit = self.plot.plot([], [], pen=pg.mkPen(FARBE_FIT, width=2.5), name="Fit")
        self.c_aktuell = self.plot.plot([], [], pen=pg.mkPen(FARBE_AKTUELL, width=1.5, style=QtCore.Qt.DashLine),
                                        name="aktuell gültig")
        theme.diagramm(self.plot, self._kurven_thema)
        pl.addWidget(self.plot, 1)
        self.anzeige.currentIndexChanged.connect(self._anzeige_gewechselt)

    def _anzeige_gewechselt(self, _i):
        self._draw()
        self.anzeige_geaendert.emit(self.anzeige.currentData())

    def _kurven_thema(self, _thema: str):
        k = theme.kurvenfarbe
        self.c_punkte.setSymbolBrush(k(FARBE_PUNKTE))
        self.c_fit.setPen(pg.mkPen(k(FARBE_FIT), width=2.5))
        self.c_aktuell.setPen(pg.mkPen(k(FARBE_AKTUELL), width=1.5, style=QtCore.Qt.DashLine))

    def zeige(self, result: Optional[AusrollErgebnis], current: Optional[physics.DynoParams] = None):
        self.result, self.current = result, current
        self._draw()

    def _draw(self):
        ps = self.anzeige.currentData() == "ps"
        self.plot.setLabel("left", "Verlustleistung [PS]" if ps else "Verlustmoment Rolle [Nm]")
        r = self.result
        y = (lambda n, m: verlust_ps(n, m)) if ps else (lambda n, m: np.asarray(m, float))
        ratio = r.ratio if r is not None else (self.current.ratio if self.current else 1.0)
        if r is not None and len(r.punkte_n):
            self.c_punkte.setData(r.punkte_n * r.ratio, y(r.punkte_n, r.punkte_m))
        else:
            self.c_punkte.setData([], [])
        if r is not None and r.ok:
            g = np.linspace(0.0, max(r.n_von, 1.0), 120)
            self.c_fit.setData(g * r.ratio, y(g, r.mv(g)))
        else:
            self.c_fit.setData([], [])
        p = self.current
        if p is not None and (p.m0 or p.m1 or p.m2):
            top = r.n_von if r is not None and r.n_von > 0 else max(p.n1, 100.0)
            g = np.linspace(0.0, top, 120)
            self.c_aktuell.setData(g * ratio, y(g, physics.loss_torque(g, p.m0, p.m1, p.n1, p.m2)))
        else:
            self.c_aktuell.setData([], [])


class KalibPage(QtWidgets.QScrollArea):
    uebernehmen = QtCore.Signal(object, object)        # AusrollErgebnis, Lauf-ID (oder None)
    lauf_auswerten = QtCore.Signal()                   # markierten Lauf auswerten
    assistent = QtCore.Signal()                        # Ausrollversuch-Assistent (Rad abgehoben) oeffnen

    def __init__(self, settings: dict, save: Callable[[], None]):
        super().__init__()
        self.settings = settings
        self.save = save
        self.cfg = settings.setdefault("ausrollen", {})
        self.result: Optional[AusrollErgebnis] = None
        self.result_rid = None
        self.current: Optional[physics.DynoParams] = None
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        lay = QtWidgets.QVBoxLayout(inner)

        # ---- Ausrollversuch ein/aus
        g = QtWidgets.QGroupBox("Ausrollversuch (Verluste von Rolle, Rad, Kette und Getriebeausgang)")
        gl = QtWidgets.QFormLayout(g)
        self.an = QtWidgets.QCheckBox("Ausrollversuch nach jedem Lauf")
        self.an.setChecked(bool(self.cfg.get("an", False)))
        self.an.setStyleSheet("font-weight: 600;")
        gl.addRow(self.an)
        self.n_ende = QtWidgets.QDoubleSpinBox()
        self.n_ende.setRange(0, 30000)
        self.n_ende.setDecimals(0)
        self.n_ende.setSingleStep(250)
        self.n_ende.setValue(float(self.cfg.get("n_ende", COAST_N_ENDE)))
        self.n_ende.setKeyboardTracking(False)
        self.n_ende.setMaximumWidth(140)
        gl.addRow("Ausrollen bis Motordrehzahl (Rolle × i) [1/min], 0 = bis Stillstand", self.n_ende)
        self.t_max = QtWidgets.QDoubleSpinBox()
        self.t_max.setRange(5, 900)
        self.t_max.setDecimals(0)
        self.t_max.setSuffix(" s")
        self.t_max.setValue(float(self.cfg.get("t_max", COAST_T_MAX)))
        self.t_max.setKeyboardTracking(False)
        self.t_max.setMaximumWidth(140)
        gl.addRow("Ausrollen höchstens", self.t_max)
        self.quad = QtWidgets.QCheckBox("Verlustmoment mit quadratischem Anteil m2 (sonst linear wie LabVIEW)")
        self.quad.setChecked(bool(self.cfg.get("quadratisch", True)))
        gl.addRow(self.quad)
        hint = QtWidgets.QLabel(
            "Ablauf: Lauf wie immer. Nach „Gas weg“ sofort die <b>Kupplung ziehen</b> und gezogen halten, "
            "bis die Rolle ausgerollt ist (Anzeige „KUPPLUNG ZIEHEN“). Nicht bremsen, nicht schalten. "
            "Die Aufzeichnung endet bei der eingestellten Drehzahl, nach der Höchstdauer oder mit "
            "„Ausrollen beenden (Esc)“. Ausgeschaltet läuft alles wie bisher.")
        hint.setWordWrap(True)
        theme.rolle(hint, "neben")
        gl.addRow(hint)
        # ohne Lauf: Assistent mit abgehobenem Rad (Verluste der Rolle allein)
        hb = QtWidgets.QHBoxLayout()
        self.assist_btn = QtWidgets.QPushButton("Ausrollversuch (Assistent) …")
        self.assist_btn.setToolTip("Ausrollversuch ohne Leistungslauf, Schritt für Schritt: Rolle beschleunigen, Rad "
                                   "von der Rolle nehmen, Messung starten. Misst nur die Verluste der Rolle selbst.")
        self.assist_btn.setStyleSheet("font-weight: 600;")
        hb.addWidget(self.assist_btn)
        assist_hint = QtWidgets.QLabel("Ohne Lauf: Rad abheben, Rolle frei auslaufen lassen – misst nur die Rolle "
                                       "(Lager, Luft).")
        assist_hint.setWordWrap(True)
        theme.rolle(assist_hint, "neben")
        hb.addWidget(assist_hint, 1)
        gl.addRow(hb)
        lay.addWidget(g)

        # ---- Ergebnis
        g = QtWidgets.QGroupBox("Letzter Ausrollversuch")
        rl = QtWidgets.QVBoxLayout(g)
        self.source_label = QtWidgets.QLabel("Noch kein Ausrollversuch ausgewertet.")
        self.source_label.setWordWrap(True)
        rl.addWidget(self.source_label)
        self.msg_label = QtWidgets.QLabel("")
        self.msg_label.setWordWrap(True)
        self.msg_label.setVisible(False)
        rl.addWidget(self.msg_label)
        top = QtWidgets.QHBoxLayout()
        self.values = QtWidgets.QLabel("")
        self.values.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.values.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.values.setMinimumWidth(300)
        top.addWidget(self.values)
        self.diagramm = VerlustDiagramm(self.cfg.get("anzeige", "ps"))
        self.anzeige, self.plot = self.diagramm.anzeige, self.diagramm.plot
        self.c_punkte, self.c_fit, self.c_aktuell = self.diagramm.c_punkte, self.diagramm.c_fit, self.diagramm.c_aktuell
        top.addWidget(self.diagramm, 1)
        rl.addLayout(top, 1)
        hb = QtWidgets.QHBoxLayout()
        self.lauf_btn = QtWidgets.QPushButton("Markierten Lauf auswerten")
        self.lauf_btn.setToolTip("Ausrollphase des in der Laufliste (Reiter Messen) markierten Laufs auswerten")
        self.apply_btn = QtWidgets.QPushButton("Übernehmen")
        self.apply_btn.setStyleSheet("font-weight: 700;")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("M0, M1, n1 und m2 in die Einstellungen (Filter & Verluste) eintragen – gilt für "
                                  "neue Läufe und den ausgewerteten Lauf")
        hb.addWidget(self.lauf_btn)
        hb.addStretch(1)
        hb.addWidget(self.apply_btn)
        rl.addLayout(hb)
        lay.addWidget(g, 1)

        # ---- aktuell gueltig
        g = QtWidgets.QGroupBox("Aktuell gültige Verluste (Einstellungen → Filter & Verluste)")
        cl = QtWidgets.QVBoxLayout(g)
        self.current_label = QtWidgets.QLabel("")
        self.current_label.setWordWrap(True)
        self.current_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        cl.addWidget(self.current_label)
        lay.addWidget(g)

        self.an.toggled.connect(self._store)
        self.quad.toggled.connect(self._store)
        self.n_ende.valueChanged.connect(self._store)
        self.t_max.valueChanged.connect(self._store)
        self.diagramm.anzeige_geaendert.connect(lambda _a: self._store())
        self.apply_btn.clicked.connect(self._apply)
        self.lauf_btn.clicked.connect(self.lauf_auswerten.emit)
        self.assist_btn.clicked.connect(self.assistent.emit)
        if self.cfg.get("letzte"):
            try:
                self.show_result(AusrollErgebnis.aus_dict(self.cfg["letzte"]), self.cfg.get("letzte_text", ""),
                                 None, store=False)
            except (TypeError, ValueError):
                pass

    # ------------------------------------------------------------------ Einstellungen
    def enabled(self) -> bool:
        return self.an.isChecked()

    def config(self):
        """(n_ende [1/min], t_max [s], quadratisch)"""
        return self.n_ende.value(), self.t_max.value(), self.quad.isChecked()

    def _store(self, *_a):
        self.cfg.update(an=self.an.isChecked(), n_ende=self.n_ende.value(), t_max=self.t_max.value(),
                        quadratisch=self.quad.isChecked(), anzeige=self.anzeige.currentData())
        self.settings["ausrollen"] = self.cfg
        self.save()

    # ------------------------------------------------------------------ Ergebnis
    def show_result(self, res: AusrollErgebnis, text: str = "", rid=None, store: bool = True):
        """Ergebnis anzeigen (text: woher, z.B. Lauf und Fahrzeug). Uebernommen wird erst mit dem Knopf."""
        self.result, self.result_rid = res, rid
        self.source_label.setText(text or "Ausrollversuch")
        if res.ok:
            hinweise = list(res.hinweise) + ([ROLLE_ALLEIN] if res.rolle_allein else [])
            self.msg_label.setText("<br>".join(hinweise))
            self.msg_label.setStyleSheet("")
            theme.stufe(self.msg_label, theme.WARNUNG)
            self.msg_label.setVisible(bool(hinweise))
        else:
            self.msg_label.setText(res.meldung)
            self.msg_label.setStyleSheet("font-weight: 600;")
            theme.stufe(self.msg_label, theme.FEHLER)
            self.msg_label.setVisible(True)
        self.values.setText(self._values_html(res))
        self.apply_btn.setEnabled(res.ok)
        self._draw()
        if store:
            self.cfg["letzte"] = res.als_dict(punkte=True)
            self.cfg["letzte_text"] = text
            self.save()

    @staticmethod
    def _values_html(r: AusrollErgebnis) -> str:
        return werte_html(r)

    def set_current(self, p: physics.DynoParams):
        """Aktuell gueltige Verluste anzeigen (nach Start, Uebernehmen und Aendern in den Einstellungen)."""
        self.current = p
        if not (p.m0 or p.m1 or p.m2):
            self.current_label.setText("Keine Verluste eingetragen (M0 = M1 = 0): Ergebnis = Radleistung.")
        else:
            quelle = p.verlust_quelle or "von Hand eingetragen"
            self.current_label.setText(
                f"M0 {_komma(p.m0)} Nm, M1 {_komma(p.m1)} Nm bei n1 = {p.n1:.0f} 1/min Rolle"
                + (f", m2 {_komma(p.m2, 3)} Nm/(1000/min)²" if p.m2 else "")
                + f"   –   Quelle: {quelle}.   " + ("Ergebnis „Motor“ = Radleistung + Verluste der Rolle (ohne Rad, "
                                                   "Kette, Getriebeausgang)." if ROLLE_ALLEIN_QUELLE in quelle else
                                                   "Ergebnis = Motorleistung (Radleistung + Verluste)."))
        self._draw()

    def _draw(self):
        self.diagramm.zeige(self.result, self.current)

    def _apply(self):
        if self.result is not None and self.result.ok:
            self.uebernehmen.emit(self.result, self.result_rid)
