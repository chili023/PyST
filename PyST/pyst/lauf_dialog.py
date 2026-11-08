"""
Dialog „Setup …“ eines Laufs in der Auswertung (Entwurf entwurf_auswertung_setup_dialog.png):
Setup des Laufs nachtraeglich zuordnen oder aendern, „Als neues Setup kopieren …“, die Setup-Felder
(dialogs.SetupForm) mit den Unterschieden zum Lauf davor in Gelb, Notiz zum Lauf.

Speichern:
- anderes Setup gewaehlt -> Lauf bekommt das Setup, der Setup-Schnappschuss des Laufs (Schnellwerte) kommt neu aus
  diesem Setup (der Lauf wurde mit diesem Setup gefahren)
- Felder geaendert -> nur die geaenderten Felder gehen ins Setup (wirkt auf alle Laeufe mit diesem Setup, der
  Hinweis nennt deren Zahl); im Schnappschuss dieses Laufs werden sie ebenfalls nachgefuehrt
- Notiz -> runs.notiz
Reine Funktionen (getestet): geaenderte_felder, schnappschuss_nachfuehren.
"""
from typing import Dict, List

from PySide6 import QtWidgets

from . import db as _db
from . import theme
from .dialogs import SetupForm
from .kacheln import zahl
from .laufleiste import datum_text, zeit_text

OHNE = ("id", "vehicle_id", "name", "notiz")


def geaenderte_felder(vorher: Dict[str, object], nachher: Dict[str, object]) -> Dict[str, str]:
    """Felder, deren Text sich geaendert hat: {feld: neuer Text} (Vergleich ueber db.wert_text)."""
    out = {}
    for k in dict.fromkeys(list(vorher) + list(nachher)):
        if k in OHNE:
            continue
        neu = _db.wert_text(nachher.get(k))
        if neu != _db.wert_text(vorher.get(k)):
            out[k] = neu
    return out


def schnappschuss_nachfuehren(setup_werte: List[Dict[str, str]], geaendert: Dict[str, str]) -> List[Dict[str, str]]:
    """Setup-Schnappschuss eines Laufs: geaenderte Felder uebernehmen (vorher geleert, die Aenderung steht ja im
    Setup); Felder, die der Schnappschuss nicht kennt, bleiben draussen."""
    out = []
    for e in setup_werte or []:
        e = dict(e)
        if e.get("feld") in geaendert:
            e["wert"] = geaendert[e["feld"]]
            e["vorher"] = ""
        out.append(e)
    return out


class LaufSetupDialog(QtWidgets.QDialog):
    """vorher: Lauf darueber in der Laufleiste (Vergleich, gelb) oder None. Nach accept(): self.geaendert True,
    wenn etwas gespeichert wurde."""

    def __init__(self, db, row, vorher=None, parent=None):
        super().__init__(parent)
        self.db, self.row, self.vorher = db, row, vorher
        self.geaendert = False
        veh = db.vehicle(row.vehicle_id) if row.vehicle_id else None
        self.setWindowTitle(f"Setup des Laufs {datum_text(row)}")
        self.resize(820, 760)
        lay = QtWidgets.QVBoxLayout(self)
        titel = " · ".join(x for x in (f"Lauf {datum_text(row)}", _db.Database.vehicle_name(veh) if veh else "",
                                       f"{zahl(row.p_max, 1)} PS" if row.p_max else "") if x)
        kopf = theme.rolle(QtWidgets.QLabel(titel), "titel")
        kopf.setStyleSheet("font-size: 18px;")
        lay.addWidget(kopf)

        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(QtWidgets.QLabel("Setup dieses Laufs"))
        self.setup = QtWidgets.QComboBox()
        self.setup.setMinimumWidth(260)
        self.setup.addItem("— ohne Setup —", None)
        if row.vehicle_id:
            for s in db.setups(row.vehicle_id):
                self.setup.addItem(s["name"] or "(ohne Namen)", s["id"])
        self.setup.setCurrentIndex(max(0, self.setup.findData(row.setup_id)))
        self.setup.setEnabled(bool(row.vehicle_id))
        hb.addWidget(self.setup)
        self.kopieren = QtWidgets.QPushButton("Als neues Setup kopieren …")
        self.kopieren.setEnabled(bool(row.vehicle_id))
        hb.addWidget(self.kopieren)
        hb.addStretch(1)
        lay.addLayout(hb)
        self.hinweis = theme.stufe(QtWidgets.QLabel(""), "warn")
        self.hinweis.setWordWrap(True)
        lay.addWidget(self.hinweis)
        self.vergleich = theme.rolle(QtWidgets.QLabel(""), "neben")
        lay.addWidget(self.vergleich)
        self.form = SetupForm()
        lay.addWidget(self.form, 1)
        lay.addWidget(theme.rolle(QtWidgets.QLabel("Notiz zum Lauf"), "titel"))
        self.notiz = QtWidgets.QLineEdit(row.notiz)
        lay.addWidget(self.notiz)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Save).setText("Speichern")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self._speichern)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setup.currentIndexChanged.connect(lambda _i: self._laden())
        self.kopieren.clicked.connect(self._kopieren)
        self._laden()

    # ------------------------------------------------------------ Anzeige
    def _werte_des_setups(self, sid) -> Dict[str, object]:
        """Felder fuer das Formular: beim eigenen Setup wie gefahren (Setup + Schnappschuss), sonst das Setup."""
        if sid == self.row.setup_id:
            return self.db.lauf_setup(self.row)
        return dict(self.db.setup(sid) or {}) if sid else {}

    def _laden(self):
        sid = self.setup.currentData()
        self.start = self._werte_des_setups(sid)
        self.form.set_values(self.start)
        self.form.setEnabled(bool(sid))
        if not self.row.vehicle_id:
            text = ("Der Lauf hat kein Fahrzeug. Fahrzeug und Setup zuerst links unter „Läufe & Kanäle“ mit "
                    "„Bearbeiten …“ zuordnen.")
        elif not sid:
            text = "Kein Setup zugeordnet. Setup wählen oder im Reiter „Fahrzeuge & Setups“ anlegen."
        else:
            andere = [r for r in self.db.runs(setup_id=sid, mittelwert=True) if r.id != self.row.id]
            wer = "nur dieser Lauf" if not andere else f"dieser und {len(andere)} weitere Läufe"
            text = (f"Ändern wirkt auf alle Läufe mit diesem Setup (hier: {wer}). Nur diesen Lauf ändern → "
                    "„Als neues Setup kopieren …“.")
        self.hinweis.setText(text)
        self._markieren()

    def _markieren(self):
        """Felder gelb, die anders sind als beim Lauf davor."""
        T = theme.tokens()
        alt = self.db.lauf_setup(self.vorher) if self.vorher is not None else None
        self.vergleich.setText(f"gelb = anders als beim Lauf davor, {zeit_text(self.vorher)}"
                               if self.vorher is not None else "")
        werte = self.form.values()
        for f in self.form.forms:
            for k, w in f.widgets.items():
                diff = alt is not None and bool(werte.get(k)) and _db.wert_text(werte.get(k)) != _db.wert_text(alt.get(k))
                w.setStyleSheet(f"background: {T['changed_bg']}; color: {T['changed_text']}; font-weight: 700;"
                                if diff else "")
                w.setProperty("anders", diff)

    def anders_felder(self) -> List[str]:
        """Felder, die gelb markiert sind (fuer Tests)."""
        return [k for f in self.form.forms for k, w in f.widgets.items() if w.property("anders")]

    # ------------------------------------------------------------ Aktionen
    def _kopieren(self):
        sid = self.setup.currentData()
        alt = self.db.setup(sid) if sid else None
        name, ok = QtWidgets.QInputDialog.getText(self, "Als neues Setup kopieren", "Name des neuen Setups:",
                                                  text=((alt or {}).get("name", "") + " (Kopie)").strip())
        name = name.strip()
        if not ok or not name:
            return
        neu = self.db.save_setup(self.row.vehicle_id, name, self.form.values(), (alt or {}).get("notiz", ""))
        self.setup.blockSignals(True)
        self.setup.addItem(name, neu)
        self.setup.setCurrentIndex(self.setup.count() - 1)
        self.setup.blockSignals(False)
        self.start = dict(self.db.setup(neu) or {})
        self.form.setEnabled(True)
        self.hinweis.setText("Neues Setup angelegt – es gilt nur für diesen Lauf, solange kein anderer Lauf es nutzt.")
        self._markieren()

    def _speichern(self):
        sid = self.setup.currentData()
        kw = {}
        werte = list(self.row.setup_werte)
        if sid != self.row.setup_id:
            kw["setup_id"] = sid
            felder = [e["feld"] for e in werte if e.get("feld")] or \
                self.db.schnellwert_plaetze(self.row.vehicle_id)
            werte = _db.setup_schnappschuss(self.db.setup(sid) if sid else {}, felder)
            kw["nur_lauf"] = False
        if sid:
            geaendert = geaenderte_felder(self.start, self.form.values())
            if geaendert:
                s = self.db.setup(sid) or {}
                data = {k: v for k, v in s.items() if k not in OHNE}
                data.update(geaendert)
                self.db.save_setup(s.get("vehicle_id") or self.row.vehicle_id, s.get("name", "Setup"), data,
                                   s.get("notiz", ""), sid)
                werte = schnappschuss_nachfuehren(werte, geaendert)
        if werte != list(self.row.setup_werte):
            kw["setup_werte"] = werte
        notiz = self.notiz.text().strip()
        if notiz != self.row.notiz:
            kw["notiz"] = notiz
        if kw:
            self.db.update_run(self.row.id, **kw)
        self.geaendert = bool(kw) or (bool(sid) and bool(geaenderte_felder(self.start, self.form.values())))
        self.accept()
