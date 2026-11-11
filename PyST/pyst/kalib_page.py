"""
Gruppe „Verluste – Ausrollversuch“ (Einstellungen › Rolle/Lauf, frueher Reiter „Kalibrierung“):

  Rollen-Kalibrierung   Anzeige der gespeicherten Kalibrierung (Verlust der Rolle allein, einmal je Pruefstand)
                        und Knopf „Rollen-Kalibrierung (Assistent) …“ (Signal assistent, ausroll_assistent.py:
                        Rad abheben, Rolle frei auslaufen lassen)
  Ausrollversuch        nach dem Lauf (Kupplung ziehen) ein/aus und Einstellungen: misst die Verluste gesamt
                        (Rolle, Reifen, Kette, Getriebeausgang)
  Letzter Ausrollversuch Ergebnis (Messpunkte + Fit als Verlustleistung oder Verlustmoment ueber der Motordrehzahl
                        Rolle x i). Mit Rollen-Kalibrierung aufgeteilt: gesamt = Rolle + Reifen/Kette/Getriebe
                        (ausrollen.aufteilen), als Werte und als drei Kurven. „Übernehmen“ traegt die Verluste gesamt
                        in M0/M1/m2 ein (Motor = Rad + Verluste); ein Ergebnis des Assistenten wird stattdessen „Als
                        Rollen-Kalibrierung speichern“ (Signal kalib_speichern).
  Aktuell gueltig       die Verluste aus „Verluste (Werte für die Rechnung)“ (links daneben)

Gerechnet wird in ausrollen.py, uebernommen und gespeichert im Hauptfenster (Signale). Diagramm (VerlustDiagramm)
und Werte (werte_html) nutzt der Assistent mit.

Einstellungen in einstellungen.json unter "ausrollen": an, n_ende, t_max, quadratisch, letzte (Ergebnis mit
Messpunkten), letzte_text, letzte_n_pmax (Motordrehzahl bei Pmax des Laufs), uebernommen (zuletzt uebernommenes
Ergebnis), assistent_ziel (Zieldrehzahl Rolle im Assistenten). Die Rollen-Kalibrierung steht unter
"rollen_kalibrierung" (ausrollen.KALIB_KEY).
"""
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from . import physics
from . import theme
from .ausrollen import KALIB_KEY, AusrollErgebnis, Aufteilung, RollenKalibrierung, aufteilen, verlust_ps
from .runner import COAST_N_ENDE, COAST_T_MAX

ANZEIGE = (("ps", "Verlustleistung [PS]"), ("nm", "Verlustmoment an der Rolle [Nm]"))
FARBE_FIT = "#c62828"          # Basisfarben, je Thema in der Helligkeit angepasst (theme.kurvenfarbe)
FARBE_PUNKTE = "#1f5fbf"
FARBE_AKTUELL = "#7f7f7f"
FARBE_ROLLE = "#2e7d32"
FARBE_ANTRIEB = "#ef6c00"

KALIB_TITEL = "Rollen-Kalibrierung (Ausrollen ohne Rad)"
KALIB_KNOPF = "Rollen-Kalibrierung (Assistent) …"
KALIB_HINWEIS = ("Einmalig: Rad abheben, Rolle frei auslaufen lassen – misst Lager und Luft der Rolle. Wiederholen "
                 "nur nach Umbau an der Rolle. Danach teilt der Ausrollversuch nach dem Lauf die Verluste auf.")
ROLLE_ALLEIN = ("Verluste der Rolle allein (Lager, Luft), Rad abgehoben – das ist die Rollen-Kalibrierung, sie wird "
                "nicht in M0/M1 eingetragen.")
OHNE_KALIB = "nur gesamt – Rolle nicht kalibriert"


def _komma(x: float, dec: int = 2) -> str:
    return f"{x:.{dec}f}".replace(".", ",") if x is not None and np.isfinite(x) else "–"


def kalib_text(k: Optional[RollenKalibrierung], ratio: Optional[float] = None) -> str:
    """„Rollen-Kalibrierung: vom …, Verlust bei … 1/min = … Nm / … PS“ bzw. „noch nicht kalibriert“."""
    return "Rollen-Kalibrierung: " + (k.kurztext(ratio) if k is not None else "noch nicht kalibriert")


def aufteilung_html(auf: Aufteilung, n_pmax: Optional[float] = None) -> str:
    """Tabelle gesamt | Rolle | Reifen/Kette/Getriebe in Nm und PS an den Bezugspunkten."""
    zellen = lambda nm, ps, neg=False: (f"<td align=right>{'<b>' if not neg else ''}{_komma(nm, 1)} Nm<br>"
                                        f"{_komma(ps, 1)} PS{'</b>' if not neg else ' (&lt; 0)'}</td>")
    kopf = ("<tr><td></td><td align=right><i>gesamt</i></td><td align=right><i>Rolle</i></td>"
            "<td align=right><i>Reifen/Kette/<br>Getriebe</i></td></tr>")
    rows = []
    for name, n in auf.bezugspunkte(n_pmax):
        w = auf.werte(n)
        rows.append(f"<tr><td>{name}<br>Motor {w['n_motor']:.0f}</td>" + zellen(w["gesamt"], w["gesamt_ps"])
                    + zellen(w["rolle"], w["rolle_ps"])
                    + zellen(w["antrieb"], w["antrieb_ps"], w["antrieb"] < 0) + "</tr>")
    return ("<p style='margin-top:6px'><b>Aufteilung der Verluste</b></p><table cellspacing=0 cellpadding=4>" + kopf
            + "".join(rows) + "</table>")


def werte_html(r: AusrollErgebnis, kalib: Optional[RollenKalibrierung] = None,
               n_pmax: Optional[float] = None) -> str:
    """Tabelle M0/M1/m2, Fit-Guete, Bereiche (Gruppe „Letzter Ausrollversuch“ und Assistent); mit Kalibrierung und
    einem Ausrollversuch nach dem Lauf zusaetzlich die Aufteilung (n_pmax: Motordrehzahl bei Pmax des Laufs)."""
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
    auf = aufteilen(r, kalib)
    if r.ok:
        n_top = r.n_von
        rows.append(("Verlust oben" + (" gesamt" if not r.rolle_allein else ""),
                     f"{_komma(float(r.mv(n_top)), 1)} Nm = {_komma(float(verlust_ps(n_top, r.mv(n_top))), 1)} PS"))
        if not r.rolle_allein and auf is None:
            rows.append(("Aufteilung", OHNE_KALIB))
    html = "<table cellspacing=3>" + "".join(f"<tr><td>{a}</td><td><b>{b}</b></td></tr>" for a, b in rows) \
        + "</table>"
    return html + (aufteilung_html(auf, n_pmax) if auf is not None else "")


class VerlustDiagramm(QtWidgets.QWidget):
    """Auswahl „Anzeige“ (PS/Nm) und Diagramm ueber der Motordrehzahl (Rolle × i): Messpunkte (Klassenmittel), Fit,
    die aktuell gueltigen Verluste (M0/M1) und die Rollen-Kalibrierung. Ausrollversuch nach dem Lauf mit
    Kalibrierung: Fit = gesamt, dazu Rolle und Reifen/Kette/Getriebe (Differenz, auch < 0 wie gerechnet). Ergebnis
    des Assistenten: Fit = Rolle (neu), dazu die bisherige Kalibrierung. zeige(ergebnis, aktuell, kalib) zeichnet
    neu; in der Legende stehen nur Kurven mit Daten."""
    anzeige_geaendert = QtCore.Signal(str)

    def __init__(self, anzeige: str = "ps", hoehe: int = 280):
        super().__init__()
        self.result: Optional[AusrollErgebnis] = None
        self.current: Optional[physics.DynoParams] = None
        self.kalib: Optional[RollenKalibrierung] = None
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
        self.legende = self.plot.addLegend(offset=(10, 10))
        self.c_punkte = self.plot.plot([], [], pen=None, symbol="o", symbolSize=6, symbolPen=None,
                                       symbolBrush=FARBE_PUNKTE)
        self.c_fit = self.plot.plot([], [], pen=pg.mkPen(FARBE_FIT, width=2.5))
        self.c_rolle = self.plot.plot([], [], pen=pg.mkPen(FARBE_ROLLE, width=2.2))
        self.c_antrieb = self.plot.plot([], [], pen=pg.mkPen(FARBE_ANTRIEB, width=2.2))
        self.c_aktuell = self.plot.plot([], [], pen=pg.mkPen(FARBE_AKTUELL, width=1.5, style=QtCore.Qt.DashLine))
        self.namen = {}                                   # Kurve -> Name in der Legende
        self.legende.clear()
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
        rolle_strich = QtCore.Qt.DashLine if self.result is not None and self.result.rolle_allein else QtCore.Qt.SolidLine
        self.c_rolle.setPen(pg.mkPen(k(FARBE_ROLLE), width=2.2, style=rolle_strich))
        self.c_antrieb.setPen(pg.mkPen(k(FARBE_ANTRIEB), width=2.2))
        self.c_aktuell.setPen(pg.mkPen(k(FARBE_AKTUELL), width=1.5, style=QtCore.Qt.DashLine))
        for _s, lab in self.legende.items:               # setLabelTextColor (Thema) zeichnet die Texte nicht neu
            lab.setText(lab.text)

    def zeige(self, result: Optional[AusrollErgebnis], current: Optional[physics.DynoParams] = None,
              kalib: Optional[RollenKalibrierung] = None):
        self.result, self.current, self.kalib = result, current, kalib
        self._draw()

    def _draw(self):
        ps = self.anzeige.currentData() == "ps"
        self.plot.setLabel("left", "Verlustleistung [PS]" if ps else "Verlustmoment Rolle [Nm]")
        r, k = self.result, self.kalib
        y = (lambda n, m: verlust_ps(n, m)) if ps else (lambda n, m: np.asarray(m, float))
        ratio = r.ratio if r is not None else (self.current.ratio if self.current else 1.0)
        namen = {}
        if r is not None and len(r.punkte_n):
            self.c_punkte.setData(r.punkte_n * r.ratio, y(r.punkte_n, r.punkte_m))
            namen[self.c_punkte] = "Messpunkte (Klassenmittel)"
        else:
            self.c_punkte.setData([], [])
        top = r.n_von if r is not None and r.n_von > 0 else max(k.bezug() if k is not None else 0.0,
                                                                 self.current.n1 if self.current else 0.0, 100.0)
        g = np.linspace(0.0, max(top, 1.0), 120)
        auf = aufteilen(r, k)
        if r is not None and r.ok:
            self.c_fit.setData(g * r.ratio, y(g, r.mv(g)))
            namen[self.c_fit] = ("Fit Rolle (neu)" if r.rolle_allein else "gesamt (Fit)" if auf is not None
                                 else "Fit")
        else:
            self.c_fit.setData([], [])
        if k is not None and (auf is not None or r is None or r.rolle_allein):
            self.c_rolle.setData(g * ratio, y(g, k.mv(g)))
            namen[self.c_rolle] = ("bisherige Rollen-Kalibrierung" if r is not None and r.rolle_allein
                                   else "Rolle (Kalibrierung)")
        else:
            self.c_rolle.setData([], [])
        if auf is not None:
            self.c_antrieb.setData(g * ratio, y(g, auf.mv_antrieb(g)))
            namen[self.c_antrieb] = "Reifen/Kette/Getriebe" + (" (unter 0: Hinweis)" if auf.negativ_bereich() else "")
        else:
            self.c_antrieb.setData([], [])
        p = self.current
        if p is not None and (p.m0 or p.m1 or p.m2):
            self.c_aktuell.setData(g * ratio, y(g, physics.loss_torque(g, p.m0, p.m1, p.n1, p.m2)))
            namen[self.c_aktuell] = "aktuell gültig (M0/M1)"
        else:
            self.c_aktuell.setData([], [])
        self._kurven_thema("")
        if namen != self.namen:
            self.legende.clear()
            for c in (self.c_punkte, self.c_fit, self.c_rolle, self.c_antrieb, self.c_aktuell):
                if c in namen:
                    self.legende.addItem(c, namen[c])
            self.namen = namen


class KalibPage(QtWidgets.QScrollArea):
    uebernehmen = QtCore.Signal(object, object)        # AusrollErgebnis, Lauf-ID (oder None): Verluste gesamt
    kalib_speichern = QtCore.Signal(object)            # AusrollErgebnis (Rad abgehoben) als Rollen-Kalibrierung
    lauf_auswerten = QtCore.Signal()                   # markierten Lauf auswerten
    assistent = QtCore.Signal()                        # Assistent Rollen-Kalibrierung (Rad abgehoben) oeffnen

    def __init__(self, settings: dict, save: Callable[[], None]):
        super().__init__()
        self.settings = settings
        self.save = save
        self.cfg = settings.setdefault("ausrollen", {})
        self.kalib: Optional[RollenKalibrierung] = RollenKalibrierung.aus_dict(settings.get(KALIB_KEY))
        self.result: Optional[AusrollErgebnis] = None
        self.result_rid = None
        self.n_pmax: Optional[float] = None
        self.current: Optional[physics.DynoParams] = None
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        lay = QtWidgets.QVBoxLayout(inner)

        # ---- Rollen-Kalibrierung (einmalig, Assistent)
        g = QtWidgets.QGroupBox(KALIB_TITEL)
        gl = QtWidgets.QVBoxLayout(g)
        hb = QtWidgets.QHBoxLayout()
        self.kalib_label = QtWidgets.QLabel("")
        self.kalib_label.setWordWrap(True)
        self.kalib_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.kalib_label.setStyleSheet("font-weight: 600;")
        hb.addWidget(self.kalib_label, 1)
        self.assist_btn = QtWidgets.QPushButton(KALIB_KNOPF)
        self.assist_btn.setToolTip("Schritt für Schritt: Rolle beschleunigen, Rad von der Rolle nehmen, Messung starten. "
                                   "Misst nur die Verluste der Rolle selbst und speichert sie als Rollen-Kalibrierung.")
        self.assist_btn.setStyleSheet("font-weight: 600;")
        hb.addWidget(self.assist_btn, 0, QtCore.Qt.AlignTop)
        gl.addLayout(hb)
        self.kalib_hint = QtWidgets.QLabel(KALIB_HINWEIS)
        self.kalib_hint.setWordWrap(True)
        theme.rolle(self.kalib_hint, "neben")
        gl.addWidget(self.kalib_hint)
        lay.addWidget(g)

        # ---- Ausrollversuch nach dem Lauf ein/aus
        g = QtWidgets.QGroupBox("Ausrollversuch nach dem Lauf (Verluste gesamt: Rolle, Reifen, Kette, Getriebe)")
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
        self.c_rolle, self.c_antrieb = self.diagramm.c_rolle, self.diagramm.c_antrieb
        top.addWidget(self.diagramm, 1)
        rl.addLayout(top, 1)
        hb = QtWidgets.QHBoxLayout()
        self.lauf_btn = QtWidgets.QPushButton("Markierten Lauf auswerten")
        self.lauf_btn.setToolTip("Ausrollphase des in der Laufliste (Reiter Messen) markierten Laufs auswerten")
        self.apply_btn = QtWidgets.QPushButton("Übernehmen")
        self.apply_btn.setStyleSheet("font-weight: 700;")
        self.apply_btn.setEnabled(False)
        hb.addWidget(self.lauf_btn)
        hb.addStretch(1)
        hb.addWidget(self.apply_btn)
        rl.addLayout(hb)
        lay.addWidget(g, 1)

        # ---- aktuell gueltig
        g = QtWidgets.QGroupBox("Aktuell gültige Verluste (links: Werte für die Rechnung)")
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
        self._apply_knopf()
        self._kalib_zeigen()
        if self.cfg.get("letzte"):
            try:
                self.show_result(AusrollErgebnis.aus_dict(self.cfg["letzte"]), self.cfg.get("letzte_text", ""),
                                 None, store=False, n_pmax=self.cfg.get("letzte_n_pmax"))
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

    # ------------------------------------------------------------------ Rollen-Kalibrierung
    def set_kalibrierung(self, k: Optional[RollenKalibrierung]):
        """Neue Rollen-Kalibrierung anzeigen (gespeichert hat das Hauptfenster); Aufteilung neu."""
        self.kalib = k if k is not None and k.gueltig else None
        self._kalib_zeigen()
        if self.result is not None:
            self._ergebnis_texte()
        if self.current is not None:
            self.set_current(self.current)
        else:
            self._draw()

    def _kalib_zeigen(self):
        ratio = self.current.ratio if self.current is not None else None
        self.kalib_label.setText(kalib_text(self.kalib, ratio))
        self.kalib_label.setToolTip(
            (self.kalib.quelle() + (f"\nRohdaten: {self.kalib.rohdaten}" if self.kalib.rohdaten else "")
             + f"\nM0 {_komma(self.kalib.m0)} Nm, M1 {_komma(self.kalib.m1)} Nm bei n1 = {self.kalib.n1:.0f} 1/min "
             f"Rolle, m2 {_komma(self.kalib.m2, 3)}") if self.kalib is not None else
            "Noch keine Rollen-Kalibrierung: Der Ausrollversuch nach dem Lauf zeigt dann nur die Verluste gesamt.")
        theme.stufe(self.kalib_label, theme.GUT if self.kalib is not None else theme.WARNUNG)

    # ------------------------------------------------------------------ Ergebnis
    def show_result(self, res: AusrollErgebnis, text: str = "", rid=None, store: bool = True,
                    n_pmax: Optional[float] = None):
        """Ergebnis anzeigen (text: woher, z.B. Lauf und Fahrzeug; n_pmax: Motordrehzahl bei Pmax des Laufs fuer die
        Aufteilung). Uebernommen bzw. als Kalibrierung gespeichert wird erst mit dem Knopf."""
        self.result, self.result_rid = res, rid
        self.n_pmax = float(n_pmax) if n_pmax else None
        self.source_label.setText(text or "Ausrollversuch")
        self._ergebnis_texte()
        if store:
            self.cfg["letzte"] = res.als_dict(punkte=True)
            self.cfg["letzte_text"] = text
            self.cfg["letzte_n_pmax"] = self.n_pmax
            self.save()

    def aufteilung(self) -> Optional[Aufteilung]:
        """Aufteilung des angezeigten Ergebnisses mit der Rollen-Kalibrierung (None = keine)."""
        return aufteilen(self.result, self.kalib)

    def _ergebnis_texte(self):
        res = self.result
        if res is None:
            return
        if res.ok:
            auf = self.aufteilung()
            hinweise = list(res.hinweise) + ([ROLLE_ALLEIN] if res.rolle_allein else []) \
                + ([auf.hinweis()] if auf is not None and auf.hinweis() else [])
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
        self._apply_knopf()
        self.apply_btn.setEnabled(res.ok)
        self._draw()

    def _values_html(self, r: AusrollErgebnis) -> str:
        return werte_html(r, self.kalib, self.n_pmax)

    def _apply_knopf(self):
        rolle = self.result is not None and self.result.rolle_allein
        self.apply_btn.setText("Als Rollen-Kalibrierung speichern" if rolle else "Übernehmen")
        self.apply_btn.setToolTip(
            "Rad abgehoben: als Rollen-Kalibrierung speichern (nicht in M0/M1) – damit teilt PyST die Verluste des "
            "Ausrollversuchs nach dem Lauf auf" if rolle else
            "Verluste gesamt (M0, M1, n1, m2) links in „Verluste (Werte für die Rechnung)“ eintragen – gilt für neue "
            "Läufe und den ausgewerteten Lauf: Motor = Rad + Verluste")

    def set_current(self, p: physics.DynoParams):
        """Aktuell gueltige Verluste anzeigen (nach Start, Uebernehmen und Aendern in den Einstellungen)."""
        self.current = p
        if not (p.m0 or p.m1 or p.m2):
            text = "Keine Verluste eingetragen (M0 = M1 = 0): Ergebnis = Radleistung."
        else:
            quelle = p.verlust_quelle or "von Hand eingetragen"
            text = (f"M0 {_komma(p.m0)} Nm, M1 {_komma(p.m1)} Nm bei n1 = {p.n1:.0f} 1/min Rolle"
                    + (f", m2 {_komma(p.m2, 3)} Nm/(1000/min)²" if p.m2 else "")
                    + f"   –   Quelle: {quelle}.   Ergebnis = Motorleistung (Radleistung + Verluste).")
        if self.kalib is not None:
            text += ("   Mit Rollen-Kalibrierung zusätzlich: Reifen an Rolle (Rad + Verlust der Rolle)"
                     + (" und Antriebsverlust (Motor − Reifen an Rolle)." if (p.m0 or p.m1 or p.m2) else "."))
        self.current_label.setText(text)
        self._kalib_zeigen()
        self._draw()

    def _draw(self):
        self.diagramm.zeige(self.result, self.current, self.kalib)

    def _apply(self):
        if self.result is None or not self.result.ok:
            return
        if self.result.rolle_allein:
            self.kalib_speichern.emit(self.result)
        else:
            self.uebernehmen.emit(self.result, self.result_rid)
