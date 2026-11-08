"""
Laufleiste der Auswertung („ANGEZEIGTE LÄUFE“) direkt ueber den Diagrammen (Entwurf v3, NOTIZEN Runde 2/3).

Je Lauf eine Zeile: mark. (fuer „Mittelwert bilden …“), anzeigen + Farbfeld (Ø beim Mittelwert), Lauf, Setup,
die Schnellwert-Spalten des Fahrzeugs (Standard Hauptduese, Clip, ZZP), Pmax @ n, Mmax, Notiz (direkt aenderbar),
„Setup …“ bzw. beim Mittelwert „Quellen ▾“ und ✕. Ein Schnellwert, der vom Lauf darueber abweicht, steht gelb
mit Pfeil und altem Wert („140 ↑ 135“). Mittelwerte stehen oben als Bezug, darunter die Laeufe alt -> neu.
Ab SCHWELLE_CHIPS Laeufen klappt die Leiste zu schmalen Chips (Farbe + Zeit) zusammen, ein Klick oeffnet sie.

Reine Funktionen (ohne Qt, getestet): datum_text, zeit_text, reihenfolge, schnellwert_zellen, kurz_titel,
chips_modus. Das Widget LaufLeiste zeigt nur an und meldet Bedienung per Signal; die Auswertung (viewer.py)
schreibt in die Datenbank.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from PySide6 import QtCore, QtWidgets

from . import db as _db
from . import theme
from .kacheln import farb_icon

SCHWELLE_CHIPS = 5            # ab so vielen Laeufen zugeklappt (Chips)
SICHTBARE_ZEILEN = 5          # offen: hoechstens so viele Zeilen, dann Rollbalken
ZEILE_PX = 38

# kurze Spaltentitel der Schnellwerte (sonst db.field_label)
KURZ = {"clip": "Clip", "zzp": "ZZP [° v. OT]", "nadelduese": "Nadeldüse", "leerlaufduese": "Leerlaufdüse",
        "gemischschraube": "Gemischschr.", "sekundaer": "Sekundär", "primaer": "Primär"}


# ============================================================================================== reine Funktionen
def datum_text(row) -> str:
    """"2026-05-26T16:24:55" -> "26.05.26 16:24" (wie die Laufliste in Messen)."""
    d = (row.datum or "").replace("T", " ")[:16]
    if len(d) == 16 and d[4] == "-":
        return f"{d[8:10]}.{d[5:7]}.{d[2:4]} {d[11:]}"
    return d


def zeit_text(row) -> str:
    """Uhrzeit "16:24" (Bezeichnung in Kacheln und Chips)."""
    d = (row.datum or "").replace("T", " ")
    return d[11:16] if len(d) >= 16 else d


def reihenfolge(rows: Sequence) -> List:
    """Anzeigereihenfolge: Mittelwerte oben (als Bezug), darunter die Laeufe alt -> neu (Datum aufsteigend)."""
    mw = sorted((r for r in rows if r.ist_mittelwert), key=lambda r: r.datum or "")
    rest = sorted((r for r in rows if not r.ist_mittelwert), key=lambda r: r.datum or "")
    return mw + rest


def kurz_titel(feld: str) -> str:
    return KURZ.get(feld, _db.field_label(feld)) if feld else ""


@dataclass
class Zelle:
    """Ein Schnellwert in der Laufleiste. alt/pfeil gesetzt = anders als im Lauf darueber (gelb)."""
    wert: str
    alt: str = ""
    pfeil: str = ""

    @property
    def geaendert(self) -> bool:
        return bool(self.pfeil)

    @property
    def text(self) -> str:
        if self.pfeil:
            return f"{self.wert}  {self.pfeil} {self.alt}"
        return self.wert or "–"


def schnellwert_zellen(werte: Sequence[Dict[str, object]], felder: Sequence[str]) -> List[List[Zelle]]:
    """Zellen der Schnellwert-Spalten je Zeile. werte: je Zeile (in Anzeigereihenfolge) {feld: wert}, z.B. aus
    db.lauf_setup. Verglichen wird mit der Zeile darueber (db.schnellwert_abweichungen); ein leerer Wert auf
    einer der beiden Seiten zaehlt nicht als Abweichung. Leerer Platz in felder -> leere Zelle."""
    out: List[List[Zelle]] = []
    vorher: Optional[Dict[str, str]] = None
    for w in werte:
        jetzt = {f: _db.wert_text(w.get(f)) for f in felder if f}
        ab = _db.schnellwert_abweichungen(vorher, jetzt) if vorher is not None else {}
        zeile = []
        for f in felder:
            if not f:
                zeile.append(Zelle(""))
                continue
            neu, alt = jetzt[f], ab.get(f, "")
            if f in ab and neu and alt:
                zeile.append(Zelle(neu, alt, _db.schnellwert_pfeil(neu, alt)))
            else:
                zeile.append(Zelle(neu))
        out.append(zeile)
        vorher = jetzt
    return out


def chips_modus(anzahl: int, offen_gewaehlt: bool) -> bool:
    """Zugeklappt (Chips) ab SCHWELLE_CHIPS Laeufen, solange der Anwender die Leiste nicht geoeffnet hat."""
    return anzahl >= SCHWELLE_CHIPS and not offen_gewaehlt


# ============================================================================================== Widget
@dataclass
class Eintrag:
    """Eine Zeile der Laufleiste (vom Aufrufer aufbereitet)."""
    rid: int
    farbe: str                       # Basisfarbe (theme.laufarbe macht sie themengerecht), Mittelwert "@avg_color"
    mittelwert: bool
    lauf: str                        # "26.05.26 16:24" bzw. Name des Mittelwerts
    kurz: str                        # "16:24" bzw. "Ø Ref." (Chips)
    setup: str = ""
    zellen: List[Zelle] = field(default_factory=list)
    pmax: str = ""                   # HTML, z.B. "<b>31,6</b> PS @ 10.349"
    mmax: str = ""
    notiz: str = ""
    sichtbar: bool = True
    markiert: bool = False
    tooltip: str = ""                # Lauf-Spalte (Name, Quelle …)
    quellen_tip: str = ""            # Mittelwert: Tooltip des Knopfs „Quellen ▾“


class LaufLeiste(QtWidgets.QWidget):
    """Kopf („ANGEZEIGTE LÄUFE“ · Fahrzeug · Legende, Auf-/Zuklappen, „Mittelwert bilden …“) und die Zeilen bzw.
    Chips. set_daten(eintraege, felder, info) baut neu auf; Bedienung kommt als Signal."""
    sichtbar_geaendert = QtCore.Signal(int, bool)
    markiert_geaendert = QtCore.Signal(int, bool)
    notiz_geaendert = QtCore.Signal(int, str)
    setup_gewaehlt = QtCore.Signal(int)
    quellen_gewaehlt = QtCore.Signal(int, object)      # (Lauf-ID, Knopf) -> Aufrufer zeigt das Menue
    entfernt = QtCore.Signal(int)
    mittelwert_gewaehlt = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.eintraege: List[Eintrag] = []
        self.felder: List[str] = []
        self.info = ""
        self.offen_gewaehlt = False                    # Anwender hat die Chips aufgeklappt
        self.zeilen: Dict[int, Dict[str, QtWidgets.QWidget]] = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        kopf = QtWidgets.QHBoxLayout()
        kopf.setSpacing(6)
        self.titel = theme.rolle(QtWidgets.QLabel("ANGEZEIGTE LÄUFE"), "abschnitt")
        self.info_label = theme.rolle(QtWidgets.QLabel(""), "neben")
        self.info_label.setWordWrap(True)                 # schmales Fenster: zweizeilig statt breiter
        kopf.addWidget(self.titel)
        kopf.addWidget(self.info_label, 1)
        self.klapp_btn = QtWidgets.QPushButton("")
        self.klapp_btn.clicked.connect(self._klappen)
        kopf.addWidget(self.klapp_btn)
        self.mittelwert_btn = theme.rolle(QtWidgets.QPushButton("Mittelwert bilden …"), "primaer")
        self.mittelwert_btn.clicked.connect(self.mittelwert_gewaehlt)
        kopf.addWidget(self.mittelwert_btn)
        lay.addLayout(kopf)

        self.rahmen = QtWidgets.QFrame()
        self.rahmen.setObjectName("laufleiste")
        rl = QtWidgets.QVBoxLayout(self.rahmen)
        rl.setContentsMargins(6, 3, 6, 4)
        rl.setSpacing(0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # schmales Fenster: waagerecht rollen (Notiz, Setup …, ✕ bleiben erreichbar), die Hoehe waechst um den Balken
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._scroll_h = 0
        self.scroll.installEventFilter(self)
        rl.addWidget(self.scroll)
        self.chips = QtWidgets.QWidget()
        self.chips_lay = QtWidgets.QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 2, 0, 2)
        self.chips_lay.setSpacing(6)
        rl.addWidget(self.chips)
        lay.addWidget(self.rahmen)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        theme.bei_wechsel(self, lambda _t: self._aufbauen(), sofort=False)
        self._aufbauen()

    # ------------------------------------------------------------ Daten
    def set_daten(self, eintraege: Sequence[Eintrag], felder: Sequence[str], info: str = ""):
        if list(eintraege) == self.eintraege and list(felder) == self.felder and info == self.info and self.zeilen:
            return                                     # nichts geaendert (z.B. nach einer Notiz): Fokus bleibt
        self.eintraege = list(eintraege)
        self.felder = list(felder)
        self.info = info
        self._aufbauen()

    def markierte(self) -> List[int]:
        return [e.rid for e in self.eintraege if e.markiert and not e.mittelwert]

    def ist_chips(self) -> bool:
        return chips_modus(len(self.eintraege), self.offen_gewaehlt)

    def set_offen(self, offen: bool):
        self.offen_gewaehlt = bool(offen)
        self._aufbauen()

    def _klappen(self):
        self.set_offen(self.ist_chips())

    # ------------------------------------------------------------ Aufbau
    def _stil(self):
        T = theme.tokens()
        self.rahmen.setStyleSheet(f"QFrame#laufleiste {{ background: {T['panel']}; border: 1px solid {T['border']};"
                                  " border-radius: 4px; } QLabel { background: transparent; }"
                                  " QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")

    def _aufbauen(self):
        self._stil()
        n_mark = len(self.markierte())
        self.mittelwert_btn.setText(f"Mittelwert bilden …  ({n_mark} markiert)")
        self.mittelwert_btn.setEnabled(n_mark >= 2)
        self.mittelwert_btn.setToolTip("Markierte Läufe (Spalte „mark.“) zu einem Mittelwert-Lauf zusammenfassen"
                                       if n_mark >= 2 else "Mindestens 2 Läufe markieren (Spalte „mark.“)")
        self.info_label.setText(self.info)
        chips = self.ist_chips()
        n = len(self.eintraege)
        self.klapp_btn.setVisible(n >= SCHWELLE_CHIPS)
        self.klapp_btn.setText(f"▾ alle {n} Läufe zeigen" if chips else "▴ zuklappen")
        self.rahmen.setVisible(n > 0)
        self.scroll.setVisible(not chips)
        self.chips.setVisible(chips)
        self.zeilen = {}
        if chips:
            self._chips_bauen()
            alt = self.scroll.takeWidget()
            if alt is not None:
                alt.deleteLater()
        else:
            self._tabelle_bauen()
        self.updateGeometry()

    def _chips_bauen(self):
        lay = self.chips_lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        T = theme.tokens()
        for e in self.eintraege:
            b = QtWidgets.QToolButton()
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.setIcon(farb_icon(e.farbe, e.mittelwert, 14))
            b.setText(e.kurz)
            b.setToolTip(f"{e.lauf}" + ("" if e.sichtbar else " (ausgeblendet)") + " – Klick öffnet die Liste")
            farbe = T["text"] if e.sichtbar else T["dim"]
            b.setStyleSheet(f"QToolButton {{ color: {farbe}; background: {T['field']}; border: 1px solid {T['border']};"
                            f" border-radius: 10px; padding: 1px 8px; font-size: 13px;"
                            f" {'font-weight: 700;' if e.mittelwert else ''} }}"
                            f"QToolButton:hover {{ border-color: {T['accent_border']}; }}")
            b.clicked.connect(lambda _c=False: self.set_offen(True))
            lay.addWidget(b)
        lay.addStretch(1)

    def _tabelle_bauen(self):
        T = theme.tokens()
        inner = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(inner)
        g.setContentsMargins(2, 2, 2, 2)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(3)
        nf = len(self.felder)
        c_setup, c_w0 = 3, 4
        c_p, c_m, c_notiz, c_knopf, c_x = c_w0 + nf, c_w0 + nf + 1, c_w0 + nf + 2, c_w0 + nf + 3, c_w0 + nf + 4
        kopf = ["mark.", "", "Lauf", "Setup"] + [kurz_titel(f) for f in self.felder] + \
            ["Pmax", "Mmax", "Notiz (direkt änderbar)", "", ""]
        for c, h in enumerate(kopf):
            lab = QtWidgets.QLabel(h)
            lab.setStyleSheet(f"color: {T['text2']}; font-size: 12px; font-weight: 700;")
            if c_w0 <= c < c_p:
                lab.setAlignment(QtCore.Qt.AlignCenter)
            g.addWidget(lab, 0, c)
        for r, e in enumerate(self.eintraege, 1):
            w: Dict[str, QtWidgets.QWidget] = {}
            mk = QtWidgets.QCheckBox()
            mk.setChecked(e.markiert and not e.mittelwert)
            mk.setEnabled(not e.mittelwert)
            mk.setToolTip("Ein Mittelwert lässt sich nicht erneut mitteln" if e.mittelwert
                          else "markieren (für „Mittelwert bilden …“)")
            mk.toggled.connect(lambda an, e=e: self._markiert(e, an))
            g.addWidget(mk, r, 0)
            w["mark"] = mk
            box = QtWidgets.QWidget()
            bl = QtWidgets.QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(6)
            an = QtWidgets.QCheckBox()
            an.setChecked(e.sichtbar)
            an.setToolTip("anzeigen")
            an.toggled.connect(lambda v, rid=e.rid: self.sichtbar_geaendert.emit(rid, v))
            bl.addWidget(an)
            bl.addWidget(self._farbfeld(e))
            g.addWidget(box, r, 1)
            w["anzeigen"] = an
            lauf = QtWidgets.QLabel(e.lauf)
            lauf.setStyleSheet(f"color: {T['text'] if e.sichtbar else T['dim']}; font-size: 13px; font-weight: 700;")
            lauf.setToolTip(e.tooltip)
            g.addWidget(lauf, r, 2)
            setup = QtWidgets.QLabel(e.setup)
            setup.setStyleSheet(f"color: {T['text']}; font-size: 13px;")
            g.addWidget(setup, r, c_setup)
            for k, z in enumerate(e.zellen[:nf]):
                lab = QtWidgets.QLabel(z.text)
                lab.setAlignment(QtCore.Qt.AlignCenter)
                if z.geaendert:
                    lab.setStyleSheet(f"QLabel {{ background: {T['changed_bg']}; color: {T['changed_text']};"
                                      " font-weight: 800; font-size: 13px; border-radius: 3px; padding: 2px 6px; }")
                    lab.setToolTip(f"{kurz_titel(self.felder[k])}: {z.wert} statt {z.alt} im Lauf darüber")
                else:
                    lab.setStyleSheet(f"color: {T['text'] if z.wert else T['dim']}; font-size: 13px;")
                g.addWidget(lab, r, c_w0 + k)
                w[f"wert{k}"] = lab
            for c, html in ((c_p, e.pmax), (c_m, e.mmax)):
                lab = QtWidgets.QLabel(html)
                lab.setStyleSheet(f"color: {T['text']}; font-size: 13px;")
                g.addWidget(lab, r, c)
            notiz = QtWidgets.QLineEdit(e.notiz)
            notiz.setPlaceholderText("Notiz …")
            notiz.setMinimumWidth(160)
            notiz.editingFinished.connect(lambda e=e, ed=notiz: self._notiz(e, ed))
            g.addWidget(notiz, r, c_notiz)
            w["notiz"] = notiz
            if e.mittelwert:
                b = QtWidgets.QPushButton("Quellen ▾")
                b.setToolTip(e.quellen_tip or "Quellläufe, Raster und Band des Mittelwerts")
                b.clicked.connect(lambda _c=False, rid=e.rid, b=b: self.quellen_gewaehlt.emit(rid, b))
            else:
                b = QtWidgets.QPushButton("Setup …")
                b.setToolTip("Setup und Notiz dieses Laufs ansehen oder ändern")
                b.clicked.connect(lambda _c=False, rid=e.rid: self.setup_gewaehlt.emit(rid))
            g.addWidget(b, r, c_knopf)
            w["knopf"] = b
            x = QtWidgets.QToolButton()
            x.setText("✕")
            x.setToolTip("aus der Auswertung entfernen")
            x.setStyleSheet(f"QToolButton {{ color: {T['text']}; border: 1px solid {T['border']}; border-radius: 3px;"
                            f" padding: 1px 6px; background: {T['panel']}; }}"
                            f"QToolButton:hover {{ border-color: {T['accent_border']}; }}")
            x.clicked.connect(lambda _c=False, rid=e.rid: self.entfernt.emit(rid))
            g.addWidget(x, r, c_x)
            w["x"] = x
            self.zeilen[e.rid] = w
        g.setColumnStretch(c_notiz, 1)
        g.setColumnMinimumWidth(2, 120)
        for k in range(nf):
            g.setColumnMinimumWidth(c_w0 + k, 52)
        g.setRowMinimumHeight(0, 18)
        for r in range(1, len(self.eintraege) + 1):
            g.setRowMinimumHeight(r, ZEILE_PX - 4)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(inner)
        zeilen = min(len(self.eintraege), SICHTBARE_ZEILEN)
        self._scroll_h = 22 + zeilen * ZEILE_PX + 4
        self._scroll_hoehe()
        self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded if len(self.eintraege) > SICHTBARE_ZEILEN
                                               else QtCore.Qt.ScrollBarAlwaysOff)

    def _scroll_hoehe(self):
        """Hoehe des Rollbereichs: Zeilen, dazu der waagerechte Rollbalken, wenn die Tabelle breiter ist."""
        inner = self.scroll.widget()
        if inner is None or self._scroll_h <= 0:
            return
        frei = self.scroll.maximumViewportSize().width()
        if len(self.eintraege) > SICHTBARE_ZEILEN:
            frei -= self.scroll.verticalScrollBar().sizeHint().width()
        breit = inner.minimumSizeHint().width() > frei
        h = self._scroll_h + (self.scroll.horizontalScrollBar().sizeHint().height() if breit else 0)
        if (self.scroll.minimumHeight(), self.scroll.maximumHeight()) != (h, h):
            self.scroll.setFixedHeight(h)

    def eventFilter(self, obj, ev):
        if obj is self.scroll and ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
            self._scroll_hoehe()
        return super().eventFilter(obj, ev)

    def _farbfeld(self, e: Eintrag) -> QtWidgets.QLabel:
        c = theme.laufarbe(e.farbe)
        lab = QtWidgets.QLabel("Ø" if e.mittelwert else "")
        lab.setAlignment(QtCore.Qt.AlignCenter)
        lab.setFixedSize(30, 18 if e.mittelwert else 14)
        schrift = "#000000" if theme.leuchtdichte(c) > 0.35 else "#ffffff"   # Kontrast auf der Laufarbe
        lab.setStyleSheet(f"background: {c}; border-radius: 3px; color: {schrift}; font-weight: 900; font-size: 13px;")
        lab.setToolTip("Mittelwert-Lauf" if e.mittelwert else "Laufarbe")
        return lab

    # ------------------------------------------------------------ Bedienung
    def _markiert(self, e: Eintrag, an: bool):
        e.markiert = an
        n_mark = len(self.markierte())
        self.mittelwert_btn.setText(f"Mittelwert bilden …  ({n_mark} markiert)")
        self.mittelwert_btn.setEnabled(n_mark >= 2)
        self.markiert_geaendert.emit(e.rid, an)

    def _notiz(self, e: Eintrag, ed: QtWidgets.QLineEdit):
        text = ed.text().strip()
        if text != e.notiz:
            e.notiz = text
            self.notiz_geaendert.emit(e.rid, text)
