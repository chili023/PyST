"""
Dialog „Mittelwert bilden“ der Auswertung (Entwurf entwurf_auswertung_mittelwert.png, NOTIZEN Runde 3).

Quelllaeufe (mindestens 2, abwaehlbar) mit Setup und Schnellwerten – abweichende Werte gelb und eine gelbe Warnung
(„Die Setups der Quellläufe unterscheiden sich: Zündzeitpunkt 17 (16:18) / 18 (16:19, 16:23). Trotzdem mitteln?“),
Drehzahlraster (Standard 50 1/min), oben weglassen (300 1/min), Streuband min–max / ± σ / kein Band, Vorschau
(Quelllaeufe duenn, Mittelwert dick, Band halbtransparent) und „Speichern als Lauf“ (Name, Fahrzeug, Setup, Notiz,
„im Messfenster als Referenz anbieten“). Gerechnet wird mit mittelwert.mitteln, gespeichert mit
mittelwert.speichern.

Die Kanaele kommen aus der Auswertung (channels.run_channels, Namen wie „Leistung“): die bekannten bekommen die
Schluessel von mittelwert.kurven_aus_ergebnis (n, ps, nm, lam, afr, egt1, egt2, ps_rad), damit Messen den Mittelwert
als Referenz lesen kann; alle weiteren Kurven-Kanaele (Übersetzung, ECU …) werden unter ihrem Namen mitgemittelt.
Einheiten und die Wahl des Bands stehen zusaetzlich in mittelwert.json („einheiten“, „band“).
"""
import json
import os
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import db as _db
from . import mittelwert as mw
from . import theme
from .channels import Channel
from .kacheln import farb_icon, zahl

# Kanalname der Auswertung -> Schluessel im Mittelwert (wie mittelwert.kurven_aus_ergebnis)
STANDARD = OrderedDict([("Drehzahl Motor", "n"), ("Leistung", "ps"), ("Drehmoment", "nm"), ("Lambda", "lam"),
                        ("AFR", "afr"), ("EGT 1", "egt1"), ("EGT 2", "egt2"), ("Leistung Rad", "ps_rad")])
RUECK = {v: k for k, v in STANDARD.items()}
RUECK["nm_rad"] = "Drehmoment Rad"
EINHEIT = {"n": "1/min", "ps": "PS", "nm": "Nm", "lam": "λ", "afr": "AFR", "egt1": "°C", "egt2": "°C",
           "ps_rad": "PS", "nm_rad": "Nm"}
# nicht gemittelt: Zeit, Fehler-/Diagnose-/Systemkanaele, Zange, konstante bzw. Rohwerte
OHNE_GRUPPEN = {"Zeit", "Fehler", "Diagnose", "System", "Zange"}
OHNE_KANAELE = {"Drehzahl Motor (roh)", "Übersetzung eingestellt"}
BAND_ARTEN = (("minmax", "min – max"), ("sigma", "± σ"), ("", "kein Band"))
BAND_STANDARD = "minmax"


# ============================================================================================== reine Funktionen
def kurven_aus_kanaelen(ch: Dict[str, Channel], seg=None) -> Tuple[Dict[str, np.ndarray], Dict[str, str]]:
    """Kanaele eines Laufs der Auswertung -> ({Schluessel: Werte} fuer mittelwert.mitteln, {Schluessel: Einheit}).
    seg: Kurvenbereich (slice) oder None. EGT <= 0 gilt als fehlend (wie kurven_aus_ergebnis)."""
    if "Drehzahl Motor" not in ch:
        raise ValueError("Lauf ohne Motordrehzahl (Dauermessung?) – lässt sich nicht mitteln.")
    sl = seg if seg is not None else slice(None)
    kurven, einheiten = {}, {}
    n_len = len(np.asarray(ch["Drehzahl Motor"].values)[sl])
    for name, c in ch.items():
        if c.group in OHNE_GRUPPEN or name in OHNE_KANAELE or name == "Zeit":
            continue
        v = np.asarray(c.values, float)[sl]
        if len(v) != n_len:
            continue
        key = STANDARD.get(name, name)
        if key.startswith("egt"):
            with np.errstate(invalid="ignore"):
                v = np.where(v > 0, v, np.nan)
        kurven[key] = v
        einheiten[key] = c.unit
    return kurven, einheiten


def kanaele_aus_mittelwert(res: mw.MittelwertErgebnis, einheiten: Optional[Dict[str, str]] = None
                           ) -> Tuple["OrderedDict[str, Channel]", Dict[str, Dict[str, Tuple[np.ndarray, np.ndarray]]]]:
    """Mittelwert -> (Kanaele wie in der Auswertung, Baender {Kanal: {"minmax": (unten, oben), "sigma": …}}).
    Die Drehzahl ist das Raster; eine Zeitachse gibt es nicht."""
    einheiten = einheiten or {}
    ch: "OrderedDict[str, Channel]" = OrderedDict()
    ch["Drehzahl Motor"] = Channel(np.asarray(res.n, float), "1/min", "Messung")
    baender = {}
    for key in res.kanaele:
        if key == "n":
            continue
        if key.startswith(mw.ECU_PREFIX):
            name = f"{key[len(mw.ECU_PREFIX):]} (ECU)"
        else:
            name = RUECK.get(key, key)
        ch[name] = Channel(np.asarray(res.werte[key], float), einheiten.get(key, EINHEIT.get(key, "")), "Mittelwert")
        baender[name] = {art: res.band(key, art) for art in ("minmax", "sigma")}
    return ch, baender


def _feldname(f: str) -> str:
    return _db.field_label(f).split(" [")[0]


def setup_unterschiede(setups: Sequence[Optional[dict]], bezeichnungen: Sequence[str],
                       felder: Optional[List[str]] = None, max_felder: int = 3) -> str:
    """Warnung des Dialogs, "" ohne Unterschied: „Die Setups der Quellläufe unterscheiden sich: Zündzeitpunkt
    17 (16:18) / 18 (16:19, 16:23). Trotzdem mitteln?“"""
    ab = mw.setup_abweichungen(list(setups), felder)
    if not ab:
        return ""
    teile = []
    for f, werte in list(ab.items())[:max_felder]:
        gruppen: "OrderedDict[str, List[str]]" = OrderedDict()
        for w, b in zip(werte, bezeichnungen):
            if w == "?":
                continue
            gruppen.setdefault(w or "–", []).append(b)
        teile.append(f"{_feldname(f)} " + " / ".join(f"{w} ({', '.join(bs)})" for w, bs in gruppen.items()))
    rest = len(ab) - max_felder
    return ("Die Setups der Quellläufe unterscheiden sich: " + "; ".join(teile)
            + (f" (und {rest} weitere)" if rest > 0 else "") + ". Trotzdem mitteln?")


def mehrheit(werte: Sequence) -> str:
    """Haeufigster nicht leere Wert (bei Gleichstand der zuerst genannte), "" wenn alle leer."""
    ws = [_db.wert_text(w) for w in werte]
    ws = [w for w in ws if w]
    if not ws:
        return ""
    zahl_ = Counter(ws)
    best = max(zahl_.values())
    return next(w for w in ws if zahl_[w] == best)


def abweichende(werte: Sequence) -> List[bool]:
    """Je Wert: weicht von der Mehrheit ab (leere zaehlen nicht) -> gelb markieren."""
    m = mehrheit(werte)
    return [bool(_db.wert_text(w)) and _db.wert_text(w) != m for w in werte]


def namensvorschlag(anzahl: int, setup_name: str = "") -> str:
    """„Ø 3 Läufe · Serie HD 135“"""
    s = (setup_name or "").replace(",", "").strip()
    return f"Ø {anzahl} Läufe" + (f" · {s}" if s else "")


def zusatz_lesen(ordner: str) -> dict:
    """Zusatzangaben der Auswertung aus mittelwert.json: {"einheiten": {...}, "band": "minmax"|"sigma"|""}."""
    try:
        with open(os.path.join(ordner, mw.JSON_NAME), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: meta[k] for k in ("einheiten", "band") if k in meta}


def zusatz_schreiben(ordner: str, **werte) -> bool:
    """Felder in mittelwert.json ergaenzen bzw. ersetzen (z.B. band="sigma")."""
    path = os.path.join(ordner, mw.JSON_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta.update(werte)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
    except (OSError, ValueError):
        return False
    return True


def band_text(art: str) -> str:
    return dict(BAND_ARTEN).get(art or "", "kein Band")


# ============================================================================================== Dialog
class MittelwertDialog(QtWidgets.QDialog):
    """quellen: [(RunRow, Kanaele der Auswertung, Kurvenbereich, Basisfarbe)]; felder: Schnellwert-Plaetze.
    Nach „Mittelwert speichern“: self.rid = neue Lauf-ID."""

    def __init__(self, db, quellen: Sequence[tuple], felder: Sequence[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mittelwert bilden")
        self.resize(900, 840)
        self.db = db
        self.felder = [f for f in felder if f]
        self.rid: Optional[int] = None
        self.ergebnis: Optional[mw.MittelwertErgebnis] = None
        self.quellen = []                                  # (row, kurven, einheiten, farbe, setup)
        self.fehler: List[str] = []
        for row, ch, seg, farbe in quellen:
            try:
                kurven, einh = kurven_aus_kanaelen(ch, seg)
            except ValueError as exc:
                self.fehler.append(f"{_kurz(row)}: {exc}")
                continue
            self.quellen.append((row, kurven, einh, farbe, db.lauf_setup(row)))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(8)
        kopf = QtWidgets.QLabel("Mittelwert bilden")
        kopf.setStyleSheet("font-size: 18px; font-weight: 700;")
        theme.rolle(kopf, "titel")
        lay.addWidget(kopf)

        # ---- Quelllaeufe
        g = QtWidgets.QGroupBox(f"Quellläufe (mindestens {mw.MIN_LAEUFE})")
        gl = QtWidgets.QVBoxLayout(g)
        self.baum = QtWidgets.QTreeWidget()
        self.baum.setRootIsDecorated(False)
        from .laufleiste import kurz_titel
        self.baum.setHeaderLabels(["Lauf", "Setup"] + [kurz_titel(f) for f in self.felder] + ["Pmax", "bei"])
        self.baum.setFixedHeight(28 + 22 * max(2, min(6, len(self.quellen))))
        setups = {s["id"]: s["name"] or "(ohne Namen)" for v in db.vehicles() for s in db.setups(v["id"])}
        werte = [[q[4].get(f) for q in self.quellen] for f in self.felder]
        anders = [abweichende(w) for w in werte]
        T = theme.tokens()
        for i, (row, _k, _e, farbe, setup) in enumerate(self.quellen):
            it = QtWidgets.QTreeWidgetItem([_kurz(row, lang=True), setups.get(row.setup_id, "")]
                                           + [_db.wert_text(setup.get(f)) for f in self.felder]
                                           + [zahl(row.p_max, 1), zahl(row.n_pmax)])
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(0, QtCore.Qt.Checked)
            it.setIcon(0, farb_icon(farbe))
            it.setData(0, QtCore.Qt.UserRole, row.id)
            for k in range(len(self.felder)):
                if anders[k][i]:
                    it.setBackground(2 + k, QtGui.QBrush(QtGui.QColor(T["changed_bg"])))
                    it.setForeground(2 + k, QtGui.QBrush(QtGui.QColor(T["changed_text"])))
            self.baum.addTopLevelItem(it)
        for c, wd in enumerate([170, 160] + [90] * len(self.felder) + [70, 70]):
            self.baum.setColumnWidth(c, wd)
        gl.addWidget(self.baum)
        self.warnung = theme.rolle(QtWidgets.QLabel(""), "banner_warnung")
        self.warnung.setWordWrap(True)
        gl.addWidget(self.warnung)
        lay.addWidget(g)

        # ---- Mittelung
        g = QtWidgets.QGroupBox("Mittelung")
        fl = QtWidgets.QFormLayout(g)
        r1 = QtWidgets.QHBoxLayout()
        self.raster = QtWidgets.QSpinBox()
        self.raster.setRange(10, 500)
        self.raster.setSingleStep(10)
        self.raster.setValue(int(mw.RASTER))
        self.raster.setSuffix(" 1/min")
        r1.addWidget(self.raster)
        r1.addWidget(QtWidgets.QLabel("oben weglassen"))
        self.oben = QtWidgets.QSpinBox()
        self.oben.setRange(0, 2000)
        self.oben.setSingleStep(50)
        self.oben.setValue(int(mw.OBEN_WEG))
        self.oben.setSuffix(" 1/min")
        self.oben.setToolTip("Der Gas-weg-Knick oben soll nicht in den Mittelwert geraten")
        r1.addWidget(self.oben)
        r1.addStretch(1)
        fl.addRow("Drehzahlraster", r1)
        self.bereich = theme.rolle(QtWidgets.QLabel(""), "neben")
        self.bereich.setWordWrap(True)
        fl.addRow("Bereich", self.bereich)
        r2 = QtWidgets.QHBoxLayout()
        self.band_knoepfe: Dict[str, QtWidgets.QRadioButton] = {}
        for art, text in BAND_ARTEN:
            rb = QtWidgets.QRadioButton(text)
            rb.setChecked(art == BAND_STANDARD)
            rb.toggled.connect(lambda _on: self._vorschau())
            self.band_knoepfe[art] = rb
            r2.addWidget(rb)
        r2.addStretch(1)
        fl.addRow("Streuband", r2)
        lay.addWidget(g)

        # ---- Vorschau
        lay.addWidget(theme.rolle(QtWidgets.QLabel("Vorschau: Quellläufe dünn, Mittelwert dick, Band halbtransparent"),
                                  "neben"))
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(240)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        theme.diagramm(self.plot, lambda _t: self._vorschau())
        lay.addWidget(self.plot, 1)

        # ---- Speichern
        g = QtWidgets.QGroupBox("Speichern als Lauf")
        fl = QtWidgets.QFormLayout(g)
        self.name = QtWidgets.QLineEdit()
        fl.addRow("Name", self.name)
        r3 = QtWidgets.QHBoxLayout()
        self.fahrzeug = QtWidgets.QComboBox()
        self.fahrzeug.addItem("ohne Fahrzeug", None)
        for v in db.vehicles():
            self.fahrzeug.addItem(v["name"], v["id"])
        self.setup = QtWidgets.QComboBox()
        self.setup.setMinimumWidth(180)
        r3.addWidget(self.fahrzeug)
        r3.addWidget(QtWidgets.QLabel("Setup"))
        r3.addWidget(self.setup)
        r3.addStretch(1)
        fl.addRow("Fahrzeug", r3)
        self.notiz = QtWidgets.QLineEdit()
        fl.addRow("Notiz", self.notiz)
        self.referenz = QtWidgets.QCheckBox("im Messfenster als Referenz anbieten (Messen › Läufe „＋“)")
        fl.addRow("", self.referenz)
        lay.addWidget(g)
        self.fehler_label = theme.rolle(QtWidgets.QLabel(""), "banner_fehler")
        self.fehler_label.setWordWrap(True)
        lay.addWidget(self.fehler_label)
        bb = QtWidgets.QHBoxLayout()
        bb.addStretch(1)
        self.ok = theme.rolle(QtWidgets.QPushButton("Mittelwert speichern"), "primaer")
        self.ok.setDefault(True)
        abbruch = QtWidgets.QPushButton("Abbrechen")
        bb.addWidget(self.ok)
        bb.addWidget(abbruch)
        lay.addLayout(bb)
        self.ok.clicked.connect(self._speichern)
        abbruch.clicked.connect(self.reject)

        # Vorgaben aus den Quellen
        vid = Counter(q[0].vehicle_id for q in self.quellen).most_common(1)
        vid = vid[0][0] if vid else None
        self.fahrzeug.setCurrentIndex(max(0, self.fahrzeug.findData(vid)))
        self._setups_fuellen()
        sid = Counter(q[0].setup_id for q in self.quellen if q[0].vehicle_id == vid).most_common(1)
        if sid:
            self.setup.setCurrentIndex(max(0, self.setup.findData(sid[0][0])))
        self.name.setText(namensvorschlag(len(self.quellen), self.setup.currentText() if self.setup.currentData()
                                          else ""))
        self.notiz.setText("Quellen: " + ", ".join(_kurz(q[0]) for q in self.quellen))
        self._name_auto = self.name.text()
        self.fahrzeug.currentIndexChanged.connect(lambda _i: self._setups_fuellen())
        self.setup.currentIndexChanged.connect(lambda _i: self._name_vorschlag())
        self.baum.itemChanged.connect(lambda _it, _c: self._rechnen())
        self.raster.valueChanged.connect(lambda _v: self._rechnen())
        self.oben.valueChanged.connect(lambda _v: self._rechnen())
        self._rechnen()

    # ------------------------------------------------------------ Auswahl
    def gewaehlte(self) -> List[int]:
        """Indizes der angehakten Quellen."""
        return [i for i in range(self.baum.topLevelItemCount())
                if self.baum.topLevelItem(i).checkState(0) == QtCore.Qt.Checked]

    def band_art(self) -> str:
        return next((a for a, rb in self.band_knoepfe.items() if rb.isChecked()), BAND_STANDARD)

    def _setups_fuellen(self):
        vid = self.fahrzeug.currentData()
        self.setup.blockSignals(True)
        self.setup.clear()
        self.setup.addItem("— ohne Setup —", None)
        if vid:
            for s in self.db.setups(vid):
                self.setup.addItem(s["name"] or "(ohne Namen)", s["id"])
        self.setup.blockSignals(False)
        self.setup.setEnabled(bool(vid))
        self._name_vorschlag()

    def _name_vorschlag(self):
        """Namen nur nachfuehren, solange der Anwender ihn nicht selbst geaendert hat."""
        if not hasattr(self, "_name_auto") or self.name.text() != self._name_auto:
            return
        self.name.setText(namensvorschlag(len(self.gewaehlte()), self.setup.currentText() if self.setup.currentData()
                                          else ""))
        self._name_auto = self.name.text()

    # ------------------------------------------------------------ Rechnen
    def _rechnen(self):
        idx = self.gewaehlte()
        q = [self.quellen[i] for i in idx]
        text = setup_unterschiede([x[4] for x in q], [_kurz(x[0]) for x in q]) if len(q) >= 2 else ""
        self.warnung.setText("⚠  " + text if text else "")
        self.warnung.setVisible(bool(text))
        fehler = list(self.fehler)
        self.ergebnis = None
        if len(q) < mw.MIN_LAEUFE:
            fehler.append(f"Für einen Mittelwert braucht es mindestens {mw.MIN_LAEUFE} Läufe.")
        else:
            try:
                self.ergebnis = mw.mitteln([x[1] for x in q], raster=float(self.raster.value()),
                                           oben_weg=float(self.oben.value()), quellen=[x[0].id for x in q],
                                           setups=[x[4] for x in q])
            except ValueError as exc:
                fehler.append(str(exc))
        r = self.ergebnis
        if r is not None:
            self.bereich.setText(f"gemeinsam {zahl(r.n_lo)} – {zahl(r.n_hi)} 1/min (nur wo alle Läufe Daten haben, "
                                 f"steigender Teil), gemittelt {zahl(r.n[0])} – {zahl(r.n[-1])} 1/min")
            if r.fehlende_kanaele:
                self.bereich.setToolTip("Nicht in allen Läufen vorhanden, nicht gemittelt: "
                                        + ", ".join(RUECK.get(k, k) for k in r.fehlende_kanaele))
        else:
            self.bereich.setText("")
        self.fehler_label.setText("\n".join(fehler))
        self.fehler_label.setVisible(bool(fehler) and r is None)
        self.ok.setEnabled(r is not None)
        self._name_vorschlag()
        self._vorschau()

    def _vorschau(self):
        if not hasattr(self, "plot"):
            return
        pi = self.plot.getPlotItem()
        pi.clear()
        T = theme.tokens()
        pi.setLabel("left", "Leistung [PS]", color=T["p_color"])
        pi.setLabel("bottom", "Motordrehzahl [1/min]", color=T["plot_axis"])
        for i in self.gewaehlte():
            row, k, _e, farbe, _s = self.quellen[i]
            if "ps" in k:
                pi.plot(k["n"], k["ps"], pen=pg.mkPen(theme.laufarbe(farbe), width=1.3), connect="finite")
        r = self.ergebnis
        if r is None or "ps" not in r.werte:
            return
        avg = theme.laufarbe("@avg_color")
        band = r.band("ps", self.band_art())
        if band is not None:
            fuell_band(pi, r.n, band[0], band[1], avg, 80)
        pi.plot(r.n, r.werte["ps"], pen=pg.mkPen(avg, width=3.6), connect="finite")
        ns = [np.nanmin(self.quellen[i][1]["n"]) for i in self.gewaehlte()] + \
            [np.nanmax(self.quellen[i][1]["n"]) for i in self.gewaehlte()]
        if ns:
            pi.setXRange(min(ns), max(ns), padding=0.02)

    # ------------------------------------------------------------ Speichern
    def _speichern(self):
        r = self.ergebnis
        if r is None:
            return
        name = self.name.text().strip() or namensvorschlag(r.anzahl)
        q = [self.quellen[i] for i in self.gewaehlte()]
        werte = [{"feld": f, "wert": mehrheit([x[4].get(f) for x in q]), "vorher": ""} for f in self.felder]
        einheiten = {}
        for x in q:
            for k, u in x[2].items():
                einheiten.setdefault(k, u)
        self.rid = mw.speichern(self.db, r, name, vehicle_id=self.fahrzeug.currentData(),
                                setup_id=self.setup.currentData(), notiz=self.notiz.text().strip(),
                                setup_werte=werte, sichtbar=self.referenz.isChecked())
        row = self.db.run(self.rid)
        if row is not None:
            zusatz_schreiben(row.ordner, einheiten={k: v for k, v in einheiten.items() if k in r.werte or k == "n"},
                             band=self.band_art())
        self.accept()


def fuell_band(pi, x, unten, oben, farbe: str, alpha: int = 70):
    """Halbtransparentes Band zwischen unten und oben (nur wo beide endlich sind) in ein PlotItem."""
    x = np.asarray(x, float)
    a, b = np.asarray(unten, float), np.asarray(oben, float)
    m = np.isfinite(x) & np.isfinite(a) & np.isfinite(b)
    if m.sum() < 2:
        return None
    c1 = pg.PlotCurveItem(x[m], a[m], pen=pg.mkPen(None))
    c2 = pg.PlotCurveItem(x[m], b[m], pen=pg.mkPen(None))
    col = QtGui.QColor(farbe)
    col.setAlpha(alpha)
    fb = pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(col))
    fb.setZValue(-5)
    pi.addItem(fb, ignoreBounds=True)
    return fb


def _kurz(row, lang: bool = False) -> str:
    from .laufleiste import datum_text, zeit_text
    return datum_text(row) if lang else zeit_text(row)
